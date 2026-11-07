from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sqlite3
import tempfile
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from .validator import ANOMALY_TYPES, validate_exports


SEED = 20261022
AS_OF = date(2026, 11, 7)
GENERATOR_VERSION = "2.0.0"
PROFILES = {
    "starter": (200, 100),
    "full": (20_000, 10_000),
}

TABLE_FIELDS = {
    "asset_types": [
        "asset_type_id", "name", "deposit_cents", "replacement_cost_cents",
        "return_window_days", "loss_threshold_days", "late_fee_cents_per_day",
        "max_cycles", "repairable", "inspection_required",
    ],
    "owners": ["owner_id", "name", "owner_type", "active"],
    "locations": ["location_id", "name", "location_type", "region", "owner_id"],
    "operators": ["operator_id", "name", "role", "location_id"],
    "shipments": [
        "shipment_id", "owner_id", "origin_location_id", "destination_location_id",
        "carrier_id", "dispatched_at", "expected_return_at", "status",
    ],
    "reusable_assets": [
        "asset_id", "asset_type_id", "owner_id", "home_location_id", "asset_tag",
        "created_at", "planned_cycles",
    ],
    "shipment_assets": ["shipment_id", "asset_id", "cycle_number"],
    "custody_events": [
        "event_id", "asset_id", "shipment_id", "cycle_number", "event_type",
        "from_location_id", "to_location_id", "actor_id", "occurred_at",
    ],
    "return_authorizations": [
        "rma_id", "asset_id", "shipment_id", "cycle_number", "authorized_at",
        "expected_return_at", "status", "processed_at",
    ],
    "inspections": [
        "inspection_id", "asset_id", "rma_id", "cycle_number", "inspected_at",
        "condition", "decision", "notes",
    ],
    "repairs": [
        "repair_id", "asset_id", "inspection_id", "rma_id", "cycle_number",
        "opened_at", "closed_at", "status", "cost_cents",
    ],
    "deposit_ledger": [
        "ledger_id", "asset_id", "shipment_id", "rma_id", "cycle_number",
        "reference_entry_id", "entry_type", "amount_cents", "currency", "posted_at",
    ],
    "asset_snapshots": [
        "asset_id", "current_location_id", "lifecycle_status", "current_cycle_number",
        "days_outstanding", "outstanding", "deposit_balance_cents",
    ],
    "lifecycle_transitions": ["from_state", "event_type", "to_state"],
}

GT_FIELDS = [
    "issue_id", "issue_type", "entity_type", "entity_id", "offending_record_id",
    "ground_truth", "expected_decision",
]

TRANSITIONS = [
    ("AVAILABLE", "DISPATCH", "IN_TRANSIT"),
    ("IN_TRANSIT", "CUSTODY_HANDOFF", "AT_CUSTOMER"),
    ("IN_TRANSIT", "CUSTODY_HANDOFF_DISPUTED", "CUSTODY_DISPUTED"),
    ("CUSTODY_DISPUTED", "CUSTODY_DISPUTE_RESOLVED", "AT_CUSTOMER"),
    ("AT_CUSTOMER", "RETURN_DISPATCH", "RETURN_IN_TRANSIT"),
    ("RETURN_IN_TRANSIT", "RETURN_RECEIVED", "AVAILABLE"),
    ("IN_TRANSIT", "LOSS_DECLARED", "LOST"),
    ("AT_CUSTOMER", "LOSS_DECLARED", "LOST"),
]
CHECKPOINT_STATES = (
    "AVAILABLE", "IN_TRANSIT", "AT_CUSTOMER", "CUSTODY_DISPUTED",
    "RETURN_IN_TRANSIT", "LOST",
)
for _state in CHECKPOINT_STATES:
    TRANSITIONS.append((_state, "CUSTODY_CHECKPOINT", _state))

ANOMALY_DECISIONS = {
    "SIMULTANEOUS_LOCATIONS": "Review the conflicting custody observations; use deterministic event replay and retain the audit trail.",
    "RETURN_BEFORE_DISPATCH": "Quarantine the return chronology for correction before accepting the return.",
    "DUPLICATE_DEPOSIT_REFUND": "Hold the duplicate refund and reconcile against the original charge.",
    "REPAIR_WITHOUT_INSPECTION": "Block repair closure until valid inspection evidence is linked.",
    "CUSTODY_ACTOR_MISSING": "Request actor attribution; retain the event without inventing an actor.",
}


@dataclass(frozen=True)
class Scale:
    assets: int
    shipments: int

    @property
    def custody_events(self) -> int:
        return self.assets * 5


