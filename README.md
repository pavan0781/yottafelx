# Reusable Asset Returns and Custody

A deterministic, standard-library-only synthetic data pipeline for coordinating reusable packaging, pallets, and equipment through repeated shipment, custody, return, inspection, repair, and deposit-settlement cycles.

## Run

Tested with Python 3.14.0; Python 3.10+ is supported. There are no third-party runtime dependencies, credentials, network calls, or external data sources. Run commands from the repository root.

```sh
python3 -m unittest discover -s tests -v
python3 -m returnables.generator --profile starter --output output/starter --verify-reproducibility
python3 -m returnables.verify --schema schema.sql --seed output/starter/seed.sql
python3 -m returnables.generator --profile full --output output/full --verify-reproducibility
```

On Windows, use `python` instead of `python3` if that is the installed launcher.

The starter profile contains 200 assets, 100 shipments, and exactly 1,000 custody events. The full profile contains exactly 20,000 assets, 10,000 shipments, and 100,000 custody events. Assets complete one to three trips. All profiles use the same generator and fixed seed `20261022`; pass `--seed N` for another deterministic run. The full profile's event target is met by adding non-moving `CUSTODY_CHECKPOINT` scans after all rule-driven events, never by dropping required lifecycle events.

`--verify-reproducibility` runs generation twice and compares SHA-256 checksums for every generated file. It writes the check evidence to `reproducibility.json`; `source_manifest.json` lists output row counts and checksums. The CLI exits nonzero on validation or reproducibility failure.

To produce a clean baseline without injected anomalies:

```sh
python3 -m returnables.generator --profile starter --output output/clean --no-anomalies
```

Anomaly counts are configurable; unspecified types default to one:

```sh
python3 -m returnables.generator --profile starter --output output/custom \
  --anomaly-count SIMULTANEOUS_LOCATIONS=2 \
  --anomaly-count RETURN_BEFORE_DISPATCH=1 \
  --anomaly-count DUPLICATE_DEPOSIT_REFUND=1 \
  --anomaly-count REPAIR_WITHOUT_INSPECTION=1 \
  --anomaly-count CUSTODY_ACTOR_MISSING=1
```

The five anomaly types are selected at seeded random positions from valid baseline candidates. The generator first creates and independently validates a clean dataset, then copies it and injects labelled anomalies. `ground_truth.csv` and `ground_truth.json` identify the precise offending record and are not included in operational feature tables or the SQL seed.

## Verify in 60 seconds

Generate the starter dataset, load `schema.sql` and `seed.sql` into an in-memory SQLite database, and run:

```sh
python3 -m returnables.verify --schema schema.sql --seed output/starter/seed.sql
```

The verifier prints row counts for assets, shipments, custody events, RMAs, and ledger entries; runs `PRAGMA foreign_key_check`; counts charges with duplicate refunds; and checks snapshot balances and locations/ages against the SQL views. A duplicate-refund count of one is expected in the default starter dataset because that negative case is deliberately labelled in ground truth. The duplicate itself is expected; foreign-key and snapshot-to-ledger reconciliation failures are not.

The independent CSV validator checks primary keys, foreign keys, enums and ID formats, date domains/order, business joins, ledger arithmetic, state transitions, location-chain continuity, and semantic duplicates. It compares anomaly detections against `ground_truth` and reports precision/recall per injected anomaly type. Expected injected findings are reported, not silently repaired; the validation report passes only when findings are accounted for by labels and no labels are missed.

## Generated files

- One CSV for each operational table, including the shipment/assets bridge, derived snapshots, and lifecycle transition matrix.
- `data.json` with operational table records.
- `ground_truth.csv` and `.json`, separate from input features.
- `seed.sql` for SQLite seed records; apply `schema.sql` first.
- `metrics.json`, `validation_summary.json`, and `source_manifest.json`.
- `reproducibility.json` when the reproducibility option is used.

`output/` is ignored by Git except the starter evidence currently included in this repository. The generator never sends data elsewhere.

## Rules, assumptions, and trade-offs

