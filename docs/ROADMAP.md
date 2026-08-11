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
- Opaque AI-generated risk or investment recommendations

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

**Implementation status:** M5.1 receipt ingestion and M5.2 deterministic token-standard decoding are implemented through `0.3.0a3`; verified contract ABIs, proxies, custom errors, traces, Blockscout, and broader action normalization remain.

Turn raw transfers and logs into understandable wallet activity while reducing dependence on two hosted vendors.

### Scope

- Add a configurable standard JSON-RPC provider for user-selected endpoints.
- Ingest transaction receipts, logs, gas usage, status, and internal traces where available.
- Add an ABI and signature registry with source and verification metadata. Initial bundled token-standard registry completed in M5.2.
- Decode contract methods, events, proxy implementations, custom errors, and common token operations. Common ERC-20, ERC-721, and ERC-1155 operations completed in M5.2.
- Normalize swaps, bridges, staking actions, lending actions, and contract deployments.
- Add Blockscout as an optional decoded-data and contract-metadata source.
- Evaluate optional TrueBlocks integration for users who want a local address index.
- Add per-provider capability discovery instead of assuming every endpoint supports traces or archive queries.

### Exit Gate

- Unknown calls remain inspectable as raw selectors and arguments instead of disappearing.
- Decoded output is deterministic across providers for the same fixture.
- Every decoded field identifies its ABI or signature source.
- Provider capability gaps produce explicit partial-data states.

## M6: DeFi, NFT, and Position Intelligence

**Release target:** `0.4.0-alpha`

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

## Immediate Next Slice: M5.2

Build deterministic method and event decoding on the persisted M5.1 receipt data:

1. Add versioned method-signature and ABI-source records with provenance and verification state.
2. Decode standard ABI argument and event types without hiding undecodable bytes.
3. Recognize proxies while preserving both proxy and implementation context.
4. Display decoded methods and events beside raw calldata and logs in Transaction Inspector.
5. Add golden fixtures for transfers, approvals, swaps, reverts, overloaded signatures, and malformed payloads.

M5.2 is complete only when decoded output is deterministic, every decoded field identifies its source, and unknown selectors/logs remain fully inspectable in raw form.
