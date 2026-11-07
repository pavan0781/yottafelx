# Data Dictionary and Entity Relationships

All identifiers are immutable strings. Timestamps use ISO-8601 local date-time values; the synthetic generator uses one fixed timeline and a fixed as-of date of `2026-11-07`. Currency values are integer cents (USD). `required` means non-null unless the field is explicitly optional. IDs in the example use the same zero-padded conventions as generated records.

## Entities

| Entity / table | Primary key | Fields (type; required; allowed values / validation) |
|---|---|---|
| `AssetType` / `asset_types` | `asset_type_id` | `name` (string; required; unique); `deposit_cents` (integer; required; >0, USD cents); `replacement_cost_cents` (integer; required; >0); `return_window_days` (integer; required; >0 days); `max_cycles` (integer; required; >0 cycles); `repairable` (boolean; required); `inspection_required` (boolean; required). |
| `Owner` / `owners` | `owner_id` | `name` (string; required; unique); `owner_type` (enum; required: `SUPPLIER`, `POOL_OPERATOR`, `CUSTOMER`); `active` (boolean; required). |
| `Location` / `locations` | `location_id` | `name` (string; required; unique); `location_type` (enum; required: `WAREHOUSE`, `CUSTOMER_SITE`, `REPAIR_CENTER`, `CARRIER_YARD`); `region` (string; required); `owner_id` (FK; optional for a third-party carrier yard). |
| `Operator` / `operators` | `operator_id` | `name` (string; required); `role` (enum; required: `PLANNER`, `CARRIER`, `WAREHOUSE_OPERATOR`, `SUPPLIER`, `LOGISTICS_ANALYST`); `location_id` (FK; required). Operators supply actor identity for custody and carrier identity for shipments. |
| `Shipment` / `shipments` | `shipment_id` | `owner_id`, `origin_location_id`, `destination_location_id`, `carrier_id` (FKs; required); `dispatched_at`, `expected_return_at` (timestamps; required; expected return cannot precede dispatch); `status` (enum; required: `IN_TRANSIT`, `PARTIALLY_RETURNED`, `CLOSED`, `LOST`; derived from linked assets). |
| `ReusableAsset` / `reusable_assets` | `asset_id` | `asset_type_id`, `owner_id`, `home_location_id` (FKs; required); `asset_tag` (string; required; unique); `created_at` (timestamp; required; no later than dispatch). Current location and lifecycle state are derived in `asset_snapshots`, not overwritten on the master. |
| `ShipmentAsset` / `shipment_assets` | (`shipment_id`, `asset_id`) | Both fields are required FKs. This bridge resolves Shipment↔ReusableAsset as N:M and prevents duplicate association rows. |
| `CustodyEvent` / `custody_events` | `event_id` | `asset_id` (FK; required); `shipment_id` (FK; optional); `event_type` (enum; required: `DISPATCH`, `CUSTODY_HANDOFF`, `CUSTODY_HANDOFF_DISPUTED`, `TRANSFER`, `RETURN_DISPATCH`, `RETURN_RECEIVED`, `LOSS_DECLARED`, `LOSS_CONFIRMED`, `CUSTODY_CHECKPOINT`); `from_location_id`, `to_location_id` (FKs; optional for unknown/lost location); `actor_id` (FK; optional only in labelled missing-actor case); `occurred_at` (timestamp; required). IDs are `CUST-<asset-id>-000001`, incremented per asset. |
| `ReturnAuthorization` / `return_authorizations` | `rma_id` | `asset_id`, `shipment_id` (FKs; required); `authorized_at`, `expected_return_at` (timestamps; required; expected date must not precede authorization except where chronology is independently labelled anomalous); `status` (enum: `RECEIVED`, `CLOSED_LOST`, `OPEN`); `processed_at` (timestamp; optional until processed). IDs are `RMA-2026-000001`. |
| `Inspection` / `inspections` | `inspection_id` | `asset_id`, `rma_id` (FKs; required); `inspected_at` (timestamp; required); `condition` (enum: `GOOD`, `DAMAGED`); `decision` (enum: `RESTOCK`, `REPAIR`, `QUARANTINE`; non-repairable damage is quarantined); `notes` (string; required). |
| `Repair` / `repairs` | `repair_id` | `asset_id` (FK; required); `inspection_id` (FK; optional only for the labelled invalid-close case); `opened_at` (timestamp; required); `closed_at` (timestamp; optional until closed); `status` (enum: `CLOSED` in generated output); `cost_cents` (integer; required; >=0, USD cents). IDs are `REP-2026-000001`. |
| `DepositLedger` / `deposit_ledger` | `ledger_id` | `asset_id`, `shipment_id` (FKs; required); `rma_id` (FK; optional on initial charge); `reference_entry_id` (self-FK; optional on charge, required on refund); `entry_type` (enum: `DEPOSIT_CHARGE`, `DEPOSIT_REFUND`); `amount_cents` (integer; nonzero; positive charge, negative refund); `currency` (enum: `USD`); `posted_at` (timestamp; required). The sum of signed amounts is the balance; refund references identify the original charge. |
| `AssetSnapshot` / `asset_snapshots` | `asset_id` | `current_location_id` (FK; optional if lost or conflicting); `lifecycle_status` (enum: `EXCEPTION`, `LOST`, `RETURNED_DAMAGED`, `RETURNED`, `OUTSTANDING`); `days_outstanding` (integer; required; >=0 days); `outstanding` (boolean; required); `deposit_balance_cents` (integer; required; must equal ledger sum). Derived from source records. |
| `GroundTruth` / `ground_truth.csv` | `issue_id` | `issue_type`, `entity_type`, `entity_id`, `ground_truth`, `expected_decision` (strings; required). This separate label set is not part of operational input features. Its `entity_id` refers to an asset or other explicitly identified entity. |

