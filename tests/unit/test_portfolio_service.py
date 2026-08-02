from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from oracle41_open.core.models import (
    Chain,
    Token,
    TokenBalance,
    WalletOverviewResult,
    WatchlistEntry,
)
from oracle41_open.core.services.portfolio_service import PortfolioService


def test_portfolio_service_aggregates_wallet_totals_and_tokens() -> None:
    entry_a = _entry(
        entry_id=1,
        address="0x1111111111111111111111111111111111111111",
        chain=Chain.ETHEREUM,
        label="A",
    )
    entry_b = _entry(
        entry_id=2,
        address="0x2222222222222222222222222222222222222222",
        chain=Chain.ETHEREUM,
        label="B",
    )
    watchlist = _MockWatchlistReader([entry_a, entry_b])
    loader = _MockWalletOverviewLoader(
        {
            entry_a.address: _overview(
                native_balance=Decimal("1"),
                native_price=Decimal("2000"),
                total_usd=Decimal("2300"),
                token_balances=[
                    _token_balance(
                        contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                        symbol="USDC",
                        name="USD Coin",
                        decimals=6,
                        balance=Decimal("100"),
                        price=Decimal("1"),
                    )
                ],
            ),
            entry_b.address: _overview(
                native_balance=Decimal("0.5"),
                native_price=Decimal("2000"),
                total_usd=Decimal("1400"),
                token_balances=[
                    _token_balance(
                        contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                        symbol="USDC",
                        name="USD Coin",
                        decimals=6,
                        balance=Decimal("300"),
                        price=Decimal("1"),
                    ),
                    _token_balance(
                        contract="0x6b175474e89094c44da98b954eedeac495271d0f",
                        symbol="DAI",
                        name="Dai",
                        decimals=18,
                        balance=Decimal("100"),
                        price=Decimal("1"),
                    ),
                ],
            ),
        }
    )
    service = PortfolioService(watchlist_reader=watchlist, wallet_loader=loader)

    result = service.load_portfolio(hide_unverified=True, hide_dust=False, dust_threshold_usd="1")

    assert result.selected_wallet_count == 2
    assert result.loaded_wallet_count == 2
    assert result.failed_wallet_count == 0
    assert result.truncated_wallet_count == 0
    assert result.wallets_missing_total_usd_count == 0
    assert result.total_usd == Decimal("3700")
    assert result.known_total_usd == Decimal("3700")
    assert len(result.chain_aggregates) == 1
    assert result.chain_aggregates[0].chain is Chain.ETHEREUM
    assert result.chain_aggregates[0].native_balance_total == Decimal("1.5")
    assert result.chain_aggregates[0].native_usd_total == Decimal("3000")

    by_symbol = {aggregate.symbol: aggregate for aggregate in result.token_aggregates}
    assert by_symbol["USDC"].wallet_count == 2
    assert by_symbol["USDC"].total_balance == Decimal("400")
    assert by_symbol["USDC"].total_usd == Decimal("400")
    assert by_symbol["DAI"].wallet_count == 1
    assert by_symbol["DAI"].total_balance == Decimal("100")

    assert loader.calls[0]["hide_unverified"] is True
    assert loader.calls[0]["hide_dust"] is False
    assert loader.calls[0]["dust_threshold_usd"] == "1"


def test_portfolio_service_handles_failures_missing_totals_and_truncation() -> None:
    entry_ok = _entry(
        entry_id=1,
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.BASE,
        label="ok",
    )
    entry_fail = _entry(
        entry_id=2,
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        chain=Chain.BASE,
        label="fail",
    )
    watchlist = _MockWatchlistReader([entry_ok, entry_fail])
    loader = _MockWalletOverviewLoader(
        {
            entry_ok.address: _overview(
                native_balance=Decimal("2"),
                native_price=None,
                total_usd=None,
                token_balances=[],
                token_balances_truncated=True,
            ),
            entry_fail.address: RuntimeError("provider unavailable"),
        }
    )
    service = PortfolioService(watchlist_reader=watchlist, wallet_loader=loader)

    result = service.load_portfolio()

    assert result.selected_wallet_count == 2
    assert result.loaded_wallet_count == 1
    assert result.failed_wallet_count == 1
    assert result.truncated_wallet_count == 1
    assert result.wallets_missing_total_usd_count == 1
    assert result.total_usd is None
    assert result.known_total_usd == Decimal("0")
    assert any(wallet.error == "provider unavailable" for wallet in result.wallet_results)


