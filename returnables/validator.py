from __future__ import annotations

import csv
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


AS_OF = date(2026, 11, 7)
ANOMALY_TYPES = (
    "SIMULTANEOUS_LOCATIONS",
    "RETURN_BEFORE_DISPATCH",
    "DUPLICATE_DEPOSIT_REFUND",
    "REPAIR_WITHOUT_INSPECTION",
    "CUSTODY_ACTOR_MISSING",
)

TABLE_KEYS = {
    "asset_types": ("asset_type_id",),
    "owners": ("owner_id",),
    "locations": ("location_id",),
    "operators": ("operator_id",),
    "shipments": ("shipment_id",),
    "reusable_assets": ("asset_id",),
    "shipment_assets": ("shipment_id", "asset_id", "cycle_number"),
    "custody_events": ("event_id",),
    "return_authorizations": ("rma_id",),
    "inspections": ("inspection_id",),
    "repairs": ("repair_id",),
    "deposit_ledger": ("ledger_id",),
    "asset_snapshots": ("asset_id",),
    "lifecycle_transitions": ("from_state", "event_type", "to_state"),
}

FOREIGN_KEYS = {
    "locations": [("owner_id", "owners", "owner_id")],
    "operators": [("location_id", "locations", "location_id")],
    "shipments": [
        ("owner_id", "owners", "owner_id"),
        ("origin_location_id", "locations", "location_id"),
        ("destination_location_id", "locations", "location_id"),
        ("carrier_id", "operators", "operator_id"),
    ],
    "reusable_assets": [
        ("asset_type_id", "asset_types", "asset_type_id"),
        ("owner_id", "owners", "owner_id"),
        ("home_location_id", "locations", "location_id"),
    ],
    "shipment_assets": [
        ("shipment_id", "shipments", "shipment_id"),
        ("asset_id", "reusable_assets", "asset_id"),
    ],
    "custody_events": [
        ("asset_id", "reusable_assets", "asset_id"),
        ("shipment_id", "shipments", "shipment_id"),
        ("from_location_id", "locations", "location_id"),
        ("to_location_id", "locations", "location_id"),
        ("actor_id", "operators", "operator_id"),
    ],
    "return_authorizations": [
        ("asset_id", "reusable_assets", "asset_id"),
        ("shipment_id", "shipments", "shipment_id"),
    ],
    "inspections": [
        ("asset_id", "reusable_assets", "asset_id"),
        ("rma_id", "return_authorizations", "rma_id"),
    ],
    "repairs": [
        ("asset_id", "reusable_assets", "asset_id"),
        ("inspection_id", "inspections", "inspection_id"),
        ("rma_id", "return_authorizations", "rma_id"),
    ],
    "deposit_ledger": [
        ("asset_id", "reusable_assets", "asset_id"),
        ("shipment_id", "shipments", "shipment_id"),
        ("rma_id", "return_authorizations", "rma_id"),
        ("reference_entry_id", "deposit_ledger", "ledger_id"),
    ],
    "asset_snapshots": [
        ("asset_id", "reusable_assets", "asset_id"),
        ("current_location_id", "locations", "location_id"),
    ],
}

ENUMS = {
    "owners": {"owner_type": {"SUPPLIER", "POOL_OPERATOR", "CUSTOMER"}},
    "locations": {
        "location_type": {"WAREHOUSE", "CUSTOMER_SITE", "CARRIER_YARD"},
    },
    "operators": {
        "role": {
            "PLANNER", "CARRIER", "WAREHOUSE_OPERATOR", "SUPPLIER",
            "LOGISTICS_ANALYST", "OPERATIONS_MANAGER",
        },
    },
    "shipments": {
        "status": {"IN_TRANSIT", "PARTIALLY_RETURNED", "CLOSED", "LOST"},
    },
    "custody_events": {
        "event_type": {
            "DISPATCH", "CUSTODY_HANDOFF", "CUSTODY_HANDOFF_DISPUTED",
            "CUSTODY_DISPUTE_RESOLVED", "RETURN_DISPATCH", "RETURN_RECEIVED",
            "LOSS_DECLARED", "CUSTODY_CHECKPOINT",
        },
    },
    "return_authorizations": {"status": {"RECEIVED", "CLOSED_LOST", "OPEN"}},
    "inspections": {
        "condition": {"GOOD", "DAMAGED"},
        "decision": {"RESTOCK", "REPAIR", "QUARANTINE"},
    },
    "repairs": {"status": {"OPEN", "CLOSED"}},
    "deposit_ledger": {
        "entry_type": {
            "DEPOSIT_CHARGE", "LATE_FEE_DEDUCTION", "DAMAGE_DEDUCTION",
            "LOSS_FORFEIT", "DEPOSIT_REFUND",
        },
        "currency": {"USD"},
    },
    "asset_snapshots": {
        "lifecycle_status": {
            "AVAILABLE", "IN_TRANSIT", "AT_CUSTOMER", "CUSTODY_DISPUTED",
            "RETURN_IN_TRANSIT", "OUTSTANDING", "OVERDUE", "LOST",
            "IN_REPAIR", "QUARANTINED",
        },
    },
}

