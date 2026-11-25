# Historical Holdings Inputs

Version `0.5.0a3` adds block identity and timing checks to the read-only audit for one wallet and one ERC-20 token between two blocks. It compares contract balance reads with saved transfer evidence. It is not a complete historical portfolio, accounting ledger, or P&L report.

## Use the Screen

1. Synchronize the relevant Activity or Token Detail history. Report collection does not download missing transfers.
2. If you want a USD estimate, save suitable observations in **Historical Prices** first. Holdings collection does not fetch prices.
3. Open **Historical Holdings** and choose the network, wallet, and exact token contract.
4. Enter an opening and closing block. These are end-of-block balances. Replay includes transfers after the opening block through the closing block, including the closing block itself.
5. Choose the maximum prior-price age. New reports derive their price lookup time from the closing block header; you do not enter a separate time.
6. Optionally enable **Require closing block to be reported finalized by the endpoint**. Select **Collect Block Balances and Audit**. Review the hashes, timing, finality label, balance difference, exclusions, and warnings.
7. Use **Find Saved Reports** to reopen the latest 50 report entries for the same wallet, network, and token. This requires no block inputs, credentials, or network access. Exports use the displayed report's original inputs, not edited controls.
8. Use **Check Saved Block References** for an explicit online comparison with current numbered headers. The original report stays unchanged. The latest check can be viewed offline and exported with **Export Latest Check JSON**.

Collection needs an enabled Alchemy or Ankr endpoint, or a custom JSON-RPC endpoint that supports historical state and canonical hash-based `eth_call` through EIP-1898. Availability depends on the network, node software, retention, and provider plan. Oracle41 does not assume every configured endpoint supports this request form. Moralis and GoldRush can supply indexed wallet history but do not supply this contract-read path. Restart after changing provider settings. No new provider key is introduced.

## Opening and Closing Balances

The collector checks `eth_chainId` against the network registry, then loads the numbered opening and closing headers. Each saved header includes its number, hash, parent hash, UTC timestamp, source name, and observation time.

It calls `balanceOf(wallet)` and `decimals()` with `{"blockHash": "…", "requireCanonical": true}` at each endpoint. It never assumes a missing opening balance is zero. Reads must keep the chain, contract, number, hash, and source context. Different decimals across the two blocks are rejected because a single-unit replay would be ambiguous.

After the state reads and finality lookup, the collector loads the numbered endpoint headers again and checks the chain ID again. Changed hashes, parents, or timestamps stop collection. Adjacent endpoints must share the expected parent link. Reversed or future timestamps are rejected. A configured provider supplies the entire operation; an ordinary failure can retry the whole operation on another provider. An observed reference change or explicit non-canonical-hash rejection stops instead of being hidden by failover. Unsupported hash reads never fall back to block-number state reads.

These checks bind state to provider-observed block identity. They do not independently verify consensus, compare multiple providers, check every ancestor between distant endpoints, or prevent a reorganization after collection. Existing protocol collectors are not changed to hash-pinned reads by this slice.

## Finality Labels

The collector requests the endpoint's `safe` and `finalized` headers and checks their numbered identities. A missing or failed tag stays explicitly unavailable, with only a safe error type saved. Remote error text and private endpoint URLs are not report inputs.

| Label | Meaning |
| --- | --- |
| `finalized_by_endpoint` | The closing height is at or below the observed finalized head |
| `safe_by_endpoint` | The closing height is at or below the safe head, without a finalized claim |
| `not_reported_safe` | At least one head was available, but neither reported the closing height safe or finalized |
| `unknown` | Neither tag was available; no finality claim is made |

These are endpoint assertions, not independent consensus or L1 settlement proofs. This distinction matters on L2 networks. The optional finalized requirement rejects a collection unless its label is `finalized_by_endpoint`; it does not change the meaning of that label. Without this option, an unknown or not-yet-safe block remains a clearly labeled observation.

## Saved Transfer Replay

The local capture reads events, receipt status, query checkpoints, and saved fees in one SQLite read transaction. It does not join query scopes into the movement list, so one event saved by Activity and Token Detail is not repeated.

- Only matching ERC-20 movements affect the token balance. Approval values are allowances, and NFT rows are not fungible token amounts.
- Incoming movements add raw units; outgoing movements subtract them. Self-transfers have net zero effect. Zero-address counterparties allow ordinary mint and burn directions.
- Replay uses raw integers rather than rounded provider decimal amounts. Decimal disagreements are flagged.
- Transaction hash and normalized log index identify a canonical log. Identical aliases are deduplicated. Conflicting aliases are all excluded.
- Missing block positions, malformed values, invalid participants, failed receipts, and receipt/event block disagreement are retained as exclusions.
- Logs without saved receipts remain provider observations. They are not presented as independently receipt-verified evidence.
- Negative projected balances remain negative and are flagged. They are not clamped to zero. Running checks use block-end totals, not a guessed transaction order within each block.

