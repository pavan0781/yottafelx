# AI Prompt History

This file records the user prompts visible in the Copilot chat used for this project. The challenge message also contained a candidate-specific recruitment URL and local PDF paths; those account/local-path details are omitted here so they are not published in the repository. The task requirements themselves are transcribed below. The user reports using Claude as well, but Claude prompts and responses were not supplied to this Copilot session and cannot be recovered here. The candidate must append the actual Claude prompts before submission; this file does not claim that Copilot was the only AI tool used.

## Prompt 1 — Challenge brief sent to Copilot

> **72 Hours Code Challenge**
>
> Associate AI Forward Deployed Engineers · AI Forward Deployed Engineer
>
> ## Problem statement
> Read it carefully. Your build should cover every point below.
>
> Business Context / Problem
> Coordinate return merchandise, reusable packaging, pallets and equipment across locations and custody transitions.
>
> Objective and Scope
> Build a working solution for this end-to-end workflow: Coordinate return merchandise, reusable packaging, pallets and equipment across locations and custody transitions. The solution should expose key decisions and exceptions, preserve traceability, and produce measurable outputs. Medium complexity means multiple related entities, conditional rules, at least one exception path and meaningful evaluation metrics.
>
> Primary Actors / Systems
> Supplier, planner, carrier/warehouse operator, logistics analyst and operations manager
>
> Business Workflow and Rules
> 1. Generate/reference master data first: AssetType, ReusableAsset, Owner, Location, Shipment.
> 2. Generate dependent transaction/event records using valid parent IDs.
> 3. Apply business logic: Create reusable asset types and lifecycle rules; assign unique asset IDs; generate dispatch and custody transfers; create return authorizations; record inspections, repairs and deposit charges/refunds; calculate days outstanding and loss status.
> 4. Persist state changes as explicit records/events where relevant; do not overwrite history.
> 5. Generate outcomes and ground-truth labels from documented rules, not independent random assignment.
>
> Entities and Relationships
> Core entities: AssetType, ReusableAsset, Owner, Location, Shipment, CustodyEvent, ReturnAuthorization, Inspection, Repair, DepositLedger. Define a primary key for every entity and foreign keys for every relationship. Document cardinality (1:1, 1:N, N:M), optional relationships, effective dates and lifecycle states. Use bridge tables for many-to-many relationships; retain event history for state changes; validate all joins. For deliberate anomaly cases, keep an issue_type/ground_truth label rather than silently corrupting unrelated rows.
>
> ID / Nomenclature Conventions
> ASSET-0000001; RMA-2026-000001; SHP-2026-000001; CUST-<asset-id>-000001; REP-2026-000001.
>
> Synthetic Data Generation Instructions (Granular)
> Step 1 — Write a data dictionary for: AssetType, ReusableAsset, Owner, Location, Shipment, CustodyEvent, ReturnAuthorization, Inspection, Repair, DepositLedger. For each field specify type, required/optional, allowed values, units, uniqueness and validation rule.
> Step 2 — Create parent/master tables first, then child and event tables. Apply this domain-specific generation logic: Create reusable asset types and lifecycle rules; assign unique asset IDs; generate dispatch and custody transfers; create return authorizations; record inspections, repairs and deposit charges/refunds; calculate days outstanding and loss status.
> Step 3 — Apply deterministic IDs and naming: ASSET-0000001; RMA-2026-000001; SHP-2026-000001; CUST-<asset-id>-000001; REP-2026-000001. Keep counters zero-padded, IDs immutable, and referenced IDs exactly consistent across files.
> Step 4 — Generate the baseline volume: Generate 20,000 reusable assets, 10,000 shipments and 100,000 custody events. Include delayed returns, damaged assets, lost assets and disputed custody handoffs. Use a fixed seed (e.g. 20261022) and parameterize scale so starter and full datasets use the same generator.
> Step 5 — Generate valid baseline records first, then inject labelled edge cases: Asset has two simultaneous locations; return processed before dispatch; deposit refunded twice; repair closes without inspection; custody event lacks actor.
> Step 6 — Calculate derived values (totals, balances, scores, statuses and dates) from source records rather than generating them independently.
> Step 7 — Produce a separate ground_truth.csv/json for known anomalies, expected decisions or labels; never leak it into input features.
> Step 8 — Run validation: primary-key uniqueness, foreign-key integrity, allowed enums, date ordering, arithmetic reconciliation, lifecycle transitions and duplicate checks.
> Step 9 — Export CSV/JSON/SQL seed data plus README, schema/data dictionary, generator command, fixed seed, source manifest and validation summary.
> Step 10 — Regenerate twice with the same seed and compare row counts/checksums to demonstrate reproducibility.
>
> Suggested Volume and Coverage
> Generate 20,000 reusable assets, 10,000 shipments and 100,000 custody events. Include delayed returns, damaged assets, lost assets and disputed custody handoffs.
>
> Required Edge Cases / Negative Scenarios
> Asset has two simultaneous locations; return processed before dispatch; deposit refunded twice; repair closes without inspection; custody event lacks actor.
>
> Validation Rules / Acceptance Criteria
> Asset location can be reconstructed from events; outstanding age is correct; deposit ledger reconciles; damaged/lost assets follow lifecycle rules.
>
> Evaluation Focus for Generated Data
> Review: (a) realistic distributions and meaningful variation; (b) consistent IDs and valid joins; (c) dependency/lifecycle consistency across entities; (d) correct derived values and reconciliation; (e) normal, boundary, failure and exception coverage; (f) separate ground truth; (g) fixed-seed reproducibility, documentation and lawful provenance.
>
> Public Data Guidance / Lawful Use
> External data is optional. If used, record source URL, dataset/version, retrieval date, license/terms, attribution, transformations and fields discarded. Use only data you are legally permitted to access and redistribute; do not bypass access controls or collect confidential/personal information. If licensing or suitability is unclear, use synthetic data and cite public reference data only for schema inspiration.
>
> Before you submit, make sure you have
> - Pushed your code to a public GitHub repository
> - Added a README explaining how to run it
> - Included any tests the problem asks for