ID_PATTERNS = {
    "asset_types": ("asset_type_id", re.compile(r"^ATYPE-\d{3}$")),
    "owners": ("owner_id", re.compile(r"^OWNER-\d{3}$")),
    "locations": ("location_id", re.compile(r"^LOC-\d{3}$")),
    "operators": ("operator_id", re.compile(r"^OP-\d{3}$")),
    "shipments": ("shipment_id", re.compile(r"^SHP-2026-\d{6}$")),
    "reusable_assets": ("asset_id", re.compile(r"^ASSET-\d{7}$")),
    "return_authorizations": ("rma_id", re.compile(r"^RMA-2026-\d{6}$")),
    "repairs": ("repair_id", re.compile(r"^REP-2026-\d{6}$")),
}

DATE_FIELDS = {
    "shipments": ("dispatched_at", "expected_return_at"),
    "reusable_assets": ("created_at",),
    "custody_events": ("occurred_at",),
    "return_authorizations": ("authorized_at", "expected_return_at", "processed_at"),
    "inspections": ("inspected_at",),
    "repairs": ("opened_at", "closed_at"),
    "deposit_ledger": ("posted_at",),
}


def _read_csv(directory: Path, table_name: str) -> list[dict[str, Any]]:
    path = directory / f"{table_name}.csv"
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for row in rows:
        for field, value in tuple(row.items()):
            if value == "":
                row[field] = None
    return rows


def _day(value: str | None) -> date | None:
    return datetime.fromisoformat(value).date() if value else None


def _datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _bool(value: Any) -> bool:
    return value is True or str(value).lower() in {"1", "true", "yes"}


