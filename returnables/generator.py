from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import sqlite3
import tempfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any


SEED = 20261022
AS_OF = date(2026, 11, 7)
GENERATOR_VERSION = "1.0.0"
PROFILES = {
    "starter": (200, 100),
    "full": (20_000, 10_000),
}

TABLE_FIELDS = {
    "asset_types": [
        "asset_type_id", "name", "deposit_cents", "replacement_cost_cents",
        "return_window_days", "max_cycles", "repairable", "inspection_required",
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
        "created_at",
    ],
    "shipment_assets": ["shipment_id", "asset_id"],
    "custody_events": [
        "event_id", "asset_id", "shipment_id", "event_type", "from_location_id",
        "to_location_id", "actor_id", "occurred_at",
    ],
    "return_authorizations": [
        "rma_id", "asset_id", "shipment_id", "authorized_at", "expected_return_at",
        "status", "processed_at",
    ],
    "inspections": [
        "inspection_id", "asset_id", "rma_id", "inspected_at", "condition",
        "decision", "notes",
    ],
    "repairs": [
        "repair_id", "asset_id", "inspection_id", "opened_at", "closed_at",
        "status", "cost_cents",
    ],
    "deposit_ledger": [
        "ledger_id", "asset_id", "shipment_id", "rma_id", "reference_entry_id",
        "entry_type", "amount_cents", "currency", "posted_at",
    ],
    "asset_snapshots": [
        "asset_id", "current_location_id", "lifecycle_status", "days_outstanding",
        "outstanding", "deposit_balance_cents",
    ],
}

OUTPUT_TABLES = tuple(TABLE_FIELDS)


@dataclass(frozen=True)
class Scale:
    assets: int
    shipments: int

    @property
    def custody_events(self) -> int:
        return self.assets * 5


def _dt(day: date, hour: int = 9) -> str:
    return datetime.combine(day, time(hour=hour)).isoformat(timespec="seconds")


def _parse_day(value: str) -> date:
    return datetime.fromisoformat(value).date()


def _money(value: int) -> int:
    return int(value)


def _csv_value(value: Any) -> Any:
    if isinstance(value, bool):
        return str(value).lower()
    return value


def generate_dataset(profile: str = "starter", seed: int = SEED) -> dict[str, Any]:
    if profile not in PROFILES:
        raise ValueError(f"Unknown profile {profile!r}; choose from {', '.join(PROFILES)}.")
    asset_count, shipment_count = PROFILES[profile]
    dataset = generate_scaled_dataset(Scale(asset_count, shipment_count), seed)
    dataset["profile"] = profile
    return dataset


