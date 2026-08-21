# Provider Strategy

Oracle41 Open keeps provider-specific response formats outside its core services. Every wallet-data adapter must produce the same domain models and preserve provider, fetch time, pagination, and completeness information.

## Current Providers

| Provider | Wallet balances and history | Transaction JSON-RPC | Settings support |
| --- | --- | --- | --- |
| Alchemy | Available | Available | API key, enabled state, priority |
| Ankr | Available | Available | API key, enabled state, priority |
| Custom JSON-RPC | Not a complete indexed wallet source | Available | One endpoint per chain |

Custom JSON-RPC is intentionally separate. Standard EVM nodes can return balances, receipts, logs, and traces, but they do not normally expose a complete indexed history for one address.

## Planned Four-Provider Update

M6.2 plans to add [Moralis](https://docs.moralis.com/get-started/global-api-reference) and [GoldRush](https://goldrush.dev/docs/chains) as complete wallet-data choices. Both currently provide indexed balances and transaction history for the EVM chains Oracle41 supports. Their APIs must be reviewed again when implementation starts because pricing, plans, limits, and response formats can change.

The provider pool follows these rules:

1. Users choose which providers are enabled.
2. Users choose an explicit priority order.
3. Disabled providers receive no requests.
4. Failover happens only after a structured provider error or a clearly unsupported capability.
5. Pagination cursors remain owned by the provider that created them.
6. A page from one provider is never continued with another provider's cursor.
7. Canonical ledger records keep source and completeness metadata.
8. Provider-specific labels or decoded summaries cannot replace raw evidence or local decoding.

## Settings Design

Version `0.4.0a2` shows enabled state and priority for Alchemy and Ankr. API keys keep their existing validation action. Later M6.2 slices will add Moralis and GoldRush and show these details for every provider:

- Enabled state
- Priority
- Credential status
- Test Connection action
- Supported chains
- Balances, history, approvals, NFTs, receipts, traces, and historical-state capabilities
- Last validation time and a plain error message

API keys stay in the operating-system keyring. They are excluded from backups, exports, logs, diagnostics, and issue-report templates.

## Admission Requirements

A provider is ready for public use only when it has:

- Recorded success, empty-page, pagination, authentication, rate-limit, timeout, and malformed-response fixtures
- The shared `DataProvider` conformance suite
- Chain-by-chain capability tests
- Deterministic canonical output tests against at least one existing provider
- Documented API terms, request destinations, and known plan restrictions
- Safe removal and key deletion from Settings

The app must continue to work with one provider. Four configured providers improve choice and resilience, but they must never become a requirement.