## Prompt 2 — Follow-up sent to Copilot

> i need to validate it

## Prompt 3 — Review checklist sent to Copilot

> This list combines my earlier reviews in priority order. Items 1-3 are compliance, 4-9 are what reviewers will score against the brief, and 10-14 are polish. I haven't seen `generator.py` or the tests, so check what the code does before fixing 4-9, since some may already be handled.
>
> **A. Compliance (roughly 2 hours; can sink the submission)**
>
> 1. **Verbatim prompt history.** Replace the agent's summary with your actual prompts: the challenge text you gave Copilot, follow-ups, and what you've sent me. The current file says no other AI tool was used, which is no longer true. If something can't be recovered, say so.
> 2. **Your own README sections (write these last).** The agent wrote the AI-disclosure and status text. After you've reviewed and run everything, rewrite them yourself: what Copilot generated, how you used Claude, and what you read, changed and tested. Add assumptions, trade-offs, testing approach and limitations.
> 3. **Honest Git history.** Run `git init`, commit the agent's output as it stands, then commit your changes separately. Include the starter output (`output/` is currently ignored) and check the public repo from a private window.
>
> **B. Correctness against the brief (roughly 10-14 hours)**
>
> 1. **Remove the answer key.** `EXCEPTION` in `asset_snapshots`, and nulled locations, reveal the injected defects. Derive status and location by replaying events with a deterministic tie-break, and report defects only through the validator.
> 2. **Write the missing rules.** You need:
>
> - a loss threshold, such as days outstanding against the return window;
> - a settlement formula (refund = deposit − late fee − damage deduction, forfeited on loss);
> - how repair and replacement costs feed the ledger;
> - what a disputed handoff changes.
>
> Only inputs like delays and inspection findings should be random. Outcomes should follow the rules.
>
> 3. **More than one lap per asset.** Let assets make 1-3 trips with varying event counts. Use `max_cycles` and `inspection_required`, or delete them. Add open repairs and an overdue state.
> 4. **State machine plus location chain.** Add a transition table (state, event, next state) enforced by the validator. Also check that each event's `from` equals the previous event's `to`. That makes "location reconstructable from events" testable.
> 5. **Two-pass anomaly injection.** Build a clean baseline that passes every check, then inject into a copy at seeded random positions, not predictable IDs. Make counts a parameter, with at least one of each type in the starter. Point each label at the exact offending row, such as the second refund's `ledger_id`.
> 6. **Independent validator with negative tests.** Write a module that reads only the exported tables and covers all seven Step 8 checks (keys, FKs, enums, date order, reconciliation, transitions, duplicates). For each check, add a tiny dataset that breaks one thing and expects exactly that finding. Report precision/recall per anomaly type against `ground_truth`.
>
> **C. Documentation, schema and reproducibility (roughly 5-6 hours)**
>
> 1. **Data dictionary to the Step 1 spec.** Generate a per-field table from the schema (type, required, allowed values, unit, unique, validation). Add ID regexes, switch timestamps to UTC, and state that master data is static or add effective dates. Add an `OPERATIONS_MANAGER` role, give the ground-truth columns allowed values, and add a Mermaid ER diagram.
> 2. **Tighter schema.** Add:
>
> - a sign/reference CHECK on the ledger;
> - `OPEN` repairs with a `closed_at` consistency check;
> - a shipment date-order check;
> - an index on `custody_events(asset_id, occurred_at)`;
> - SQL views for location, days outstanding and balance.
>
> 3. **Cross-platform reproducibility.** Force LF output and add `.gitattributes`. Use forward slashes and `python3` in README commands, and state your tested Python version. Add a small GitHub Actions workflow that runs the tests and `--verify-reproducibility` on Linux, and commit the checksum evidence.
> 4. **"Verify in 60 seconds" section.** Load `schema.sql` and `seed.sql` into SQLite, then run row counts, `PRAGMA foreign_key_check`, a double-refund query and a balance reconciliation.
> 5. **Pre-push hygiene.** Grep for absolute paths and your username, check that no file exceeds 100 MB, and do a clean-clone run.
>
> **If time runs short**, do 1-5, 8, 9 and 12, and list the rest as known limitations with next steps. Item 6 is the biggest code change, so descope it first. The FAQ allows partial solutions if you say what's incomplete. The deadline is Mon 9 Nov 14:55 UTC, so convert it to your local time and set an alarm a few hours earlier.
>
> Whatever you finish, prepare a short walkthrough covering how location is rebuilt from events, how loss and deposits are derived, how anomalies are injected, and what's weakest.
>
> Want me to draft the rulebook for 5 and 7 (loss threshold, settlement formula, transition table) next? Those two drive most of the code changes.