One capture is limited to 20,000 candidate token rows and 20,000 saved fee rows. Exceeding a limit stops collection rather than silently truncating evidence. Unknown-block rows are retained as exclusions, so some malformed histories need repair rather than just a narrower range.

## Reconciliation and Coverage

`matches_saved_transfers` means the opening read plus usable saved deltas equals the closing read, with no excluded candidate ERC-20 rows, negative block-end projections, or decimal disagreements. It does not prove complete intermediate activity: missing incoming and outgoing events can offset each other.

`unreconciled` means the endpoint comparison or usable evidence needs investigation. The report preserves the raw difference. Missing events, rebasing, fee-on-transfer mechanics, changed history, or inconsistent source data may explain a difference; Oracle41 does not choose a cause automatically.

Every report sets `is_complete_history` to false. Saved checkpoints retain their scopes, block windows, cursors, and provider status as context only. Even a checkpoint labeled complete is not proof of complete history for this report's exact token and interval.

## Fees and Prices

Saved fees paid by this wallet in the interval remain native-asset context. Fees on failed transactions still count when their saved units and block are usable. The sum is deduplicated by the ledger's chain/transaction key. Fees with unknown block or wrong asset units are excluded from the known sum but remain in the input document.

No saved fee rows means an unknown fee total, not zero. The sum is not claimed to cover all transactions, is not priced in USD here, and is never deducted from ERC-20 holdings. Native-asset holdings need their own reconstruction in later work.

The report uses M7.1's prior-or-exact saved price lookup at the closing header's timestamp, with an age limit and a collection-time knowledge cutoff. It pins the fetch ID, provider timestamp, source reference, price text, and eligibility state. Replay checks those times and the saved block-timing links again before using the price. Missing, stale, or later points do not fall back to today's price or a stablecoin peg. An actual zero quote remains valid evidence.

The optional USD figure multiplies the observed closing contract balance by the eligible saved quote. It does not value the transfer projection, add transaction income, or calculate profit. Daily prices are samples, not exact execution prices, so the result remains an estimate even when balances reconcile. Existing Portfolio totals are unchanged.

## Frozen Reports and Exports

Each collection gets a new report ID and stores the captured inputs and derived results as one JSON document. A failed insert does not replace older reports. Ledger and price revisions do not rewrite an earlier report. SQLite schema version 17 adds a separate append-only reference-check table linked to the existing reports. Migration preserves saved reports, and database backups include both tables automatically.

The core `analyze_holdings_inputs` function can reproduce the derived result from the frozen inputs without network access. Reports are immutable through the repository API, not tamper-proof against someone editing the local database directly.

New report exports use `oracle41-historical-holdings`, version 2. JSON contains the full report. CSV separates the summary, balance anchors with headers, timing, finality, price input, raw ledger rows, checkpoint context, applied movements, exclusions, and fee evidence into typed rows. Nested details are JSON in a cell; raw integers and decimal prices remain text. API keys and private endpoint URLs are not report inputs.

### Later Reference Checks

A check compares the saved opening and closing references with current headers from one selected endpoint. It rechecks those current headers and records current finality separately. `references_match_at_check` means the references agreed during that check, not that they can never change. `changed_reference` means a hash, parent, or timestamp differs; reorganization or endpoint disagreement needs investigation. The check retains both saved and current evidence and the current source name.

Every successful check receives a new ID. Earlier checks remain in SQLite, while the screen loads the latest one. Checks export separately as `oracle41-block-reference-check`, version 1. They never change the original report, valuation, saved events, or prices. Failed checks do not erase earlier evidence. This slice detects reference differences; it does not roll back or repair the canonical event ledger automatically.

### Older Reports

Reports created in `0.5.0a2` keep format version 1, their original replay results, and their original user-supplied-time and block-number warnings. They remain readable and exportable offline. A check returns `legacy_no_hash_references` rather than adding hashes that were not observed during the original collection. Collect a new report to obtain the stronger references; an old report is never silently upgraded.

This slice does not reconstruct native balances, NFT inventory, protocol positions, nested vault ownership, or a complete wallet net worth. It does not classify accounting lots, calculate P&L or returns, modify imported history, or execute transactions. Daily portfolio history still needs day-boundary selection, coverage controls, snapshots, retention, and series reporting before broader totals can be claimed.

The request form follows [EIP-1898](https://eips.ethereum.org/EIPS/eip-1898). Header and chain-ID methods are documented in the [Ethereum JSON-RPC reference](https://ethereum.org/developers/docs/apis/json-rpc/). Live access and network-specific finality behavior require separate validation; public tests use synthetic responses and no real keys.
