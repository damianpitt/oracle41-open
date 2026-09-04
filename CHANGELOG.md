# Changelog

All notable changes to Oracle41 Open will be documented here.

## [0.4.0a12] - Unreleased

### Added

- Protocol risk reports with health factor, loan-to-value, liquidation threshold, raw collateral, raw debt, available borrow, warnings, and source provenance.
- Fresh, stale, and future observation states with a configurable stale threshold that defaults to one hour.
- Protocol Risk CSV and JSON templates with adapter identity, source reference, provider, block, timestamps, freshness, and raw account-health evidence.
- Portfolio risk and health details with clear raw-unit labels and snapshot age.
- Deterministic clock tests for freshness boundaries, no-debt health factors, missing risk evidence, and stale portfolio behavior.

### Changed

- Stale protocol snapshots and future observation timestamps now prevent a complete combined portfolio total while preserving known values.
- Protocol-position exports now include observation age and freshness.
- The `oracle41-portfolio` export format is now version 2.

### Known Limitations

- Freshness measures provider observation age. It does not compare a stored block with the latest chain block.
- Aave collateral, debt, available borrow, and base-currency values remain raw protocol units and are not presented as USD.
- Health data reports protocol evidence and does not provide financial advice.
- Aave V3 remains the only production protocol adapter.

## [0.4.0a11] - 2026-09-02

### Added

- Format-v1 protocol-position CSV and JSON templates with raw amounts, normalized amounts, prices, signed values, asset roles, completeness, block numbers, and provider provenance.
- Portfolio controls for newest snapshots or one exact stored block.
- Local discovery of stored protocol block history for selected wallets and one chain.
- An explicit protocol refresh action that recollects Aave V3 state at the requested block.

### Changed

- Forced protocol refresh bypasses a finished snapshot, makes new exact-block provider calls, and safely replaces the stored result.
- Missing snapshots at a requested block are reported separately from storage and provider failures.
- Exact protocol-block mode withholds the complete total because wallet overview balances remain current.
- Export filenames now include the selected portfolio template.

### Known Limitations

- Manual protocol refresh currently collects Aave V3 because it is the only production protocol adapter.
- One refresh operation uses one block number. Cross-chain block heights must be refreshed separately.
- Exact-block protocol values are shown beside current wallet balances as a known estimate, not a complete historical portfolio.
- Current prices are still applied to stored block amounts; historical price-at-block valuation is not included.

## [0.4.0a10] - 2026-09-01

### Added

- Current USD valuation for stored protocol assets through the existing cached pricing service.
- Explicit protocol asset, liability, and signed net totals in portfolio results.
- Newest-snapshot selection for every stored protocol on each wallet and chain.
- Protocol block, provider, observation time, unpriced count, and partial-state details in the Portfolio view and summary/wallet exports.
- Aave receipt-token and debt-token exclusion derived from positive snapshot balances.

### Changed

- Debt positions reduce net portfolio value instead of appearing as positive holdings.
- Wallet token aggregates exclude receipt-token balances when the matching economic position is included.
- A complete portfolio total is withheld when protocol storage fails, a snapshot is partial, or a position lacks a safe price.
- Protocol prices are loaded once per chain and underlying contract instead of once per wallet position.

### Known Limitations

- Current prices are applied to amounts recorded at the stored snapshot block. Historical price-at-block valuation is not included.
- Dedicated protocol-position CSV and JSON templates are not included in this version; summary and wallet exports contain protocol accounting metadata.
- Aave V3 is still the only production protocol adapter.

## [0.4.0a9] - 2026-08-31

### Added

- SQLite schema v10 with durable protocol snapshots and in-progress collection checkpoints.
- Exact round-trip storage for protocol positions, nested assets, risk metrics, raw evidence, warnings, completeness, and provider provenance.
- Per-reserve Aave V3 checkpoints that continue from the next unfinished reserve after an interruption.
- Snapshot history queries by wallet, chain, protocol, and block.