### Unique values and ID conventions

`ASSET-0000001`, `SHP-2026-000001`, `RMA-2026-000001`, and `REP-2026-000001` use immutable, zero-padded sequence identifiers. Custody IDs include the asset ID plus a per-asset six-digit sequence. The asset tag and names for asset types, owners, and locations are unique. Other entity primary keys use deterministic prefixes such as `ATYPE-001`, `LOC-001`, `OP-001`, `INSP-2026-000001`, and `DLG-2026-00000001`.

## Relationships and cardinality

| Parent → child | Cardinality | Optionality / rule |
|---|---|---|
| AssetType → ReusableAsset | 1:N | Every asset has exactly one type; a type can be unused. |
| Owner → ReusableAsset | 1:N | Every asset has one recorded owner. |
| Owner → Location | 1:N | A location may have no owner when operated by an external carrier. |
| Location → ReusableAsset (home) | 1:N | Every asset has one home location. |
| Owner/Location/Operator → Shipment | 1:N | Shipment owner, origin, destination, and carrier are required. |
| Shipment ↔ ReusableAsset | N:M | `shipment_assets` is the bridge; assets can cycle through shipments. Generated sample assigns one shipment per asset. |
| ReusableAsset → CustodyEvent | 1:N | Every event has an asset; event history is append-only. |
| Shipment → CustodyEvent | 1:N | Optional for non-shipment custody events. |
| Operator → CustodyEvent | 1:N | Actor is optional only in the explicitly labelled anomaly. |
| ReusableAsset → ReturnAuthorization | 1:N | Each generated asset has one RMA; future return cycles may have multiple. |
| ReturnAuthorization → Inspection | 1:N | A received return normally has one inspection; the deliberate missing-inspection repair exception is labelled. |
| Inspection → Repair | 1:N | Repairs may link to inspection; only the deliberate exception has no inspection. |
| ReusableAsset → DepositLedger | 1:N | Each charge/refund is an explicit immutable ledger entry. |
| DepositLedger → DepositLedger | 1:N | Refund entries reference their original charge. |
| ReusableAsset → AssetSnapshot | 1:1 | One derived snapshot per asset, as of `2026-11-07`; event and ledger rows remain the source of truth. |
| ReusableAsset → GroundTruth | 1:N | Zero or more separate labels; ground-truth rows are excluded from feature tables and SQL seeds. |

## Lifecycle and derived values

Normal flow: dispatch → custody handoff/transfer → RMA → return dispatch → return receipt → inspection → optional repair → deposit refund. Lost assets end with a loss declaration/confirmation and retain the deposit. Outstanding age is whole elapsed days from dispatch through the actual return date, loss date, or fixed as-of date for open assets. A returned asset is delayed only when its receipt occurs after the RMA's expected return date. Amounts and lifecycle metrics are derived from events and ledger rows, never independently randomized.

The five intentionally invalid cases are isolated and labelled: two destinations at the same event time, return before dispatch, a second refund against the same charge, closed repair without an inspection, and a custody event without an actor. They are expected validation warnings and downstream exceptions, not unexplained data corruption.
