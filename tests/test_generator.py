import csv
import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from returnables.generator import (
    ANOMALY_DECISIONS,
    GT_FIELDS,
    TABLE_FIELDS,
    Scale,
    _validate_sqlite,
    _write_dataset,
    _write_operational_tables,
    generate_clean_dataset,
    generate_dataset,
    verify_reproducibility,
)
from returnables.validator import ANOMALY_TYPES, validate_exports
from returnables.verify import verify_sqlite


ROOT = Path(__file__).resolve().parents[1]


def read_csv(path):
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def write_csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys(), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


class GeneratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clean = generate_clean_dataset(Scale(200, 100), seed=20261022)

    def test_seeded_generation_is_reproducible(self):
        other = generate_clean_dataset(Scale(200, 100), seed=20261022)
        self.assertEqual(self.clean, other)

    def test_clean_baseline_passes_independent_export_validator(self):
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "baseline"
            _write_operational_tables(self.clean, output)
            report = validate_exports(output)
            self.assertTrue(report["passed"], report["findings"])
            self.assertTrue(all(report["checks"].values()), report["checks"])
            self.assertEqual(report["findings"], [])

    def test_starter_volumes_and_seeded_anomaly_coverage(self):
        dataset = generate_dataset("starter", seed=20261022)
        tables = dataset["tables"]
        self.assertEqual(len(tables["reusable_assets"]), 200)
        self.assertEqual(len(tables["shipments"]), 100)
        self.assertEqual(len(tables["custody_events"]), 1_000)
        self.assertGreater(len(tables["return_authorizations"]), 200)
        anomaly_labels = {
            row["issue_type"] for row in dataset["ground_truth"]
            if row["issue_type"] in ANOMALY_TYPES
        }
        self.assertEqual(anomaly_labels, set(ANOMALY_TYPES))
        self.assertNotIn("EXCEPTION", {row["lifecycle_status"] for row in tables["asset_snapshots"]})
        self.assertTrue(all(row["current_location_id"] for row in tables["asset_snapshots"]))
        self.assertEqual(len({row["asset_id"] for row in tables["reusable_assets"]}), 200)

    def test_anomaly_precision_and_recall_use_exact_record_ids(self):
        dataset = generate_dataset("starter", seed=20261022)
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "data"
            _write_operational_tables(dataset, output)
            report = validate_exports(output)
            self.assertTrue(report["passed"], report["unmatched_findings"])
            for result in report["anomaly_metrics"].values():
                self.assertEqual(result["precision"], 1.0)
                self.assertEqual(result["recall"], 1.0)
                self.assertEqual(result["false_positive"], 0)
                self.assertEqual(result["false_negative"], 0)
            labels = {
                (row["issue_type"], row["offending_record_id"])
                for row in dataset["ground_truth"]
                if row["issue_type"] in ANOMALY_TYPES
            }
            predictions = {
                (row["issue_type"], row["record_id"])
                for row in report["findings"]
                if row["issue_type"] in ANOMALY_TYPES
            }
            self.assertEqual(labels, predictions)

    def test_anomaly_counts_are_configurable(self):
        counts = {issue_type: 2 for issue_type in ANOMALY_TYPES}
        dataset = generate_dataset("starter", seed=20261022, anomaly_counts=counts)
        actual = {
            issue_type: sum(row["issue_type"] == issue_type for row in dataset["ground_truth"])
            for issue_type in ANOMALY_TYPES
        }
        self.assertEqual(actual, counts)

    def test_sql_schema_accepts_export_and_views_reconcile(self):
        dataset = generate_dataset("starter", seed=20261022)
        with tempfile.TemporaryDirectory() as root:
            output = Path(root) / "data"
            _write_operational_tables(dataset, output)
            validation = validate_exports(output)
            _write_dataset(dataset, output, validation)
            _validate_sqlite(dataset, ROOT / "schema.sql")
            sqlite_result = verify_sqlite(ROOT / "schema.sql", output / "seed.sql")
            self.assertTrue(sqlite_result["passed"], sqlite_result)
            self.assertEqual(sqlite_result["row_counts"]["custody_events"], 1_000)

    def test_cross_run_checksums_match(self):
        report = verify_reproducibility("starter", 20261022)
        self.assertTrue(report["passed"])
        self.assertGreater(report["files_compared"], 0)


class IndependentValidatorNegativeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.output = Path(self.temporary.name) / "exports"
        _write_operational_tables(self._new_clean(), self.output)

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def _new_clean():
        return generate_clean_dataset(Scale(20, 3), seed=20261022)

    def _edit(self, table, mutation):
        path = self.output / f"{table}.csv"
        rows = read_csv(path)
        mutation(rows)
        write_csv(path, rows)

    def _has(self, issue_type):
        report = validate_exports(self.output)
        self.assertEqual(
            {row["issue_type"] for row in report["findings"]},
            {issue_type},
            report["findings"],
        )
        return report

    def test_primary_key_duplicate_is_reported(self):
        self._edit("owners", lambda rows: rows.append(deepcopy(rows[0])))
        self._has("DUPLICATE_PRIMARY_KEY")

    def test_foreign_key_violation_is_reported(self):
        self._edit("locations", lambda rows: rows[0].update(owner_id="OWNER-999"))
        self._has("FOREIGN_KEY_VIOLATION")

    def test_invalid_enum_is_reported(self):
        self._edit("operators", lambda rows: rows[0].update(role="UNKNOWN_ROLE"))
        self._has("INVALID_ENUM")

    def test_date_order_violation_is_reported(self):
        self._edit(
            "shipments",
            lambda rows: rows[0].update(expected_return_at="2026-01-01T00:00:00+00:00"),
        )
        self._has("DATE_ORDER")

    def test_ledger_arithmetic_violation_is_reported(self):
        def mutate(rows):
            entry = next(row for row in rows if row["entry_type"] != "DEPOSIT_CHARGE")
            entry["amount_cents"] = "-1"

        self._edit(
            "deposit_ledger",
            mutate,
        )
        self._has("LEDGER_RECONCILIATION")

    def test_invalid_state_transition_is_reported(self):
        def mutate(rows):
            transition = next(
                row for row in rows
                if row["from_state"] == "AVAILABLE"
                and row["event_type"] == "CUSTODY_CHECKPOINT"
            )
            rows.remove(transition)
        self._edit("lifecycle_transitions", mutate)
        self._has("INVALID_STATE_TRANSITION")

    def test_semantic_duplicate_is_reported(self):
        def mutate(rows):
            source = next(row for row in rows if row["event_type"] == "CUSTODY_CHECKPOINT")
            duplicate = deepcopy(source)
            duplicate["event_id"] = "CUST-ASSET-9999999-999999"
            rows.append(duplicate)
        self._edit("custody_events", mutate)
        self._has("DUPLICATE_RECORD")


if __name__ == "__main__":
    unittest.main()