### Changed

- Finished snapshots are returned from SQLite without repeating provider calls for the same block.
- Final snapshot replacement and checkpoint deletion now share one atomic transaction.
- Application startup now gives `ProtocolPositionService` the shared protocol-position repository.
- Local backups include finished and in-progress protocol state through the existing SQLite snapshot.

### Known Limitations

- Stored protocol positions are not yet priced, included in portfolio totals, exported, or shown in the desktop interface.
- Resume checkpoints require the same source provider to preserve one coherent provenance chain.
- Account values remain in Aave's raw base-currency units and should not be presented as USD without verified oracle metadata.

## [0.4.0a8] - 2026-08-29

### Added

- Block-specific contract reads through the shared transaction-provider interface.
- Automatic Aave V3 reserve discovery and user-position collection on Ethereum, Optimism, Polygon, Base, and Arbitrum.
- Automatic Aave account-health collection from the Pool, Addresses Provider, and price oracle at one explicit block.
- Source-provider and observation-time provenance for every collected snapshot.

### Changed

- Optional Aave read failures now produce partial results with structured collection warnings instead of discarding all valid evidence.
- A snapshot rejects successful responses from different providers so one result cannot silently mix blockchain states.
- Zero-balance reserves skip unnecessary reserve-token address reads.

### Known Limitations

- Protocol positions and risk snapshots are not yet persisted, priced, aggregated, exported, or shown in the desktop interface.
- Historical reads depend on the selected transaction provider, chain, account plan, and archive-state availability.
- Account values remain in Aave's raw base-currency units and should not be presented as USD without verified oracle metadata.

## [0.4.0a7] - 2026-08-29

### Added

- A production Aave V3 adapter for supplied assets, collateral, and debt on Ethereum, Optimism, Polygon, Base, and Arbitrum.
- Raw Aave account-health snapshots with collateral, debt, available borrow, loan-to-value, liquidation threshold, health factor, and base-currency unit.
- An explicit liquidation-threshold state that distinguishes no debt, health factor below `1.0`, and health factor at or above `1.0` without adding financial advice.
- A production protocol registry and a recorded Aave V3 conformance fixture.

### Changed

- Collateral-enabled Aave supply is emitted once as collateral instead of being duplicated as supplied.
- Stable and variable Aave debt are combined into one underlying-asset debt position while raw evidence remains available.
- Protocol adapter validation now checks that risk snapshots match the wallet, chain, block, protocol, and adapter context.

### Known Limitations

- The application does not collect Aave contract snapshots automatically in this version.
- Protocol positions and risk snapshots are not persisted, priced, aggregated, exported, or shown in the desktop interface.
- Account values remain in Aave's raw base-currency units and should not be presented as USD without verified oracle metadata.

## [0.4.0a6] - 2026-08-28

### Added

- Non-secret provider credential diagnostics with keyring/environment source, validation state, and UTC time of the last successful check.
- Credential readiness details for Alchemy, Ankr, Moralis, and GoldRush in Settings.
- An explicitly gated local `--validate-providers-live` command that exercises one bounded page of every wallet-data operation for all four providers.

### Changed

- Saving ordinary settings and restoring backups preserve safe credential diagnostic metadata.
- Live validation output reports only provider IDs, operation counts, and redacted failure categories.

### Security

- Credential diagnostics never store keys, key fingerprints, request URLs, wallet results, or raw provider errors.
- Live validation remains disabled unless `ORACLE41_RUN_LIVE_PROVIDER_VALIDATION=1` is set and every required input is present.
- Provider credentials remain off hosted CI runners; live validation runs only in an explicitly configured local environment.

### Known Limitations

- Public CI remains fixture based and does not receive provider credentials.
- A successful credential check confirms access at that time; provider plan, chain, trace, and historical-state capabilities can still vary.

## [0.4.0a5] - 2026-08-26

### Added

