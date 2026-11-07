from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def verify_sqlite(schema_path: Path, seed_path: Path) -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.executescript(seed_path.read_text(encoding="utf-8"))
        foreign_key_violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        counts = {
            table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "reusable_assets", "shipments", "custody_events", "return_authorizations",
                "deposit_ledger",
            )
        }
        duplicate_refund_charges = connection.execute("""
            SELECT COUNT(*) FROM (
                SELECT reference_entry_id
                FROM deposit_ledger
                WHERE entry_type = 'DEPOSIT_REFUND'
                GROUP BY reference_entry_id
                HAVING COUNT(*) > 1
            )
        """).fetchone()[0]
        balance_mismatches = connection.execute("""
            SELECT COUNT(*)
            FROM asset_snapshots s
            LEFT JOIN (
                SELECT asset_id, SUM(amount_cents) AS balance_cents
                FROM deposit_ledger
                GROUP BY asset_id
            ) l ON l.asset_id = s.asset_id
            WHERE s.deposit_balance_cents != COALESCE(l.balance_cents, 0)
        """).fetchone()[0]
        location_mismatches = connection.execute("""
            SELECT COUNT(*)
            FROM asset_snapshots s
            LEFT JOIN asset_location_as_of l ON l.asset_id = s.asset_id
            WHERE l.current_location_id IS NULL
               OR s.current_location_id != l.current_location_id
        """).fetchone()[0]
        age_mismatches = connection.execute("""
            SELECT COUNT(*)
            FROM asset_snapshots s
            LEFT JOIN asset_days_outstanding d ON d.asset_id = s.asset_id
            WHERE d.days_outstanding IS NULL
               OR s.days_outstanding != d.days_outstanding
        """).fetchone()[0]
        return {
            "passed": (
                not foreign_key_violations
                and balance_mismatches == 0
                and location_mismatches == 0
                and age_mismatches == 0
            ),
            "row_counts": counts,
            "foreign_key_violations": foreign_key_violations,
            "duplicate_refund_charge_count": duplicate_refund_charges,
            "snapshot_balance_mismatch_count": balance_mismatches,
            "snapshot_location_mismatch_count": location_mismatches,
            "snapshot_age_mismatch_count": age_mismatches,
        }
    finally:
        connection.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load schema and seed into SQLite and verify core outputs.")
    parser.add_argument("--schema", type=Path, default=Path("schema.sql"))
    parser.add_argument("--seed", type=Path, default=Path("output/starter/seed.sql"))
    args = parser.parse_args(argv)
    try:
        result = verify_sqlite(args.schema, args.seed)
    except (OSError, sqlite3.Error) as error:
        parser.exit(1, f"SQLite verification failed: {error}\n")
    print(json.dumps(result, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
