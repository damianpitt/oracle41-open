# Product Roadmap

## North Star

Oracle41 Open aims to become the most trustworthy and extensible open-source desktop analytics client for EVM wallets. It should combine deep wallet analysis, transparent data provenance, local ownership of data, and a contributor-friendly adapter model without becoming a wallet, exchange, or hosted surveillance service.

The product is successful when a user can answer four questions from one local application:

1. What does this wallet own and owe across supported chains and protocols?
2. What happened, including decoded contract interactions and internal value flows?
3. How did value, exposure, performance, and risk change over time?
4. How complete and current is every result, and where did the data come from?

## Product Boundaries

Oracle41 Open remains:

- Read-only: no private keys, transaction signing, broadcasting, or custody.
- Local-first: user-created state and normalized analytics remain on the user's machine.
- Evidence-driven: displayed and exported results carry source, freshness, and completeness metadata.
- Provider-independent: services depend on stable contracts rather than vendor response shapes.
- Linux-first: Debian and Ubuntu remain the primary packaged platforms.

The following are not priorities before `1.0`:

- Trading, swaps, bridges, or approval revocation executed from the application
- Tax filing for specific jurisdictions
- Non-EVM chains
- Mobile clients
- Opaque automated risk or investment recommendations

## Current Foundation

Version `0.1.0` already provides:

- Wallet balances and activity across Ethereum, Optimism, Polygon, Base, and Arbitrum
- ERC-20, ERC-721, and ERC-1155 token details and recent approvals
- Alchemy and Ankr adapters with failover, retries, and structured errors
- ENS and address labels, token filtering, pricing cache, and diagnostics
- Watchlists, portfolio aggregation, notes, tags, saved views, and snapshots
- CSV and JSON exports plus local backup and restore
- SQLite persistence with GUI, core, provider, storage, and export boundaries
- Native AMD64 and ARM64 Debian release pipelines

This is a strong wallet-inspection base. The next milestones turn it into a durable analytics platform.

## Quality Principles

Every milestone must preserve these rules:

- Never silently truncate, discard, or replace live data with demonstration data.
- Store provider provenance, query range, cursor state, and completeness with normalized results.
- Keep raw provider payloads out of GUI and domain APIs.
- Make ingestion resumable and safe across process interruption and chain reorganizations.
- Version SQLite schemas and export formats before external contributors depend on them.
- Use deterministic fixtures for providers, decoders, protocol adapters, and accounting rules.
- Keep network and database work off the Qt event loop.
- Redact credentials and sensitive query URLs from logs, diagnostics, and bug reports.

## M4: Canonical Event Ledger

**Release target:** `0.2.0-alpha`

**Implementation status:** completed for `0.2.0a1`; live-provider and clean-install validation remains part of alpha testing.

Create one durable representation of wallet history that all analytics can consume.

### Scope

- Add normalized transaction, event, asset movement, fee, approval, and provenance models.
- Add SQLite repositories for transactions, events, assets, sync checkpoints, and ingestion runs.
- Add schema versioning and forward-only migrations.
- Persist provider cursors and block ranges so interrupted synchronization can resume.
- Record completeness states such as complete, partial, capped, stale, and provider-limited.
- Route Activity and Token Detail through the ledger instead of maintaining separate cached histories.
- Include completeness and provenance fields in CSV and JSON exports.
- Replace the bounded approval scan with paginated, checkpointed ingestion.
- Accept ENS names in Overview, Activity, and Token Detail and preserve the resolved-name context.
- Add GUI integration tests for Activity, Token Detail, filtering, pagination, and cancellation.

### Exit Gate

- A wallet can synchronize incrementally without duplicate events.
- Restarting during synchronization cannot corrupt or lose the last valid checkpoint.
- Repeating the same fixture ingestion produces identical database state.
- Partial results are visible in the GUI and exports.
- Existing `0.1.0` databases migrate without data loss.

## M5: Transaction Decoding and Data Independence

**Release target:** `0.3.0-alpha`

**Implementation status:** Complete in `0.3.0a9`. Transaction receipts, deterministic decoding, proxy and revert context, internal traces, wallet actions, optional Blockscout context, action completeness, beacon proxies, cross-provider validation, and learned historical-state capability reporting are available.