def _dt(day: date, hour: int = 9) -> str:
    return _iso(datetime.combine(day, time(hour=hour)))


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _write_json(path: Path, value: Any) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _write_csv(path: Path, fields: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _allocate_wave_shipments(trip_counts: list[int], shipment_count: int) -> list[int]:
    active = [index for index, count in enumerate(trip_counts) if count]
    if shipment_count < len(active):
        raise ValueError("Shipment scale must provide at least one shipment for every active trip wave.")
    allocations = [0, 0, 0]
    for index in active:
        allocations[index] = 1
    remaining = shipment_count - len(active)
    total = sum(trip_counts)
    quotas = {
        index: remaining * trip_counts[index] / total
        for index in active
    }
    for index in active:
        allocations[index] += int(quotas[index])
    remainder = shipment_count - sum(allocations)
    for index in sorted(active, key=lambda value: quotas[value] % 1, reverse=True)[:remainder]:
        allocations[index] += 1
    return allocations


def _new_ground_truth(
    rows: list[dict[str, Any]],
    issue_type: str,
    entity_type: str,
    entity_id: str,
    record_id: str,
    decision: str,
    label: str | None = None,
) -> None:
    rows.append({
        "issue_id": f"GT-{len(rows) + 1:07d}",
        "issue_type": issue_type,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "offending_record_id": record_id,
        "ground_truth": label or issue_type,
        "expected_decision": decision,
    })


def _new_event(
    asset_id: str,
    shipment_id: str,
    cycle_number: int,
    event_type: str,
    from_location_id: str,
    to_location_id: str,
    actor_id: str,
    occurred_at: datetime,
) -> dict[str, Any]:
    return {
        "event_id": "",
        "asset_id": asset_id,
        "shipment_id": shipment_id,
        "cycle_number": cycle_number,
        "event_type": event_type,
        "from_location_id": from_location_id,
        "to_location_id": to_location_id,
        "actor_id": actor_id,
        "occurred_at": _iso(occurred_at),
    }


def _replay_location(
    initial_location: str,
    events: list[dict[str, Any]],
) -> tuple[str, str]:
    location = initial_location
    state = "AVAILABLE"
    transitions = {(source, event): target for source, event, target in TRANSITIONS}
    for event in sorted(events, key=lambda row: (row["occurred_at"], row["event_id"])):
        if event["event_type"] == "CUSTODY_HANDOFF_DISPUTED":
            state = "CUSTODY_DISPUTED"
        elif event["event_type"] == "CUSTODY_CHECKPOINT":
            continue
        elif event["event_type"] == "LOSS_DECLARED":
            state = "LOST"
        else:
            state = transitions.get((state, event["event_type"]), state)
            location = event["to_location_id"] or location
    return location, state


def _derive_snapshots(tables: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    assets = {row["asset_id"]: row for row in tables["reusable_assets"]}
    events_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rmas_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in tables["custody_events"]:
        events_by_asset[event["asset_id"]].append(event)
    for rma in tables["return_authorizations"]:
        rmas_by_asset[rma["asset_id"]].append(rma)
    inspections = {row["rma_id"]: row for row in tables["inspections"]}
    repairs = {row["rma_id"]: row for row in tables["repairs"]}
    ledger_balance: Counter[str] = Counter()
    for row in tables["deposit_ledger"]:
        ledger_balance[row["asset_id"]] += int(row["amount_cents"])

    snapshots = []
    for asset_id, asset in assets.items():
        events = sorted(events_by_asset[asset_id], key=lambda row: (row["occurred_at"], row["event_id"]))
        location, replay_state = _replay_location(asset["home_location_id"], events)
        dispatch_events = [event for event in events if event["event_type"] == "DISPATCH"]
        latest_dispatch = dispatch_events[-1] if dispatch_events else None
        cycle_number = int(latest_dispatch["cycle_number"]) if latest_dispatch else 0
        rma = next(
            (row for row in rmas_by_asset[asset_id] if int(row["cycle_number"]) == cycle_number),
            None,
        )
        trip_events = [
            event for event in events if int(event["cycle_number"]) == cycle_number
        ]
        return_event = next(
            (event for event in reversed(trip_events) if event["event_type"] == "RETURN_RECEIVED"),
            None,
        )
        loss_event = next(
            (event for event in reversed(trip_events) if event["event_type"] == "LOSS_DECLARED"),
            None,
        )
        inspection = inspections.get(rma["rma_id"]) if rma else None
        repair = repairs.get(rma["rma_id"]) if rma else None
        outstanding = bool(rma and rma["status"] == "OPEN" and return_event is None and loss_event is None)
        if loss_event:
            lifecycle = "LOST"
        elif replay_state == "CUSTODY_DISPUTED":
            lifecycle = "CUSTODY_DISPUTED"
        elif repair and repair["status"] == "OPEN":
            lifecycle = "IN_REPAIR"
        elif inspection and inspection["decision"] == "QUARANTINE":
            lifecycle = "QUARANTINED"
        elif outstanding and rma and rma["expected_return_at"] < _dt(AS_OF):
            lifecycle = "OVERDUE"
        elif return_event:
            lifecycle = "AVAILABLE"
        else:
            lifecycle = replay_state

        if latest_dispatch:
            start_day = _timestamp(latest_dispatch["occurred_at"]).date()
            terminal = return_event or loss_event
            end_day = _timestamp(terminal["occurred_at"]).date() if terminal else AS_OF
            days_outstanding = max(0, (end_day - start_day).days)
        else:
            days_outstanding = 0
        snapshots.append({
            "asset_id": asset_id,
            "current_location_id": location,
            "lifecycle_status": lifecycle,
            "current_cycle_number": cycle_number,
            "days_outstanding": days_outstanding,
            "outstanding": outstanding,
            "deposit_balance_cents": ledger_balance[asset_id],
        })
    return snapshots


def _settlement_rows(
    asset: dict[str, Any],
    asset_type: dict[str, Any],
    shipment_id: str,
    rma: dict[str, Any],
    charge_id: str,
    return_day: date | None,
    inspection: dict[str, Any] | None,
    repair: dict[str, Any] | None,
    loss_day: date | None,
    next_ledger_id: int,
) -> tuple[list[dict[str, Any]], int, int]:
    rows = []

    def post(entry_type: str, amount: int, posted_at: str) -> None:
        nonlocal next_ledger_id
        if amount == 0:
            return
        rows.append({
            "ledger_id": f"DLG-2026-{next_ledger_id:08d}",
            "asset_id": asset["asset_id"],
            "shipment_id": shipment_id,
            "rma_id": rma["rma_id"],
            "cycle_number": rma["cycle_number"],
            "reference_entry_id": charge_id,
            "entry_type": entry_type,
            "amount_cents": -abs(amount),
            "currency": "USD",
            "posted_at": posted_at,
        })
        next_ledger_id += 1

    damage_assessed = 0
    if loss_day:
        post("LOSS_FORFEIT", int(asset_type["deposit_cents"]), _dt(loss_day))
        return rows, next_ledger_id, int(asset_type["deposit_cents"])
    if return_day is None:
        return rows, next_ledger_id, damage_assessed

    settlement_day = return_day
    if repair:
        if repair["status"] != "CLOSED" or not repair["closed_at"]:
            return rows, next_ledger_id, int(repair["cost_cents"])
        settlement_day = _timestamp(repair["closed_at"]).date()
    elif inspection:
        settlement_day = _timestamp(inspection["inspected_at"]).date()

    due_day = _timestamp(rma["expected_return_at"]).date()
    days_late = max(0, (return_day - due_day).days)
    deposit_remaining = int(asset_type["deposit_cents"])
    late_fee = min(days_late * int(asset_type["late_fee_cents_per_day"]), deposit_remaining)
    post("LATE_FEE_DEDUCTION", late_fee, _dt(settlement_day))
    deposit_remaining -= late_fee

    if inspection and inspection["decision"] == "REPAIR":
        damage_assessed = int(repair["cost_cents"]) if repair else 0
    elif inspection and inspection["decision"] == "QUARANTINE":
        damage_assessed = int(asset_type["replacement_cost_cents"])
    damage_deduction = min(damage_assessed, deposit_remaining)
    post("DAMAGE_DEDUCTION", damage_deduction, _dt(settlement_day))
    deposit_remaining -= damage_deduction
    post("DEPOSIT_REFUND", deposit_remaining, _dt(settlement_day))
    return rows, next_ledger_id, damage_assessed


def generate_clean_dataset(scale: Scale, seed: int = SEED) -> dict[str, Any]:
    if scale.assets < 20:
        raise ValueError("At least 20 assets are required for stable lifecycle coverage and anomaly candidates.")
    if scale.shipments < 3 or scale.shipments > scale.assets:
        raise ValueError("Shipment count must be between 3 and asset count.")

    rng = random.Random(seed)
    asset_types = [
        {
            "asset_type_id": "ATYPE-001", "name": "Reusable tote",
            "deposit_cents": 2_500, "replacement_cost_cents": 5_000,
            "return_window_days": 21, "loss_threshold_days": 60,
            "late_fee_cents_per_day": 100, "max_cycles": 100,
            "repairable": True, "inspection_required": True,
        },
        {
            "asset_type_id": "ATYPE-002", "name": "Plastic pallet",
            "deposit_cents": 12_500, "replacement_cost_cents": 25_000,
            "return_window_days": 30, "loss_threshold_days": 90,
            "late_fee_cents_per_day": 150, "max_cycles": 80,
            "repairable": True, "inspection_required": True,
        },
        {
            "asset_type_id": "ATYPE-003", "name": "Insulated crate",
            "deposit_cents": 7_500, "replacement_cost_cents": 15_000,
            "return_window_days": 14, "loss_threshold_days": 120,
            "late_fee_cents_per_day": 75, "max_cycles": 60,
            "repairable": False, "inspection_required": True,
        },
    ]
    owners = [
        {"owner_id": "OWNER-001", "name": "Northstar Supply", "owner_type": "SUPPLIER", "active": True},
        {"owner_id": "OWNER-002", "name": "YottaFlex Pool", "owner_type": "POOL_OPERATOR", "active": True},
        {"owner_id": "OWNER-003", "name": "Meridian Retail", "owner_type": "CUSTOMER", "active": True},
    ]
    locations = [
        {"location_id": "LOC-001", "name": "North Depot", "location_type": "WAREHOUSE", "region": "NORTH", "owner_id": "OWNER-001"},
        {"location_id": "LOC-002", "name": "Central Hub", "location_type": "WAREHOUSE", "region": "CENTRAL", "owner_id": "OWNER-002"},
        {"location_id": "LOC-003", "name": "South Hub", "location_type": "WAREHOUSE", "region": "SOUTH", "owner_id": "OWNER-002"},
        {"location_id": "LOC-004", "name": "Retail DC 1", "location_type": "CUSTOMER_SITE", "region": "NORTH", "owner_id": "OWNER-003"},
        {"location_id": "LOC-005", "name": "Retail DC 2", "location_type": "CUSTOMER_SITE", "region": "SOUTH", "owner_id": "OWNER-003"},
        {"location_id": "LOC-006", "name": "Carrier Yard North", "location_type": "CARRIER_YARD", "region": "NORTH", "owner_id": None},
        {"location_id": "LOC-007", "name": "Carrier Yard South", "location_type": "CARRIER_YARD", "region": "SOUTH", "owner_id": None},
    ]
    operators = [
        {"operator_id": "OP-001", "name": "Avery Planner", "role": "PLANNER", "location_id": "LOC-002"},
        {"operator_id": "OP-002", "name": "Jordan Carrier", "role": "CARRIER", "location_id": "LOC-006"},
        {"operator_id": "OP-003", "name": "Morgan Warehouse", "role": "WAREHOUSE_OPERATOR", "location_id": "LOC-002"},
        {"operator_id": "OP-004", "name": "Riley Supplier", "role": "SUPPLIER", "location_id": "LOC-001"},
        {"operator_id": "OP-005", "name": "Casey Analyst", "role": "LOGISTICS_ANALYST", "location_id": "LOC-002"},
        {"operator_id": "OP-006", "name": "Taylor Operations", "role": "OPERATIONS_MANAGER", "location_id": "LOC-002"},
    ]

    trip_counts = []
    for _ in range(scale.assets):
        draw = rng.random()
        trip_counts.append(1 if draw < 0.85 else 2 if draw < 0.95 else 3)
    trip_counts[0] = 3
    trip_counts[1] = max(2, trip_counts[1])
    trip_counts[2] = 1
    triple_cycle_assets = [
        asset_number for asset_number, count in enumerate(trip_counts, start=1)
        if count == 3
    ]
    open_dispute_assets = set(rng.sample(triple_cycle_assets, min(3, len(triple_cycle_assets))))
    wave_trip_counts = [
        sum(cycle_count >= wave + 1 for cycle_count in trip_counts)
        for wave in range(3)
    ]
    shipment_allocations = _allocate_wave_shipments(wave_trip_counts, scale.shipments)
    stage_shipment_ids: list[list[str]] = []
    shipment_number = 1
    for count in shipment_allocations:
        ids = [f"SHP-2026-{value:06d}" for value in range(shipment_number, shipment_number + count)]
        stage_shipment_ids.append(ids)
        shipment_number += count

    wave_ranges = ((14, 35), (130, 150), (235, 255))
    shipment_rows = []
    shipment_dates: dict[str, date] = {}
    for wave, ids in enumerate(stage_shipment_ids):
        low, high = wave_ranges[wave]
        for shipment_id in ids:
            dispatch_day = date(2026, 1, 1) + timedelta(days=rng.randint(low, high))
            shipment_dates[shipment_id] = dispatch_day
            shipment_rows.append({
                "shipment_id": shipment_id,
                "owner_id": "OWNER-002",
                "origin_location_id": "LOC-002",
                "destination_location_id": "LOC-004" if int(shipment_id[-1]) % 2 else "LOC-005",
                "carrier_id": "OP-002",
                "dispatched_at": _dt(dispatch_day),
                "expected_return_at": None,
                "status": "IN_TRANSIT",
            })
    shipments_by_id = {row["shipment_id"]: row for row in shipment_rows}

    assets: list[dict[str, Any]] = []
    shipment_assets: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    rmas: list[dict[str, Any]] = []
    inspections: list[dict[str, Any]] = []
    repairs: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    ground_truth: list[dict[str, Any]] = []
    asset_types_by_id = {row["asset_type_id"]: row for row in asset_types}
    next_rma = next_inspection = next_repair = next_ledger = 1
    event_sequence_by_asset: Counter[str] = Counter()
    shipment_trip_outcomes: dict[str, list[str]] = defaultdict(list)
    assessed_damage_costs = 0

    stage_position = [0, 0, 0]
    for asset_index in range(1, scale.assets + 1):
        asset_id = f"ASSET-{asset_index:07d}"
        asset_type = asset_types[(asset_index - 1) % len(asset_types)]
        cycle_count = trip_counts[asset_index - 1]
        if cycle_count > int(asset_type["max_cycles"]):
            raise ValueError(f"{asset_id} exceeds its asset type's max_cycles.")
        created_at = None
        prior_repair_open = False

        for cycle_number in range(1, cycle_count + 1):
            wave = cycle_number - 1
            shipment_id = stage_shipment_ids[wave][stage_position[wave] % len(stage_shipment_ids[wave])]
            stage_position[wave] += 1
            shipment = shipments_by_id[shipment_id]
            dispatch_day = shipment_dates[shipment_id]
            if created_at is None:
                created_at = _dt(dispatch_day)
            home_location = "LOC-002"
            destination = shipment["destination_location_id"]
            yard = "LOC-006" if destination == "LOC-004" else "LOC-007"
            dispatch_time = datetime.combine(dispatch_day, time(hour=9))
            due_day = dispatch_day + timedelta(days=int(asset_type["return_window_days"]))
            rma_id = f"RMA-2026-{next_rma:06d}"
            next_rma += 1

            final_cycle = cycle_number == cycle_count
            never_returns = final_cycle and rng.random() < 0.12
            force_open_dispute = (
                final_cycle and cycle_number == 3 and asset_index in open_dispute_assets
            )
            dispute_occurs = rng.random() < 0.04 or force_open_dispute
            dispute_open = force_open_dispute or (
                final_cycle and cycle_number == 3 and dispute_occurs and rng.random() < 0.20
            )
            dispute_lag = rng.randint(2, 30)
            resolved_at = dispatch_time + timedelta(days=dispute_lag) if dispute_occurs else None
            if resolved_at and resolved_at > datetime.combine(AS_OF, time(hour=23, minute=59)):
                dispute_open = True
            if dispute_open:
                never_returns = True

            delay_draw = rng.random()
            if delay_draw < 0.15:
                delay_days = rng.randint(-7, -1)
            elif delay_draw < 0.55:
                delay_days = 0
            elif delay_draw < 0.82:
                delay_days = rng.randint(1, 7)
            elif delay_draw < 0.96:
                delay_days = rng.randint(8, 21)
            else:
                delay_days = rng.randint(22, 35)
            proposed_return_day = due_day + timedelta(days=delay_days)
            latest_return_day = AS_OF - timedelta(days=5)
            return_day = min(proposed_return_day, latest_return_day)
            if resolved_at and not dispute_open:
                return_day = max(return_day, resolved_at.date() + timedelta(days=2))
                return_day = min(return_day, latest_return_day)
            has_return = not never_returns and (resolved_at is None or resolved_at.date() < return_day)
            if not final_cycle:
                has_return = True
                dispute_open = False

            rma = {
                "rma_id": rma_id,
                "asset_id": asset_id,
                "shipment_id": shipment_id,
                "cycle_number": cycle_number,
                "authorized_at": _dt(dispatch_day),
                "expected_return_at": _dt(due_day),
                "status": "RECEIVED" if has_return else "OPEN",
                "processed_at": _dt(return_day) if has_return else None,
            }
            rmas.append(rma)
            shipment_assets.append({
                "shipment_id": shipment_id,
                "asset_id": asset_id,
                "cycle_number": cycle_number,
            })

            charge_id = f"DLG-2026-{next_ledger:08d}"
            next_ledger += 1
            ledger.append({
                "ledger_id": charge_id,
                "asset_id": asset_id,
                "shipment_id": shipment_id,
                "rma_id": rma_id,
                "cycle_number": cycle_number,
                "reference_entry_id": None,
                "entry_type": "DEPOSIT_CHARGE",
                "amount_cents": int(asset_type["deposit_cents"]),
                "currency": "USD",
                "posted_at": _dt(dispatch_day),
            })

            trip_events = [
                _new_event(
                    asset_id, shipment_id, cycle_number, "DISPATCH",
                    home_location, yard, "OP-004", dispatch_time,
                ),
            ]
            if dispute_occurs:
                disputed_at = dispatch_time + timedelta(days=1)
                trip_events.append(_new_event(
                    asset_id, shipment_id, cycle_number, "CUSTODY_HANDOFF_DISPUTED",
                    yard, destination, "OP-002", disputed_at,
                ))
                if not dispute_open:
                    resolve_time = max(disputed_at + timedelta(hours=1), resolved_at)
                    trip_events.append(_new_event(
                        asset_id, shipment_id, cycle_number, "CUSTODY_DISPUTE_RESOLVED",
                        yard, destination, "OP-006", resolve_time,
                    ))
            else:
                trip_events.append(_new_event(
                    asset_id, shipment_id, cycle_number, "CUSTODY_HANDOFF",
                    yard, destination, "OP-002", dispatch_time + timedelta(days=1),
                ))

            if has_return:
                trip_events.extend([
                    _new_event(
                        asset_id, shipment_id, cycle_number, "RETURN_DISPATCH",
                        destination, yard, "OP-002",
                        datetime.combine(return_day - timedelta(days=1), time(hour=9)),
                    ),
                    _new_event(
                        asset_id, shipment_id, cycle_number, "RETURN_RECEIVED",
                        yard, home_location, "OP-003",
                        datetime.combine(return_day, time(hour=9)),
                    ),
                ])

                inspection = None
                repair = None
                if bool(asset_type["inspection_required"]):
                    damaged = rng.random() < 0.12
                    if damaged:
                        decision = "REPAIR" if asset_type["repairable"] else "QUARANTINE"
                        inspection_id = f"INSP-2026-{next_inspection:06d}"
                        next_inspection += 1
                        inspected_at = _dt(return_day + timedelta(days=1))
                        inspection = {
                            "inspection_id": inspection_id,
                            "asset_id": asset_id,
                            "rma_id": rma_id,
                            "cycle_number": cycle_number,
                            "inspected_at": inspected_at,
                            "condition": "DAMAGED",
                            "decision": decision,
                            "notes": "Synthetic receiving inspection; finding is a seeded input.",
                        }
                        inspections.append(inspection)
                        if decision == "REPAIR":
                            cost = max(1, int(asset_type["replacement_cost_cents"]) // 5)
                            repair_open = final_cycle and rng.random() < 0.30
                            opened_at = datetime.fromisoformat(inspected_at) + timedelta(days=1)
                            repair = {
                                "repair_id": f"REP-2026-{next_repair:06d}",
                                "asset_id": asset_id,
                                "inspection_id": inspection_id,
                                "rma_id": rma_id,
                                "cycle_number": cycle_number,
                                "opened_at": _iso(opened_at),
                                "closed_at": None if repair_open else _iso(opened_at + timedelta(days=2)),
                                "status": "OPEN" if repair_open else "CLOSED",
                                "cost_cents": cost,
                            }
                            next_repair += 1
                            repairs.append(repair)
                            prior_repair_open = repair_open

                if not prior_repair_open:
                    settlement_rows, next_ledger, assessed = _settlement_rows(
                        {"asset_id": asset_id},
                        asset_type,
                        shipment_id,
                        rma,
                        charge_id,
                        return_day,
                        inspection,
                        repair,
                        None,
                        next_ledger,
                    )
                    ledger.extend(settlement_rows)
                    assessed_damage_costs += assessed
                else:
                    assessed_damage_costs += int(repair["cost_cents"]) if repair else 0
                if inspection and inspection["condition"] == "DAMAGED":
                    _new_ground_truth(
                        ground_truth, "DAMAGED_ASSET", "ReusableAsset", asset_id,
                        inspection["inspection_id"],
                        "Apply repair or quarantine deductions after required settlement evidence.",
                    )
                if repair and repair["status"] == "OPEN":
                    _new_ground_truth(
                        ground_truth, "OPEN_REPAIR", "Repair", repair["repair_id"],
                        repair["repair_id"],
                        "Keep the deposit unsettled until the repair is closed.",
                    )
            else:
                loss_day = due_day + timedelta(days=int(asset_type["loss_threshold_days"]))
                if not dispute_open and loss_day <= AS_OF:
                    loss_time = datetime.combine(loss_day, time(hour=9))
                    last_verified = destination if dispute_occurs and not dispute_open else (
                        yard if dispute_occurs else destination
                    )
                    if dispute_occurs and not dispute_open:
                        last_verified = destination
                    trip_events.append(_new_event(
                        asset_id, shipment_id, cycle_number, "LOSS_DECLARED",
                        last_verified, last_verified, "OP-006", loss_time,
                    ))
                    rma["status"] = "CLOSED_LOST"
                    rows, next_ledger, _ = _settlement_rows(
                        {"asset_id": asset_id}, asset_type, shipment_id, rma, charge_id,
                        None, None, None, loss_day, next_ledger,
                    )
                    ledger.extend(rows)
                    _new_ground_truth(
                        ground_truth, "LOSS_EVENT_PENDING", "ReusableAsset", asset_id,
                        "", "Declare loss exactly at the type-specific threshold and forfeit the deposit.",
                    )
                else:
                    rma["status"] = "OPEN"
                    if due_day < AS_OF:
                        _new_ground_truth(
                            ground_truth, "OVERDUE_RETURN", "ReturnAuthorization", rma_id,
                            rma_id, "Escalate as overdue after the expected return date; do not mark lost before threshold.",
                        )

            trip_events.sort(key=lambda row: (row["occurred_at"], row["event_type"]))
            for event in trip_events:
                event_sequence_by_asset[asset_id] += 1
                event["event_id"] = f"CUST-{asset_id}-{event_sequence_by_asset[asset_id]:06d}"
            if trip_events and trip_events[-1]["event_type"] == "LOSS_DECLARED":
                pending = next(
                    row for row in reversed(ground_truth)
                    if row["issue_type"] == "LOSS_EVENT_PENDING" and row["entity_id"] == asset_id
                )
                pending["issue_type"] = "LOST_ASSET"
                pending["offending_record_id"] = trip_events[-1]["event_id"]
            if dispute_occurs:
                disputed_event = next(
                    row for row in trip_events if row["event_type"] == "CUSTODY_HANDOFF_DISPUTED"
                )
                _new_ground_truth(
                    ground_truth, "DISPUTED_CUSTODY_HANDOFF", "CustodyEvent", asset_id,
                    disputed_event["event_id"],
                    "Hold the last verified location and block subsequent custody transitions until resolution.",
                )
            if has_return and rma["processed_at"] > rma["expected_return_at"]:
                return_event = next(row for row in trip_events if row["event_type"] == "RETURN_RECEIVED")
                _new_ground_truth(
                    ground_truth, "DELAYED_RETURN", "CustodyEvent", asset_id,
                    return_event["event_id"], "Apply the type-specific late fee and record the actual return date.",
                )

            event_rows.extend(trip_events)
            shipment_trip_outcomes[shipment_id].append(
                "RETURNED" if has_return else "LOST" if rma["status"] == "CLOSED_LOST" else "OPEN"
            )
            prior_repair_open = bool(repair and repair["status"] == "OPEN") if has_return else False

        assets.append({
            "asset_id": asset_id,
            "asset_type_id": asset_type["asset_type_id"],
            "owner_id": "OWNER-002",
            "home_location_id": "LOC-002",
            "asset_tag": f"TAG-{asset_index:07d}",
            "created_at": created_at or _dt(date(2026, 1, 1)),
            "planned_cycles": cycle_count,
        })

    for shipment in shipment_rows:
        outcomes = shipment_trip_outcomes[shipment["shipment_id"]]
        shipment["expected_return_at"] = _dt(
            shipment_dates[shipment["shipment_id"]] + timedelta(days=30)
        )
        shipment["status"] = (
            "CLOSED" if outcomes and all(value == "RETURNED" for value in outcomes)
            else "LOST" if outcomes and all(value == "LOST" for value in outcomes)
            else "PARTIALLY_RETURNED" if "RETURNED" in outcomes
            else "IN_TRANSIT"
        )

    target_events = scale.custody_events
    if len(event_rows) > target_events:
        raise ValueError(
            f"Rule-driven events ({len(event_rows)}) exceed the fixed target ({target_events}); "
            "reduce input cycle/dispute frequency rather than dropping events."
        )
    filler_count = target_events - len(event_rows)
    events_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in event_rows:
        events_by_asset[event["asset_id"]].append(event)
    eligible_assets = list(events_by_asset)
    for _ in range(filler_count):
        asset_id = rng.choice(eligible_assets)
        existing = events_by_asset[asset_id]
        location, _ = _replay_location("LOC-002", existing)
        ordinal = sum(event["event_type"] == "CUSTODY_CHECKPOINT" for event in existing)
        checkpoint_at = datetime.combine(AS_OF, time(hour=23)) - timedelta(seconds=ordinal)
        event_sequence_by_asset[asset_id] += 1
        checkpoint = _new_event(
            asset_id,
            max(existing, key=lambda row: row["cycle_number"])["shipment_id"],
            max(int(event["cycle_number"]) for event in existing),
            "CUSTODY_CHECKPOINT", location, location, "OP-005", checkpoint_at,
        )
        checkpoint["event_id"] = f"CUST-{asset_id}-{event_sequence_by_asset[asset_id]:06d}"
        existing.append(checkpoint)
    event_rows = [event for rows in events_by_asset.values() for event in rows]

    tables = {
        "asset_types": asset_types,
        "owners": owners,
        "locations": locations,
        "operators": operators,
        "shipments": shipment_rows,
        "reusable_assets": assets,
        "shipment_assets": shipment_assets,
        "custody_events": event_rows,
        "return_authorizations": rmas,
        "inspections": inspections,
        "repairs": repairs,
        "deposit_ledger": ledger,
        "asset_snapshots": [],
        "lifecycle_transitions": [
            {"from_state": source, "event_type": event, "to_state": target}
            for source, event, target in TRANSITIONS
        ],
    }
    tables["asset_snapshots"] = _derive_snapshots(tables)
    return {
        "tables": tables,
        "ground_truth": ground_truth,
        "seed": seed,
        "profile": "custom",
        "as_of_date": AS_OF.isoformat(),
        "assessed_damage_costs_cents": assessed_damage_costs,
    }


def _inject_anomalies(
    baseline: dict[str, Any],
    seed: int,
    counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    requested = {issue_type: 1 for issue_type in ANOMALY_TYPES}
    if counts:
        unknown = set(counts) - set(ANOMALY_TYPES)
        if unknown:
            raise ValueError(f"Unknown anomaly types: {', '.join(sorted(unknown))}.")
        for issue_type, count in counts.items():
            if count < 0:
                raise ValueError("Anomaly counts cannot be negative.")
            requested[issue_type] = count
    output = deepcopy(baseline)
    tables = output["tables"]
    rng = random.Random(seed ^ 0x5EED2026)
    ground_truth = output["ground_truth"]
    used_assets: set[str] = set()

    def choose_asset(candidates: list[str], issue_type: str) -> list[str]:
        available = sorted(set(candidates) - used_assets)
        count = requested[issue_type]
        if len(available) < count:
            raise ValueError(
                f"Cannot inject {count} {issue_type} cases; only {len(available)} independent candidates exist."
            )
        selected = rng.sample(available, count)
        used_assets.update(selected)
        return selected

    events_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in tables["custody_events"]:
        events_by_asset[event["asset_id"]].append(event)
    checkpoint_assets = [
        asset_id for asset_id, rows in events_by_asset.items()
        if any(event["event_type"] == "CUSTODY_CHECKPOINT" for event in rows)
    ]
    for asset_id in choose_asset(checkpoint_assets, "SIMULTANEOUS_LOCATIONS"):
        checkpoint = next(
            event for event in events_by_asset[asset_id]
            if event["event_type"] == "CUSTODY_CHECKPOINT"
        )
        checkpoint["occurred_at"] = next(
            event["occurred_at"] for event in events_by_asset[asset_id]
            if event["cycle_number"] == checkpoint["cycle_number"]
            and event["event_type"] == "DISPATCH"
        )
        alternatives = [
            row["location_id"] for row in tables["locations"]
            if row["location_id"] != checkpoint["from_location_id"]
        ]
        checkpoint["to_location_id"] = rng.choice(alternatives)
        _new_ground_truth(
            ground_truth, "SIMULTANEOUS_LOCATIONS", "CustodyEvent", asset_id,
            checkpoint["event_id"], ANOMALY_DECISIONS["SIMULTANEOUS_LOCATIONS"],
        )

    returned_cycles = [
        rma for rma in tables["return_authorizations"] if rma["status"] == "RECEIVED"
    ]
    return_assets = sorted({row["asset_id"] for row in returned_cycles} - used_assets)
    for asset_id in choose_asset(return_assets, "RETURN_BEFORE_DISPATCH"):
        candidates = [
            event for event in events_by_asset[asset_id]
            if event["event_type"] == "RETURN_RECEIVED"
        ]
        event = rng.choice(candidates)
        dispatch = next(
            row for row in events_by_asset[asset_id]
            if row["cycle_number"] == event["cycle_number"] and row["event_type"] == "DISPATCH"
        )
        event["occurred_at"] = (
            _timestamp(dispatch["occurred_at"]) - timedelta(days=1)
        ).isoformat(timespec="seconds")
        _new_ground_truth(
            ground_truth, "RETURN_BEFORE_DISPATCH", "CustodyEvent", asset_id,
            event["event_id"], ANOMALY_DECISIONS["RETURN_BEFORE_DISPATCH"],
        )

    refunds_by_charge: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tables["deposit_ledger"]:
        if row["entry_type"] == "DEPOSIT_REFUND":
            refunds_by_charge[row["reference_entry_id"]].append(row)
    refund_candidates_by_asset = {
        row["asset_id"]: row for row in tables["deposit_ledger"]
        if row["entry_type"] == "DEPOSIT_REFUND"
        and len(refunds_by_charge[row["reference_entry_id"]]) == 1
        and row["asset_id"] not in used_assets
    }
    for original in choose_asset(list(refund_candidates_by_asset), "DUPLICATE_DEPOSIT_REFUND"):
        charge_refund = refund_candidates_by_asset[original]
        duplicate = dict(charge_refund)
        duplicate["ledger_id"] = f"DLG-2026-{max(int(row['ledger_id'][-8:]) for row in tables['deposit_ledger']) + 1:08d}"
        duplicate["posted_at"] = _dt(min(AS_OF, _timestamp(charge_refund["posted_at"]).date() + timedelta(days=1)))
        tables["deposit_ledger"].append(duplicate)
        _new_ground_truth(
            ground_truth, "DUPLICATE_DEPOSIT_REFUND", "DepositLedger", original,
            duplicate["ledger_id"], ANOMALY_DECISIONS["DUPLICATE_DEPOSIT_REFUND"],
        )

    repair_candidates_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in tables["repairs"]:
        if (
            row["status"] == "CLOSED" and row["inspection_id"] is not None
            and row["asset_id"] not in used_assets
        ):
            repair_candidates_by_asset[row["asset_id"]].append(row)
    selected_repairs = choose_asset(
        list(repair_candidates_by_asset), "REPAIR_WITHOUT_INSPECTION"
    )
    for asset_id in selected_repairs:
        repair = rng.choice(repair_candidates_by_asset[asset_id])
        repair["inspection_id"] = None
        used_assets.add(asset_id)
        _new_ground_truth(
            ground_truth, "REPAIR_WITHOUT_INSPECTION", "Repair", asset_id,
            repair["repair_id"], ANOMALY_DECISIONS["REPAIR_WITHOUT_INSPECTION"],
        )

    event_candidates_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in tables["custody_events"]:
        if event["actor_id"] is not None and event["asset_id"] not in used_assets:
            event_candidates_by_asset[event["asset_id"]].append(event)
    selected_assets = choose_asset(
        list(event_candidates_by_asset), "CUSTODY_ACTOR_MISSING"
    )
    for asset_id in selected_assets:
        event = rng.choice(event_candidates_by_asset[asset_id])
        event["actor_id"] = None
        used_assets.add(asset_id)
        _new_ground_truth(
            ground_truth, "CUSTODY_ACTOR_MISSING", "CustodyEvent", asset_id,
            event["event_id"], ANOMALY_DECISIONS["CUSTODY_ACTOR_MISSING"],
        )

    output["tables"]["asset_snapshots"] = _derive_snapshots(output["tables"])
    return output


def _write_sql_seed(tables: dict[str, list[dict[str, Any]]], path: Path) -> None:
    def literal(value: Any) -> str:
        if value is None:
            return "NULL"
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return str(value)
        return "'" + str(value).replace("'", "''") + "'"

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("-- Synthetic generated seed; apply schema.sql first.\n")
        handle.write("PRAGMA foreign_keys = ON;\n")
        for table_name, fields in TABLE_FIELDS.items():
            for row in tables[table_name]:
                values = ", ".join(literal(row.get(field)) for field in fields)
                handle.write(
                    f"INSERT INTO {table_name} ({', '.join(fields)}) VALUES ({values});\n"
                )


def _write_dataset(
    dataset: dict[str, Any],
    directory: Path,
    validation: dict[str, Any],
) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    tables = dataset["tables"]
    for table_name, fields in TABLE_FIELDS.items():
        _write_csv(directory / f"{table_name}.csv", fields, tables[table_name])
    _write_csv(directory / "ground_truth.csv", GT_FIELDS, dataset["ground_truth"])
    data_payload = {
        "seed": dataset["seed"],
        "profile": dataset["profile"],
        "as_of_date": dataset["as_of_date"],
        "tables": tables,
    }
    _write_json(directory / "data.json", data_payload)
    _write_json(directory / "ground_truth.json", dataset["ground_truth"])
    _write_json(directory / "metrics.json", validation["metrics"])
    _write_json(directory / "validation_summary.json", validation)
    _write_sql_seed(tables, directory / "seed.sql")

    hashes = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir()) if path.is_file()
    }
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": dataset["seed"],
        "profile": dataset["profile"],
        "as_of_date": dataset["as_of_date"],
        "provenance": "Entirely synthetic; no external datasets or personal data used.",
        "row_counts": validation["row_counts"],
        "output_sha256": hashes,
    }
    _write_json(directory / "source_manifest.json", manifest)
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(directory.iterdir()) if path.is_file()
    }


