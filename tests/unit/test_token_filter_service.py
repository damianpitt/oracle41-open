"""Test low-signal and dust token filtering.

The cases cover verification, metadata quality, suspicious symbols, balances, prices, and reason messages.
They keep filtering predictable and explainable.
"""

from __future__ import annotations

from decimal import Decimal

from oracle41_open.core.models import Token, TokenBalance
from oracle41_open.core.services.token_filter_service import TokenFilterService, TokenFilterSettings


def test_token_filter_service_hides_unverified_and_low_signal_tokens() -> None:
    balances = [
        _balance(index=1, symbol="USDC", name="USD Coin", verified=True, balance="10", price="1"),
        _balance(index=2, symbol="SPAM", name="Spam Token", verified=False, balance="500", price="0.001"),
        _balance(index=3, symbol="UNKNOWN", name="Unknown", verified=True, balance="12", price="0.5"),
    ]
    service = TokenFilterService()

    filtered = service.filter_balances(
        balances=balances,
        settings=TokenFilterSettings(
            hide_unverified=True,
            hide_dust=False,
            dust_threshold_usd=Decimal("0"),
        ),
    )

    assert [item.token.symbol for item in filtered] == ["USDC"]


def test_token_filter_service_hides_dust_by_usd_threshold() -> None:
    balances = [
        _balance(index=10, symbol="AAA", name="Token AAA", verified=True, balance="0.5", price="1"),
        _balance(index=11, symbol="BBB", name="Token BBB", verified=True, balance="2", price="1"),
        _balance(index=12, symbol="CCC", name="Token CCC", verified=True, balance="4", price=None),
    ]
    service = TokenFilterService()

    filtered = service.filter_balances(
        balances=balances,
        settings=TokenFilterSettings(
            hide_unverified=False,
            hide_dust=True,
            dust_threshold_usd=Decimal("1"),
        ),
    )

    assert [item.token.symbol for item in filtered] == ["BBB", "CCC"]


def _balance(
    index: int,
    symbol: str,
    name: str,
    verified: bool,
    balance: str,
    price: str | None,
) -> TokenBalance:
    return TokenBalance(
        token=Token(
            contract_address=f"0x{index:040x}",
            symbol=symbol,
            name=name,
            decimals=18,
            is_verified=verified,
        ),
        balance_decimal=Decimal(balance),
        price_usd=None if price is None else Decimal(price),
    )