Turn raw transfers and logs into understandable wallet activity while reducing dependence on two hosted vendors.

### Scope

- Add a configurable standard JSON-RPC provider for user-selected endpoints.
- Ingest transaction receipts, logs, gas usage, status, and internal traces where available.
- Add an ABI and signature registry with source and verification metadata. Bundled token standards completed in M5.2; user and Blockscout-verified contract ABIs completed in M5.3.
- Decode contract methods, events, proxy implementations, custom errors, and common token operations. Token standards completed in M5.2; EIP-1967/EIP-1167 and custom errors completed in M5.3; EIP-1967 beacons completed in M5.7.
- Normalize receipt and trace evidence into transfers, approvals, simple swaps, deployments, contract calls, and explicit unknown actions. Protocol-specific positions and economic interpretations continue in M6.
- Add Blockscout as an optional decoded-data and contract-metadata source.
- Keep TrueBlocks behind a documented optional-adapter boundary for users who want a local address index.
- Discover per-provider trace and historical-state capabilities instead of assuming every endpoint supports them.

### Exit Gate

- Unknown calls remain inspectable as raw selectors and arguments instead of disappearing.
- Decoded output is deterministic across providers for the same fixture.
- Every decoded field identifies its ABI or signature source.
- Provider capability gaps produce explicit partial-data states.

**Exit status:** Passed in `0.3.0a9`. The reproducible matrix is documented in [M5_VALIDATION.md](M5_VALIDATION.md), and the TrueBlocks decision is recorded in [ADR 0002](adr/0002-trueblocks-optional-local-index.md).

## M6: DeFi, NFT, and Position Intelligence

**Release target:** `0.4.0-alpha`

**Implementation status:** M6.3F is complete in `0.4.0a12`. Oracle41 collects, resumes, stores, prices, aggregates, displays, refreshes, and exports Aave V3 positions with exact-block controls, health evidence, and clear partial or stale states. Historical prices and broader protocol coverage remain open.

Model economic positions rather than treating every contract token as a simple wallet balance.

### Scope

- Define a versioned protocol-adapter interface and fixture format.
- Model supplied assets, debt, collateral, liquidity positions, staking, vesting, and claimable rewards.
- Implement initial adapters for high-usage lending, DEX, liquid-staking, and bridge protocols.
- Add underlying-asset decomposition for LP and vault tokens.
- Add NFT collection views, metadata provenance, floor-price provenance, and spam controls.
- Add liabilities and net exposure to portfolio aggregation.
- Move chain configuration into a data-driven registry with RPC and explorer capabilities.
- Publish an adapter authoring guide and reference adapter.

### Exit Gate

- Unknown protocols degrade to decoded events and token balances without breaking a portfolio.
- Each adapter has historical fixtures, malformed-response tests, and versioned output.
- Portfolio totals distinguish liquid assets, supplied assets, debt, rewards, and unpriced positions.
- Pricing and protocol values expose timestamps and confidence limitations.

## M7: Historical Analytics and Accounting

**Release target:** `0.5.0-beta`

Provide reproducible performance analysis from the event ledger.

### Scope

- Add timestamped historical prices with provider provenance and stale-price policy.
- Build automatic daily portfolio snapshots and configurable retention.
- Add net-worth, asset-allocation, chain, protocol, and counterparty time series.
- Calculate deposits, withdrawals, fees, income, realized P&L, and unrealized P&L.
- Support configurable lot methods such as FIFO, LIFO, and HIFO without claiming jurisdiction-specific tax compliance.
- Add time-weighted and money-weighted return calculations with documented formulas.
- Add accounting overrides with a complete local audit trail.
- Export normalized ledgers and accounting reports with schema versions.

### Exit Gate

- Calculations are reproducible from a backup plus the documented pricing inputs.
- Golden accounting scenarios cover transfers, swaps, fees, bridges, staking, LP positions, and missing prices.
- Every total can be traced back to constituent events and price observations.
- Corrections never mutate imported history without recording an override.

## M8: Security and Monitoring

**Release target:** `0.6.0-beta`

Make Oracle41 useful for ongoing wallet hygiene without adding transaction execution.