def validate_exports(directory: Path) -> dict[str, Any]:
    directory = Path(directory)
    table_names = tuple(TABLE_KEYS)
    tables = {name: _read_csv(directory, name) for name in table_names}
    ground_truth = _read_csv(directory, "ground_truth")
    findings: list[dict[str, Any]] = []

    def add(
        issue_type: str,
        record_id: str | None,
        entity_id: str | None,
        message: str,
        severity: str = "ERROR",
    ) -> None:
        findings.append({
            "issue_type": issue_type,
            "record_id": record_id,
            "entity_id": entity_id,
            "severity": severity,
            "message": message,
        })

    # Primary-key uniqueness.
    for table_name, key_fields in TABLE_KEYS.items():
        seen: set[tuple[Any, ...]] = set()
        for row in tables[table_name]:
            key = tuple(row.get(field) for field in key_fields)
            if key in seen:
                add("DUPLICATE_PRIMARY_KEY", str(key), str(key[0]), f"{table_name} repeats primary key {key}.")
            seen.add(key)

    # Foreign-key integrity.
    for table_name, references in FOREIGN_KEYS.items():
        for field, parent_table, parent_field in references:
            parent_values = {row.get(parent_field) for row in tables[parent_table]}
            for row in tables[table_name]:
                value = row.get(field)
                if value is not None and value not in parent_values:
                    add(
                        "FOREIGN_KEY_VIOLATION", row.get(TABLE_KEYS[table_name][0]),
                        row.get("asset_id"), f"{table_name}.{field} references missing {parent_table}.{value}.",
                    )

    # Allowed values and identifier conventions.
    for table_name, fields in ENUMS.items():
        for field, values in fields.items():
            for row in tables[table_name]:
                if row.get(field) not in values:
                    add(
                        "INVALID_ENUM", row.get(TABLE_KEYS[table_name][0]),
                        row.get("asset_id"), f"{table_name}.{field} has invalid value {row.get(field)!r}.",
                    )
    for table_name, (field, pattern) in ID_PATTERNS.items():
        for row in tables[table_name]:
            if not row.get(field) or not pattern.fullmatch(str(row[field])):
                add(
                    "INVALID_ID_FORMAT", row.get(field), row.get("asset_id"),
                    f"{table_name}.{field} does not match the documented ID convention.",
                )

    # Date domains and ordering.
    shipments = {row["shipment_id"]: row for row in tables["shipments"]}
    rmas = {row["rma_id"]: row for row in tables["return_authorizations"]}
    assets = {row["asset_id"]: row for row in tables["reusable_assets"]}
    asset_types = {row["asset_type_id"]: row for row in tables["asset_types"]}
    for table_name, fields in DATE_FIELDS.items():
        for row in tables[table_name]:
            for field in fields:
                value = _datetime(row.get(field))
                if value is None:
                    continue
                if value.year != 2026 or value.date() > AS_OF:
                    add(
                        "DATE_ORDER", row.get(TABLE_KEYS[table_name][0]), row.get("asset_id"),
                        f"{table_name}.{field} must be within 2026 and no later than {AS_OF.isoformat()}.",
                    )
    for row in tables["shipments"]:
        if row.get("dispatched_at") and row.get("expected_return_at"):
            if row["expected_return_at"] < row["dispatched_at"]:
                add("DATE_ORDER", row["shipment_id"], None, "Shipment expected return precedes dispatch.")
    for row in tables["return_authorizations"]:
        if row.get("authorized_at") and row.get("expected_return_at"):
            if row["expected_return_at"] < row["authorized_at"]:
                add("DATE_ORDER", row["rma_id"], row["asset_id"], "RMA due date precedes authorization.")
        if row.get("processed_at") and row.get("authorized_at"):
            if row["processed_at"] < row["authorized_at"]:
                add("DATE_ORDER", row["rma_id"], row["asset_id"], "RMA was processed before authorization.")
    for row in tables["repairs"]:
        if row.get("closed_at") and row["closed_at"] < row["opened_at"]:
            add("DATE_ORDER", row["repair_id"], row["asset_id"], "Repair closes before it opens.")

    # Relationship bridge and event/transaction joins.
    bridge_keys: set[tuple[str, str, str]] = set()
    bridge_by_asset_cycle: set[tuple[str, str, str]] = set()
    for row in tables["shipment_assets"]:
        key = (row["shipment_id"], row["asset_id"], row["cycle_number"])
        if key in bridge_keys:
            add("DUPLICATE_RECORD", row["asset_id"], row["asset_id"], "Duplicate shipment-assets bridge row.")
        bridge_keys.add(key)
        bridge_by_asset_cycle.add((row["asset_id"], row["cycle_number"], row["shipment_id"]))
    rma_by_trip = {}
    for row in tables["return_authorizations"]:
        rma_by_trip[(row["asset_id"], row["cycle_number"])] = row
        if (row["asset_id"], row["cycle_number"], row["shipment_id"]) not in bridge_by_asset_cycle:
            add("RELATIONSHIP_MISMATCH", row["rma_id"], row["asset_id"], "RMA does not match a shipment-assets bridge row.")
    for table_name in ("custody_events", "deposit_ledger"):
        for row in tables[table_name]:
            key = (row["asset_id"], row["cycle_number"], row["shipment_id"])
            if key not in bridge_by_asset_cycle:
                add("RELATIONSHIP_MISMATCH", row[TABLE_KEYS[table_name][0]], row["asset_id"], f"{table_name} is not linked through shipment_assets.")

    # Discover injected anomalies by reading source tables only.
    events_by_asset_cycle: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    events_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in tables["custody_events"]:
        events_by_asset_cycle[(event["asset_id"], event["cycle_number"])].append(event)
        events_by_asset[event["asset_id"]].append(event)
        if event.get("actor_id") is None:
            add(
                "CUSTODY_ACTOR_MISSING", event["event_id"], event["asset_id"],
                "Custody event has no attributed actor.",
            )
    simultaneous_count = 0
    for (_, _), rows in events_by_asset_cycle.items():
        by_time: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            if row.get("to_location_id"):
                by_time[row["occurred_at"]].append(row)
        for timestamp, same_time in by_time.items():
            destinations = {row["to_location_id"] for row in same_time}
            if len(destinations) > 1:
                first_destination = sorted(same_time, key=lambda row: row["event_id"])[0]["to_location_id"]
                for row in same_time:
                    if row["to_location_id"] != first_destination:
                        simultaneous_count += 1
                        add(
                            "SIMULTANEOUS_LOCATIONS", row["event_id"], row["asset_id"],
                            f"Conflicting destination observations at {timestamp}.",
                        )

    for key, rows in events_by_asset_cycle.items():
        dispatch = next((row for row in rows if row["event_type"] == "DISPATCH"), None)
        receipt = next((row for row in rows if row["event_type"] == "RETURN_RECEIVED"), None)
        if dispatch and receipt and receipt["occurred_at"] < dispatch["occurred_at"]:
            add(
                "RETURN_BEFORE_DISPATCH", receipt["event_id"], receipt["asset_id"],
                "Return receipt precedes dispatch for the same asset cycle.",
            )

    refunds_by_charge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tables["deposit_ledger"]:
        if row.get("entry_type") == "DEPOSIT_REFUND":
            refunds_by_charge[row.get("reference_entry_id")].append(row)
    for charge_id, rows in refunds_by_charge.items():
        for duplicate in sorted(rows, key=lambda row: row["ledger_id"])[1:]:
            add(
                "DUPLICATE_DEPOSIT_REFUND", duplicate["ledger_id"], duplicate["asset_id"],
                f"Multiple refunds reference deposit charge {charge_id}.",
            )

    inspections_by_id = {row["inspection_id"]: row for row in tables["inspections"]}
    inspection_by_rma = {row["rma_id"]: row for row in tables["inspections"]}
    repair_by_rma = {row["rma_id"]: row for row in tables["repairs"]}
    repairs_by_asset = defaultdict(list)
    for repair in tables["repairs"]:
        repairs_by_asset[repair["asset_id"]].append(repair)
        if repair["status"] == "CLOSED" and not repair.get("inspection_id"):
            add(
                "REPAIR_WITHOUT_INSPECTION", repair["repair_id"], repair["asset_id"],
                "Closed repair has no inspection evidence.",
            )

    # Ledger sign/reference checks and per-trip settlement reconciliation.
    ledger_by_id = {row["ledger_id"]: row for row in tables["deposit_ledger"]}
    ledger_by_trip: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in tables["deposit_ledger"]:
        ledger_by_trip[(row["asset_id"], row["cycle_number"])].append(row)
        amount = int(row["amount_cents"])
        reference = ledger_by_id.get(row.get("reference_entry_id"))
        if row["entry_type"] == "DEPOSIT_CHARGE":
            if amount <= 0 or row.get("reference_entry_id") is not None:
                add("LEDGER_RECONCILIATION", row["ledger_id"], row["asset_id"], "Deposit charge sign/reference is invalid.")
        elif amount >= 0 or reference is None or reference["entry_type"] != "DEPOSIT_CHARGE":
            add("LEDGER_RECONCILIATION", row["ledger_id"], row["asset_id"], "Deduction/refund must be negative and reference a charge.")
        elif (
            reference["asset_id"] != row["asset_id"]
            or reference["cycle_number"] != row["cycle_number"]
            or abs(amount) > int(reference["amount_cents"])
        ):
            add("LEDGER_RECONCILIATION", row["ledger_id"], row["asset_id"], "Ledger entry does not reconcile to its referenced charge.")
        if amount == 0:
            add("LEDGER_RECONCILIATION", row["ledger_id"], row["asset_id"], "Zero-value ledger rows are prohibited.")

    settlement_types = {
        "LATE_FEE_DEDUCTION", "DAMAGE_DEDUCTION", "LOSS_FORFEIT", "DEPOSIT_REFUND"
    }
    for key, rma in rma_by_trip.items():
        trip_ledger = ledger_by_trip.get(key, [])
        charge_rows = [row for row in trip_ledger if row["entry_type"] == "DEPOSIT_CHARGE"]
        if len(charge_rows) != 1:
            add("LEDGER_RECONCILIATION", rma["rma_id"], rma["asset_id"], "Each asset cycle requires exactly one deposit charge.")
            continue
        charge = charge_rows[0]
        deposit = int(charge["amount_cents"])
        settlement_rows = [row for row in trip_ledger if row["entry_type"] in settlement_types]
        cycle_events = events_by_asset_cycle.get(key, [])
        loss_event = next((row for row in cycle_events if row["event_type"] == "LOSS_DECLARED"), None)
        return_event = next((row for row in cycle_events if row["event_type"] == "RETURN_RECEIVED"), None)
        inspection = inspection_by_rma.get(rma["rma_id"])
        repair = repair_by_rma.get(rma["rma_id"])
        asset = assets.get(rma["asset_id"])
        asset_type = asset_types.get(asset["asset_type_id"]) if asset else None
        if not asset_type:
            continue
        expected: Counter[str] = Counter()
        if loss_event:
            expected["LOSS_FORFEIT"] = -deposit
        elif return_event and not (repair and repair["status"] == "OPEN"):
            due = _day(rma["expected_return_at"])
            returned = _day(rma["processed_at"])
            days_late = max(0, (returned - due).days) if due and returned else 0
            late_fee = min(
                days_late * int(asset_type["late_fee_cents_per_day"]),
                deposit,
            )
            damage_assessed = 0
            if inspection and inspection["decision"] == "REPAIR" and repair:
                damage_assessed = int(repair["cost_cents"])
            elif inspection and inspection["decision"] == "QUARANTINE":
                damage_assessed = int(asset_type["replacement_cost_cents"])
            damage_deduction = min(damage_assessed, deposit - late_fee)
            refund = deposit - late_fee - damage_deduction
            if late_fee:
                expected["LATE_FEE_DEDUCTION"] = -late_fee
            if damage_deduction:
                expected["DAMAGE_DEDUCTION"] = -damage_deduction
            if refund:
                expected["DEPOSIT_REFUND"] = -refund
        actual = Counter()
        for row in settlement_rows:
            actual[row["entry_type"]] += int(row["amount_cents"])
        if actual != expected:
            extra_rows = [
                row for row in settlement_rows
                if actual[row["entry_type"]] != expected[row["entry_type"]]
            ]
            offending_id = extra_rows[-1]["ledger_id"] if extra_rows else rma["rma_id"]
            add(
                "LEDGER_RECONCILIATION", offending_id, rma["asset_id"],
                f"Settlement rows {dict(actual)} differ from rule-derived settlement {dict(expected)}.",
            )
        if settlement_rows:
            settlement_date = max(row["posted_at"] for row in settlement_rows)
            required_after = (
                repair["closed_at"] if repair and repair["status"] == "CLOSED"
                else inspection["inspected_at"] if inspection
                else return_event["occurred_at"] if return_event
                else None
            )
            if required_after and settlement_date < required_after:
                add("LEDGER_RECONCILIATION", rma["rma_id"], rma["asset_id"], "Settlement precedes inspection or repair closure.")

    # State-machine validation and location-chain replay.
    transitions = {
        (row["from_state"], row["event_type"]): row["to_state"]
        for row in tables["lifecycle_transitions"]
    }
    snapshot_by_asset = {row["asset_id"]: row for row in tables["asset_snapshots"]}
    ledger_balance: Counter[str] = Counter()
    for row in tables["deposit_ledger"]:
        ledger_balance[row["asset_id"]] += int(row["amount_cents"])
    replay_by_asset = {}
    for asset_id, asset in assets.items():
        current_location = asset["home_location_id"]
        state = "AVAILABLE"
        ordered = sorted(events_by_asset.get(asset_id, []), key=lambda row: (row["occurred_at"], row["event_id"]))
        for event in ordered:
            target_state = transitions.get((state, event["event_type"]))
            if target_state is None:
                add(
                    "INVALID_STATE_TRANSITION", event["event_id"], asset_id,
                    f"Transition {state} --{event['event_type']}--> is not allowed.",
                )
            else:
                state = target_state
            if event["event_type"] == "CUSTODY_HANDOFF_DISPUTED":
                if event["from_location_id"] != current_location:
                    add("LOCATION_CHAIN_MISMATCH", event["event_id"], asset_id, "Disputed handoff does not start at the last verified location.")
            elif event["event_type"] == "LOSS_DECLARED":
                if event["from_location_id"] != current_location or event["to_location_id"] != current_location:
                    add("LOCATION_CHAIN_MISMATCH", event["event_id"], asset_id, "Loss declaration must preserve the last verified location.")
            elif event["event_type"] == "CUSTODY_CHECKPOINT":
                if event["from_location_id"] != current_location or event["to_location_id"] != current_location:
                    add("LOCATION_CHAIN_MISMATCH", event["event_id"], asset_id, "Checkpoint must preserve the last verified location.")
            else:
                if event["from_location_id"] != current_location:
                    add(
                        "LOCATION_CHAIN_MISMATCH", event["event_id"], asset_id,
                        f"Event starts at {event['from_location_id']}, previous verified location is {current_location}.",
                    )
                if event.get("to_location_id"):
                    current_location = event["to_location_id"]
        replay_by_asset[asset_id] = (current_location, state, ordered)

    for asset_id, snapshot in snapshot_by_asset.items():
        current_location, state, ordered = replay_by_asset.get(
            asset_id, (assets.get(asset_id, {}).get("home_location_id"), "AVAILABLE", [])
        )
        if snapshot.get("current_location_id") != current_location:
            add("SNAPSHOT_MISMATCH", asset_id, asset_id, "Snapshot location differs from deterministic event replay.")
        if int(snapshot.get("deposit_balance_cents") or 0) != ledger_balance[asset_id]:
            add("LEDGER_RECONCILIATION", asset_id, asset_id, "Snapshot balance differs from ledger sum.")
        dispatch = next(
            (row for row in reversed(ordered) if row["event_type"] == "DISPATCH"),
            None,
        )
        cycle_number = int(dispatch["cycle_number"]) if dispatch else 0
        rma = rma_by_trip.get((asset_id, str(cycle_number)))
        cycle_events = [
            row for row in ordered if int(row["cycle_number"]) == cycle_number
        ]
        receipt = next((row for row in cycle_events if row["event_type"] == "RETURN_RECEIVED"), None)
        loss = next((row for row in cycle_events if row["event_type"] == "LOSS_DECLARED"), None)
        repair = repair_by_rma.get(rma["rma_id"]) if rma else None
        inspection = inspection_by_rma.get(rma["rma_id"]) if rma else None
        if loss:
            expected_lifecycle = "LOST"
            end_day = _day(loss["occurred_at"])
        elif state == "CUSTODY_DISPUTED":
            expected_lifecycle = "CUSTODY_DISPUTED"
            end_day = AS_OF
        elif repair and repair["status"] == "OPEN":
            expected_lifecycle = "IN_REPAIR"
            end_day = _day(receipt["occurred_at"]) if receipt else AS_OF
        elif inspection and inspection["decision"] == "QUARANTINE":
            expected_lifecycle = "QUARANTINED"
            end_day = _day(receipt["occurred_at"]) if receipt else AS_OF
        elif receipt:
            expected_lifecycle = "AVAILABLE"
            end_day = _day(receipt["occurred_at"])
        elif rma and _day(rma["expected_return_at"]) < AS_OF:
            expected_lifecycle = "OVERDUE"
            end_day = AS_OF
        elif rma:
            expected_lifecycle = "OUTSTANDING"
            end_day = AS_OF
        else:
            expected_lifecycle = state
            end_day = AS_OF
        if snapshot["lifecycle_status"] != expected_lifecycle:
            add("SNAPSHOT_MISMATCH", asset_id, asset_id, "Snapshot lifecycle differs from source-derived state.")
        if _bool(snapshot["outstanding"]) != bool(rma and rma["status"] == "OPEN" and receipt is None and loss is None):
            add("SNAPSHOT_MISMATCH", asset_id, asset_id, "Snapshot outstanding flag differs from return/loss events.")
        start_day = _day(dispatch["occurred_at"]) if dispatch else end_day
        expected_age = max(0, (end_day - start_day).days) if end_day and start_day else 0
        if int(snapshot["days_outstanding"]) != expected_age:
            add("SNAPSHOT_MISMATCH", asset_id, asset_id, "Snapshot days_outstanding differs from event-derived age.")

    # Domain threshold checks.
    for rma in tables["return_authorizations"]:
        asset = assets.get(rma["asset_id"])
        if not asset:
            continue
        asset_type = asset_types.get(asset["asset_type_id"])
        if not asset_type:
            continue
        due = _day(rma["expected_return_at"])
        dispatch = _day(shipments[rma["shipment_id"]]["dispatched_at"])
        if due and dispatch and due != dispatch + timedelta(days=int(asset_type["return_window_days"])):
            add("RETURN_WINDOW_MISMATCH", rma["rma_id"], rma["asset_id"], "RMA due date differs from asset-type return window.")
        expected_loss_day = due + timedelta(days=int(asset_type["loss_threshold_days"])) if due else None
        cycle_events = events_by_asset_cycle.get((rma["asset_id"], rma["cycle_number"]), [])
        loss = next((row for row in cycle_events if row["event_type"] == "LOSS_DECLARED"), None)
        if loss and _day(loss["occurred_at"]) != expected_loss_day:
            add("LOSS_THRESHOLD_MISMATCH", loss["event_id"], rma["asset_id"], "Loss was not declared exactly at the configured threshold.")
        if loss and any(row["event_type"] == "RETURN_RECEIVED" for row in cycle_events):
            add("LOSS_THRESHOLD_MISMATCH", loss["event_id"], rma["asset_id"], "A return receipt and loss declaration conflict for the same cycle.")

    # Duplicate-record checks independent of primary keys.
    semantic_seen = set()
    for event in tables["custody_events"]:
        semantic_key = (
            event["asset_id"], event["cycle_number"], event["event_type"],
            event["from_location_id"], event["to_location_id"], event["actor_id"],
            event["occurred_at"],
        )
        if semantic_key in semantic_seen:
            add("DUPLICATE_RECORD", event["event_id"], event["asset_id"], "Duplicate custody event content.")
        semantic_seen.add(semantic_key)
    for asset_id, rows in events_by_asset.items():
        expected = int(assets[asset_id]["planned_cycles"])
        dispatches = sum(row["event_type"] == "DISPATCH" for row in rows)
        if dispatches != expected:
            add("DUPLICATE_RECORD", asset_id, asset_id, "Dispatch count differs from planned trip count.")

    gt_keys = {
        (row.get("issue_type"), row.get("offending_record_id"))
        for row in ground_truth
        if row.get("issue_type") in ANOMALY_TYPES
    }
    prediction_keys = {
        (row["issue_type"], row["record_id"])
        for row in findings
        if row["issue_type"] in ANOMALY_TYPES and row["record_id"]
    }
    anomaly_metrics = {}
    for issue_type in ANOMALY_TYPES:
        expected = {key for key in gt_keys if key[0] == issue_type}
        predicted = {key for key in prediction_keys if key[0] == issue_type}
        true_positive = len(expected & predicted)
        false_positive = len(predicted - expected)
        false_negative = len(expected - predicted)
        anomaly_metrics[issue_type] = {
            "true_positive": true_positive,
            "false_positive": false_positive,
            "false_negative": false_negative,
            "precision": round(true_positive / max(1, true_positive + false_positive), 4),
            "recall": round(true_positive / max(1, true_positive + false_negative), 4),
        }

    labelled_record_ids = {row.get("offending_record_id") for row in ground_truth}
    unmatched = [
        row for row in findings if row.get("record_id") not in labelled_record_ids
    ]
    missing_ground_truth = [
        {"issue_type": issue_type, "record_id": record_id}
        for issue_type, record_id in sorted(gt_keys - prediction_keys)
    ]
    checks = {
        "primary_key_uniqueness": not any(row["issue_type"] == "DUPLICATE_PRIMARY_KEY" for row in findings),
        "foreign_key_integrity": not any(row["issue_type"] == "FOREIGN_KEY_VIOLATION" for row in findings),
        "allowed_enums_and_ids": not any(row["issue_type"] in {"INVALID_ENUM", "INVALID_ID_FORMAT"} for row in findings),
        "date_order_and_domain": not any(row["issue_type"] == "DATE_ORDER" for row in findings),
        "arithmetic_reconciliation": not any(row["issue_type"] == "LEDGER_RECONCILIATION" for row in findings),
        "lifecycle_transitions_and_location_chain": not any(
            row["issue_type"] in {"INVALID_STATE_TRANSITION", "LOCATION_CHAIN_MISMATCH"}
            for row in findings
        ),
        "duplicate_checks": not any(row["issue_type"] in {"DUPLICATE_RECORD", "DUPLICATE_PRIMARY_KEY"} for row in findings),
    }
    row_counts = {name: len(rows) for name, rows in tables.items()}
    net_deposit = sum(int(row["amount_cents"]) for row in tables["deposit_ledger"])
    total_assessed_damage = sum(
        int(repair["cost_cents"]) for repair in tables["repairs"]
    ) + sum(
        int(asset_types[assets[inspection["asset_id"]]["asset_type_id"]]["replacement_cost_cents"])
        for inspection in tables["inspections"] if inspection["decision"] == "QUARANTINE"
    )
    damage_deductions = -sum(
        int(row["amount_cents"]) for row in tables["deposit_ledger"]
        if row["entry_type"] == "DAMAGE_DEDUCTION"
    )
    metrics = {
        "assets": row_counts["reusable_assets"],
        "shipments": row_counts["shipments"],
        "custody_events": row_counts["custody_events"],
        "trip_count": len(tables["return_authorizations"]),
        "delayed_returns": sum(
            row["status"] == "RECEIVED" and row["processed_at"] > row["expected_return_at"]
            for row in tables["return_authorizations"]
        ),
        "overdue_open_returns": sum(
            row["status"] == "OPEN" and row["expected_return_at"] < _dt_asof()
            for row in tables["return_authorizations"]
        ),
        "lost_assets": sum(row["event_type"] == "LOSS_DECLARED" for row in tables["custody_events"]),
        "damaged_assets": sum(row["condition"] == "DAMAGED" for row in tables["inspections"]),
        "open_repairs": sum(row["status"] == "OPEN" for row in tables["repairs"]),
        "disputed_handoffs": sum(row["event_type"] == "CUSTODY_HANDOFF_DISPUTED" for row in tables["custody_events"]),
        "open_custody_disputes": sum(
            snapshot["lifecycle_status"] == "CUSTODY_DISPUTED"
            for snapshot in tables["asset_snapshots"]
        ),
        "deposit_charges_cents": sum(
            int(row["amount_cents"]) for row in tables["deposit_ledger"]
            if row["entry_type"] == "DEPOSIT_CHARGE"
        ),
        "deposit_refunds_cents": -sum(
            int(row["amount_cents"]) for row in tables["deposit_ledger"]
            if row["entry_type"] == "DEPOSIT_REFUND"
        ),
        "late_fee_deductions_cents": -sum(
            int(row["amount_cents"]) for row in tables["deposit_ledger"]
            if row["entry_type"] == "LATE_FEE_DEDUCTION"
        ),
        "damage_deductions_cents": damage_deductions,
        "loss_forfeitures_cents": -sum(
            int(row["amount_cents"]) for row in tables["deposit_ledger"]
            if row["entry_type"] == "LOSS_FORFEIT"
        ),
        "net_deposit_balance_cents": net_deposit,
        "owner_absorbed_damage_cost_cents": max(0, total_assessed_damage - damage_deductions),
        "ground_truth_anomalies": len(gt_keys),
        "anomaly_metrics": anomaly_metrics,
    }
    return {
        "passed": not unmatched and not missing_ground_truth,
        "checks": checks,
        "errors": [row for row in findings if row["severity"] == "ERROR"],
        "findings": findings,
        "unmatched_findings": unmatched,
        "missed_ground_truth": missing_ground_truth,
        "anomaly_metrics": anomaly_metrics,
        "metrics": metrics,
        "row_counts": row_counts,
    }


def _dt_asof() -> str:
    return AS_OF.isoformat() + "T09:00:00+00:00"