def generate_scaled_dataset(scale: Scale, seed: int = SEED) -> dict[str, Any]:
    if scale.assets < 5:
        raise ValueError("At least five assets are required to cover all labelled edge cases.")
    if scale.shipments < 1:
        raise ValueError("At least one shipment is required.")
    if scale.shipments > scale.assets:
        raise ValueError("Shipment count cannot exceed asset count in the generated one-shipment-per-asset sample.")

    rng = random.Random(seed)
    types = [
        {
            "asset_type_id": "ATYPE-001", "name": "Reusable tote",
            "deposit_cents": 2_500, "replacement_cost_cents": 5_000,
            "return_window_days": 21, "max_cycles": 100,
            "repairable": True, "inspection_required": True,
        },
        {
            "asset_type_id": "ATYPE-002", "name": "Plastic pallet",
            "deposit_cents": 12_500, "replacement_cost_cents": 25_000,
            "return_window_days": 30, "max_cycles": 80,
            "repairable": True, "inspection_required": True,
        },
        {
            "asset_type_id": "ATYPE-003", "name": "Insulated crate",
            "deposit_cents": 7_500, "replacement_cost_cents": 15_000,
            "return_window_days": 14, "max_cycles": 60,
            "repairable": False, "inspection_required": True,
        },
    ]
    owners = [
        {"owner_id": "OWNER-001", "name": "Northstar Supply", "owner_type": "SUPPLIER", "active": True},
        {"owner_id": "OWNER-002", "name": "YottaFlex Pool", "owner_type": "POOL_OPERATOR", "active": True},
        {"owner_id": "OWNER-003", "name": "Meridian Retail", "owner_type": "CUSTOMER", "active": True},
    ]
    locations = [
        {"location_id": f"LOC-{i:03d}", "name": name, "location_type": kind,
         "region": region, "owner_id": owner_id}
        for i, (name, kind, region, owner_id) in enumerate([
            ("North Depot", "WAREHOUSE", "NORTH", "OWNER-001"),
            ("Central Hub", "WAREHOUSE", "CENTRAL", "OWNER-002"),
            ("South Hub", "WAREHOUSE", "SOUTH", "OWNER-002"),
            ("Retail DC 1", "CUSTOMER_SITE", "NORTH", "OWNER-003"),
            ("Retail DC 2", "CUSTOMER_SITE", "SOUTH", "OWNER-003"),
            ("Repair Center", "REPAIR_CENTER", "CENTRAL", "OWNER-002"),
            ("Carrier Yard North", "CARRIER_YARD", "NORTH", None),
            ("Carrier Yard South", "CARRIER_YARD", "SOUTH", None),
        ], start=1)
    ]
    warehouse_ids = [row["location_id"] for row in locations if row["location_type"] == "WAREHOUSE"]
    customer_ids = [row["location_id"] for row in locations if row["location_type"] == "CUSTOMER_SITE"]
    operators = [
        {"operator_id": f"OP-{i:03d}", "name": name, "role": role, "location_id": location_id}
        for i, (name, role, location_id) in enumerate([
            ("Avery Planner", "PLANNER", "LOC-001"),
            ("Jordan Carrier", "CARRIER", "LOC-007"),
            ("Morgan Warehouse", "WAREHOUSE_OPERATOR", "LOC-002"),
            ("Riley Supplier", "SUPPLIER", "LOC-001"),
            ("Casey Analyst", "LOGISTICS_ANALYST", "LOC-002"),
        ], start=1)
    ]

    shipment_dates = [
        AS_OF - timedelta(days=rng.randint(35, 240))
        for _ in range(scale.shipments)
    ]
    shipments = []
    for i, dispatched in enumerate(shipment_dates, start=1):
        origin = warehouse_ids[(i - 1) % len(warehouse_ids)]
        destination = customer_ids[(i - 1) % len(customer_ids)]
        shipments.append({
            "shipment_id": f"SHP-2026-{i:06d}",
            "owner_id": "OWNER-002",
            "origin_location_id": origin,
            "destination_location_id": destination,
            "carrier_id": "OP-002",
            "dispatched_at": _dt(dispatched),
            "expected_return_at": _dt(dispatched + timedelta(days=30)),
            "status": "IN_TRANSIT",
        })

    assets = []
    shipment_assets = []
    custody_events = []
    rmas = []
    inspections = []
    repairs = []
    ledger = []
    ground_truth = []
    snapshots = []
    shipment_outcomes: dict[str, list[str]] = defaultdict(list)
    ledger_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    next_rma = next_inspection = next_repair = next_ledger = 1

    for index in range(1, scale.assets + 1):
        asset_id = f"ASSET-{index:07d}"
        shipment = shipments[(index - 1) % len(shipments)]
        shipment_id = shipment["shipment_id"]
        dispatched = _parse_day(shipment["dispatched_at"])
        asset_type = types[(index - 1) % len(types)]
        home = shipment["origin_location_id"]
        destination = shipment["destination_location_id"]
        transit_yard = "LOC-007" if destination == "LOC-004" else "LOC-008"

        scenario = {
            1: "SIMULTANEOUS_LOCATIONS",
            2: "RETURN_BEFORE_DISPATCH",
            3: "DUPLICATE_DEPOSIT_REFUND",
            4: "REPAIR_WITHOUT_INSPECTION",
            5: "CUSTODY_ACTOR_MISSING",
        }.get(index)
        if scenario:
            ground_truth.append({
                "issue_id": f"GT-{len(ground_truth) + 1:06d}",
                "issue_type": scenario,
                "entity_type": "ReusableAsset",
                "entity_id": asset_id,
                "ground_truth": "EXCEPTION",
                "expected_decision": {
                    "SIMULTANEOUS_LOCATIONS": "Flag conflicting current locations; do not silently select one.",
                    "RETURN_BEFORE_DISPATCH": "Reject or quarantine return chronology.",
                    "DUPLICATE_DEPOSIT_REFUND": "Flag the second refund and retain both ledger entries.",
                    "REPAIR_WITHOUT_INSPECTION": "Block repair closure pending inspection evidence.",
                    "CUSTODY_ACTOR_MISSING": "Flag the custody event for attribution.",
                }[scenario],
            })

        is_lost = rng.random() < 0.05
        is_returned = not is_lost and rng.random() < 0.80
        is_damaged = is_returned and rng.random() < 0.12
        if index == 1:
            is_lost, is_returned, is_damaged = False, True, False
        elif index in (2, 3):
            is_lost, is_returned = False, True
        elif index == 4:
            is_lost, is_returned, is_damaged = False, True, True
        elif index == 5:
            is_lost, is_returned = False, True

        delay_requested = is_returned and rng.random() < 0.25
        is_disputed = rng.random() < 0.03
        if delay_requested:
            return_day = dispatched + timedelta(
                days=asset_type["return_window_days"] + rng.randint(1, 25)
            )
        else:
            return_day = dispatched + timedelta(
                days=max(5, asset_type["return_window_days"] - rng.randint(1, 10))
            )
        if scenario == "RETURN_BEFORE_DISPATCH":
            return_day = dispatched - timedelta(days=1)
        assets.append({
            "asset_id": asset_id,
            "asset_type_id": asset_type["asset_type_id"],
            "owner_id": "OWNER-002",
            "home_location_id": home,
            "asset_tag": f"TAG-{index:07d}",
            "created_at": _dt(dispatched - timedelta(days=rng.randint(30, 365))),
        })
        shipment_assets.append({"shipment_id": shipment_id, "asset_id": asset_id})

        event_types = [
            "DISPATCH",
            "CUSTODY_HANDOFF_DISPUTED" if is_disputed else "CUSTODY_HANDOFF",
            "TRANSFER",
            "RETURN_DISPATCH" if is_returned else "LOSS_DECLARED" if is_lost else "CUSTODY_CHECKPOINT",
            "RETURN_RECEIVED" if is_returned else "LOSS_CONFIRMED" if is_lost else "CUSTODY_CHECKPOINT",
        ]
        dispatch_time = datetime.combine(dispatched, time(hour=9))
        return_time = datetime.combine(return_day, time(hour=9))
        event_times = [
            dispatch_time,
            dispatch_time + timedelta(hours=12),
            dispatch_time + timedelta(days=2),
            return_time - timedelta(days=1) if is_returned else dispatch_time + timedelta(days=10),
            return_time if is_returned else dispatch_time + timedelta(days=15),
        ]
        event_locations = [
            (home, destination),
            (transit_yard, destination),
            (destination, "LOC-002"),
            ("LOC-002", home) if is_returned else ("LOC-002", None),
            ("LOC-002", home) if is_returned else ("LOC-002", None),
        ]
        if scenario == "SIMULTANEOUS_LOCATIONS":
            event_times[2] = event_times[1]
            event_locations[2] = (transit_yard, "LOC-006")

        event_actors = ["OP-004", "OP-002", "OP-003", "OP-002", "OP-003"]
        if scenario == "CUSTODY_ACTOR_MISSING":
            event_actors[1] = None
        for seq in range(1, 6):
            from_location, to_location = event_locations[seq - 1]
            custody_events.append({
                "event_id": f"CUST-{asset_id}-{seq:06d}",
                "asset_id": asset_id,
                "shipment_id": shipment_id,
                "event_type": event_types[seq - 1],
                "from_location_id": from_location,
                "to_location_id": to_location,
                "actor_id": event_actors[seq - 1],
                "occurred_at": event_times[seq - 1].isoformat(timespec="seconds"),
            })

        authorized_day = dispatched + timedelta(days=2)
        processed_day = return_day if is_returned else None
        if scenario == "RETURN_BEFORE_DISPATCH":
            authorized_day = dispatched - timedelta(days=2)
            processed_day = return_day
        rma_id = f"RMA-2026-{next_rma:06d}"
        next_rma += 1
        rmas.append({
            "rma_id": rma_id,
            "asset_id": asset_id,
            "shipment_id": shipment_id,
            "authorized_at": _dt(authorized_day),
            "expected_return_at": _dt(dispatched + timedelta(days=asset_type["return_window_days"])),
            "status": "RECEIVED" if is_returned else "CLOSED_LOST" if is_lost else "OPEN",
            "processed_at": _dt(processed_day) if processed_day else None,
        })

        charge_id = f"DLG-2026-{next_ledger:08d}"
        charge = {
            "ledger_id": charge_id, "asset_id": asset_id, "shipment_id": shipment_id,
            "rma_id": None, "reference_entry_id": None, "entry_type": "DEPOSIT_CHARGE",
            "amount_cents": _money(asset_type["deposit_cents"]), "currency": "USD",
            "posted_at": shipment["dispatched_at"],
        }
        next_ledger += 1
        ledger.append(charge)
        ledger_by_asset[asset_id].append(charge)
        if is_returned:
            refund = {
                "ledger_id": f"DLG-2026-{next_ledger:08d}", "asset_id": asset_id,
                "shipment_id": shipment_id, "rma_id": rma_id, "reference_entry_id": charge_id,
                "entry_type": "DEPOSIT_REFUND", "amount_cents": -charge["amount_cents"],
                "currency": "USD", "posted_at": _dt(processed_day),
            }
            next_ledger += 1
            ledger.append(refund)
            ledger_by_asset[asset_id].append(refund)
            if scenario == "DUPLICATE_DEPOSIT_REFUND":
                duplicate = dict(refund)
                duplicate["ledger_id"] = f"DLG-2026-{next_ledger:08d}"
                duplicate["posted_at"] = _dt(processed_day + timedelta(days=1))
                next_ledger += 1
                ledger.append(duplicate)
                ledger_by_asset[asset_id].append(duplicate)

        has_damaged_inspection = False
        if is_returned and not (scenario == "REPAIR_WITHOUT_INSPECTION"):
            condition = "DAMAGED" if is_damaged else "GOOD"
            has_damaged_inspection = condition == "DAMAGED"
            decision = (
                "REPAIR" if is_damaged and asset_type["repairable"]
                else "QUARANTINE" if is_damaged
                else "RESTOCK"
            )
            inspection_id = f"INSP-2026-{next_inspection:06d}"
            next_inspection += 1
            inspections.append({
                "inspection_id": inspection_id, "asset_id": asset_id, "rma_id": rma_id,
                "inspected_at": _dt(processed_day + timedelta(days=1)),
                "condition": condition, "decision": decision,
                "notes": "Synthetic receiving inspection.",
            })
            if is_damaged and asset_type["repairable"]:
                repairs.append({
                    "repair_id": f"REP-2026-{next_repair:06d}", "asset_id": asset_id,
                    "inspection_id": inspection_id, "opened_at": _dt(processed_day + timedelta(days=2)),
                    "closed_at": _dt(processed_day + timedelta(days=4)),
                    "status": "CLOSED", "cost_cents": 1_500,
                })
                next_repair += 1
        elif scenario == "REPAIR_WITHOUT_INSPECTION":
            repairs.append({
                "repair_id": f"REP-2026-{next_repair:06d}", "asset_id": asset_id,
                "inspection_id": None, "opened_at": _dt(processed_day + timedelta(days=1)),
                "closed_at": _dt(processed_day + timedelta(days=3)),
                "status": "CLOSED", "cost_cents": 1_500,
            })
            next_repair += 1

        asset_events = custody_events[-5:]
        missing_inspection = scenario == "REPAIR_WITHOUT_INSPECTION"
        is_delayed = bool(
            rmas[-1]["processed_at"]
            and rmas[-1]["processed_at"] > rmas[-1]["expected_return_at"]
        )
        if is_delayed:
            ground_truth.append({
                "issue_id": f"GT-{len(ground_truth) + 1:06d}", "issue_type": "DELAYED_RETURN",
                "entity_type": "ReusableAsset", "entity_id": asset_id, "ground_truth": "DELAYED",
                "expected_decision": "Apply the lifecycle overdue rule using actual return date and RMA due date.",
            })
        if any(event["event_type"] == "LOSS_DECLARED" for event in asset_events):
            ground_truth.append({
                "issue_id": f"GT-{len(ground_truth) + 1:06d}", "issue_type": "LOST_ASSET",
                "entity_type": "ReusableAsset", "entity_id": asset_id, "ground_truth": "LOST",
                "expected_decision": "Keep the deposit charged and mark the asset lost from custody events.",
            })
        if has_damaged_inspection or missing_inspection:
            ground_truth.append({
                "issue_id": f"GT-{len(ground_truth) + 1:06d}", "issue_type": "DAMAGED_ASSET",
                "entity_type": "ReusableAsset", "entity_id": asset_id, "ground_truth": "DAMAGED",
                "expected_decision": "Require an inspection; repairable assets need a linked repair record.",
            })
        if any(event["event_type"] == "CUSTODY_HANDOFF_DISPUTED" for event in asset_events):
            ground_truth.append({
                "issue_id": f"GT-{len(ground_truth) + 1:06d}", "issue_type": "DISPUTED_CUSTODY_HANDOFF",
                "entity_type": "ReusableAsset", "entity_id": asset_id, "ground_truth": "DISPUTED",
                "expected_decision": "Escalate the handoff for operator confirmation.",
            })

        event_locations_by_time: dict[str, set[str]] = defaultdict(set)
        for event in asset_events:
            if event["to_location_id"] is not None:
                event_locations_by_time[event["occurred_at"]].add(event["to_location_id"])
        location_conflict = any(len(value) > 1 for value in event_locations_by_time.values())
        dispatch_event = next(event for event in asset_events if event["event_type"] == "DISPATCH")
        return_event = next((event for event in asset_events if event["event_type"] == "RETURN_RECEIVED"), None)
        return_before_dispatch = bool(return_event and return_event["occurred_at"] < dispatch_event["occurred_at"])
        asset_ledger = ledger_by_asset[asset_id]
        refund_counts = Counter(
            row["reference_entry_id"] for row in asset_ledger
            if row["entry_type"] == "DEPOSIT_REFUND"
        )
        duplicate_refund = any(count > 1 for count in refund_counts.values())
        missing_actor = any(event["actor_id"] is None for event in asset_events)
        if location_conflict or return_before_dispatch or duplicate_refund or missing_inspection or missing_actor:
            lifecycle_status = "EXCEPTION"
        elif any(event["event_type"] in {"LOSS_DECLARED", "LOSS_CONFIRMED"} for event in asset_events):
            lifecycle_status = "LOST"
        elif has_damaged_inspection or missing_inspection:
            lifecycle_status = "RETURNED_DAMAGED"
        elif return_event:
            lifecycle_status = "RETURNED"
        else:
            lifecycle_status = "OUTSTANDING"

        loss_event = next((event for event in asset_events if event["event_type"] == "LOSS_DECLARED"), None)
        if return_event:
            endpoint = _parse_day(return_event["occurred_at"])
        elif loss_event:
            endpoint = _parse_day(loss_event["occurred_at"])
        else:
            endpoint = AS_OF
        days_outstanding = max(0, (endpoint - dispatched).days)
        current_location = None if loss_event or location_conflict else next(
            (
                event["to_location_id"]
                for event in sorted(asset_events, key=lambda row: row["occurred_at"], reverse=True)
                if event["to_location_id"]
            ),
            None,
        )
        snapshots.append({
            "asset_id": asset_id,
            "current_location_id": current_location,
            "lifecycle_status": lifecycle_status,
            "days_outstanding": days_outstanding,
            "outstanding": return_event is None and loss_event is None,
            "deposit_balance_cents": sum(row["amount_cents"] for row in ledger_by_asset[asset_id]),
        })
        shipment_outcomes[shipment_id].append(
            "LOST" if any(event["event_type"] == "LOSS_DECLARED" for event in asset_events)
            else "RETURNED" if return_event
            else "OPEN"
        )

    for shipment in shipments:
        outcomes = shipment_outcomes[shipment["shipment_id"]]
        shipment["status"] = (
            "CLOSED" if all(value == "RETURNED" for value in outcomes)
            else "PARTIALLY_RETURNED" if "RETURNED" in outcomes
            else "LOST" if all(value == "LOST" for value in outcomes)
            else "IN_TRANSIT"
        )

    tables = {
        "asset_types": types,
        "owners": owners,
        "locations": locations,
        "operators": operators,
        "shipments": shipments,
        "reusable_assets": assets,
        "shipment_assets": shipment_assets,
        "custody_events": custody_events,
        "return_authorizations": rmas,
        "inspections": inspections,
        "repairs": repairs,
        "deposit_ledger": ledger,
        "asset_snapshots": snapshots,
    }
    return {"tables": tables, "ground_truth": ground_truth, "seed": seed, "profile": "custom"}