### Scope

- Add a unified approval-exposure center for ERC-20, ERC-721, and ERC-1155 permissions.
- Highlight unlimited allowances, unknown spenders, stale approvals, and changes in approval scope.
- Add local rules for large transfers, new counterparties, failed transactions, and asset-drain patterns.
- Add contract verification, proxy, deployment-age, and label context where sources permit.
- Add background watchlist synchronization and desktop notifications.
- Add alert history, acknowledgement, snoozing, and per-wallet thresholds.
- Link to external revocation or explorer tools without constructing or signing transactions.
- Document each risk rule and avoid opaque aggregate scores.

### Exit Gate

- Alerts state the exact evidence, data age, and rule that triggered them.
- Stale or incomplete synchronization cannot present an alert as current.
- Notification tests cover deduplication, restart behavior, and false-positive suppression.
- No monitoring feature requires custody or write access to a wallet.

## M9: Research Workbench and Extensibility

**Release target:** `0.7.0-beta`

Open the analytics engine to advanced users and contributors.

### Scope

- Add global search for addresses, transactions, tokens, contracts, labels, and notes.
- Add transaction and address comparison workspaces.
- Add fund-flow and counterparty graphs with bounded local queries.
- Add reusable dashboards, charts, filters, and field-selectable reports.
- Add a documented read-only CLI and local API over the same core services.
- Define plugin manifests, compatibility versions, permissions, and lifecycle hooks.
- Support third-party provider, decoder, protocol, label, pricing, and export adapters.
- Add adapter conformance tests and a repository template for community plugins.
- Add importers for common wallet-history CSV formats after the ledger schema stabilizes.

### Exit Gate

- GUI, CLI, and local API return the same normalized results.
- Plugins cannot access secrets unless the user grants a documented capability.
- One external sample plugin passes conformance tests without importing GUI internals.
- Large graph and history queries remain cancellable and do not block the interface.

## M10: Stable Open-Source Release

**Release target:** `1.0.0`

Turn feature breadth into a supportable product and contributor ecosystem.

### Scope

- Validate Debian packages on the documented AMD64 and ARM64 distribution matrix.
- Add Flatpak or AppImage after the Debian path remains stable.
- Publish signed checksums, SBOMs, build provenance, and documented vulnerability handling.
- Add migration rollback guidance, database repair diagnostics, and export compatibility policy.
- Add accessibility review, keyboard navigation, localization infrastructure, and user documentation.
- Establish release cadence, maintainer responsibilities, deprecation policy, and governance.
- Publish anonymized benchmark fixtures for large wallets and long histories.
- Add a public compatibility dashboard for chains, providers, protocols, and package targets.

### Exit Gate

- Supported migrations, backups, and restores pass destructive failure-injection tests.
- A clean install can complete the first wallet analysis without developer tools.
- Security and privacy documentation matches observed network and storage behavior.
- Release artifacts are reproducible, signed, and independently verifiable.
- Contributor documentation supports a first adapter contribution without maintainer intervention.

## Continuous Workstreams

These do not wait for a single milestone:

### Chain Coverage

- Add chains only through capability-tested provider mappings and fixtures.
- Prioritize EVM networks by user demand, data quality, and protocol coverage rather than headline count.
- Track finality, native asset, explorer, trace, and archive behavior per chain.

### Performance

- Benchmark cold sync, incremental sync, database size, memory, and query latency.
- Establish fixtures for wallets with 10,000 and 100,000 normalized events.
- Add indexes based on measured query plans rather than speculative optimization.

### Provider Reliability

- Run opt-in scheduled smoke tests with maintainer-owned provider keys outside pull-request CI.
- Track provider capability and fixture drift without exposing credentials or request URLs.
- Keep vendor outages and rate limits distinguishable from parsing or application defects.
- Maintain one conformance suite for Alchemy, Ankr, Moralis, and GoldRush wallet-data adapters.
- Keep provider enablement, priority, and chain capabilities visible and user-controlled.

### Security and Privacy

- Add dependency, secret, and static security scanning to CI.
- Maintain a threat model for provider keys, backups, plugins, imports, and local APIs.
- Keep analytics read-only and make all external network destinations inspectable.

### Community

