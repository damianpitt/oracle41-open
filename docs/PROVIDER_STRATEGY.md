# Provider Strategy

Oracle41 Open keeps provider-specific response formats outside its core services. Every wallet-data adapter must produce the same domain models and preserve provider, fetch time, pagination, and completeness information.

## Provider Roles

Oracle41 separates network access into three roles:

- **Wallet data:** indexed balances, token holdings, activity, NFTs, and token history for one address.
- **Transaction inspection:** receipts, logs, contract reads, proxy checks, revert data, and optional internal-call traces.
- **Pricing:** current market prices used for portfolio values.

| Provider | Wallet balances and history | Transaction inspection | Market pricing | Settings support |
| --- | --- | --- | --- | --- |
| Alchemy | Available | Available | Available | API key, enabled state, priority |
| Ankr | Available | Available | Not used | API key, enabled state, priority |
| Moralis | Available | Not used | Not used | API key, enabled state, priority |
| GoldRush | Available | Not used | Not used | API key, enabled state, priority |
| Custom JSON-RPC | No complete wallet index | Available | Not used | One endpoint per chain |

Custom JSON-RPC is intentionally separate. Standard EVM nodes can return balances, receipts, logs, and traces, but they do not normally expose a complete indexed history for one address.

"Available" means the role is implemented in Oracle41. It does not guarantee that every provider account or endpoint exposes traces and historical state. These features can depend on the selected chain, provider plan, node configuration, and retention policy.

## Network Coverage

Ethereum, Optimism, Polygon, Base, and Arbitrum have recorded wallet-data coverage through all four providers. Robinhood Chain has recorded wallet-data coverage through Alchemy and GoldRush. Ankr and Moralis are excluded because they do not currently list Robinhood support.

