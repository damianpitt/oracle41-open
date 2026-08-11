# Changelog

All notable changes to Oracle41 Open will be documented here.

## [0.3.0a3] - Unreleased

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

- The local registry currently covers common token-standard calls and events; verified contract ABIs, proxies, and custom errors follow in later M5 slices.
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
