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
5. The service loads cache data or calls a provider adapter.
6. The result or structured error is delivered back to the GUI thread through Qt signals.
7. The view renders the result and enables the relevant actions.

## Provider Contract

Provider adapters implement the protocols in `providers/data_provider.py` and `providers/pricing_provider.py`. This keeps services independent of a specific vendor and allows stub providers and fixture-backed tests.

Provider errors are normalized into domain error types. Failover is applied at the provider boundary, not in individual views.

Failover is enabled only when two live providers are configured. Demonstration providers are used only when no live provider is configured and never receive failed live requests.

## Persistence

SQLite is used for user-created state: watchlists, notes, tags, saved views, and snapshots. The JSON cache is separate from SQLite and is guarded by a lock for thread-safe service access.

Backup files include settings and SQLite state. Provider secrets are intentionally excluded.

## Extension Points

New chains should be added to the `Chain` model and implemented in the provider network mappings. New provider vendors should implement the provider protocols, add fixture tests, and be connected through the application bootstrap or failover layer. New reports should be implemented under `exports` without adding serialization logic to GUI views.

Architecture decisions that affect trust boundaries or distribution are recorded under `docs/adr`.