- A GoldRush wallet-data adapter for native balances, token holdings, decoded wallet activity, token-specific history, NFT transfers, and complete decoded approval history.
- GoldRush API key validation, keyring storage, environment-variable loading, Settings controls, and ordered-pool startup.
- Recorded GoldRush Foundational API fixtures in the shared wallet-data provider conformance suite.
- Structured GoldRush authentication, credit-exhaustion, rate-limit, timeout, network, malformed-response, and HTTP errors with bounded retry.
- ERC-1155 batch expansion and stable per-token action IDs for decoded GoldRush logs.

### Changed

- Four wallet-data providers can now be enabled and ordered in Settings.
- The shared provider fixture contract records whether token-balance pagination is available.
- Provider documentation now separates indexed wallet data, transaction inspection, and market pricing, with practical setup combinations and provider-specific limits.

### Known Limitations

- GoldRush transaction pages do not accept the application's block floor directly. Oracle41 filters each returned page locally, which can use more provider credits for older wallets.
- GoldRush is an indexed wallet-data source only. Its API key is not added as a transaction JSON-RPC or pricing provider.
- Live four-provider validation with private credentials remains opt-in and is not performed in public CI.

## [0.4.0a4] - 2026-08-25

### Added

- A Moralis wallet-data adapter for native balances, paginated token balances, decoded wallet activity, ERC-20 transfers, and ERC-721 / ERC-1155 transfers.
- Active ERC-20 approval loading from the Moralis Wallet API, reported separately from complete approval history.
- Moralis API key validation, keyring storage, environment-variable loading, Settings controls, and ordered-pool startup.
- Recorded Moralis REST fixtures in the shared wallet-data provider conformance suite.
- Structured Moralis authentication, rate-limit, timeout, network, malformed-response, and HTTP errors with bounded retry.

### Changed

- Three wallet-data providers can now be enabled and ordered in Settings.
- The capability catalog distinguishes active approvals from complete approval history.

### Known Limitations

- Moralis returns current active ERC-20 approvals. It does not provide a complete archive of approvals that were later revoked through this adapter.
- Moralis is an indexed wallet-data source only. Its Data API key is not added as a transaction JSON-RPC or pricing provider.

## [0.4.0a3] - 2026-08-23

### Added

- One public catalog for wallet-data provider availability, supported chains, features, and credential-validation destinations.
- Plain capability summaries for Alchemy and Ankr in Settings.
- Format-v1 recorded conformance fixtures for Alchemy and Ankr.
- A shared conformance suite covering native balances, token balances, wallet activity, token history, pagination markers, provenance, and NFT categories.
- A public JSON schema for future provider conformance fixtures.

### Changed

- Alchemy, Ankr, and demonstration token-balance pages now identify their source provider directly.
- Planned providers claim no runtime capabilities until their adapters pass the shared suite.

### Known Limitations

- Moralis and GoldRush remain planned and unavailable.
- The conformance suite uses recorded responses and does not replace opt-in live-provider testing.
- Runtime capability reporting does not yet include credential status or the last validation time.

## [0.4.0a2] - 2026-08-22

### Added

- Persisted enabled state and priority for Alchemy and Ankr wallet-data providers.
- An ordered provider pool that supports one or more configured wallet-data adapters.
- Provider-owned pagination cursors for balances, activity, and token history.
- Settings controls for enabling Alchemy or Ankr and choosing which provider is tried first.

### Changed

- Fresh wallet-data requests fail over in the saved provider order.
- A continuation page always returns to the provider that created its cursor.
- Disabled providers are excluded from wallet-data, pricing, and transaction-inspection setup.

### Known Limitations

- Moralis and GoldRush remain planned. Their adapters and Settings rows are not included yet.
- Provider preference changes require an application restart.
- Cursors saved before 0.4.0a2 cannot be safely assigned to a provider. Start a new load when the application reports a legacy cursor.

## [0.4.0a1] - 2026-08-21

### Added

