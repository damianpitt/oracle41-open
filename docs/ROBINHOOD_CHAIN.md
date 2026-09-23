# Robinhood Chain Support

Oracle41 Open supports read-only wallet and transaction analytics for Robinhood Chain mainnet, chain ID `4663`.

## Available Data

- Alchemy and GoldRush provide indexed wallet balances and history.
- Alchemy and custom JSON-RPC endpoints provide transaction receipts and contract reads.
- Blockscout provides optional explorer and verified-contract context.
- Robinhood's public read-only API provides official Stock Token metadata and current USD quotes.
- Ankr and Moralis are not used because their supported-chain lists do not currently include Robinhood Chain.
- Generic token-by-address pricing and protocol positions are not enabled yet.

## Stock Token Identity and Pricing

Oracle41 accepts a contract as an official Robinhood Stock Token only when the `/assets` catalog lists that exact address for chain ID `4663`. A matching symbol alone is not enough.

The `/prices/{symbol}` endpoint publishes the underlying equity's raw USD bid and ask. These values are not adjusted for corporate actions. Oracle41 reads `currentMultiplier` from the matched asset and calculates:

`token price = underlying price x current shares per token`

Portfolio valuation uses the midpoint of the adjusted token bid and ask. Token Detail keeps the raw bid and ask, adjusted midpoint, quote time, active or inactive state, trading-halt state, and current multiplier visible. A pending multiplier is shown with its effective time but is not applied before it becomes current.

The asset catalog is cached for one hour. Quotes are cached for 15 seconds. A newly fetched quote more than five minutes old, or more than one minute in the future, is rejected before valuation. Recent previously accepted values can still follow the application's configured stale-price policy when the API is temporarily unavailable.

This is an analytics estimate, not an executable trade quote. WETH, USDG, unknown ERC-20 contracts, NFTs, and protocol positions require other pricing sources.

## Transaction Fees

Robinhood Chain uses ETH for gas and settles data to Ethereum. A paid transaction fee contains:

- An L2 execution fee for work performed on Robinhood Chain.
- An L1 data fee for publishing transaction data to Ethereum.

Both parts are paid together. Oracle41 always reports the bundled total from `gasUsed × effectiveGasPrice`.

Some Nitro RPC receipts also include `gasUsedForL1`. When that field is present, Oracle41 separates the total into L2 execution and L1 data components. When it is missing, Oracle41 reports `total_only` instead of estimating either component.

The receipt field is stored in SQLite so a cached transaction keeps the same fee evidence after restart.

## Canonical Bridge Intelligence

Robinhood Chain uses the standard Arbitrum canonical bridge between Ethereum and Robinhood Chain. Oracle41 recognizes these lifecycle facts:

| Current transaction | Observation |
| --- | --- |
| Ethereum deposit event | L1 to L2 deposit initiated |
| Robinhood deposit event | L1 to L2 deposit finalized |
| Robinhood withdrawal event | L2 to L1 withdrawal initiated |
| Ethereum withdrawal event | L2 to L1 withdrawal finalized |
| Robinhood ArbSys `sendTxToL1` call | L2 to L1 message initiated |

A bridge result requires two pieces of local evidence:

1. A known Arbitrum bridge call or event signature.
2. A contract address published in Robinhood's official protocol-contract registry.

This prevents a contract with a similar event name from being presented as the canonical bridge.

Each result keeps the direction, observed stage, source and destination chains, sender, recipient, token, raw amount, message ID when available, contract address, and source call or log. The same result appears as a normalized `bridge` wallet action.

One transaction proves only one stage. A deposit initiation does not prove that its L2 transaction succeeded, and a withdrawal initiation does not prove that the L1 claim was completed. Cross-chain matching is not included yet.

Partner bridges such as LayerZero, Chainlink CCIP, Relay, Across, LiFi, and 0x use different contracts and message identifiers. Oracle41 does not classify them as canonical bridge activity.

## Sources

- [Robinhood Chain connection details](https://docs.robinhood.com/chain/connecting/)
- [Robinhood Chain gas and fees](https://docs.robinhood.com/chain/gas-and-fees/)
- [Robinhood Chain bridging](https://docs.robinhood.com/chain/bridging/)
- [Robinhood Chain protocol contracts](https://docs.robinhood.com/chain/protocol-contracts/)
- [Robinhood Chain cross-chain messaging](https://docs.robinhood.com/chain/cross-chain-messaging/)
- [Robinhood Stock Token APIs](https://docs.robinhood.com/chain/stock-token-apis/)
- [Robinhood Stock Token overview](https://docs.robinhood.com/chain/stock-tokens/)
