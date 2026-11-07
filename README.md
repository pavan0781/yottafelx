# Reusable Asset Returns and Custody

A deterministic, standard-library-only synthetic data pipeline for coordinating reusable packaging, pallets, and equipment through dispatch, custody changes, return authorization, inspection, repair, and deposit settlement.

## Run it

Requires Python 3.10 or newer. No third-party packages, network access, credentials, or external data are required.

```powershell
python -m returnables.generator --profile starter --output output\starter
python -m returnables.generator --profile full --output output\full --verify-reproducibility
python -m unittest discover -s tests -v
```

The starter profile contains 200 assets, 100 shipments, and 1,000 custody events so it can be inspected quickly. The full profile contains exactly 20,000 assets, 10,000 shipments, and 100,000 custody events. Both profiles call the same seeded generator. The default seed is `20261022`; use `--seed` to select another seed. `--verify-reproducibility` generates the profile twice in temporary directories and compares SHA-256 checksums for every generated file.

The CLI validates primary and foreign keys, core lifecycle exceptions, deposit reconciliation, and then loads the rows into an in-memory SQLite database using [`schema.sql`](./schema.sql). It exits nonzero if validation fails. Generated output is written to the selected directory, which is ignored by Git.

## Generated files

- One CSV per operational table, including the many-to-many `shipment_assets` bridge and derived `asset_snapshots`.
- `data.json` with the operational tables and derived snapshots.
- `ground_truth.csv` and `ground_truth.json`, kept separate from operational input features.
- `seed.sql` containing operational-table inserts; apply `schema.sql` first.
- `metrics.json`, `validation_summary.json`, and `source_manifest.json` with row counts, validation results, provenance, seed, and hashes.

The full profile intentionally includes labelled examples for simultaneous locations, return-before-dispatch, duplicate deposit refund, repair closure without inspection, and a custody event missing its actor. These cases are isolated to known rows and reported as validation warnings, not silently hidden. Baseline labels also cover delayed returns, damaged/lost assets, and disputed custody handoffs. A downstream model or rules engine should not use the separate ground-truth files as input features.

## Rules and assumptions

- A shipment may contain many assets and an asset may be associated with multiple shipments over its lifetime; `shipment_assets` is the explicit bridge. The generated sample has one shipment per asset.
- Every shipment dispatch posts a deposit charge. Successful return posts a refund; a lost or still-outstanding asset retains its charged deposit. Ledger amounts are signed integer cents.
- Return due dates are based on the asset type's return window. `days_outstanding` is elapsed whole days from dispatch to return, loss declaration, or the fixed as-of date (`2026-11-07`) for an asset still outstanding.
- Current asset state is a derived snapshot; custody and financial source events are retained. For a conflicting-location exception, the snapshot reports no current location rather than selecting one arbitrarily.
- Asset IDs, shipment IDs, RMA IDs, custody-event IDs, and repair IDs follow the requested stable, zero-padded conventions. Other entities have their own deterministic keys.
- Operators are the event actors and carrier references. An actor is optional only for the deliberately labelled missing-actor case.
- Inputs are entirely synthetic. No external datasets, personal data, or third-party source material are used.

## Data dictionary and relationships

See [`DATA_DICTIONARY.md`](./DATA_DICTIONARY.md) for field types, requiredness, allowed values, validation, keys, relationship cardinalities, and lifecycle definitions.

## AI-use disclosure

This implementation was developed with Copilot assistance. The code, deterministic rules, test cases, and documentation were reviewed and validated in this workspace. A record of the user-provided task prompt and the scope of Copilot assistance is in [`PROMPT_HISTORY.md`](./PROMPT_HISTORY.md). Keep/export the original chat transcript if a verbatim prompt history is required; AI responses are not required by the supplied submission FAQ.

## Submission status and limitations

This workspace did not contain a Git repository or a configured GitHub remote when implementation began. No repository was created or pushed. To meet the public-repository requirement, create a dedicated public GitHub repository and push this project from your account. Review and understand the assumptions and generator logic before submission. The sample is synthetic and intended to demonstrate data/workflow validation; it does not integrate a live carrier, warehouse, or payment system.
