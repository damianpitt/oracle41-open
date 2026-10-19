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
3. Select **Refresh Audit History** to request the current official catalog and corporate actions. Refresh is available when live providers are configured and respects public catalog cache windows. Published records remain visible if the request fails.
4. For xStocks, **Pause Sync** stops after the current page. **Resume Audit Sync** continues from the saved page after a pause, failure, or application restart. **Start New Sync** discards pending pages and starts again; it does not delete published history.
5. Select **Export Audit CSV** or **Export Audit JSON** to export published local records and the current sync status. Pending record payloads are not exported as published history.

Reports use `oracle41-rwa-audit` format version 3. They include the chain, exact contract, issuer IDs, source references, action details, multiplier values, and observation times. Decimal values are written as text to preserve precision. CSV action details are JSON objects within a cell. xStocks reports also include revision numbers, original statuses, previous multipliers, reasons, issuer activation times, and the last successful fetch time.

Robinhood reports use scope `all_locally_saved_records`. Its multiplier observations start when Oracle41 first sees a contract; corporate-action rows retain their latest state and first/last observation times. xStocks reports use scope `published_locally_saved_records`. Multiplier records come from the issuer's historical feed and keep activation times. Previously published action revisions remain available. A report does not establish that every historical issuer action has been collected.

Sync metadata includes status, run ID, start and update times, next pages, completed feeds, pages saved, and staged record counts. These columns are present in both formats, with empty values where tracking does not apply. Before the first completed xStocks sync, the last successful fetch is empty. JSON then has no published items; CSV includes a `sync_status` row so progress is still available. A completed run has no pending checkpoint, so its pending counters and run ID are empty.

## xStocks History

xStocks action history uses pages starting at 1. Multiplier history uses pages starting at 0 and includes the selected network in each request. Oracle41 checks every continuation and saves each page with its resume point. Each run requests at most 20 pages of 100 records across both feeds. Larger histories continue in later runs without repeating completed pages. Saved progress is readable offline.

Pending pages remain separate until both feeds finish. Publication and pending-checkpoint removal share one transaction, so interrupted publication keeps the previous history and the resume point. Duplicate issuer records are deduplicated; conflicting evidence stops the sync without changing published records.

The issuer's Initial, Corrected, Cancelled, and Scheduled action states are preserved. A corrected revision does not erase its earlier revision. Raw cashflow, withholding, notes, ISIN, and other supplied fields remain available in details and exports.

On EVM networks, xStocks token balances already include rebasing. Multiplier history is shown for inspection and is never applied again to wallet balances. Wrapped deployments remain tied to their exact catalog addresses; this release does not estimate redemption amounts.

## Current Limits

This release uses xStocks for identity and issuer history, and Centrifuge for reviewed identity. Neither is a general market-price provider. Centrifuge fund-history collection remains future work. Robinhood audit collection keeps its existing refresh behavior; the new page checkpoints apply to xStocks.

xStocks endpoints use page numbers rather than a fixed snapshot cursor. Issuer changes during collection can shift page contents. A completed sync means both feeds reached their reported end; it does not prove a time-locked snapshot or independently complete issuer coverage. **Start New Sync** revisits earlier pages while retaining previously published revisions. Pause takes effect between requests and does not interrupt an HTTP request already in flight.

## Official References

- [Robinhood Stock Token APIs](https://docs.robinhood.com/chain/stock-token-apis/)
- [xStocks API reference](https://docs.xstocks.fi/apis/openapi/assets)
- [xStocks developer guide](https://docs.xstocks.fi/developers)
- [xStocks corporate-action API](https://docs.xstocks.fi/apis/openapi/corporate-actions)
- [Centrifuge deployments](https://docs.centrifuge.io/developer/protocol/deployments/)
