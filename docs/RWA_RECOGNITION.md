# Real-World Asset Recognition

Oracle41 identifies supported tokenized real-world assets by network and exact contract address. A familiar name or ticker is not proof because anyone can deploy a token with copied metadata.

## Sources

| Source | Identity method | Supported Oracle41 networks |
| --- | --- | --- |
| Robinhood Stock Token API | Live asset catalog with chain ID and contract | Robinhood Chain |
| xStocks public API v2 | Live asset catalog with token and wrapper deployments | Ethereum, Optimism, Polygon, Base, Arbitrum |
| Centrifuge deployment registry | Reviewed contracts shipped with the application | Ethereum, Optimism, Base, Arbitrum |

The live catalogs are used only when live analytics are enabled. The xStocks catalog is cached for one hour. The Centrifuge list works offline but needs a code update when its official deployment registry changes.

## Categories

Oracle41 currently uses these display categories:

- Robinhood Stock Token
- Tokenized equity
- Tokenized ETF
- Tokenized fund

These labels describe the source catalog. They are not legal, tax, risk, or investment advice. The source issuer's documents remain authoritative.

## Safety Rules

- Match both the selected chain and the complete contract address.
- Do not classify from a name, symbol, logo, or wallet-provider label.
- Keep the source name and public reference with the identity.
- Leave unknown contracts unclassified.
- Treat wrapper contracts as separate exact deployments when the issuer catalog publishes them.

## Browse and Export Saved History

In Token Detail, choose the chain, enter a token contract address, and select **Open RWA Audit History**. The history window works without entering a wallet address. It supports Robinhood Stock Tokens on Robinhood Chain and verified xStocks deployments on Ethereum, Optimism, Polygon, Base, and Arbitrum.

1. Select **Load Saved History** to read all records saved on this computer. This works offline.
2. Browse **Corporate Actions** and **Multiplier Observations** for Robinhood, or **Action Revisions** and **Issuer Multiplier History** for xStocks. Select a row for its full details and source.
3. Select **Refresh Audit History** to request the current official catalog and corporate actions. Refresh is available when live providers are configured and respects public catalog cache windows. Saved records remain visible if the request fails.
4. Select **Export Audit CSV** or **Export Audit JSON** to export all loaded local records.

Reports use `oracle41-rwa-audit` format version 2. They include the chain, exact contract, issuer IDs, source references, action details, multiplier values, and observation times. Decimal values are written as text to preserve precision. CSV action details are JSON objects within a cell. xStocks reports also include revision numbers, original statuses, previous multipliers, reasons, issuer activation times, and the last successful fetch time.

The report scope is `all_locally_saved_records`. Robinhood multiplier observations start when Oracle41 first sees a contract; its corporate-action rows retain their latest state and first/last observation times. xStocks multiplier records come from the issuer's historical feed and keep activation times. All fetched xStocks action revisions remain available. A report does not establish that every historical issuer action has been collected.

## xStocks History

xStocks action history uses pages starting at 1. Multiplier history uses pages starting at 0 and includes the selected network in each request. Oracle41 checks every continuation and saves a refresh only after both feeds finish validation. Duplicate issuer IDs are deduplicated; conflicting evidence rolls back the refresh.

The issuer's Initial, Corrected, Cancelled, and Scheduled action states are preserved. A corrected revision does not erase its earlier revision. Raw cashflow, withholding, notes, ISIN, and other supplied fields remain available in details and exports.

On EVM networks, xStocks token balances already include rebasing. Multiplier history is shown for inspection and is never applied again to wallet balances. Wrapped deployments remain tied to their exact catalog addresses; this release does not estimate redemption amounts.

## Current Limits

This release uses xStocks for identity and issuer history, and Centrifuge for reviewed identity. Neither is a general market-price provider. xStocks refresh reads up to 20 pages of 100 records per feed. Larger histories report a limit and keep the prior saved history. Large-history resume and Centrifuge fund-history collection are future work.

## Official References

- [Robinhood Stock Token APIs](https://docs.robinhood.com/chain/stock-token-apis/)
- [xStocks API reference](https://docs.xstocks.fi/apis/openapi/assets)
- [xStocks developer guide](https://docs.xstocks.fi/developers)
- [xStocks corporate-action API](https://docs.xstocks.fi/apis/openapi/corporate-actions)
- [Centrifuge deployments](https://docs.centrifuge.io/developer/protocol/deployments/)