def test_portfolio_service_filters_by_chain_and_selected_ids() -> None:
    entry_eth = _entry(
        entry_id=1,
        address="0x1111111111111111111111111111111111111111",
        chain=Chain.ETHEREUM,
        label="eth",
    )
    entry_base = _entry(
        entry_id=2,
        address="0x2222222222222222222222222222222222222222",
        chain=Chain.BASE,
        label="base",
    )
    watchlist = _MockWatchlistReader([entry_eth, entry_base])
    loader = _MockWalletOverviewLoader(
        {
            entry_eth.address: _overview(
                native_balance=Decimal("1"),
                native_price=Decimal("2000"),
                total_usd=Decimal("2000"),
                token_balances=[],
            ),
            entry_base.address: _overview(
                native_balance=Decimal("1"),
                native_price=Decimal("2000"),
                total_usd=Decimal("2000"),
                token_balances=[],
            ),
        }
    )
    service = PortfolioService(watchlist_reader=watchlist, wallet_loader=loader)

    result = service.load_portfolio(chain=Chain.ETHEREUM, selected_entry_ids=[2, 1])

    assert result.selected_wallet_count == 1
    assert result.loaded_wallet_count == 1
    assert result.wallet_results[0].entry.id == 1
    assert [call["address"] for call in loader.calls] == [entry_eth.address]


def test_portfolio_service_total_is_partial_when_any_wallet_fails() -> None:
    entry_ok = _entry(
        entry_id=1,
        address="0x1111111111111111111111111111111111111111",
        chain=Chain.ETHEREUM,
        label="ok",
    )
    entry_fail = _entry(
        entry_id=2,
        address="0x2222222222222222222222222222222222222222",
        chain=Chain.ETHEREUM,
        label="fail",
    )
    watchlist = _MockWatchlistReader([entry_ok, entry_fail])
    loader = _MockWalletOverviewLoader(
        {
            entry_ok.address: _overview(
                native_balance=Decimal("1"),
                native_price=Decimal("2000"),
                total_usd=Decimal("2500"),
                token_balances=[],
            ),
            entry_fail.address: RuntimeError("provider timeout"),
        }
    )
    service = PortfolioService(watchlist_reader=watchlist, wallet_loader=loader)

    result = service.load_portfolio()

    assert result.loaded_wallet_count == 1
    assert result.failed_wallet_count == 1
    assert result.total_usd is None
    assert result.known_total_usd == Decimal("2500")


class _MockWatchlistReader:
    def __init__(self, entries: list[WatchlistEntry]) -> None:
        self._entries = entries

    def list_entries(self, chain: Chain | None = None) -> list[WatchlistEntry]:
        if chain is None:
            return list(self._entries)
        return [entry for entry in self._entries if entry.chain is chain]


class _MockWalletOverviewLoader:
    def __init__(self, plans_by_address: dict[str, WalletOverviewResult | Exception]) -> None:
        self._plans_by_address = plans_by_address
        self.calls: list[dict[str, object]] = []

    def load_wallet_overview(
        self,
        address: str,
        chain: Chain,
        hide_unverified: bool = True,
        hide_dust: bool = False,
        dust_threshold_usd: str | Decimal = "1",
        force_refresh: bool = False,
    ) -> WalletOverviewResult:
        self.calls.append(
            {
                "address": address,
                "chain": chain,
                "hide_unverified": hide_unverified,
                "hide_dust": hide_dust,
                "dust_threshold_usd": dust_threshold_usd,
                "force_refresh": force_refresh,
            }
        )
        plan = self._plans_by_address[address]
        if isinstance(plan, Exception):
            raise plan
        return plan


def _entry(entry_id: int, address: str, chain: Chain, label: str | None) -> WatchlistEntry:
    return WatchlistEntry(
        id=entry_id,
        address=address,
        chain=chain,
        label=label,
        created_at=datetime(2026, 3, 5, 10, 0, tzinfo=UTC),
    )


def _token_balance(
    contract: str,
    symbol: str,
    name: str,
    decimals: int,
    balance: Decimal,
    price: Decimal | None,
) -> TokenBalance:
    return TokenBalance(
        token=Token(
            contract_address=contract,
            symbol=symbol,
            name=name,
            decimals=decimals,
            is_verified=True,
        ),
        balance_decimal=balance,
        price_usd=price,
    )


def _overview(
    native_balance: Decimal,
    native_price: Decimal | None,
    total_usd: Decimal | None,
    token_balances: list[TokenBalance],
    token_balances_truncated: bool = False,
) -> WalletOverviewResult:
    return WalletOverviewResult(
        native_balance=native_balance,
        native_price_usd=native_price,
        token_balances=token_balances,
        total_usd=total_usd,
        updated_at=datetime(2026, 3, 5, 9, 0, tzinfo=UTC),
        token_balance_page_count=1,
        token_balances_truncated=token_balances_truncated,
    )