- Immutable protocol models for supplied assets, debt, collateral, liquidity, staking, vesting, and rewards.
- A versioned protocol-adapter contract with explicit chain, contract, and position capabilities.
- Deterministic registry selection with duplicate-ID and overlapping-contract protection.
- An evidence-preserving unknown-protocol fallback that keeps actions, balances, decoded events, and raw records visible.
- A reference lending adapter that demonstrates supplied, debt, and reward output without claiming a production deployment.
- Format-v1 recorded protocol fixtures, a public JSON schema, a shared conformance suite, and an adapter authoring guide.

### Changed

- The roadmap advances to M6 DeFi, NFT, and position intelligence work.
- Core architecture documentation now defines protocol adapters as a GUI-free and storage-free extension boundary.

### Known Limitations

- No production DeFi protocol adapter is included yet.
- Protocol positions are not persisted, exported, priced, aggregated, or displayed in the GUI yet.
- The reference lending contract and snapshot are illustrative test data only.

## [0.3.0a9] - 2026-08-20

### Added

- Paired Alchemy and Ankr transaction fixtures that prove identical receipt mapping, ABI decoding, and wallet actions for the same raw evidence.
- Learned per-chain historical-state capability reporting from successful block-specific reads and explicit node-pruning errors.
- Provider capability details in Transaction Inspector for receipts, traces, historical state, proxy resolution, and revert replay.
- A reproducible M5 exit-gate matrix covering unknown, malformed, incomplete, and provider-failure paths.
- An architecture decision that keeps TrueBlocks behind an optional future adapter with documented fallbacks.

### Changed

- Temporary network, authentication, rate-limit, and timeout failures do not change historical-state capability.
- M5 documentation now separates deterministic low-level actions from protocol-specific positions planned for M6.

### Known Limitations

- Historical-state support is learned during proxy resolution or revert replay; it remains not checked until one of those block-specific queries runs.
- TrueBlocks is not integrated or bundled. Alchemy, Ankr, and configured JSON-RPC endpoints remain the available history sources.

## [0.3.0a8] - 2026-08-18

### Added

- Action-set evidence completeness derived from complete, partial, unsupported, unavailable, or missing transaction traces.
- Plain missing-evidence reasons in Transaction Inspector and `oracle41-wallet-actions` CSV/JSON format version 2.
- Historical EIP-1967 beacon resolution through the standard beacon `implementation()` call.
- Schema-v9 persistence for the beacon address alongside the resolved implementation and block-specific proxy record.

### Changed

- Normalized action records remain unchanged; completeness is a separate derived envelope over the complete action list.
- Beacon slot reads and implementation calls use the transaction block, preserving historical implementation context.

### Known Limitations

- Complete evidence means the configured endpoint returned a complete supported trace. It does not mean every unknown contract call has a known semantic label.
- Diamond and non-standard proxies remain explicit unresolved or raw contract context.

## [0.3.0a7] - 2026-08-17

### Added

- Optional Blockscout API v2 transaction context for readable methods, transaction types, decoded parameters, and contract labels.
- Contract verification, implementation name, creator, and creation-transaction details with direct source links.
- Schema-v8 storage for explorer enrichment with explicit available, not-found, unsupported, and unavailable states.
- Transaction Inspector output that clearly separates optional explorer fields from local decoding and raw JSON-RPC evidence.

### Changed

- Successful, missing, and unsupported explorer results are cached; temporary failures are retried on the next inspection.
- Blockscout enrichment runs after action normalization and cannot change deterministic wallet actions.

### Known Limitations

- Public Blockscout instances may differ in coverage, indexing delay, and API field availability.
- Explorer-decoded values are supporting context. Raw receipts and local ABI decoding remain authoritative inside the application.

## [0.3.0a6] - 2026-08-15

### Added

