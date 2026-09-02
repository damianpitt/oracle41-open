# Protocol Adapter Guide

Protocol adapters turn saved wallet evidence into economic positions. They do not fetch wallet history, update the database, change decoded events, or call the desktop interface.

## Public Contract

Adapters implement `ProtocolAdapter` from `oracle41_open.core.protocols`. Each adapter provides:

- A stable adapter ID and adapter version
- A protocol ID and display name
- Supported chains
- Supported position kinds
- Known protocol contracts for each chain
- A deterministic `supports()` check
- An `analyze()` method that returns `ProtocolAdapterResult`

The result can contain supplied assets, debt, collateral, liquidity, staking, vesting, and rewards. Raw amounts remain integer strings. Decimal conversion, prices, and portfolio totals belong to later services.

## Evidence Rules

An adapter receives one immutable `ProtocolAdapterContext`. It contains:

- Wallet, chain, and block context
- Contracts found in the evidence
- Normalized wallet actions
- Token balances
- Decoded events
- Raw evidence records
- Provider and observation time

Every result must return the original actions, balances, events, and raw evidence. A position supplements these records; it never replaces them.

Unknown contracts use `UnknownProtocolAdapter`. The fallback returns no position, marks the result `unknown_protocol`, and keeps all source data visible.

## Registry Rules

`ProtocolAdapterRegistry` selects adapters by their declared capabilities. It rejects:

- Duplicate adapter IDs
- Two adapters claiming the same contract on the same chain

Registration order is stable, but adapters should not rely on priority to resolve overlapping contracts. Shared routers or proxy registries need an explicit dispatch design before integration.

## Fixture Format

Fixtures use `oracle41-protocol-adapter-fixture` format version 1. The JSON schema is [protocol-adapter-fixture-v1.schema.json](schemas/protocol-adapter-fixture-v1.schema.json).

Each fixture records:

- Case ID and format version
- Chain and exact block number
- Wallet and protocol contracts
- Source provider and observation time
- Token balances and decoded events
- Raw transaction or snapshot evidence
- Expected adapter, status, position kinds, amounts, and passthrough counts

Examples are in `tests/fixtures/protocols`. Fixtures must not contain API keys, private endpoints, private wallet data, or copyrighted provider datasets that cannot be redistributed.

## Reference Adapter

`ReferenceLendingAdapter` is an illustrative adapter used by the conformance tests. It emits supplied, debt, and reward positions from a recorded snapshot. Its contract address is not a production deployment and the adapter is not connected to application runtime services.

Use it to understand the minimum structure, not as a template for protocol-specific financial assumptions.

## Aave V3 Adapter

`AaveV3Adapter` is the first production protocol normalizer. It recognizes the official Aave V3 Pool, Pool Addresses Provider, and Protocol Data Provider contracts on Ethereum, Optimism, Polygon, Base, and Arbitrum. Deployment addresses were checked against the [Aave DAO address book](https://github.com/aave-dao/aave-address-book) on 2026-08-28 and must be reviewed when Aave adds or replaces a market.

The adapter expects two kinds of block-specific evidence:

- `aave_v3_reserve_position` records `getUserReserveData` values for one underlying reserve.
- `aave_v3_account_data` records `getUserAccountData` values and the oracle base-currency unit.

A supplied reserve is reported as collateral when Aave says collateral is enabled. It is otherwise reported as supplied. The adapter combines stable and variable debt into one debt position for the same underlying asset. This avoids showing the same supplied balance twice and keeps portfolio totals safe for later work.

Account health values remain raw integers. Health factor uses Aave's 18-decimal WAD unit. Oracle41 reports whether it is below `1.0`, equal to or above `1.0`, or has no debt. It does not create extra risk grades or financial recommendations.

Version `0.4.0a8` uses `ProtocolPositionService` to make the required calls through the configured transaction-provider pool. It discovers reserves with `getAllReservesTokens()`, reads each wallet reserve with `getUserReserveData()`, and loads configuration and token addresses only when needed. It also reads `getUserAccountData()`, the active price oracle, and its base-currency unit.

All calls use one explicit block number. Successful reads in one snapshot must come from the same provider. Reserve discovery is required because Oracle41 cannot know which assets to inspect without it. A later reserve or account read can fail without losing the rest of the snapshot; the result is marked partial and includes a plain collection warning. The service limits one market snapshot to 128 reserves to reject unreasonable responses.

The collector supports configured transaction providers that can perform historical `eth_call`, including Alchemy, Ankr, and custom JSON-RPC endpoints. Historical state may depend on the provider plan and node retention. Moralis and GoldRush remain wallet-data providers and do not perform these contract reads.

Collection is automatic at the service layer. Version `0.4.0a9` also saves completed snapshots in SQLite and checkpoints progress after every reserve. Restarting the same wallet, chain, protocol, and block continues from the next unfinished reserve. A completed snapshot is reused without another provider request.

The stored snapshot keeps normalized positions, assets, risk values, completeness, warnings, raw evidence, adapter version, source provider, and observation time. Final snapshot storage and checkpoint removal are atomic. If the final write fails, the checkpoint remains available for another attempt.

Version `0.4.0a10` uses the newest stored snapshot for every protocol on a wallet and chain. Underlying assets receive current available prices, debt is reported as a liability, and missing prices remain visible. Aave aTokens and debt tokens are removed from wallet totals only when positive reserve evidence proves that the matching economic position is included. This prevents receipt-token double counting without hiding unrelated wallet tokens.

Version `0.4.0a11` adds newest-snapshot and exact-block controls to the Portfolio view. Finding stored blocks reads SQLite only. Refreshing a protocol snapshot is a separate action that requires one chain and one block, bypasses the finished result for that block, recollects Aave data, and safely replaces the stored snapshot. Wallet overview balances remain current, so exact protocol-block mode reports a known mixed-time estimate instead of a complete historical portfolio total.

The dedicated protocol-position CSV and JSON templates use `oracle41-portfolio` format version 1. They include wallet, chain, protocol, block, position kind, asset role, token details, raw and normalized amounts, current price, gross and signed values, completeness, provider, and observation time. Historical price-at-block valuation remains later work.

## Adding an Adapter

1. Record protocol contracts and supported chains from an official source.
2. Define which saved calls, events, traces, and balances are required.
3. Preserve raw values and source references.
4. Return `partial` with plain warnings when required evidence is missing.
5. Add complete, partial, malformed, and unknown fixtures.
6. Run the shared conformance tests.
7. Document proxy behavior, position semantics, and unsupported protocol versions.
8. Keep provider, storage, and GUI imports out of the adapter.

A production adapter is not complete until its values can be reproduced from the recorded fixture at the specified block.
