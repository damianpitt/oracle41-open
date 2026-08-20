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

## Adding an Adapter

1. Record the protocol contracts and supported chains.
2. Define which saved calls, events, traces, and balances are required.
3. Preserve raw values and source references.
4. Return `partial` with plain warnings when required evidence is missing.
5. Add complete, partial, malformed, and unknown fixtures.
6. Run the shared conformance tests.
7. Document proxy behavior, position semantics, and unsupported protocol versions.
8. Keep provider, storage, and GUI imports out of the adapter.

A production adapter is not complete until its values can be reproduced from the recorded fixture at the specified block.