- Versioned wallet-action models for transfers, approvals, simple swaps, deployments, contract calls, and unknown activity.
- Action participants, raw asset amounts, transaction-initiator directions, confidence, protocol hints, and source-evidence references.
- Schema-v7 storage for ordered normalized actions and their normalizer version.
- Normalized Action summaries in Activity and Transaction Inspector.
- Versioned `oracle41-wallet-actions` CSV and JSON exports with complete nested evidence.

### Changed

- Transaction Inspector re-normalizes saved raw evidence locally and updates stored actions only when deterministic output changes.
- Backup and restore preserve normalized actions as part of SQLite state.

### Known Limitations

- Swap recognition is intentionally limited to one wallet outflow and one wallet inflow involving different token contracts.
- Token action amounts are raw on-chain values; decimal and price enrichment remains in the existing activity and portfolio layers.
- Protocol hints use decoded method names where available and do not yet provide a full protocol-label registry.

## [0.3.0a5] - 2026-08-13

### Added

- Normalized internal-call models for calls, contract creation, native value movement, nested errors, and revert locations.
- Automatic per-chain discovery of Geth `debug_traceTransaction` and Parity `trace_transaction` support.
- Schema-v6 storage for trace summaries, ordered internal calls, provider dialects, errors, and full raw trace payloads.
- Transaction Inspector output and an expandable call tree with visible complete, partial, unsupported, and unavailable states.

### Changed

- Confirmed trace capability results are reused, while temporary provider failures are retried on the next inspection.
- Backup and restore preserve transaction traces as part of SQLite state.

### Known Limitations

- Trace support depends on the configured JSON-RPC endpoint and may require a paid plan or node setting.
- Geth call-tracer and Parity trace formats are supported; provider-specific trace formats remain visible only as raw data.
- Internal calls are not yet converted into higher-level actions such as swaps, bridges, or staking operations.

## [0.3.0a4] - 2026-08-12

### Added

- Schema-v5 storage for contract ABIs, source provenance, block-specific proxy resolutions, implementation context, and decoded revert data.
- User ABI import, listing, and removal from Settings with local unverified-source labeling.
- Optional verified ABI retrieval from Blockscout API v2 for every supported chain.
- Historical EIP-1967 and EIP-1167 proxy resolution through standard JSON-RPC endpoints.
- Deterministic custom-error decoding plus Solidity `Error(string)` and `Panic(uint256)` decoding.
- Transaction Inspector sections for proxy context, implementation address, revert arguments, provenance, and raw revert bytes.

### Changed

- Decoder cache versions include the exact address-to-ABI registry fingerprint, so replacing an ABI re-decodes persisted transactions.
- Reverted transactions use best-effort historical `eth_call` replay while preserving the raw provider response.
- Backup and restore preserve contract ABIs, proxy resolutions, and revert decoding as part of SQLite state.

### Known Limitations

- Historical `eth_call` replay uses end-of-block state and may not reproduce every transaction-index-specific revert.
- Proxy discovery currently covers EIP-1967 implementation slots and standard EIP-1167 minimal proxies; beacon, diamond, and non-standard proxies remain raw.
- Verified ABI availability depends on the selected chain's public Blockscout instance and its verification coverage.

## [0.3.0a3] - 2026-08-11

### Added

- Deterministic calldata and event decoding for common ERC-20, ERC-721, and ERC-1155 operations.
- Versioned local signature registry with verified source provenance for every recognized signature.
- Schema-v4 persistence for decoded calls, decoded events, unknown payloads, malformed payloads, and signature sources.
- Human-readable decoded arguments and signature trust details in Transaction Inspector while retaining all raw input and log data.
- Golden-vector tests for overloaded token event signatures, dynamic arrays, unknown signatures, and malformed payloads.

### Changed

- Cached transaction inspections are re-decoded when the local decoder version changes.
- Backup and restore now preserve schema-v4 decoding state as part of the SQLite snapshot.

### Known Limitations

- The local registry covers common token-standard calls and events; contract-specific ABIs and proxy resolution follow in M5.3.
- Indexed dynamic event values remain topic hashes because the original value is not recoverable from an EVM log.