def generate_dataset(
    profile: str = "starter",
    seed: int = SEED,
    anomaly_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}; choose from {', '.join(PROFILES)}.")
    asset_count, shipment_count = PROFILES[profile]
    baseline = generate_clean_dataset(Scale(asset_count, shipment_count), seed)
    with tempfile.TemporaryDirectory(prefix="returnables-baseline-") as directory:
        _write_operational_tables(baseline, Path(directory))
        baseline_validation = validate_exports(Path(directory))
        if not baseline_validation["passed"]:
            raise ValueError(
                "Clean synthetic baseline failed independent validation: "
                + json.dumps(baseline_validation["unmatched_findings"][:5])
            )
    output = _inject_anomalies(baseline, seed, anomaly_counts)
    output["profile"] = profile
    return output


def _write_operational_tables(dataset: dict[str, Any], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, fields in TABLE_FIELDS.items():
        _write_csv(directory / f"{name}.csv", fields, dataset["tables"][name])
    _write_csv(directory / "ground_truth.csv", GT_FIELDS, dataset["ground_truth"])


def verify_reproducibility(
    profile: str,
    seed: int,
    anomaly_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="returnables-repro-") as root:
        first = Path(root) / "first"
        second = Path(root) / "second"
        for output in (first, second):
            dataset = generate_dataset(profile, seed, anomaly_counts)
            _write_operational_tables(dataset, output)
            validation = validate_exports(output)
            if not validation["passed"]:
                raise ValueError("Independent validation failed during reproducibility check.")
            _write_dataset(dataset, output, validation)
        first_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in first.iterdir() if path.is_file()
        }
        second_hashes = {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in second.iterdir() if path.is_file()
        }
        return {
            "passed": first_hashes == second_hashes,
            "profile": profile,
            "seed": seed,
            "anomaly_counts": anomaly_counts,
            "files_compared": len(first_hashes),
            "sha256": first_hashes,
        }