Network identity and RPC details come from the [Robinhood Chain connection guide](https://docs.robinhood.com/chain/connecting/). Provider decisions follow the official [Alchemy Robinhood API overview](https://www.alchemy.com/docs/robinhood-chain/robinhood-chain-api-overview), [GoldRush chain catalog](https://goldrush.dev/chains/), [Ankr chain list](https://www.ankr.com/docs/rpc-service/chains/chains-list/), and [Moralis chain list](https://docs.moralis.com/data-api/supported-chains).

| Provider or source | Robinhood status in `0.4.0a20` |
| --- | --- |
| Alchemy wallet data | Available for balances, activity, token and NFT history, approvals, and pagination |
| Alchemy JSON-RPC | Available for transaction inspection |
| Alchemy token pricing | Generic token-by-address prices are not confirmed |
| Robinhood public Stock Token API | Official contract identity, multiplier-aware pricing, and corporate-action history |
| Ankr | Not supported |
| Moralis | Not supported |
| GoldRush wallet data | Available for balances, activity, token and NFT history, approvals, and pagination |
| Custom JSON-RPC | Available for transaction inspection; no complete wallet index |
| Blockscout | Available for explorer and verified-contract context |

The provider pool checks the selected chain and operation before making a request. Unsupported providers are skipped. If no enabled provider has validated coverage, the application reports that limitation instead of contacting an unsuitable endpoint.

## Choosing a Setup

| Setup | Wallet analytics | Transaction inspection | Pricing | Main trade-off |
| --- | --- | --- | --- | --- |
| Alchemy only | Yes | Yes | Yes | One account supplies every current role. |
| Ankr only | Yes | Yes | No | Portfolio market values need another pricing source. |
| Moralis only | Yes | No | No | Indexed analytics work, but advanced inspection and pricing are limited. |
| GoldRush only | Yes | No | Robinhood Stock Tokens only | Indexed analytics work, but advanced inspection and general pricing are limited. |
| Moralis or GoldRush plus custom JSON-RPC | Yes | Yes | Robinhood Stock Tokens only | Good provider independence, without a general pricing feed. |
| Alchemy plus any other wallet provider | Yes, with failover | Yes | Yes | Broader resilience, with more than one account to configure. |

Alchemy currently gives the broadest single-provider experience. It is not required: users can combine a specialized wallet-data provider with a custom JSON-RPC endpoint. Oracle41 keeps these roles separate so a provider is used only for capabilities its public API supports.

## Four-Provider Wallet Data

M6.2 adds [Moralis](https://docs.moralis.com/get-started/global-api-reference) and [GoldRush](https://goldrush.dev/docs/chains) as wallet-data choices. Both provide indexed balances and transaction history for Oracle41's five established networks. GoldRush also provides core structured wallet data for Robinhood as a Frontier Chain. Version `0.4.0a20` keeps that path enabled after chain-specific fixture validation.

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

Version `0.4.0a20` records wallet features per provider and chain. Adding a new network no longer gives it every existing provider capability automatically. Robinhood advertises Alchemy and GoldRush wallet data because both adapters have completed chain-specific fixtures.

Settings reads the catalog without creating network clients. Alchemy credential checks connect to `api.g.alchemy.com`. Ankr checks connect to `rpc.ankr.com`. Moralis checks connect to `deep-index.moralis.io`. GoldRush checks connect to `api.covalenthq.com`. These destinations are shown before the user starts validation.

Each available provider row shows:

- Enabled state
- Priority
- Supported chains
- Wallet balances, history, approvals, NFT, and pagination capabilities
- Credential-check destination

The Save API Keys action validates entered Alchemy, Ankr, Moralis, and GoldRush credentials. Settings shows whether each key comes from the system keyring or environment and records the last successful validation time for that source. Receipt, trace, and historical-state support belongs to the separate transaction-provider capability view.

API keys stay in the operating-system keyring. They are excluded from backups, exports, logs, diagnostics, and issue-report templates.

Credential diagnostics contain only a provider ID, source label, successful state, and UTC timestamp. They contain no key, fingerprint, URL, wallet result, or raw error. These safe fields can be included in settings backups.

## Shared Conformance Suite

Alchemy, Ankr, Moralis, and GoldRush use separate recorded response fixtures with the same normalized expected results. The shared suite checks:

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

## GoldRush Scope

The GoldRush adapter uses the Foundational REST API for native balances, token holdings, decoded wallet history, token transfers, NFT transfers, and decoded approval history. Its API key is sent only in the bearer authorization header.

The transaction endpoint is page based and does not accept Oracle41's block floor. The adapter filters old transactions after each page is loaded, so a deep sync can use more credits. GoldRush is not registered as a JSON-RPC or pricing provider.

## Transaction Inspection Scope

Alchemy, Ankr, and custom JSON-RPC endpoints can supply standard transaction data. Oracle41 can request receipts, raw logs, contract storage, proxy information, revert evidence, and internal-call traces through these endpoints.

Trace and historical-state methods are not universal JSON-RPC features. Some endpoints disable them, retain only recent state, or require a paid plan. Oracle41 learns these capabilities per chain and reports missing evidence in Transaction Inspector.

## Pricing Scope

The general pricing adapter uses Alchemy. Robinhood Stock Tokens use Robinhood's public read-only API because the asset catalog provides exact chain deployments and the multiplier required to interpret raw underlier prices. Generic Robinhood ERC-20 contracts do not inherit Stock Token pricing.

For an official Stock Token, Oracle41 validates the contract in both the asset catalog and quote response, multiplies the raw underlier bid and ask by `currentMultiplier`, and uses the adjusted midpoint for analytics. Pending multipliers are not applied early. Quote timestamps and trading-halt state remain available to Token Detail.

## Public RWA Identity Sources

Oracle41 uses public issuer or protocol sources only for identity. These sources do not join the wallet-data failover pool and require no user credential.

| Source | Current use | Networks in Oracle41 |
| --- | --- | --- |
| Robinhood Stock Token API | Exact identity, quote, multiplier, corporate actions | Robinhood Chain |
| xStocks public API v2 | Exact token and wrapper identity | Ethereum, Optimism, Polygon, Base, Arbitrum |
| Centrifuge deployment registry | Reviewed share-token identity | Ethereum, Optimism, Base, Arbitrum |

Names and symbols are never enough to verify an RWA. xStocks addresses are loaded from its public catalog and cached for one hour. Centrifuge addresses are reviewed from its official deployment page and shipped with the application, so a new deployment needs a source update before Oracle41 recognizes it. This release does not use xStocks or Centrifuge as general price feeds.

Wallet providers may return other vendor-specific quote fields, but Oracle41 does not treat those values as a shared pricing source. This avoids silently mixing prices with different timestamps, currencies, or methodologies.

When no pricing provider is configured, balances and token quantities remain available. USD portfolio values may be missing or may use a previously cached value when the configured cache policy allows it.

## Admission Requirements

A provider is ready for public use only when it has:

- Recorded success, empty-page, pagination, authentication, rate-limit, timeout, and malformed-response fixtures
- The shared `DataProvider` conformance suite
- Chain-by-chain capability tests
- Deterministic canonical output tests against at least one existing provider
- Documented API terms, request destinations, and known plan restrictions
- Safe removal and key deletion from Settings

The app must continue to work with one provider. Four configured providers improve choice and resilience, but they must never become a requirement.

## Opt-in Live Validation

Recorded fixtures remain the public CI requirement. Maintainers can separately run one bounded live page for native balance, token balances, wallet activity, and token history against every eligible provider. The selected chain controls which credentials are required. Robinhood runs the Alchemy and GoldRush adapters.

The local command requires explicit opt-in. This Ethereum example requires all four credentials:

```bash
export ORACLE41_RUN_LIVE_PROVIDER_VALIDATION=1
export ORACLE41_LIVE_TEST_CHAIN="ethereum"
export ORACLE41_LIVE_TEST_WALLET="0x..."
export ORACLE41_LIVE_TEST_TOKEN="0x..."
export ORACLE41_ALCHEMY_API_KEY="..."
export ORACLE41_ANKR_API_KEY="..."
export ORACLE41_MORALIS_API_KEY="..."
export ORACLE41_GOLDRUSH_API_KEY="..."
make validate-providers-live
```

Use only public test addresses. They are sent to each provider and may appear in provider account logs. The command does not print addresses, keys, request URLs, balances, activity, or raw transport errors. It returns `0` when all providers pass, `1` when a provider fails, and `2` when opt-in or configuration is missing.

Live provider credentials remain off GitHub-hosted runners. Normal pushes and pull requests use recorded fixtures only.
