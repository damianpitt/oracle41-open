# Architecture

## Layering

The application is organized into six boundaries:

- `gui`: PySide6 windows and views. GUI code owns widgets, user interaction, and presentation.
- `core`: domain models and services. Core code validates input, coordinates providers, applies filters, and produces domain results.
- `core/protocols`: versioned protocol adapters, capability-based selection, and unknown-protocol fallback behavior.
- `providers`: Alchemy, Ankr, Moralis, GoldRush, ordered routing, retry, HTTP, JSON-RPC, pricing, and stub implementations.
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

Wallet-data providers are placed in a pool using the enabled state and priority saved in Settings. A fresh request tries that order after structured provider errors. Every pagination cursor records its provider owner and operation. A continuation request returns only to that provider, even when another provider is available.

The provider capability catalog is separate from network clients. It records stable IDs, availability, supported chains, wallet features, and the public host used for credential checks. Settings reads this catalog without creating a provider or making a request. New providers have no runtime capabilities until their adapters pass the shared conformance suite.

The format-v1 provider conformance suite gives each adapter its own recorded response shapes and applies the same assertions to the normalized results. The suite covers all four `DataProvider` methods, pagination markers, source provenance, chain identity, and ERC-721/ERC-1155 history.

Transaction providers report receipt, trace, historical-state, proxy, and revert capabilities separately. Trace support is learned from supported RPC methods. Historical-state support is learned only from successful block-specific reads or explicit node-pruning errors, so a timeout is not mistaken for a missing capability.

The same pool works with one or more configured providers. Disabled providers are not added to the runtime. Demonstration providers are used only when no live wallet-data provider is configured and never receive failed live requests.

## Persistence

SQLite schema v9 stores normalized transactions, events, assets, movements, approvals, fees, query scopes, synchronization checkpoints, ingestion runs, transaction metadata, receipts, ordered raw logs, internal calls, decoded calls/events/reverts, wallet actions, contract ABIs, block-specific proxy and beacon resolutions, optional explorer context, and source provenance. Activity and Token Detail read the same canonical ledger. Event upserts, query-scope links, and checkpoints share one transaction so an interrupted write leaves the previous checkpoint valid.

`TransactionInspectionService` loads immutable transaction and receipt data through a standard JSON-RPC provider and persists it through `TransactionRepository`. Transaction metadata, receipt, raw logs, and the derived native fee share one transaction. The provider checks for Geth-compatible or Parity-compatible trace methods and converts either format into the same internal-call model. Trace status is stored separately from its calls, so partial data and unsupported endpoints cannot look complete. Full raw trace payloads remain available for future decoders.

A deterministic decoder combines the bundled token-standard registry with address-specific user or Blockscout-verified ABIs after the raw inspection is durable. EIP-1967 implementation slots, EIP-1967 beacons, and EIP-1167 resolutions are keyed by chain, proxy, and block so historical implementation context remains stable. Beacon records keep both the beacon and implementation addresses. Decoded, unknown, and malformed outcomes are persisted separately, so decoding cannot replace or discard source data. The GUI receives domain models rather than provider dictionaries.

`WalletActionNormalizer` reads the saved receipt, decoded call and events, and internal trace. It produces ordered actions with participants, assets, confidence, a rule version, and references such as `call`, `log:3`, or `trace:0.1`. Unknown evidence remains an unknown action. The action layer can be rebuilt when rules improve because it never edits its source records.

Each normalized action list has a separate evidence-completeness result. A complete trace produces a complete action set. Partial, unsupported, unavailable, or missing traces produce a partial set with a plain explanation of which internal calls or native transfers may be absent. Action CSV and JSON format version 2 includes this context without changing individual action records.

Blockscout enrichment is optional. It can add readable contract names, creation details, verification state, and explorer-decoded method context after local actions are complete. These fields use a separate schema-v8 table with source links and clear availability states. They do not change receipts, local ABI decoding, traces, or normalized actions.

Protocol adapters receive immutable actions, balances, decoded events, and raw evidence at one chain and block. They can add supplied, debt, collateral, liquidity, staking, vesting, or reward positions, but every result returns its original evidence unchanged. The registry rejects ambiguous chain/contract claims and uses an unknown-protocol fallback when nothing matches.

The Aave V3 adapter recognizes official Pool deployments on Ethereum, Optimism, Polygon, Base, and Arbitrum. It converts recorded reserve calls into supplied, collateral, and debt positions. A collateral position is still one supplied balance, so it is not also emitted as a second supplied position. Stable and variable debt use the same underlying token and are combined into one debt amount. Account totals, loan-to-value, liquidation threshold, and health factor stay in Aave's original integer units. The adapter does not fetch calls, calculate prices, or give financial advice. Protocol positions are not yet stored or connected to the desktop interface.

SQLite also stores watchlists, notes, tags, saved views, and snapshots. The JSON cache is separate, disposable, and guarded by a lock for thread-safe service access. Completed ledger results older than the freshness threshold are reported as stale; partial results retain their partial status.

Activity JSON exports use `oracle41-activity` format version 2. GUI-created CSV exports carry the same format/version, completeness, provider, fetch time, queried block range, and persistence fields.

Backup files include settings and the complete SQLite state, including event-ledger checkpoints, transaction traces, and normalized actions. Provider secrets are intentionally excluded. A schema-v1 backup is accepted and migrated forward after restore.

## Extension Points

New chains should be added to the `Chain` model and implemented in the provider network mappings. New provider vendors should implement the provider protocols, add fixture tests, and be connected through the application bootstrap or failover layer. New reports should be implemented under `exports` without adding serialization logic to GUI views.

Architecture decisions that affect trust boundaries or distribution are recorded under `docs/adr`.
