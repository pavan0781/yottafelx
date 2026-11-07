PRAGMA foreign_keys = ON;

CREATE TABLE asset_types (
    asset_type_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    deposit_cents INTEGER NOT NULL CHECK (deposit_cents > 0),
    replacement_cost_cents INTEGER NOT NULL CHECK (replacement_cost_cents > 0),
    return_window_days INTEGER NOT NULL CHECK (return_window_days > 0),
    loss_threshold_days INTEGER NOT NULL CHECK (loss_threshold_days BETWEEN 60 AND 120),
    late_fee_cents_per_day INTEGER NOT NULL CHECK (late_fee_cents_per_day >= 0),
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
    location_type TEXT NOT NULL CHECK (location_type IN ('WAREHOUSE', 'CUSTOMER_SITE', 'CARRIER_YARD')),
    region TEXT NOT NULL,
    owner_id TEXT REFERENCES owners(owner_id)
);

CREATE TABLE operators (
    operator_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN (
        'PLANNER', 'CARRIER', 'WAREHOUSE_OPERATOR', 'SUPPLIER',
        'LOGISTICS_ANALYST', 'OPERATIONS_MANAGER'
    )),
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
    status TEXT NOT NULL CHECK (status IN ('IN_TRANSIT', 'PARTIALLY_RETURNED', 'CLOSED', 'LOST')),
    CHECK (date(expected_return_at) >= date(dispatched_at))
);

CREATE TABLE reusable_assets (
    asset_id TEXT PRIMARY KEY,
    asset_type_id TEXT NOT NULL REFERENCES asset_types(asset_type_id),
    owner_id TEXT NOT NULL REFERENCES owners(owner_id),
    home_location_id TEXT NOT NULL REFERENCES locations(location_id),
    asset_tag TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    planned_cycles INTEGER NOT NULL CHECK (planned_cycles BETWEEN 1 AND 3)
);

CREATE TABLE shipment_assets (
    shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id),
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    cycle_number INTEGER NOT NULL CHECK (cycle_number BETWEEN 1 AND 3),
    PRIMARY KEY (shipment_id, asset_id, cycle_number),
    UNIQUE (asset_id, cycle_number)
);

CREATE TABLE custody_events (
    event_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id),
    cycle_number INTEGER NOT NULL CHECK (cycle_number BETWEEN 1 AND 3),
    event_type TEXT NOT NULL CHECK (event_type IN (
        'DISPATCH', 'CUSTODY_HANDOFF', 'CUSTODY_HANDOFF_DISPUTED',
        'CUSTODY_DISPUTE_RESOLVED', 'RETURN_DISPATCH', 'RETURN_RECEIVED',
        'LOSS_DECLARED', 'CUSTODY_CHECKPOINT'
    )),
    from_location_id TEXT NOT NULL REFERENCES locations(location_id),
    to_location_id TEXT NOT NULL REFERENCES locations(location_id),
    actor_id TEXT REFERENCES operators(operator_id),
    occurred_at TEXT NOT NULL
);
CREATE INDEX idx_custody_events_asset_time
    ON custody_events(asset_id, occurred_at, event_id);

CREATE TABLE return_authorizations (
    rma_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id),
    cycle_number INTEGER NOT NULL CHECK (cycle_number BETWEEN 1 AND 3),
    authorized_at TEXT NOT NULL,
    expected_return_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('RECEIVED', 'CLOSED_LOST', 'OPEN')),
    processed_at TEXT,
    UNIQUE (asset_id, cycle_number),
    CHECK (date(expected_return_at) >= date(authorized_at))
);

CREATE TABLE inspections (
    inspection_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    rma_id TEXT NOT NULL REFERENCES return_authorizations(rma_id),
    cycle_number INTEGER NOT NULL CHECK (cycle_number BETWEEN 1 AND 3),
    inspected_at TEXT NOT NULL,
    condition TEXT NOT NULL CHECK (condition IN ('GOOD', 'DAMAGED')),
    decision TEXT NOT NULL CHECK (decision IN ('RESTOCK', 'REPAIR', 'QUARANTINE')),
    notes TEXT NOT NULL,
    UNIQUE (rma_id)
);

CREATE TABLE repairs (
    repair_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    inspection_id TEXT REFERENCES inspections(inspection_id),
    rma_id TEXT NOT NULL REFERENCES return_authorizations(rma_id),
    cycle_number INTEGER NOT NULL CHECK (cycle_number BETWEEN 1 AND 3),
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('OPEN', 'CLOSED')),
    cost_cents INTEGER NOT NULL CHECK (cost_cents >= 0),
    UNIQUE (rma_id),
    CHECK (
        (status = 'OPEN' AND closed_at IS NULL)
        OR (status = 'CLOSED' AND closed_at IS NOT NULL AND date(closed_at) >= date(opened_at))
    )
);

