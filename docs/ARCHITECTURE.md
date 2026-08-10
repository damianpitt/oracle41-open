# Architecture

## Layering

The application is organized into five boundaries:

- `gui`: PySide6 windows and views. GUI code owns widgets, user interaction, and presentation.
- `core`: domain models and services. Core code validates input, coordinates providers, applies filters, and produces domain results.
- `providers`: Alchemy, Ankr, failover, retry, HTTP, JSON-RPC, pricing, and stub implementations.
- `storage`: settings, system-keyring access, SQLite repositories, cache persistence, backup/restore, and cache telemetry.
- `exports`: CSV and JSON serialization for user-facing reports.

The GUI depends on services. Core and provider modules must not import PySide6. The architecture boundary is enforced by tests.

## Runtime Flow

1. `app.bootstrap.build_container()` constructs settings, secrets, storage, providers, and services.
2. `MainWindow` creates views with the application container.
3. A view validates user input on the GUI thread.
4. The view submits network-backed work to `BackgroundTaskRunner`.
5. The service loads cache/ledger data or calls a provider adapter.
6. Provider results and their cursor are committed atomically to the canonical event ledger.
7. The result or structured error is delivered back to the GUI thread through Qt signals.
8. The view renders the result and enables the relevant actions.

## Provider Contract

Provider adapters implement the protocols in `providers/data_provider.py`, `providers/pricing_provider.py`, and `providers/transaction_provider.py`. This keeps services independent of a specific vendor and allows stub providers and fixture-backed tests.

Provider errors are normalized into domain error types. Failover is applied at the provider boundary, not in individual views.

Failover is enabled only when two live providers are configured. Demonstration providers are used only when no live provider is configured and never receive failed live requests.

## Persistence

SQLite schema v3 stores normalized transactions, events, assets, movements, approvals, fees, query scopes, synchronization checkpoints, ingestion runs, transaction metadata, receipts, and ordered raw logs. Activity and Token Detail read the same canonical ledger. Event upserts, query-scope links, and checkpoints share one transaction so an interrupted write leaves the previous checkpoint valid.

`TransactionInspectionService` loads immutable transaction and receipt data through a standard JSON-RPC provider and persists it through `TransactionRepository`. Transaction metadata, receipt, raw logs, and the derived native fee share one transaction. The GUI receives domain models rather than provider dictionaries.

SQLite also stores watchlists, notes, tags, saved views, and snapshots. The JSON cache is separate, disposable, and guarded by a lock for thread-safe service access. Completed ledger results older than the freshness threshold are reported as stale; partial results retain their partial status.

Activity JSON exports use `oracle41-activity` format version 2. GUI-created CSV exports carry the same format/version, completeness, provider, fetch time, queried block range, and persistence fields.

Backup files include settings and the complete SQLite state, including event-ledger checkpoints. Provider secrets are intentionally excluded. A schema-v1 backup is accepted and migrated forward after restore.

## Extension Points

New chains should be added to the `Chain` model and implemented in the provider network mappings. New provider vendors should implement the provider protocols, add fixture tests, and be connected through the application bootstrap or failover layer. New reports should be implemented under `exports` without adding serialization logic to GUI views.

Architecture decisions that affect trust boundaries or distribution are recorded under `docs/adr`.
