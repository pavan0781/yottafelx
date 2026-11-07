PRAGMA foreign_keys = ON;

CREATE TABLE asset_types (
    asset_type_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    deposit_cents INTEGER NOT NULL CHECK (deposit_cents > 0),
    replacement_cost_cents INTEGER NOT NULL CHECK (replacement_cost_cents > 0),
    return_window_days INTEGER NOT NULL CHECK (return_window_days > 0),
    max_cycles INTEGER NOT NULL CHECK (max_cycles > 0),
    repairable INTEGER NOT NULL CHECK (repairable IN (0, 1)),
    inspection_required INTEGER NOT NULL CHECK (inspection_required IN (0, 1))
);

CREATE TABLE owners (
    owner_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    owner_type TEXT NOT NULL CHECK (owner_type IN ('SUPPLIER', 'POOL_OPERATOR', 'CUSTOMER')),
    active INTEGER NOT NULL CHECK (active IN (0, 1))
);

CREATE TABLE locations (
    location_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    location_type TEXT NOT NULL CHECK (location_type IN ('WAREHOUSE', 'CUSTOMER_SITE', 'REPAIR_CENTER', 'CARRIER_YARD')),
    region TEXT NOT NULL,
    owner_id TEXT REFERENCES owners(owner_id)
);

CREATE TABLE operators (
    operator_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'SUPPLIER', 'LOGISTICS_ANALYST')),
    location_id TEXT NOT NULL REFERENCES locations(location_id)
);

CREATE TABLE shipments (
    shipment_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    origin_location_id TEXT NOT NULL REFERENCES locations(location_id),
    destination_location_id TEXT NOT NULL REFERENCES locations(location_id),
    carrier_id TEXT NOT NULL REFERENCES operators(operator_id),
    dispatched_at TEXT NOT NULL,
    expected_return_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('IN_TRANSIT', 'PARTIALLY_RETURNED', 'CLOSED', 'LOST'))
);

CREATE TABLE reusable_assets (
    asset_id TEXT PRIMARY KEY,
    asset_type_id TEXT NOT NULL REFERENCES asset_types(asset_type_id),
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    home_location_id TEXT NOT NULL REFERENCES locations(location_id),
    asset_tag TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE shipment_assets (
    shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id),
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    PRIMARY KEY (shipment_id, asset_id)
);

CREATE TABLE custody_events (
    event_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    shipment_id TEXT REFERENCES shipments(shipment_id),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'DISPATCH', 'CUSTODY_HANDOFF', 'CUSTODY_HANDOFF_DISPUTED', 'TRANSFER',
        'RETURN_DISPATCH', 'RETURN_RECEIVED', 'LOSS_DECLARED', 'LOSS_CONFIRMED',
        'CUSTODY_CHECKPOINT'
    )),
    from_location_id TEXT REFERENCES locations(location_id),
    to_location_id TEXT REFERENCES locations(location_id),
    actor_id TEXT REFERENCES operators(operator_id),
    occurred_at TEXT NOT NULL
);

CREATE TABLE return_authorizations (
    rma_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id),
    authorized_at TEXT NOT NULL,
    expected_return_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RECEIVED', 'CLOSED_LOST', 'OPEN')),
    processed_at TEXT
);

CREATE TABLE inspections (
    inspection_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    rma_id TEXT NOT NULL REFERENCES return_authorizations(rma_id),
    inspected_at TEXT NOT NULL,
    condition TEXT NOT NULL CHECK (condition IN ('GOOD', 'DAMAGED')),
    decision TEXT NOT NULL CHECK (decision IN ('RESTOCK', 'REPAIR', 'QUARANTINE')),
    notes TEXT NOT NULL
);

CREATE TABLE repairs (
    repair_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    inspection_id TEXT REFERENCES inspections(inspection_id),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('CLOSED')),
    cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0)
);

CREATE TABLE deposit_ledger (
    ledger_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id),
    rma_id TEXT REFERENCES return_authorizations(rma_id),
    reference_entry_id TEXT REFERENCES deposit_ledger(ledger_id),
    entry_type TEXT NOT NULL CHECK (entry_type IN ('DEPOSIT_CHARGE', 'DEPOSIT_REFUND')),
    amount_cents INTEGER NOT NULL CHECK (amount_cents != 0),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    posted_at TEXT NOT NULL
);

CREATE TABLE asset_snapshots (
    asset_id TEXT PRIMARY KEY REFERENCES reusable_assets(asset_id),
    current_location_id TEXT REFERENCES locations(location_id),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'EXCEPTION', 'LOST', 'RETURNED_DAMAGED', 'RETURNED', 'OUTSTANDING'
    )),
    days_outstanding INTEGER NOT NULL CHECK (days_outstanding >= 0),
    outstanding INTEGER NOT NULL CHECK (outstanding IN (0, 1)),
    deposit_balance_cents INTEGER NOT NULL
);
