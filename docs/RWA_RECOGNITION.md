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

## Current Limits

This release uses xStocks and Centrifuge for identity only. It does not use them as general market-price providers. Robinhood corporate actions and multiplier changes are stored locally; xStocks corporate-action and multiplier-history ingestion is planned separately.

## Official References

- [Robinhood Stock Token APIs](https://docs.robinhood.com/chain/stock-token-apis/)
- [xStocks API reference](https://docs.xstocks.fi/apis/openapi/assets)
- [xStocks developer guide](https://docs.xstocks.fi/developers)
- [Centrifuge deployments](https://docs.centrifuge.io/developer/protocol/deployments/)