CREATE TABLE deposit_ledger (
    ledger_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES reusable_assets(asset_id),
    shipment_id TEXT NOT NULL REFERENCES shipments(shipment_id),
    rma_id TEXT NOT NULL REFERENCES return_authorizations(rma_id),
    cycle_number INTEGER NOT NULL CHECK (cycle_number BETWEEN 1 AND 3),
    reference_entry_id TEXT REFERENCES deposit_ledger(ledger_id),
    entry_type TEXT NOT NULL CHECK (entry_type IN (
        'DEPOSIT_CHARGE', 'LATE_FEE_DEDUCTION', 'DAMAGE_DEDUCTION',
        'LOSS_FORFEIT', 'DEPOSIT_REFUND'
    )),
    amount_cents INTEGER NOT NULL CHECK (
        (entry_type = 'DEPOSIT_CHARGE' AND amount_cents > 0 AND reference_entry_id IS NULL)
        OR (entry_type != 'DEPOSIT_CHARGE' AND amount_cents < 0 AND reference_entry_id IS NOT NULL)
    ),
    currency TEXT NOT NULL CHECK (currency = 'USD'),
    posted_at TEXT NOT NULL
);

CREATE TABLE asset_snapshots (
    asset_id TEXT PRIMARY KEY REFERENCES reusable_assets(asset_id),
    current_location_id TEXT NOT NULL REFERENCES locations(location_id),
    lifecycle_status TEXT NOT NULL CHECK (lifecycle_status IN (
        'AVAILABLE', 'IN_TRANSIT', 'AT_CUSTOMER', 'CUSTODY_DISPUTED',
        'RETURN_IN_TRANSIT', 'OUTSTANDING', 'OVERDUE', 'LOST',
        'IN_REPAIR', 'QUARANTINED'
    )),
    current_cycle_number INTEGER NOT NULL CHECK (current_cycle_number BETWEEN 1 AND 3),
    days_outstanding INTEGER NOT NULL CHECK (days_outstanding >= 0),
    outstanding INTEGER NOT NULL CHECK (outstanding IN (0, 1)),
    deposit_balance_cents INTEGER NOT NULL
);

CREATE TABLE lifecycle_transitions (
    from_state TEXT NOT NULL,
    event_type TEXT NOT NULL,
    to_state TEXT NOT NULL,
    PRIMARY KEY (from_state, event_type, to_state)
);

CREATE VIEW asset_location_as_of AS
WITH moving_events AS (
    SELECT asset_id, to_location_id, occurred_at, event_id,
           ROW_NUMBER() OVER (
               PARTITION BY asset_id ORDER BY occurred_at DESC, event_id DESC
           ) AS position
    FROM custody_events
    WHERE event_type NOT IN (
        'CUSTODY_HANDOFF_DISPUTED', 'CUSTODY_CHECKPOINT', 'LOSS_DECLARED'
    )
)
SELECT asset_id, to_location_id AS current_location_id
FROM moving_events
WHERE position = 1;

CREATE VIEW asset_days_outstanding AS
WITH latest_cycle AS (
    SELECT asset_id, MAX(cycle_number) AS cycle_number
    FROM return_authorizations
    GROUP BY asset_id
),
cycle_dates AS (
    SELECT r.asset_id, r.cycle_number,
           date(s.dispatched_at) AS dispatch_day,
           date(COALESCE(
               (SELECT e.occurred_at FROM custody_events e
                WHERE e.asset_id = r.asset_id AND e.cycle_number = r.cycle_number
                  AND e.event_type = 'RETURN_RECEIVED'
                ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1),
               (SELECT e.occurred_at FROM custody_events e
                WHERE e.asset_id = r.asset_id AND e.cycle_number = r.cycle_number
                  AND e.event_type = 'LOSS_DECLARED'
                ORDER BY e.occurred_at DESC, e.event_id DESC LIMIT 1),
               '2026-11-07'
           )) AS end_day
    FROM return_authorizations r
    JOIN shipments s ON s.shipment_id = r.shipment_id
    JOIN latest_cycle lc ON lc.asset_id = r.asset_id AND lc.cycle_number = r.cycle_number
)
SELECT asset_id, MAX(0, CAST(julianday(end_day) - julianday(dispatch_day) AS INTEGER)) AS days_outstanding
FROM cycle_dates;

CREATE VIEW asset_deposit_balance AS
SELECT a.asset_id, COALESCE(SUM(l.amount_cents), 0) AS deposit_balance_cents
FROM reusable_assets a
LEFT JOIN deposit_ledger l ON l.asset_id = a.asset_id
GROUP BY a.asset_id;