- The synthetic as-of date is `2026-11-07`. All operational timestamps are UTC, all shipment/RMA/repair activity is in 2026, and no event is later than the as-of date.
- Asset types have 14/21/30-day return windows, 60/90/120-day loss thresholds, and per-type late-fee rates. An asset becomes overdue the day after its expected return date. Loss is declared only if no return has been received at exactly `due_date + loss_threshold_days`; a late but received return is not loss.
- Seeded inputs include trip counts, return delay/never-return, inspection findings, repair backlog, and custody-dispute occurrence/resolution lag. Business statuses, loss dates, settlement values, days outstanding, and snapshots are derived from those inputs and source records.
- Return settlement consists of signed ledger entries: deposit charge; late-fee deduction at the asset-type daily rate, calculated from the RMA's `processed_at` date; damage deduction equal to repair cost or replacement cost for quarantine; and refund of the remaining deposit. Each deduction is capped at the remaining deposit, zero rows are omitted, and every settlement row references the original charge. Loss forfeits the full deposit. An open repair or unresolved dispute blocks settlement; the full deposit remains held. Damage costs not covered by the deposit are reported as owner-absorbed cost, not charged in a separate owner transaction.
- A disputed handoff proposes a destination but does not move the asset. The last verified location remains current until `CUSTODY_DISPUTE_RESOLVED` confirms the destination. No further custody transitions or refunds occur during the dispute; a few disputes remain open at the as-of date. Checkpoint scans are non-moving observations.
- Current location is reconstructed by replaying custody events in `(occurred_at, event_id)` order. The event ID is the stable tie-break when timestamps coincide. Snapshots contain a derived operational status, never an anomaly sentinel or a null location.
- Master reference rows are static for this synthetic snapshot. The generator does not model real carrier systems, multiple currencies, tax, real payment processing, or asset-specific legal terms.
- All data is synthetic, fixed-seed, and created locally. No external dataset, personal information, or licensed content is used.

## Data model

See [DATA_DICTIONARY.md](./DATA_DICTIONARY.md) for per-field type/requiredness/validation, exact ID formats, cardinalities, lifecycle rules, and the Mermaid ER diagram. [schema.sql](./schema.sql) defines the database and location, days-outstanding, and deposit-balance views.

## Testing and validation approach

- Unit tests include focused negative cases for primary keys, foreign keys, enums, dates, ledger reconciliation, state transitions, and duplicates.
- A clean baseline is exported and checked before any anomalies are injected; the final exported CSVs are then independently validated without importing the generator's in-memory data structures.
- Starter output and checksums are generated by the commands above. Full-volume acceptance was run locally at the exact required row counts.
- SQLite schema/seed execution is checked in tests and by the 60-second verification command.
- A GitHub Actions workflow runs tests and starter reproducibility on Linux.

## Known limitations

- The sample uses a fixed as-of date and three synthetic asset types; longer histories, asset-specific effective-dated master changes, and real operational integrations are outside scope.
- Anomaly counts default to one per required negative case and can be overridden. Anomaly injection requires suitable valid baseline candidates, so very small custom scales are intentionally rejected.
- `ground_truth` is evaluation-only and should never be joined into a model or decision-engine feature set.

## AI-use disclosure

Copilot in VS Code was used to create and revise the generator, exported-data validator, SQLite schema/views, tests, and project documentation. The visible Copilot prompts are recorded in [PROMPT_HISTORY.md](./PROMPT_HISTORY.md). The candidate reports that Claude was also used, but Claude prompts, responses, and a transcript were not provided to this session; those details are not reconstructed here. Before submission, the candidate should add an accurate account of Claude's contribution and their own review and edits.

## Local verification and submission status

The local run passed all 14 unit tests, starter generation/reproducibility, SQLite seed/schema verification, and full-profile generation/reproducibility at the required volumes. The starter profile is included as checked-in evidence; the full profile is reproducible with the command above. The initial implementation and its revision are kept in separate Git commits. This workspace has no configured GitHub remote, so a public URL, public-visibility check, and private-window review cannot be reported here; connect and verify the intended public repository before submission.