## [0.3.0a2] - 2026-08-10

### Fixed

- Qt background tasks no longer pass auto-deleted `QRunnable` wrappers through queued cross-thread signals, preventing the Python 3.11 Linux CI segmentation fault seen in `0.3.0a1`.

## [0.3.0a1] - 2026-08-10

### Added

- Configurable standard EVM JSON-RPC endpoints stored in the OS keyring and selectable per chain.
- Schema-v3 transaction details, receipts, ordered raw logs, and receipt-derived network fees.
- Activity transaction inspector for status, calldata, method selector, gas, fees, contract creation, raw logs, and provider provenance.
- Chain-specific transaction-provider capability reporting with custom RPC, Alchemy, and Ankr failover.

### Changed

- Receipt loading uses shared retry and structured provider errors without exposing endpoint URLs.
- Transaction details, receipts, logs, and fees are committed atomically and reused from SQLite.

### Known Limitations

- Method selectors and log topics remain raw.
- Internal traces are not requested yet.
- Only transactions already present in the canonical Activity ledger can be enriched.

## [0.2.0a1] - Unreleased

### Added

- Canonical SQLite event ledger for transactions, assets, movements, approvals, optional fees, query scopes, and ingestion runs.
- Forward-only schema-v1 to schema-v2 migration with atomic version updates.
- Durable sync checkpoints with provider, cursor, block range, fetch time, and completeness metadata.
- Restart-safe Activity and Token Detail synchronization with deduplicated canonical events.
- Paginated Alchemy and Ankr approval scans that can continue back to genesis in bounded block windows.
- Stale and partial ledger states in the GUI and versioned activity CSV/JSON provenance fields.
- ENS wallet input in Overview, Activity, and Token Detail with preserved display context.
- GUI cancellation and integration coverage for loading, filtering, pagination, approvals, and late-result suppression.

### Changed

- Activity and Token Detail now use SQLite as durable history; the JSON cache remains an optional fast path.
- Completed transfer pagination no longer restarts while older approval windows continue.
- Approval allowance values are no longer treated as transferred USD value.
- Backups now preserve canonical events and resumable checkpoints.

### Known Limitations

- Fee storage is available, but fee population waits for receipt ingestion in M5.
- ENS input is limited to the primary analysis views; local metadata editors use resolved addresses.
- Provider scan throughput remains subject to API limits and chain-specific log-range policies.

## [0.1.0] - 2026-08-04

### Added

- Linux-first PySide6 desktop application for read-only EVM wallet analytics.
- Alchemy and Ankr provider integrations with failover, retry, and structured errors.
- Wallet overview, activity feed, token detail, portfolio, watchlist, notes, tags, saved views, and snapshots.
- ENS/label resolution, token filtering, cache telemetry, SQLite persistence, and backup/restore.
- CSV and JSON exports for activity, portfolios, watchlists, and snapshots.
- Native AMD64 and ARM64 Debian packaging and GitHub Actions quality/release workflows.
- Self-contained Debian runtime with source, frozen-binary, and installed-package smoke checks.
- Architecture-specific release artifacts with portable SHA-256 checksum files.
- Linux desktop icon, launcher metadata, and AppStream package metadata.
- Environment-variable provider key fallback for headless and development environments.

### Changed

- Live-provider failures remain explicit instead of falling back to demonstration data.
- Approval history uses a bounded recent-block scan and reports incomplete provider responses.
- Backup and restore operations run outside the Qt event loop.

### Fixed

- Provider errors no longer expose request URLs that may contain API credentials.
- Invalid provider timestamps are rejected instead of being replaced with the current time.
- Provider failover no longer hides unexpected programming errors.

### Known Limitations

- This is an alpha release; Debian compatibility targets and derivative distributions require clean-system validation beyond the Ubuntu CI runners.
- Live provider behavior depends on third-party API limits and metadata quality.
- The application does not sign or broadcast transactions.
