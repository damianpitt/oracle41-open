# Historical Holdings Inputs

Version `0.5.0a2` adds a read-only audit for one wallet and one ERC-20 token between two blocks. It compares contract balance reads with saved transfer evidence. It is not a complete historical portfolio, accounting ledger, or P&L report.

## Use the Screen

1. Synchronize the relevant Activity or Token Detail history. Report collection does not download missing transfers.
2. If you want a USD estimate, save suitable observations in **Historical Prices** first. Holdings collection does not fetch prices.
3. Open **Historical Holdings** and choose the network, wallet, and exact token contract.
4. Enter an opening and closing block. These are end-of-block balances. Replay includes transfers after the opening block through the closing block, including the closing block itself.
5. Enter a price lookup time with `Z` or a time-zone offset and choose the maximum price age. This time is your input; it is not verified against the closing block header.
6. Select **Collect Block Balances and Audit**. Review the balance difference, exclusions, price evidence, and warnings.
7. Use **Find Saved Reports** to reopen the latest 50 report entries for the same wallet, network, and token. This requires no block inputs, credentials, or network access. Exports use the displayed report's original inputs, not edited controls.

Collection needs an enabled Alchemy or Ankr endpoint, or a custom JSON-RPC endpoint that can read the requested historical state. Availability depends on the network, node retention, and provider plan. Moralis and GoldRush can supply indexed wallet history but do not supply this contract-read path. Restart after changing provider settings. No new provider key is introduced.

## Opening and Closing Balances

The collector calls `balanceOf(wallet)` and `decimals()` at each explicit block. It never assumes a missing opening balance is zero. Successful reads must retain the requested chain, contract, block, and source-provider context. Different decimals across the two blocks are rejected because a single-unit replay would be ambiguous.

The reads use block numbers, not hash-pinned references. The report does not verify finality, block timestamps, or agreement between independent providers. Chain reorganizations or endpoint configuration mistakes remain possible. The saved source name and observation times explain where the inputs came from, not a proof that the source is correct.

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

The report uses M7.1's prior-or-exact saved price lookup with an age limit and a collection-time knowledge cutoff. It pins the fetch ID, provider timestamp, source reference, price text, and eligibility state. Replay checks those times again before using the price. Missing, stale, or later points do not fall back to today's price or a stablecoin peg. An actual zero quote remains valid evidence.

The optional USD figure multiplies the observed closing contract balance by the eligible saved quote. It does not value the transfer projection, add transaction income, or calculate profit. Because the requested price time is not block-verified and daily prices are samples, the result is labeled an estimate even when balances reconcile. Existing Portfolio totals are unchanged.

## Frozen Reports and Exports

SQLite schema version 16 adds a separate report table. Each collection gets a new report ID and stores the captured inputs and derived results as one JSON document. A failed insert does not replace older reports. Ledger and price revisions do not rewrite an earlier report, and the existing database backup includes these reports automatically.

The core `analyze_holdings_inputs` function can reproduce the derived result from the frozen inputs without network access. Reports are immutable through the repository API, not tamper-proof against someone editing the local database directly.

Both exports use `oracle41-historical-holdings`, version 1. JSON contains the full report. CSV separates the summary, balance anchors, price input, raw ledger rows, checkpoint context, applied movements, exclusions, and fee evidence into typed rows. Nested details are JSON in a cell; raw integers and decimal prices remain text. API keys and private endpoint URLs are not report inputs.

This slice does not reconstruct native balances, NFT inventory, protocol positions, nested vault ownership, or a complete wallet net worth. It does not classify accounting lots, calculate P&L or returns, modify imported history, or execute transactions. Daily portfolio history must add suitable timing and coverage controls before reporting broader totals.
