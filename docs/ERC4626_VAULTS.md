# ERC-4626 Vault Positions

Oracle41 can explain directly owned shares in reviewed ERC-4626 vaults. It reads the vault at one selected block, then shows the underlying asset represented by those shares. It does not deposit, withdraw, approve, or sign transactions.

## Reviewed Deployment

| Network | Vault | Share Contract | Expected Underlying |
| --- | --- | --- | --- |
| Ethereum | Savings DAI (sDAI) | `0x83f20f44975d03b1b09e64809b757c47f942beea` | DAI, `0x6b175474e89094c44da98b954eedeac495271d0f` |

The share deployment was checked against the [official Savings DAI repository](https://github.com/sky-ecosystem/sdai#deployments) on 2026-10-23. Every collection also checks that `asset()` returns the expected underlying contract. A different asset is rejected rather than silently relabeled.

The registry is a reviewed list in the code, not a scan of every wallet token. New entries require official deployment references and fixtures. A token on another network is not treated as a vault merely because its address or symbol looks familiar. Bridged share tokens may not expose a vault interface on their destination network.

## Read a Position

1. Add the Ethereum wallet to the watchlist and open Portfolio.
2. Select Ethereum and **Exact stored block**. Enter a block where the vault was deployed.
3. Select **Refresh Protocol Snapshot**. This refreshes supported protocols, including the configured vault.
4. Read the Savings DAI underlying position, raw share balance, share decimals, conversion method, redemption preview, and redemption limit.
5. Reopen saved snapshots offline, or use the **Protocol Positions** CSV/JSON export template.

Collection uses the existing transaction JSON-RPC pool. Alchemy, Ankr, or a custom endpoint must support contract reads at the requested block. Availability depends on node retention and account capabilities. Selecting Moralis or GoldRush as a wallet-data source does not give it this JSON-RPC role. Missing historical state is reported as an error, not replaced with current state.

## What the Values Mean

All calls use the same network, block, contract, and provider context. Amounts remain raw integers in storage. Share and underlying decimals are read independently.

| Read | Meaning |
| --- | --- |
| `asset()` | Underlying ERC-20 contract; must match the reviewed entry |
| `balanceOf(wallet)` | Shares owned directly by the wallet |
| Share and asset `decimals()` | Separate display units for the two tokens |
| `convertToAssets(shares)` | Underlying accounting value before withdrawal fees or limits |
| `previewRedeem(shares)` | Separate redemption estimate that may reflect fees but does not check owner limits |
| `maxRedeem(wallet)` | Maximum shares the contract reports as redeemable for this owner |

These meanings follow [ERC-4626](https://eips.ethereum.org/EIPS/eip-4626). Conversion is not a guaranteed withdrawal quote. A preview does not prove that redemption is available, and none of these reads guarantees future liquidity or contract safety. A restricted redemption limit is shown as a warning without replacing the accounting amount.

## Storage and Portfolio Accounting

Finished results reuse the existing protocol snapshot tables, keyed by wallet, chain, protocol, and block. No database migration is required; schema version 14 remains current. Loading a finished snapshot makes no provider calls. Explicit refresh replaces it only after collection and normalization finish. Failed mandatory reads leave a saved result untouched. This small collector does not add per-call resume checkpoints; an interrupted vault read restarts that vault, while finished snapshots remain reusable.

The underlying appears as one supplied position. Shares, conversion values, preview values, limits, and the registry source remain in its metadata. Shares are not added as a second priced protocol asset.

When a converted underlying asset is included, the matching receipt token is removed from wallet totals. If conversion is unavailable, the position has no fabricated underlying amount, remains partial, and the wallet receipt token is retained. A valid zero conversion is distinct from an unavailable conversion. Zero shares produce no position.

USD valuation uses current available prices, not a historical price at the snapshot block. No stablecoin peg is assumed. Missing prices, stale observations, and partial results keep Portfolio totals incomplete. Exact-block protocol data is still combined with current wallet balances, so it is a mixed-time estimate.

The liquidation-health state is `not_applicable`: the collector does not measure a borrowing account. This is not a claim that the vault has no risk. Contract failures, missing reads, and redemption restrictions remain separate warnings.

## Exports and Limits

Portfolio format version 5 adds `position_metadata` to protocol-position reports. JSON keeps it as a named object. CSV stores it as JSON in one cell. Raw shares, conversion values, and redemption checks stay integer text. Other adapters can use the same field without adding vault-specific columns to every report.

An unavailable conversion has no normalized underlying asset row. Its partial status and warnings remain in the protocol-risk report, and the original evidence remains in SQLite. A supplied row does not imply a successful withdrawal simulation.

This slice does not discover arbitrary vaults, recursively decompose nested vaults, find shares held by other contracts, calculate yield or profit, collect rewards, or execute transactions. Additional vaults and networks need separate reviewed entries and tests.