## Prompt 4 — Rulebook choices sent to Copilot

> Use the proposed defaults with these changes:
>
> 1. Overdue and lost are separate rules. Overdue = past the expected return date (dispatch + return_window_days). Lost = no return received and more than loss_threshold_days past that date (e.g. 60-120 days by asset type); LOSS_DECLARED is dated exactly at that threshold. Add loss_threshold_days and late_fee_cents_per_day to asset_types. Random draws are inputs only (return delay including "never returns", inspection finding, dispute occurrence); loss status must be derived from them by this rule.
> 2. Settlement posts explicit ledger rows: DEPOSIT_CHARGE (positive); LATE_FEE_DEDUCTION, DAMAGE_DEDUCTION, LOSS_FORFEIT and DEPOSIT_REFUND (negative, each referencing the charge). Late fees accrue from the due date at the type's daily rate; each deduction is capped at the remaining deposit. Never post a zero-amount row (the schema forbids it). Return settlement posts after inspection, or after repair closure when a repair is needed; LOSS_FORFEIT posts at LOSS_DECLARED. Rows for one settlement post together, so a settled asset nets to exactly 0 and an outstanding or unsettled asset holds the full deposit.
> 3. Damage deduction = repair cost for REPAIR, replacement_cost_cents for QUARANTINE. No separate owner charge: report any uncovered amount in metrics as owner-absorbed cost.
> 4. Add a CUSTODY_DISPUTE_RESOLVED event. A disputed handoff does not move the asset: its verified location stays the last verified one, and no further custody transitions or refunds post until the resolution event records the confirmed destination. Resolve most disputes after a seeded lag and leave a few open at the as-of date.
> 5. Constraints: no event dated after the as-of date; shipment, RMA and repair dates inside 2026 so the year in their IDs is true; the full profile still has exactly 20,000 assets, 10,000 shipments and 100,000 custody events, reached by adjusting only optional CUSTODY_CHECKPOINT scans, never by dropping rule-driven events. Update schema.sql and DATA_DICTIONARY.md, and document all of this as synthetic assumptions.

## Claude prompt history still required

The user stated that Claude was also used, but no Claude prompts, responses, or exported transcript were provided in this chat. They cannot be inferred. Add the actual Claude prompts and a truthful description of what Claude contributed before publishing this repository.
