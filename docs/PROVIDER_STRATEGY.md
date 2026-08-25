# Provider Strategy

Oracle41 Open keeps provider-specific response formats outside its core services. Every wallet-data adapter must produce the same domain models and preserve provider, fetch time, pagination, and completeness information.

## Current Providers

| Provider | Wallet balances and history | Transaction JSON-RPC | Settings support |
| --- | --- | --- | --- |
| Alchemy | Available | Available | API key, enabled state, priority |
| Ankr | Available | Available | API key, enabled state, priority |
| Moralis | Available | Not used as JSON-RPC | API key, enabled state, priority |
| Custom JSON-RPC | Not a complete indexed wallet source | Available | One endpoint per chain |

Custom JSON-RPC is intentionally separate. Standard EVM nodes can return balances, receipts, logs, and traces, but they do not normally expose a complete indexed history for one address.

## Planned Four-Provider Update

M6.2 adds [Moralis](https://docs.moralis.com/get-started/global-api-reference) and plans to add [GoldRush](https://goldrush.dev/docs/chains) as wallet-data choices. Moralis provides indexed balances and transaction history for the EVM chains Oracle41 supports. GoldRush still requires an API review and adapter validation before it can be enabled.

The provider pool follows these rules:

1. Users choose which providers are enabled.
2. Users choose an explicit priority order.
3. Disabled providers receive no requests.
4. Failover happens only after a structured provider error or a clearly unsupported capability.
5. Pagination cursors remain owned by the provider that created them.
6. A page from one provider is never continued with another provider's cursor.
7. Canonical ledger records keep source and completeness metadata.
8. Provider-specific labels or decoded summaries cannot replace raw evidence or local decoding.

## Capability Catalog

Version `0.4.0a4` has one local catalog for stable provider IDs, availability, supported chains, wallet features, and credential-check destinations. Alchemy, Ankr, and Moralis are available. GoldRush remains planned and claims no capabilities yet.

Settings reads the catalog without creating network clients. Alchemy credential checks connect to `api.g.alchemy.com`. Ankr checks connect to `rpc.ankr.com`. Moralis checks connect to `deep-index.moralis.io`. These destinations are shown before the user starts validation.

Each available provider row shows:

- Enabled state
- Priority
- Supported chains
- Wallet balances, history, approvals, NFT, and pagination capabilities
- Credential-check destination

The Save API Keys action validates entered Alchemy, Ankr, and Moralis credentials. A later slice should add saved credential status and the last validation time. Receipt, trace, and historical-state support belongs to the separate transaction-provider capability view.

API keys stay in the operating-system keyring. They are excluded from backups, exports, logs, diagnostics, and issue-report templates.

## Shared Conformance Suite

Alchemy, Ankr, and Moralis use separate recorded response fixtures with the same normalized expected results. The shared suite checks:

- Native balance
- Token balances and pagination markers
- Wallet activity and source provenance
- Token-specific history
- ERC-721 and ERC-1155 categories
- Chain identity

The fixture format is versioned. Its public schema is `docs/schemas/data-provider-conformance-v1.schema.json`. New wallet-data adapters must add a fixture and pass this suite before they are marked available.

## Moralis Scope

The Moralis adapter uses documented REST endpoints for native balance, token balance pages, decoded wallet history, ERC-20 transfers, NFT transfers, and active ERC-20 approvals. Its API key is sent only in the `X-API-Key` header.

Moralis active approvals are a current allowance snapshot. They do not include approvals that were later revoked, so the capability catalog reports active approvals separately from complete approval history. The adapter is not registered as a JSON-RPC or pricing provider.

## Admission Requirements

A provider is ready for public use only when it has:

- Recorded success, empty-page, pagination, authentication, rate-limit, timeout, and malformed-response fixtures
- The shared `DataProvider` conformance suite
- Chain-by-chain capability tests
- Deterministic canonical output tests against at least one existing provider
- Documented API terms, request destinations, and known plan restrictions
- Safe removal and key deletion from Settings

The app must continue to work with one provider. Four configured providers improve choice and resilience, but they must never become a requirement.