def _validate_sqlite(dataset: dict[str, Any], schema_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.execute("PRAGMA foreign_keys = ON")
        for table_name, fields in TABLE_FIELDS.items():
            for row in dataset["tables"][table_name]:
                values = [row.get(field) for field in fields]
                connection.execute(
                    f"INSERT INTO {table_name} ({', '.join(fields)}) VALUES "
                    f"({', '.join('?' for _ in fields)})", values
                )
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(f"SQLite foreign-key validation failed: {violations[:5]}")
    finally:
        connection.close()


def _parse_anomaly_counts(values: list[str] | None) -> dict[str, int] | None:
    if not values:
        return None
    parsed = {}
    for value in values:
        try:
            issue_type, raw_count = value.split("=", 1)
            parsed[issue_type] = int(raw_count)
        except ValueError as error:
            raise argparse.ArgumentTypeError(
                f"Anomaly count must use TYPE=N, got {value!r}."
            ) from error
    return parsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate and independently validate synthetic returnable-asset logistics data."
    )
    parser.add_argument("--profile", choices=PROFILES, default="starter")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", type=Path, default=Path("output/starter"))
    parser.add_argument(
        "--anomaly-count", action="append", metavar="TYPE=N",
        help="Override an anomaly count; repeat as needed. Unspecified types default to one.",
    )
    parser.add_argument("--no-anomalies", action="store_true")
    parser.add_argument("--verify-reproducibility", action="store_true")
    args = parser.parse_args(argv)

    anomaly_counts = _parse_anomaly_counts(args.anomaly_count)
    if args.no_anomalies and anomaly_counts:
        parser.error("--no-anomalies cannot be combined with --anomaly-count.")
    if args.no_anomalies:
        anomaly_counts = {issue_type: 0 for issue_type in ANOMALY_TYPES}

    try:
        dataset = generate_dataset(args.profile, args.seed, anomaly_counts)
        schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
        _validate_sqlite(dataset, schema_path)
        args.output.mkdir(parents=True, exist_ok=True)
        if not args.verify_reproducibility:
            try:
                (args.output / "reproducibility.json").unlink()
            except FileNotFoundError:
                pass
        _write_operational_tables(dataset, args.output)
        validation = validate_exports(args.output)
        _write_dataset(dataset, args.output, validation)
        report: dict[str, Any] = {
            "output": str(args.output.resolve()),
            "profile": args.profile,
            "seed": args.seed,
            "validation_passed": validation["passed"],
            "row_counts": validation["row_counts"],
            "metrics": validation["metrics"],
            "files_written": len(list(args.output.iterdir())),
        }
        if args.verify_reproducibility:
            reproducibility = verify_reproducibility(args.profile, args.seed, anomaly_counts)
            report["reproducibility"] = {
                "passed": reproducibility["passed"],
                "files_compared": reproducibility["files_compared"],
                "sha256": reproducibility["sha256"],
            }
            if not reproducibility["passed"]:
                print(json.dumps(report, indent=2))
                return 1
            _write_json(args.output / "reproducibility.json", {
                "passed": reproducibility["passed"],
                "profile": reproducibility["profile"],
                "seed": reproducibility["seed"],
                "anomaly_counts": reproducibility["anomaly_counts"] or {
                    issue_type: 1 for issue_type in ANOMALY_TYPES
                },
                "files_compared": reproducibility["files_compared"],
                "sha256": reproducibility["sha256"],
            })
            _write_dataset(dataset, args.output, validation)
        report["files_written"] = len(list(args.output.iterdir()))
        print(json.dumps(report, indent=2))
        return 0 if validation["passed"] else 1
    except (OSError, sqlite3.Error, ValueError) as error:
        parser.exit(1, f"Generation or validation failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
