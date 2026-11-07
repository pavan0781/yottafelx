import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path

from returnables.generator import (
    Scale,
    _validate_sqlite,
    _write_dataset,
    generate_scaled_dataset,
    validate_dataset,
)


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.dataset = generate_scaled_dataset(Scale(30, 15), seed=20261022)

    def test_seeded_generation_is_reproducible(self):
        other = generate_scaled_dataset(Scale(30, 15), seed=20261022)
        self.assertEqual(self.dataset, other)

    def test_volume_and_required_edge_cases(self):
        tables = self.dataset["tables"]
        self.assertEqual(len(tables["reusable_assets"]), 30)
        self.assertEqual(len(tables["shipments"]), 15)
        self.assertEqual(len(tables["custody_events"]), 150)
        issue_types = {row["issue_type"] for row in self.dataset["ground_truth"]}
        self.assertTrue({
            "SIMULTANEOUS_LOCATIONS",
            "RETURN_BEFORE_DISPATCH",
            "DUPLICATE_DEPOSIT_REFUND",
            "REPAIR_WITHOUT_INSPECTION",
            "CUSTODY_ACTOR_MISSING",
        }.issubset(issue_types))

    def test_validation_passes_and_labels_exceptions(self):
        result = validate_dataset(self.dataset)
        self.assertTrue(result["passed"], result["errors"])
        self.assertEqual(result["metrics"]["exception_assets"], 5)

    def test_sql_schema_accepts_generated_rows(self):
        schema_path = Path(__file__).resolve().parents[1] / "schema.sql"
        _validate_sqlite(self.dataset, schema_path)

    def test_exports_are_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as root:
            first = Path(root) / "first"
            second = Path(root) / "second"
            _write_dataset(self.dataset, first)
            _write_dataset(generate_scaled_dataset(Scale(30, 15), seed=20261022), second)
            hashes = lambda directory: {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in directory.iterdir()
            }
            self.assertEqual(hashes(first), hashes(second))
            connection = sqlite3.connect(":memory:")
            try:
                root = Path(__file__).resolve().parents[1]
                connection.executescript((root / "schema.sql").read_text(encoding="utf-8"))
                connection.executescript((first / "seed.sql").read_text(encoding="utf-8"))
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM custody_events").fetchone()[0],
                    150,
                )
                self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            finally:
                connection.close()

    def test_scale_requires_room_for_edge_cases(self):
        with self.assertRaises(ValueError):
            generate_scaled_dataset(Scale(4, 2))


if __name__ == "__main__":
    unittest.main()
