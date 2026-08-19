# ADR 0002: Keep TrueBlocks as an Optional Local Index

- Status: Accepted
- Date: 2026-08-19

## Context

Oracle41 Open needs complete wallet history without depending only on hosted analytics APIs. [TrueBlocks](https://github.com/TrueBlocks/trueblocks-core) provides the local `chifra` command and an address-appearance index called the Unchained Index. It can query a local or remote EVM endpoint and keep a local cache.

TrueBlocks is a separate application with its own configuration, index, cache, RPC connection, and update process. Users can download a published index or build one with `chifra scrape`. The [TrueBlocks index guide](https://trueblocks.io/docs/install/build-unchained-index/) explains that storage and setup time vary widely. Building the index locally requires a tracing or archive node, while downloaded index data can lag behind the chain and still needs RPC calls for full transaction data.

Oracle41 currently supports Ethereum, Optimism, Polygon, Base, and Arbitrum through one provider-neutral ledger. TrueBlocks documents [limits in its multi-chain index support](https://trueblocks.io/docs/prologue/multi-chain/), so it cannot replace the current providers across the full supported chain set.

## Decision

TrueBlocks will remain an optional future adapter. Oracle41 will not bundle `chifra`, start its daemon, download index data, or manage its files in the standard installation.

A future adapter may connect to a user-managed `chifra` installation when all of these rules are met:

1. The user enables it for a specific chain.
2. Oracle41 checks the installed version and capabilities before use.
3. The adapter imports transaction references into the canonical ledger and then uses the normal receipt and decoding pipeline.
4. TrueBlocks cache and index files remain owned by TrueBlocks.
5. Failure or missing index data falls back to Alchemy, Ankr, or the configured JSON-RPC endpoint.
6. Oracle41 does not send provider credentials to a subprocess or write them into TrueBlocks configuration.

## Consequences

- The Debian package stays self-contained and does not hide a large secondary data download.
- Existing provider failover remains the default and works on every supported chain.
- Advanced users may later gain a private local address index without changing canonical event, decoding, or action models.
- A future implementation needs subprocess or local API security review, version compatibility tests, chain-specific fixtures, cancellation, progress reporting, and clear disk-use controls.
