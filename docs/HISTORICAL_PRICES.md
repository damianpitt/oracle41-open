# Historical Price Evidence

Version `0.5.0a1` starts M7 with saved daily USD price observations for exact token contracts. This is a foundation for historical analytics, not a complete accounting release. It does not change current wallet or Portfolio valuation.

## Use the Screen

1. Open **Historical Prices** and choose the network.
2. Enter the token contract address, not a wallet address or ticker.
3. Choose a start and end day. Both days use UTC and the end day is included. One request can cover up to 30 days.
4. Select **Fetch Daily Prices** to make an explicit Alchemy request, or **Load Saved Range** to reopen the newest saved response for exactly those dates.
5. Export the displayed fetch as CSV or JSON. The report includes its fetch ID so the same evidence can be reopened later.

Fetching requires an enabled Alchemy provider and its existing key. Restart after changing provider settings. Saved reads, exports, and lookups work without credentials. Opening the screen does not fetch data. A failed refresh keeps the displayed saved fetch intact, and changing the token or dates clears the old display.

Collection is enabled for the existing Ethereum, Optimism, Polygon, Base, and Arbitrum pricing-network mappings. Token coverage, historical depth, and access depend on Alchemy. Robinhood generic historical pricing is not enabled. Ankr, Moralis, and GoldRush do not supply this new pricing path. Native assets, ticker-based requests, NFT prices, and issuer-specific Stock Token history are outside this slice.

## Source and Identity

The adapter uses Alchemy's [Historical Token Prices endpoint](https://www.alchemy.com/docs/data/prices-api/prices-api-endpoints/prices-api-endpoints/get-historical-token-prices) with a network, contract address, time range, and daily interval. It checks the echoed network, address, and USD currency before accepting any points. It never falls back to a ticker, another chain, a stablecoin peg, or a current quote.

Times must include a time zone and are normalized to UTC. Decimal amounts are parsed without passing through binary floating point. Invalid values, conflicting duplicate timestamps, and out-of-range observations reject the response. Repeated identical points are deduplicated. Missing days remain gaps. An empty successful response is saved as empty evidence; it does not mean the token was worth zero.

The API receives the token contract, network, and requested times, not a wallet address. Keys and credential-bearing request URLs are not part of saved evidence, exports, or application error messages. Normal tests use synthetic responses and no real credentials.

## Lookup Policy

Enter an ISO lookup time with `Z` or an explicit offset, choose a maximum prior-price age in seconds, and select **Find Saved Price at Time**. This reads SQLite only. The default maximum age is 86,400 seconds.

| Result | Meaning |
| --- | --- |
| `exact` | A saved provider timestamp equals the requested time |
| `prior` | The newest available point before that time is within the age limit |
| `stale` | A prior point exists but is too old; its value is not authorized for use |
| `missing` | No saved point exists at or before that time |

The policy never picks a later provider timestamp, interpolates a gap, or substitutes today's price. An actual zero quote remains distinct from missing data. A timestamp match does not make a daily sample a trade execution price or prove when the source formed that price. This slice does not assume a candle close, exact execution-time value, or complete market history.

By default, lookup uses the latest saved revision at the selected provider timestamp. Core callers can supply `known_by` to restrict which fetch times are eligible. Reports that need reproducibility should pin the fetch ID and observation timestamp, not rely on a later default lookup choosing the same revision.

## Storage and Revisions

SQLite schema version 15 adds fetch and observation tables. Each request, source, fetch time, and all its observations commit in one transaction. Failure rolls back the whole new fetch. Older data remains intact. Every refresh gets a new ID, even for the same dates, so a provider correction does not overwrite earlier evidence.

The existing SQLite backup includes this history and its fetch IDs. No key is stored with it. Loading a saved range makes no network request. An empty newer fetch remains visibly empty when loaded; earlier observations are still available by their fetch IDs and may still qualify for a lookup under its age policy. The application does not interpret an empty response as a retraction of earlier prices.

The core service exposes `get_fetch(fetch_id)` to reopen pinned evidence. Source records are append-only through the repository, not tamper-proof: anyone with direct write access to the local database can alter it. Storage retention and deletion controls will be addressed separately rather than silently pruning accounting inputs.

## Export Format

Both formats use `oracle41-historical-prices`, version 1. They include the chain, contract, USD currency, interval, requested range, source reference, fetch time, fetch ID, point count, and scope. Prices remain decimal text. CSV includes a `fetch_status` row when a fetch is empty, with no invented price.

An export describes one saved fetch, not complete token history, a wallet balance, profit, or a tax report. Existing activity, Portfolio, and RWA export versions are unchanged.

## What Comes Next

M7.2 uses this evidence in a bounded [ERC-20 holdings-input report](HISTORICAL_HOLDINGS.md), including opening balances, saved transfer replay, fee context, and price gaps. M7.3A adds hash-pinned endpoint reads and closing-header-derived price time; older reports retain their original timing limits. Neither slice claims complete historical holdings. Daily portfolio series, coverage controls, P&L and lots, returns, and accounting overrides follow in separate tested slices. M6 protocol and network expansion remains possible in parallel.