PRIMARY_KEYS = {
    "asset_types": ("asset_type_id",),
    "owners": ("owner_id",),
    "locations": ("location_id",),
    "operators": ("operator_id",),
    "shipments": ("shipment_id",),
    "reusable_assets": ("asset_id",),
    "shipment_assets": ("shipment_id", "asset_id"),
    "custody_events": ("event_id",),
    "return_authorizations": ("rma_id",),
    "inspections": ("inspection_id",),
    "repairs": ("repair_id",),
    "deposit_ledger": ("ledger_id",),
    "asset_snapshots": ("asset_id",),
}
EVENT_TYPES = {
    "DISPATCH", "CUSTODY_HANDOFF", "CUSTODY_HANDOFF_DISPUTED", "TRANSFER",
    "RETURN_DISPATCH", "RETURN_RECEIVED", "LOSS_DECLARED", "LOSS_CONFIRMED",
    "CUSTODY_CHECKPOINT",
}


def validate_dataset(dataset: dict[str, Any]) -> dict[str, Any]:
    tables = dataset["tables"]
    errors: list[str] = []
    warnings: list[str] = []
    checks: dict[str, bool] = {}

    for table, keys in PRIMARY_KEYS.items():
        seen = set()
        unique = True
        for row in tables[table]:
            key = tuple(row[field] for field in keys)
            if key in seen:
                unique = False
                errors.append(f"{table} has duplicate primary key {key}.")
            seen.add(key)
        checks[f"primary_key:{table}"] = unique

    fk_specs = {
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
    for table, specs in fk_specs.items():
        valid = True
        for field, parent_table, parent_field in specs:
            parent_ids = {row[parent_field] for row in tables[parent_table]}
            for row in tables[table]:
                value = row[field]
                if value is not None and value not in parent_ids:
                    valid = False
                    errors.append(f"{table}.{field} references missing {parent_table}.{value}.")
        checks[f"foreign_keys:{table}"] = valid

    shipment_links: dict[str, set[str]] = defaultdict(set)
    for row in tables["shipment_assets"]:
        shipment_links[row["asset_id"]].add(row["shipment_id"])
    relationship_valid = len(shipment_links) == len(tables["reusable_assets"])
    if not relationship_valid or any(len(value) != 1 for value in shipment_links.values()):
        relationship_valid = False
        errors.append("Every generated asset must link to exactly one shipment through shipment_assets.")
    rma_by_asset = {row["asset_id"]: row for row in tables["return_authorizations"]}
    if len(rma_by_asset) != len(tables["return_authorizations"]) or set(rma_by_asset) != {
        row["asset_id"] for row in tables["reusable_assets"]
    }:
        relationship_valid = False
        errors.append("Every generated asset must have exactly one return authorization.")
    for table_name in ("custody_events", "return_authorizations", "deposit_ledger"):
        for row in tables[table_name]:
            asset_id = row["asset_id"]
            if row["shipment_id"] not in shipment_links.get(asset_id, set()):
                relationship_valid = False
                errors.append(f"{table_name} row for {asset_id} references an unrelated shipment.")
    checks["entity_relationships"] = relationship_valid

    assets_by_id = {row["asset_id"]: row for row in tables["reusable_assets"]}
    gt_by_asset: dict[str, set[str]] = defaultdict(set)
    ground_truth_ids = set()
    for row in dataset["ground_truth"]:
        if row["issue_id"] in ground_truth_ids:
            errors.append(f"ground_truth has duplicate issue_id {row['issue_id']}.")
        ground_truth_ids.add(row["issue_id"])
        if row["entity_type"] == "ReusableAsset" and row["entity_id"] not in assets_by_id:
            errors.append(f"ground_truth references missing entity {row['entity_id']}.")
        gt_by_asset[row["entity_id"]].add(row["issue_type"])
    rma_by_asset = {row["asset_id"]: row for row in tables["return_authorizations"]}

    allowed = {
        "asset_types": {"repairable": {True, False}, "inspection_required": {True, False}},
        "owners": {"owner_type": {"SUPPLIER", "POOL_OPERATOR", "CUSTOMER"}},
        "locations": {"location_type": {"WAREHOUSE", "CUSTOMER_SITE", "REPAIR_CENTER", "CARRIER_YARD"}},
        "operators": {"role": {"PLANNER", "CARRIER", "WAREHOUSE_OPERATOR", "SUPPLIER", "LOGISTICS_ANALYST"}},
        "shipments": {"status": {"IN_TRANSIT", "PARTIALLY_RETURNED", "CLOSED", "LOST"}},
        "custody_events": {"event_type": EVENT_TYPES},
        "return_authorizations": {"status": {"RECEIVED", "CLOSED_LOST", "OPEN"}},
        "inspections": {
            "condition": {"GOOD", "DAMAGED"},
            "decision": {"RESTOCK", "REPAIR", "QUARANTINE"},
        },
        "repairs": {"status": {"CLOSED"}},
        "deposit_ledger": {"entry_type": {"DEPOSIT_CHARGE", "DEPOSIT_REFUND"}},
        "asset_snapshots": {
            "lifecycle_status": {"EXCEPTION", "LOST", "RETURNED_DAMAGED", "RETURNED", "OUTSTANDING"}
        },
    }
    enum_valid = True
    for table, fields in allowed.items():
        for field, choices in fields.items():
            for row in tables[table]:
                if row[field] not in choices:
                    enum_valid = False
                    errors.append(f"{table}.{field} has invalid value {row[field]!r}.")
    checks["allowed_values"] = enum_valid

    event_counts = Counter(row["asset_id"] for row in tables["custody_events"])
    event_count_valid = all(count == 5 for count in event_counts.values()) and len(event_counts) == len(assets_by_id)
    if not event_count_valid:
        errors.append("Each asset must have exactly five custody events.")
    checks["custody_events_per_asset"] = event_count_valid

    event_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in tables["custody_events"]:
        event_by_asset[event["asset_id"]].append(event)
    event_ids_valid = all(
        {event["event_id"] for event in event_by_asset.get(asset_id, [])}
        == {f"CUST-{asset_id}-{sequence:06d}" for sequence in range(1, 6)}
        for asset_id in assets_by_id
    )
    checks["custody_event_id_convention"] = event_ids_valid
    if not event_ids_valid:
        errors.append("Custody-event IDs do not match the per-asset sequence convention.")
    missing_actor_valid = True
    conflict_count = chronology_count = 0
    event_order_valid = True
    rma_state_valid = True
    for asset_id, events in event_by_asset.items():
        issue_types = gt_by_asset[asset_id]
        if any(event["actor_id"] is None for event in events):
            if "CUSTODY_ACTOR_MISSING" not in issue_types:
                missing_actor_valid = False
                errors.append(f"{asset_id} has a custody event without an actor and no ground-truth label.")
            else:
                warnings.append(f"{asset_id} contains the labelled missing-actor exception.")
        times: dict[str, set[str]] = defaultdict(set)
        for event in events:
            if event["to_location_id"]:
                times[event["occurred_at"]].add(event["to_location_id"])
        if any(len(locations_at_time) > 1 for locations_at_time in times.values()):
            conflict_count += 1
            if "SIMULTANEOUS_LOCATIONS" not in issue_types:
                errors.append(f"{asset_id} has simultaneous locations without a ground-truth label.")
            else:
                warnings.append(f"{asset_id} contains the labelled simultaneous-location exception.")
        rma = rma_by_asset.get(asset_id)
        dispatch = next((event for event in events if event["event_type"] == "DISPATCH"), None)
        return_event = next((event for event in events if event["event_type"] == "RETURN_RECEIVED"), None)
        if dispatch and "RETURN_BEFORE_DISPATCH" not in issue_types:
            ordered_events = sorted(events, key=lambda event: event["event_id"])
            if any(
                previous["occurred_at"] > current["occurred_at"]
                for previous, current in zip(ordered_events, ordered_events[1:])
            ):
                event_order_valid = False
                errors.append(f"{asset_id} custody events are not chronological.")
        if return_event and dispatch and return_event["occurred_at"] < dispatch["occurred_at"]:
            chronology_count += 1
            if "RETURN_BEFORE_DISPATCH" not in issue_types:
                errors.append(f"{asset_id} return event predates dispatch without a ground-truth label.")
            else:
                warnings.append(f"{asset_id} contains the labelled return-before-dispatch exception.")
        if rma and rma["processed_at"] and dispatch and rma["processed_at"] < dispatch["occurred_at"]:
            if "RETURN_BEFORE_DISPATCH" not in issue_types:
                errors.append(f"{asset_id} RMA was processed before dispatch without a ground-truth label.")
        if rma and rma["processed_at"] and rma["expected_return_at"] < rma["authorized_at"]:
            errors.append(f"{rma['rma_id']} has a due date before authorization.")
        if rma and dispatch:
            if rma["expected_return_at"] < dispatch["occurred_at"]:
                errors.append(f"{rma['rma_id']} due date predates dispatch.")
            if rma["status"] == "RECEIVED" and (return_event is None or rma["processed_at"] is None):
                rma_state_valid = False
                errors.append(f"{rma['rma_id']} is received without a processed return event.")
            if rma["status"] == "OPEN" and (return_event or any(event["event_type"] == "LOSS_DECLARED" for event in events)):
                rma_state_valid = False
                errors.append(f"{rma['rma_id']} is open after a return or loss event.")
            if rma["status"] == "CLOSED_LOST" and not any(event["event_type"] == "LOSS_DECLARED" for event in events):
                rma_state_valid = False
                errors.append(f"{rma['rma_id']} is closed as lost without a loss event.")
    checks["missing_actor_exceptions_labelled"] = missing_actor_valid
    checks["simultaneous_location_exception_count"] = conflict_count == 1
    checks["return_before_dispatch_exception_count"] = chronology_count == 1
    if conflict_count != 1:
        errors.append(f"Expected exactly one labelled simultaneous-location case, found {conflict_count}.")
    if chronology_count != 1:
        errors.append(f"Expected exactly one labelled return-before-dispatch case, found {chronology_count}.")
    checks["custody_event_order"] = event_order_valid
    checks["return_authorization_lifecycle"] = rma_state_valid

    inspections_by_id = {row["inspection_id"]: row for row in tables["inspections"]}
    asset_type_by_id = {row["asset_type_id"]: row for row in tables["asset_types"]}
    inspection_valid = True
    returned_assets = {
        event["asset_id"] for event in tables["custody_events"]
        if event["event_type"] == "RETURN_RECEIVED"
    }
    inspections_by_asset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for inspection in tables["inspections"]:
        inspections_by_asset[inspection["asset_id"]].append(inspection)
        asset = assets_by_id[inspection["asset_id"]]
        repairable = asset_type_by_id[asset["asset_type_id"]]["repairable"]
        expected_decision = (
            "RESTOCK" if inspection["condition"] == "GOOD"
            else "REPAIR" if repairable else "QUARANTINE"
        )
        if inspection["decision"] != expected_decision:
            inspection_valid = False
            errors.append(f"{inspection['inspection_id']} decision conflicts with condition or asset-type rules.")
        if inspection["asset_id"] not in returned_assets:
            inspection_valid = False
            errors.append(f"{inspection['inspection_id']} exists without a recorded return.")
    for asset_id in returned_assets:
        if not inspections_by_asset[asset_id] and "REPAIR_WITHOUT_INSPECTION" not in gt_by_asset[asset_id]:
            inspection_valid = False
            errors.append(f"{asset_id} has a return without an inspection.")
    checks["inspection_lifecycle"] = inspection_valid

    repair_valid = True
    for repair in tables["repairs"]:
        if repair["closed_at"] and repair["closed_at"] < repair["opened_at"]:
            repair_valid = False
            errors.append(f"{repair['repair_id']} closes before it opens.")
        if repair["status"] == "CLOSED" and not repair["inspection_id"]:
            if "REPAIR_WITHOUT_INSPECTION" not in gt_by_asset[repair["asset_id"]]:
                repair_valid = False
                errors.append(f"{repair['repair_id']} is closed without inspection evidence.")
            else:
                warnings.append(f"{repair['repair_id']} is the labelled no-inspection closure.")
        elif repair["inspection_id"] and repair["inspection_id"] not in inspections_by_id:
            repair_valid = False
            errors.append(f"{repair['repair_id']} references a missing inspection.")
        elif repair["inspection_id"]:
            inspection = inspections_by_id[repair["inspection_id"]]
            if (
                inspection["asset_id"] != repair["asset_id"]
                or inspection["condition"] != "DAMAGED"
                or inspection["decision"] != "REPAIR"
            ):
                repair_valid = False
                errors.append(f"{repair['repair_id']} is not supported by its linked inspection.")
    checks["repair_lifecycle"] = repair_valid

    charges: dict[str, int] = defaultdict(int)
    refunds: dict[str, int] = defaultdict(int)
    refund_by_charge: Counter[str] = Counter()
    ledger_by_id = {row["ledger_id"]: row for row in tables["deposit_ledger"]}
    ledger_valid = True
    for row in tables["deposit_ledger"]:
        if row["entry_type"] == "DEPOSIT_CHARGE":
            charges[row["asset_id"]] += row["amount_cents"]
            if row["amount_cents"] <= 0 or row["reference_entry_id"] is not None:
                ledger_valid = False
                errors.append(f"{row['ledger_id']} is not a valid deposit charge.")
        else:
            refunds[row["asset_id"]] += -row["amount_cents"]
            refund_by_charge[row["reference_entry_id"]] += 1
            if row["amount_cents"] >= 0:
                ledger_valid = False
                errors.append(f"{row['ledger_id']} is not a negative deposit refund.")
            reference = ledger_by_id.get(row["reference_entry_id"])
            if (
                reference is None
                or reference["entry_type"] != "DEPOSIT_CHARGE"
                or reference["asset_id"] != row["asset_id"]
                or reference["amount_cents"] != -row["amount_cents"]
            ):
                ledger_valid = False
                errors.append(f"{row['ledger_id']} does not reconcile to its referenced charge.")
    for asset_id, amount in refunds.items():
        if amount > charges[asset_id]:
            if "DUPLICATE_DEPOSIT_REFUND" not in gt_by_asset[asset_id]:
                ledger_valid = False
                errors.append(f"{asset_id} has refunds exceeding its deposit charge.")
            else:
                warnings.append(f"{asset_id} contains the labelled duplicate deposit refund.")
    duplicate_refunds = sum(1 for count in refund_by_charge.values() if count > 1)
    charge_asset = {
        row["ledger_id"]: row["asset_id"]
        for row in tables["deposit_ledger"]
        if row["entry_type"] == "DEPOSIT_CHARGE"
    }
    duplicate_refund_assets = {
        charge_asset[entry_id]
        for entry_id, count in refund_by_charge.items()
        if count > 1 and entry_id in charge_asset
    }
    if duplicate_refunds != 1:
        ledger_valid = False
        errors.append("Expected exactly one labelled duplicate-refund charge reference.")
    checks["deposit_ledger_reconciliation"] = ledger_valid

    charges_per_asset = Counter(
        row["asset_id"] for row in tables["deposit_ledger"] if row["entry_type"] == "DEPOSIT_CHARGE"
    )
    refund_count_by_asset = Counter(
        row["asset_id"] for row in tables["deposit_ledger"] if row["entry_type"] == "DEPOSIT_REFUND"
    )
    ledger_lifecycle_valid = all(charges_per_asset[asset_id] == 1 for asset_id in assets_by_id)
    for asset_id in assets_by_id:
        event_types = {event["event_type"] for event in event_by_asset[asset_id]}
        expected_refunds = (
            2 if "DUPLICATE_DEPOSIT_REFUND" in gt_by_asset[asset_id]
            else 1 if "RETURN_RECEIVED" in event_types
            else 0
        )
        if refund_count_by_asset[asset_id] != expected_refunds:
            ledger_lifecycle_valid = False
            errors.append(f"{asset_id} refund count does not match its return lifecycle.")
    checks["deposit_lifecycle"] = ledger_lifecycle_valid
    if not ledger_lifecycle_valid:
        errors.append("Each asset must have one deposit charge and refunds consistent with its lifecycle.")

    snapshot_by_id = {row["asset_id"]: row for row in tables["asset_snapshots"]}
    snapshot_state_valid = True
    gt_matches_source = True
    source_label_ids: dict[str, set[str]] = defaultdict(set)
    for rma in tables["return_authorizations"]:
        if (
            rma["status"] == "RECEIVED"
            and rma["processed_at"] is not None
            and rma["processed_at"] > rma["expected_return_at"]
        ):
            source_label_ids["DELAYED_RETURN"].add(rma["asset_id"])
    for event in tables["custody_events"]:
        if event["event_type"] == "LOSS_DECLARED":
            source_label_ids["LOST_ASSET"].add(event["asset_id"])
        elif event["event_type"] == "CUSTODY_HANDOFF_DISPUTED":
            source_label_ids["DISPUTED_CUSTODY_HANDOFF"].add(event["asset_id"])
    for inspection in tables["inspections"]:
        if inspection["condition"] == "DAMAGED":
            source_label_ids["DAMAGED_ASSET"].add(inspection["asset_id"])
    for repair in tables["repairs"]:
        if repair["inspection_id"] is None:
            source_label_ids["DAMAGED_ASSET"].add(repair["asset_id"])
    label_types = (
        "DELAYED_RETURN", "LOST_ASSET", "DAMAGED_ASSET", "DISPUTED_CUSTODY_HANDOFF"
    )
    for label_type in label_types:
        labelled = {
            asset_id for asset_id, issue_types in gt_by_asset.items() if label_type in issue_types
        }
        if labelled != source_label_ids[label_type]:
            gt_matches_source = False
            errors.append(f"{label_type} ground truth does not match its source records.")

    for asset_id, snapshot in snapshot_by_id.items():
        expected_balance = charges[asset_id] - refunds[asset_id]
        if snapshot["deposit_balance_cents"] != expected_balance:
            errors.append(f"{asset_id} snapshot deposit balance does not match ledger entries.")
        events = event_by_asset.get(asset_id, [])
        if not events:
            snapshot_state_valid = False
            errors.append(f"{asset_id} snapshot has no custody events.")
            continue
        time_locations: dict[str, set[str]] = defaultdict(set)
        for event in events:
            if event["to_location_id"] is not None:
                time_locations[event["occurred_at"]].add(event["to_location_id"])
        has_conflict = any(len(value) > 1 for value in time_locations.values())
        has_return = any(event["event_type"] == "RETURN_RECEIVED" for event in events)
        loss_event = next((event for event in events if event["event_type"] == "LOSS_DECLARED"), None)
        has_missing_actor = any(event["actor_id"] is None for event in events)
        duplicate_refund = asset_id in duplicate_refund_assets
        no_inspection_repair = any(
            row["asset_id"] == asset_id and row["inspection_id"] is None
            for row in tables["repairs"]
        )
        dispatch = next((event for event in events if event["event_type"] == "DISPATCH"), None)
        return_event = next((event for event in events if event["event_type"] == "RETURN_RECEIVED"), None)
        return_before_dispatch = bool(
            return_event and dispatch and return_event["occurred_at"] < dispatch["occurred_at"]
        )
        exceptional = (
            has_conflict or has_missing_actor or duplicate_refund or no_inspection_repair
            or return_before_dispatch
        )
        damaged = (
            asset_id in source_label_ids["DAMAGED_ASSET"]
        )
        expected_status = (
            "EXCEPTION" if exceptional else
            "LOST" if loss_event else
            "RETURNED_DAMAGED" if damaged else
            "RETURNED" if has_return else
            "OUTSTANDING"
        )
        expected_outstanding = not has_return and loss_event is None
        dispatched_day = _parse_day(dispatch["occurred_at"]) if dispatch else AS_OF
        endpoint = (
            _parse_day(return_event["occurred_at"]) if return_event else
            _parse_day(loss_event["occurred_at"]) if loss_event else
            AS_OF
        )
        expected_age = max(0, (endpoint - dispatched_day).days)
        if (
            snapshot["lifecycle_status"] != expected_status
            or snapshot["outstanding"] != expected_outstanding
            or snapshot["days_outstanding"] != expected_age
        ):
            snapshot_state_valid = False
            errors.append(f"{asset_id} snapshot lifecycle or outstanding age disagrees with source events.")
        expected_location = None
        if not loss_event and not has_conflict:
            expected_location = next(
                (
                    event["to_location_id"]
                    for event in sorted(events, key=lambda row: row["occurred_at"], reverse=True)
                    if event["to_location_id"] is not None
                ),
                None,
            )
        if snapshot["current_location_id"] != expected_location:
            snapshot_state_valid = False
            errors.append(f"{asset_id} snapshot location disagrees with custody history.")
    checks["snapshot_ledger_balance"] = not any("snapshot deposit balance" in e for e in errors)
    checks["all_assets_have_snapshot"] = len(snapshot_by_id) == len(assets_by_id)
    if not checks["all_assets_have_snapshot"]:
        errors.append("Every asset must have exactly one derived snapshot.")
    checks["snapshot_lifecycle_and_age"] = snapshot_state_valid
    checks["ground_truth_matches_source_rules"] = gt_matches_source

    delayed_count = sum(
        row["status"] == "RECEIVED"
        and row["processed_at"] is not None
        and row["processed_at"] > row["expected_return_at"]
        for row in tables["return_authorizations"]
    )
    received_count = sum(row["status"] == "RECEIVED" for row in tables["return_authorizations"])
    lost_count = sum(
        event["event_type"] == "LOSS_DECLARED"
        for event in tables["custody_events"]
    )
    damaged_count = len(source_label_ids["DAMAGED_ASSET"])
    disputed_count = len(source_label_ids["DISPUTED_CUSTODY_HANDOFF"])
    total_charges = sum(charges.values())
    total_refunds = sum(refunds.values())
    metrics = {
        "assets": len(tables["reusable_assets"]),
        "shipments": len(tables["shipments"]),
        "custody_events": len(tables["custody_events"]),
        "delayed_returns": delayed_count,
        "lost_assets": lost_count,
        "damaged_assets": damaged_count,
        "disputed_handoffs": disputed_count,
        "ground_truth_labels": len(dataset["ground_truth"]),
        "deposit_charges_cents": total_charges,
        "deposit_refunds_cents": total_refunds,
        "net_deposit_balance_cents": total_charges - total_refunds,
        "assets_with_nonzero_deposit_balance": sum(1 for value in snapshot_by_id.values() if value["deposit_balance_cents"]),
        "exception_assets": sum(1 for value in snapshot_by_id.values() if value["lifecycle_status"] == "EXCEPTION"),
        "on_time_return_rate": round(
            (received_count - delayed_count) / max(1, received_count),
            4,
        ),
    }
    return {
        "passed": not errors and all(checks.values()),
        "checks": checks,
        "errors": errors,
        "warnings": warnings,
        "metrics": metrics,
        "row_counts": {name: len(rows) for name, rows in tables.items()},
    }


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    return "'" + str(value).replace("'", "''") + "'"


def _write_dataset(
    dataset: dict[str, Any],
    directory: Path,
    validation: dict[str, Any] | None = None,
) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)
    tables = dataset["tables"]
    for table_name, fields in TABLE_FIELDS.items():
        with (directory / f"{table_name}.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="raise")
            writer.writeheader()
            writer.writerows({key: _csv_value(row.get(key)) for key in fields} for row in tables[table_name])

    data_payload = {
        "seed": dataset["seed"],
        "profile": dataset["profile"],
        "tables": tables,
    }
    with (directory / "data.json").open("w", encoding="utf-8") as handle:
        json.dump(data_payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    with (directory / "ground_truth.json").open("w", encoding="utf-8") as handle:
        json.dump(dataset["ground_truth"], handle, indent=2, sort_keys=True)
        handle.write("\n")
    gt_fields = ["issue_id", "issue_type", "entity_type", "entity_id", "ground_truth", "expected_decision"]
    with (directory / "ground_truth.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=gt_fields)
        writer.writeheader()
        writer.writerows(dataset["ground_truth"])

    with (directory / "seed.sql").open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("-- Generated synthetic SQL seed; apply schema.sql first.\n")
        handle.write("PRAGMA foreign_keys = ON;\n")
        for table_name, fields in TABLE_FIELDS.items():
            for row in tables[table_name]:
                columns = ", ".join(fields)
                values = ", ".join(_sql_literal(row.get(field)) for field in fields)
                handle.write(f"INSERT INTO {table_name} ({columns}) VALUES ({values});\n")

    validation = validation or validate_dataset(dataset)
    (directory / "metrics.json").write_text(
        json.dumps(validation["metrics"], indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (directory / "validation_summary.json").write_text(
        json.dumps({key: value for key, value in validation.items() if key != "metrics"},
                   indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    hashes = {}
    for path in sorted(directory.iterdir()):
        if path.is_file():
            hashes[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "generator_version": GENERATOR_VERSION,
        "seed": dataset["seed"],
        "profile": dataset["profile"],
        "as_of_date": AS_OF.isoformat(),
        "provenance": "Entirely synthetic; no external datasets or personal data used.",
        "row_counts": validation["row_counts"],
        "output_sha256": hashes,
    }
    (directory / "source_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    hashes["source_manifest.json"] = hashlib.sha256(
        (directory / "source_manifest.json").read_bytes()
    ).hexdigest()
    return hashes


def verify_reproducibility(profile: str, seed: int) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="returnables-repro-") as root:
        first = Path(root) / "first"
        second = Path(root) / "second"
        first_hashes = _write_dataset(generate_dataset(profile, seed), first)
        second_hashes = _write_dataset(generate_dataset(profile, seed), second)
        return {
            "passed": first_hashes == second_hashes,
            "profile": profile,
            "seed": seed,
            "files_compared": len(first_hashes),
            "first_sha256": first_hashes,
            "second_sha256": second_hashes,
        }


def _validate_sqlite(dataset: dict[str, Any], schema_path: Path) -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(schema_path.read_text(encoding="utf-8"))
        connection.execute("PRAGMA foreign_keys = ON")
        for table_name, fields in TABLE_FIELDS.items():
            if table_name == "asset_snapshots":
                for row in dataset["tables"][table_name]:
                    values = [row.get(field) for field in fields]
                    connection.execute(
                        f"INSERT INTO asset_snapshots ({', '.join(fields)}) VALUES "
                        f"({', '.join('?' for _ in fields)})", values
                    )
                continue
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate synthetic reusable-asset logistics data.")
    parser.add_argument("--profile", choices=PROFILES, default="starter")
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--output", type=Path, default=Path("output/starter"))
    parser.add_argument("--verify-reproducibility", action="store_true")
    args = parser.parse_args(argv)

    dataset = generate_dataset(args.profile, args.seed)
    validation = validate_dataset(dataset)
    if not validation["passed"]:
        print(json.dumps(validation, indent=2))
        return 1

    schema_path = Path(__file__).resolve().parent.parent / "schema.sql"
    try:
        _validate_sqlite(dataset, schema_path)
    except (sqlite3.Error, ValueError) as error:
        print(f"SQLite validation failed: {error}")
        return 1

    hashes = _write_dataset(dataset, args.output, validation)
    report: dict[str, Any] = {
        "output": str(args.output.resolve()),
        "profile": args.profile,
        "seed": args.seed,
        "validation_passed": validation["passed"],
        "row_counts": validation["row_counts"],
        "metrics": validation["metrics"],
        "files_written": len(hashes),
    }
    if args.verify_reproducibility:
        reproducibility = verify_reproducibility(args.profile, args.seed)
        report["reproducibility"] = {
            "passed": reproducibility["passed"],
            "files_compared": reproducibility["files_compared"],
        }
        if not reproducibility["passed"]:
            print(json.dumps(report, indent=2))
            return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