- Convert milestone slices into focused GitHub issues with acceptance criteria.
- Label protocol and chain adapters separately from core architecture work.
- Maintain `good first issue` tasks for fixtures, labels, documentation, and exports.
- Require provenance and licensing review for imported ABI, label, token, and protocol data.

## Market Reference Points

The roadmap uses these projects as directional references, not feature-equivalence claims:

- [rotki](https://rotki.com/features) for local-first accounting, DeFi history, and P&L.
- [Blockscout](https://docs.blockscout.com/about/features) for decoded explorer data and broad EVM support.
- [TrueBlocks](https://trueblocks.io/) for lightweight local address indexing.
- [DefiLlama adapters](https://github.com/DefiLlama/DefiLlama-Adapters) for contributor-driven protocol coverage.

Oracle41's differentiator is the combination of these ideas behind a read-only desktop boundary, explicit completeness metadata, and a small provider-independent Python architecture.

## Completed Slice: M5.4

Add internal execution visibility and explicit provider capability states:

1. Add normalized internal-call and trace models without coupling them to one trace RPC dialect.
2. Discover `debug_traceTransaction` and `trace_transaction` support per configured endpoint.
3. Map call trees, contract creations, native internal transfers, and nested revert locations.
4. Persist trace completeness and provider capability gaps beside the canonical transaction.
5. Display an expandable execution tree while preserving raw trace payloads for unsupported frames.

M5.4 is complete only when trace-capable endpoints produce deterministic internal actions, unsupported endpoints report an explicit capability gap, and a partial trace cannot be mistaken for complete execution history.

**Status:** Complete in `0.3.0a5`. Geth call-tracer and Parity trace responses use one stored model. The inspector shows an expandable call tree and trace completeness, and temporary errors are kept separate from unsupported endpoint capabilities.

## Completed Slice: M5.5

Turn decoded calls, logs, value transfers, and internal calls into a small set of understandable wallet actions:

1. Define a versioned action model with participants, assets, amounts, protocol hints, and confidence.
2. Normalize simple transfers, approvals, token swaps, contract deployments, and failed actions without changing raw records.
3. Keep unmatched transactions as explicit unknown actions with their decoded and raw evidence.
4. Store action provenance and decoder version so rules can be improved safely.
5. Display action summaries in Activity and Transaction Inspector and include them in versioned exports.

M5.5 is complete only when the same fixture creates the same ordered actions across providers and every summary links back to its source call, event, or internal transfer.

**Status:** Complete in `0.3.0a6`. Actions are rebuilt from provider-independent stored evidence, saved with rule version 1, shown in Activity and Transaction Inspector, and available through versioned CSV and JSON exports.

## Completed Slice: M5.6

Add Blockscout transaction and contract context as an optional, provenance-aware enrichment source:

1. Discover Blockscout API capabilities per supported chain.
2. Load verified contract names, creation details, and decoded transaction context on request.
3. Merge Blockscout context without replacing JSON-RPC receipts, raw logs, traces, or local ABI results.
4. Record source references and verification state for every added label or decoded field.
5. Show clear unavailable and unsupported states when a chain or endpoint lacks the requested data.

M5.6 is complete only when optional Blockscout enrichment improves understandable output without changing deterministic results from existing raw evidence.

**Status:** Complete in `0.3.0a7`. Blockscout API v2 context is stored separately with source links, verification flags, capability states, and retryable failure handling. Local decoding and normalized actions finish independently and remain unchanged by explorer output.

## Completed Slice: M5.7

Make partial transaction understanding clearer and expand common proxy support:

1. Add an action-set completeness result based on receipt and trace availability.
2. Include completeness and missing-evidence reasons in action exports and Transaction Inspector.
3. Add EIP-1967 beacon proxy resolution without changing historical block context.
4. Keep unresolved and unsupported proxy forms explicit instead of guessing an implementation.

M5.7 is complete only when users can see whether internal actions may be missing and beacon proxies resolve through block-specific, provenance-aware records.

**Status:** Complete in `0.3.0a8`. Every inspected action list reports trace-based evidence completeness and missing-evidence reasons in the UI and versioned exports. Beacon proxies store the beacon and implementation addresses at the queried block; empty implementations remain unresolved.

## Completed Slice: M5.8

Close M5 with data-independence and capability validation:

1. Add cross-provider fixtures that prove the same raw transaction produces the same decoding and actions.
2. Report archive-query capability separately from receipt and trace support.
3. Audit unknown-call, malformed-data, and provider-failure paths against the M5 exit gate.
4. Evaluate TrueBlocks as an optional local address index and record the decision without adding a mandatory dependency.

M5.8 is complete only when the M5 exit gate has a reproducible test matrix and every optional dependency has a documented fallback.

**Status:** Complete in `0.3.0a9`. Paired Alchemy and Ankr fixtures produce identical decoding and actions, historical-state support is learned separately from receipt and trace support, failure paths have an explicit test matrix, and TrueBlocks remains an optional future adapter.

## Completed Slice: M6.1

Create the protocol-adapter foundation before adding individual DeFi integrations:

1. Define a versioned adapter protocol for positions, liabilities, rewards, and source provenance.
2. Define a recorded fixture format that includes chain, block, contracts, raw evidence, and expected normalized positions.
3. Add an adapter registry with explicit chain and protocol capabilities.
4. Provide an unknown-protocol fallback that keeps decoded events and token balances visible.

M6.1 is complete only when a reference adapter and an unknown-protocol fixture pass the same conformance suite without importing GUI code.

**Status:** Complete in `0.4.0a1`. The reference lending and unknown-protocol fixtures use the same deterministic conformance path. Unknown evidence preserves token balances, decoded events, actions, and raw records. Registry ambiguity is rejected before analysis.

## Completed Milestone: M6.2

**Release series:** `0.4.0` alpha

Expand wallet-data choice from two providers to four:

1. Add Moralis and GoldRush adapters for native balances, token holdings, wallet activity, and token-specific history.
2. Replace the fixed primary/fallback pair with an ordered provider pool.
3. Let users enable or disable each provider, choose priority, validate credentials, and see supported chains and features in Settings.
4. Keep custom JSON-RPC endpoints as a separate transaction-inspection option instead of presenting them as complete wallet-history providers.
5. Store every API key in the OS keyring, exclude all keys from backups and logs, and show the network destination before validation.
6. Record provider provenance and completeness on every result so failover cannot silently mix incompatible pages.
7. Add recorded fixtures for every provider and a shared conformance suite over the `DataProvider` contract.

Moralis and GoldRush were selected because both provide indexed wallet balances and history across the current Oracle41 chain set. Their adapters remain subject to API stability, licensing, plan limits, and continued fixture validation.

M6.2 is complete only when any one configured provider can run the supported wallet flows, an ordered subset can fail over safely, disabled providers make no requests, and Settings clearly reports feature gaps per provider and chain.

### Completed Slice: M6.2A

**Status:** Complete in `0.4.0a2`.

M6.2A replaces the fixed Alchemy/Ankr pair with an ordered provider pool. Settings now stores enabled state and priority for both available wallet-data providers. Fresh requests can fail over in that order, while balances, activity, and token-history cursors remain bound to the provider and operation that created them. Disabled providers are left out of automatic runtime setup.

At the end of M6.2A, Moralis and GoldRush adapters, capability reporting, and the shared provider conformance suite remained open.

### Completed Slice: M6.2B

**Status:** Complete in `0.4.0a3`.

M6.2B adds one provider capability catalog and shows supported chains, wallet features, and credential-check destinations in Settings. Planned adapters remain explicitly unavailable and claim no support.

Alchemy and Ankr now run through one format-v1 recorded-fixture conformance suite. The same assertions cover native balances, token balances, wallet activity, token history, pagination markers, provenance, chain identity, and NFT categories. A public JSON schema defines the fixture format for future adapters.

Moralis and GoldRush implementations, their credentials, and four-provider runtime validation remain open.

### Completed Slice: M6.2C

**Status:** Complete in `0.4.0a4`.

M6.2C adds Moralis as the third available wallet-data provider. The REST adapter loads native balances, paginated token balances, decoded wallet activity, ERC-20 transfers, and ERC-721 / ERC-1155 transfers on every current Oracle41 chain.

Settings can save, validate, enable, disable, and order Moralis. Startup adds it to the provider pool only when it is enabled and has a key. Its Data API key is not treated as a transaction JSON-RPC or pricing source.

Moralis also loads current active ERC-20 approvals. The capability catalog keeps this separate from complete approval history because revoked approvals are not returned by that endpoint. Recorded Moralis responses now pass the shared provider conformance suite. GoldRush and final four-provider validation remain open.

### Completed Slice: M6.2D

**Status:** Complete in `0.4.0a5`.

M6.2D adds GoldRush as the fourth available wallet-data provider. The Foundational REST adapter loads native balances, token holdings, decoded wallet activity, ERC-20 transfers, ERC-721 transfers, ERC-1155 single and batch transfers, and decoded approval history on every current Oracle41 chain.

Settings can save, validate, enable, disable, and order GoldRush. Startup adds it to the provider pool only when it is enabled and has a key. Its key is sent only as a bearer header and is not treated as a transaction JSON-RPC or pricing source.

Recorded GoldRush responses pass the shared provider conformance suite. Unit tests cover chain names, authentication, credits, rate limits, timeouts, malformed data, retries, paging, approval revocations, and ERC-1155 batches. Final opt-in live validation across all four providers remains open.

### Completed Slice: M6.2E

**Status:** Complete in `0.4.0a6`.

M6.2E adds safe credential diagnostics to Settings. Each provider reports whether its current credential comes from the system keyring or environment, whether that source has passed validation, and the UTC time of the last successful check. Persisted diagnostics contain no credential-derived value.

An explicit local validator now exercises one bounded page of native balance, token balances, wallet activity, and token history for Alchemy, Ankr, Moralis, and GoldRush. It requires all inputs through environment variables, redacts output, and returns clear shell status codes.

The M6.2 implementation is complete. Recorded conformance remains mandatory in public CI, while maintainers run live validation locally when provider credentials and API credits are intentionally available.

## Current Milestone: M6.3

**Release series:** `0.4.0` alpha

Turn protocol evidence into useful, reproducible DeFi positions:

1. Add production adapters for lending, DEX liquidity, liquid staking, and common vaults.
2. Collect protocol snapshots at one explicit block through transaction providers.
3. Save positions, risk metrics, source references, and completeness in SQLite.
4. Add protocol assets and liabilities to portfolio totals without double counting receipt tokens.
5. Show positions, debt, rewards, health data, missing prices, and stale data in the desktop interface.
6. Add versioned protocol-position CSV and JSON exports.

M6.3 is complete only when a wallet can load, resume, store, display, and export supported positions with raw evidence and clear partial-data states.

### Completed Slice: M6.3A

**Status:** Complete in `0.4.0a7`.

M6.3A adds the first production protocol normalizer. `AaveV3Adapter` recognizes official Aave V3 deployments on Ethereum, Optimism, Polygon, Base, and Arbitrum. It converts recorded reserve snapshots into supplied, collateral, and debt positions and reports raw account collateral, debt, available borrow, loan-to-value, liquidation threshold, and health factor values.

Collateral-enabled supply is emitted once as collateral instead of being duplicated as supplied. Stable and variable debt are combined for the same underlying reserve. Missing or malformed snapshots return a partial result without inventing values. Recorded fixtures and tests cover complete, partial, malformed, zero-debt, below-threshold, and cross-chain matching cases.

Automatic contract-call collection is completed in M6.3B. SQLite persistence, portfolio integration, exports, and the desktop position view remain open for later slices.

### Completed Slice: M6.3B

**Status:** Complete in `0.4.0a8`.

M6.3B adds automatic Aave V3 snapshot collection through the shared transaction-provider pool. The service discovers market reserves and reads wallet reserve balances, reserve settings, account health, and the oracle base-currency unit at one requested block. It works on Ethereum, Optimism, Polygon, Base, and Arbitrum.

One snapshot cannot mix successful responses from different providers. Reserve discovery must succeed, while later failures produce a partial result and keep all valid evidence. Tests cover exact-block calls, provider failover, zero positions, malformed reserve data, mixed-provider protection, structured partial failures, and invalid input.

At the end of M6.3B, SQLite persistence and resumable protocol sync were assigned to M6.3C. Pricing, portfolio totals, exports, and the desktop position view remained later work.

### Completed Slice: M6.3C

**Status:** Complete in `0.4.0a9`.

M6.3C adds SQLite schema v10 and a dedicated protocol-position repository. Finished snapshots keep positions, assets, risk metrics, raw evidence, completeness, warnings, adapter details, provider provenance, and observation time. Snapshot history can be read by wallet, chain, protocol, and exact block.

Aave collection now saves a checkpoint after reserve discovery and after every completed reserve. If the process stops, the next run continues from the next reserve instead of starting over. Finished snapshot storage and checkpoint deletion use one transaction, so a failed final write leaves the previous checkpoint safe. A finished snapshot is reused without making the same network calls again.

Tests cover v9-to-v10 migration, checkpoint and snapshot round trips, restart behavior, cached reads, newest-first history, and rollback during finalization. Pricing, portfolio totals, exports, and the desktop position view remain open for M6.3D and later slices.

### Completed Slice: M6.3D

**Status:** Complete in `0.4.0a10`.

M6.3D adds protocol-aware portfolio valuation. Oracle41 loads the newest stored snapshot for every protocol on each selected wallet and chain, batches prices by chain and underlying token, adds supplied assets and collateral, and subtracts debt as a liability. Known values remain available when another position cannot be priced.

Aave receipt and debt tokens are excluded only when positive snapshot evidence identifies the matching position. This prevents double counting while preserving unrelated wallet tokens. Partial snapshots, missing prices, and storage failures make the complete total unavailable instead of silently understating risk.

The Portfolio view and summary/wallet exports report protocol assets, liabilities, net value, unpriced positions, partial snapshots, excluded token counts, block numbers, providers, and observation times. Tests cover signed debt, batched prices, missing quotes, partial evidence, storage failure, latest-snapshot selection, and receipt-token exclusion.

Dedicated protocol-position exports were assigned to M6.3E. Historical price-at-block valuation and additional protocol adapters remained later work.

### Completed Slice: M6.3E

**Status:** Complete in `0.4.0a11`.

M6.3E adds `oracle41-portfolio` format-v1 protocol-position CSV and JSON exports. Each row keeps the wallet, chain, protocol, snapshot block, position and asset type, token address, raw amount, normalized amount, current price, gross value, signed net value, completeness, provider, and observation time.

The Portfolio view can load the newest stored snapshot for every protocol or use one exact stored block. A local history action finds available blocks without contacting a provider. If an exact snapshot is missing for a wallet, the application reports it. Exact protocol snapshots are combined with current wallet overview balances, so the application keeps the estimate visible but does not label it as a complete historical portfolio total.

Manual refresh is separate from normal loading. It requires one chain and one exact block, bypasses the finished Aave snapshot, recollects provider evidence, replaces the stored result, and reloads the portfolio. Tests cover exact-block repository reads, forced recollection, missing snapshots, public export fields, and interface controls.

M6.3 now has one complete production path for Aave V3. Historical price-at-block valuation and additional lending, liquidity, staking, and vault adapters remain future expansion work.

### Completed Slice: M6.3F

**Status:** Complete in `0.4.0a12`.

M6.3F adds one risk and freshness report for every stored protocol snapshot. The report keeps the adapter status and provenance, provider, block, observation and storage times, warnings, raw Aave collateral, debt, available borrow, loan-to-value, liquidation threshold, and health factor. Health factor is normalized from its WAD value for display while the original integer remains available.

The Portfolio view now shows protocol health details and marks observations as fresh, stale, or future. The stale threshold is configurable and defaults to one hour. Stale or future observations keep known values visible but prevent a complete total. The classification uses observation time, not the distance between the stored block and the current chain head.

`oracle41-portfolio` format version 2 adds observation age and freshness to protocol-position exports and adds dedicated protocol-risk CSV and JSON records. Tests use a fixed clock and cover fresh, stale, future, no-debt, missing-risk, export, settings, and portfolio-completeness behavior.

Raw Aave account totals remain in protocol base units and are not presented as USD. The application reports evidence and state without giving financial advice. Historical price-at-block valuation and additional protocol adapters remain future work.
