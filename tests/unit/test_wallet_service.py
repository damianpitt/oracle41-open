from __future__ import annotations

import time
from decimal import Decimal
from pathlib import Path

import pytest

from oracle41_open.core.models import Chain, Token, TokenBalance, TokenBalancePage, ValidationError
from oracle41_open.core.services.wallet_service import WalletService
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.providers.stub import StubDataProvider, StubPricingProvider
from oracle41_open.storage.cache_store import DiskCacheStore


def test_wallet_service_returns_enriched_totals() -> None:
    service = WalletService(
        data_provider=StubDataProvider(),
        pricing_provider=StubPricingProvider(),
    )

    result = service.load_wallet_overview(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert result.native_balance == Decimal("1.2345")
    assert result.native_price_usd == Decimal("3200.00")
    assert result.total_usd is not None
    assert len(result.token_balances) == 2
    assert all(balance.price_usd == Decimal("1") for balance in result.token_balances)
    assert result.token_balance_page_count == 1
    assert not result.token_balances_truncated


def test_wallet_service_uses_cache_when_fresh(tmp_path: Path) -> None:
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[_token_balance(index=1, symbol="AAA", balance="2")],
                next_page_key=None,
            )
        },
        native_balance=Decimal("5"),
    )
    pricing_provider = _FixedPricingProvider(
        native_price=Decimal("100"),
        token_prices={_address(1): Decimal("3")},
    )
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
        cache_store=DiskCacheStore(file_path=tmp_path / "cache.json"),
        cache_ttl_seconds=60,
    )

    first = service.load_wallet_overview(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )
    second = service.load_wallet_overview(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert data_provider.native_requests == 1
    assert data_provider.token_requests == [None]
    assert pricing_provider.native_requests == 1
    assert pricing_provider.token_requests == 1
    assert second.updated_at == first.updated_at
    assert service.has_fresh_overview(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )


def test_wallet_service_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[_token_balance(index=7, symbol="AAA", balance="2")],
                next_page_key=None,
            )
        },
        native_balance=Decimal("2"),
    )
    pricing_provider = _FixedPricingProvider(
        native_price=Decimal("100"),
        token_prices={_address(7): Decimal("1")},
    )
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
        cache_store=DiskCacheStore(file_path=tmp_path / "cache-force-refresh.json"),
        cache_ttl_seconds=60,
    )

    address = "0x7777777777777777777777777777777777777777"
    service.load_wallet_overview(address=address, chain=Chain.ETHEREUM)
    service.load_wallet_overview(address=address, chain=Chain.ETHEREUM, force_refresh=True)

    assert data_provider.native_requests == 2
    assert data_provider.token_requests == [None, None]
    assert pricing_provider.native_requests == 2
    assert pricing_provider.token_requests == 2


def test_wallet_service_clear_cached_overview_removes_cached_entry(tmp_path: Path) -> None:
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[_token_balance(index=8, symbol="BBB", balance="1")],
                next_page_key=None,
            )
        },
        native_balance=Decimal("1"),
    )
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=_FixedPricingProvider(
            native_price=Decimal("1"),
            token_prices={_address(8): Decimal("1")},
        ),
        cache_store=DiskCacheStore(file_path=tmp_path / "cache-clear-overview.json"),
        cache_ttl_seconds=60,
    )

    address = "0x8888888888888888888888888888888888888888"
    service.load_wallet_overview(address=address, chain=Chain.ETHEREUM)
    assert service.has_fresh_overview(address=address, chain=Chain.ETHEREUM)

    assert service.clear_cached_overview(address=address, chain=Chain.ETHEREUM)
    assert not service.has_fresh_overview(address=address, chain=Chain.ETHEREUM)
    service.load_wallet_overview(address=address, chain=Chain.ETHEREUM)

    assert data_provider.native_requests == 2
    assert data_provider.token_requests == [None, None]


def test_wallet_service_cache_expires_after_ttl(tmp_path: Path) -> None:
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[_token_balance(index=2, symbol="BBB", balance="1")],
                next_page_key=None,
            )
        }
    )
    pricing_provider = _FixedPricingProvider(native_price=Decimal("1"), token_prices={_address(2): Decimal("1")})
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
        cache_store=DiskCacheStore(file_path=tmp_path / "cache.json"),
        cache_ttl_seconds=1,
    )

    service.load_wallet_overview(
        address="0x1111111111111111111111111111111111111111",
        chain=Chain.ETHEREUM,
    )
    assert service.has_fresh_overview(
        address="0x1111111111111111111111111111111111111111",
        chain=Chain.ETHEREUM,
    )

    time.sleep(1.1)

    assert not service.has_fresh_overview(
        address="0x1111111111111111111111111111111111111111",
        chain=Chain.ETHEREUM,
    )


def test_wallet_service_sets_truncated_flag_when_page_cap_is_hit() -> None:
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[_token_balance(index=10, symbol="T0", balance="1")],
                next_page_key="p1",
            ),
            "p1": TokenBalancePage(
                balances=[_token_balance(index=11, symbol="T1", balance="1")],
                next_page_key="p2",
            ),
            "p2": TokenBalancePage(
                balances=[_token_balance(index=12, symbol="T2", balance="1")],
                next_page_key=None,
            ),
        },
    )
    pricing_provider = _FixedPricingProvider(native_price=None, token_prices={})
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
        max_token_balance_pages=2,
    )

    result = service.load_wallet_overview(
        address="0x2222222222222222222222222222222222222222",
        chain=Chain.ETHEREUM,
    )

    assert result.token_balance_page_count == 2
    assert result.token_balances_truncated
    assert len(result.token_balances) == 2
    assert data_provider.token_requests == [None, "p1"]


def test_wallet_service_stops_when_provider_repeats_page_key() -> None:
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[_token_balance(index=20, symbol="R0", balance="1")],
                next_page_key="p1",
            ),
            "p1": TokenBalancePage(
                balances=[_token_balance(index=21, symbol="R1", balance="1")],
                next_page_key="p1",
            ),
        },
    )
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=_FixedPricingProvider(native_price=None, token_prices={}),
        max_token_balance_pages=5,
    )

    result = service.load_wallet_overview(
        address="0x3333333333333333333333333333333333333333",
        chain=Chain.ETHEREUM,
    )

    assert result.token_balance_page_count == 2
    assert not result.token_balances_truncated
    assert len(result.token_balances) == 2
    assert data_provider.token_requests == [None, "p1"]


def test_wallet_service_hides_unverified_and_low_signal_tokens() -> None:
    verified = _token_balance(index=30, symbol="USDC", balance="2", verified=True, name="USD Coin")
    unverified = _token_balance(index=31, symbol="SPAM", balance="1000", verified=False, name="Spam Token")
    low_signal = _token_balance(index=32, symbol="UNKNOWN", balance="10", verified=True, name="Unknown")
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[verified, unverified, low_signal],
                next_page_key=None,
            )
        }
    )
    pricing_provider = _FixedPricingProvider(
        native_price=Decimal("0"),
        token_prices={
            _address(30): Decimal("1"),
            _address(31): Decimal("1"),
            _address(32): Decimal("1"),
        },
    )
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
    )

    result = service.load_wallet_overview(
        address="0x4444444444444444444444444444444444444444",
        chain=Chain.ETHEREUM,
        hide_unverified=True,
    )

    assert [balance.token.symbol for balance in result.token_balances] == ["USDC"]
    assert result.total_usd == Decimal("2")


def test_wallet_service_hides_dust_tokens_by_threshold() -> None:
    low_value = _token_balance(index=40, symbol="AAA", balance="0.5", verified=True, name="AAA")
    high_value = _token_balance(index=41, symbol="BBB", balance="5", verified=True, name="BBB")
    data_provider = _PagingDataProvider(
        pages_by_key={
            None: TokenBalancePage(
                balances=[low_value, high_value],
                next_page_key=None,
            )
        }
    )
    pricing_provider = _FixedPricingProvider(
        native_price=Decimal("0"),
        token_prices={
            _address(40): Decimal("1"),
            _address(41): Decimal("1"),
        },
    )
    service = WalletService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
    )

    result = service.load_wallet_overview(
        address="0x5555555555555555555555555555555555555555",
        chain=Chain.ETHEREUM,
        hide_unverified=False,
        hide_dust=True,
        dust_threshold_usd="1",
    )

    assert [balance.token.symbol for balance in result.token_balances] == ["BBB"]
    assert result.total_usd == Decimal("5")


def test_wallet_service_rejects_invalid_dust_threshold() -> None:
    service = WalletService(
        data_provider=_PagingDataProvider(
            pages_by_key={
                None: TokenBalancePage(
                    balances=[],
                    next_page_key=None,
                )
            }
        ),
        pricing_provider=_FixedPricingProvider(native_price=None, token_prices={}),
    )

    with pytest.raises(ValidationError):
        service.load_wallet_overview(
            address="0x6666666666666666666666666666666666666666",
            chain=Chain.ETHEREUM,
            hide_dust=True,
            dust_threshold_usd="not-a-number",
        )


class _PagingDataProvider:
    def __init__(
        self,
        pages_by_key: dict[str | None, TokenBalancePage],
        native_balance: Decimal = Decimal("0"),
    ) -> None:
        self._pages_by_key = pages_by_key
        self._native_balance = native_balance
        self.native_requests = 0
        self.token_requests: list[str | None] = []

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        _ = address
        _ = chain
        self.native_requests += 1
        return self._native_balance

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        _ = address
        _ = chain
        self.token_requests.append(page_key)
        return self._pages_by_key[page_key]


class _FixedPricingProvider(PricingProvider):
    def __init__(self, native_price: Decimal | None, token_prices: dict[str, Decimal]) -> None:
        self._native_price = native_price
        self._token_prices = {address.lower(): value for address, value in token_prices.items()}
        self.native_requests = 0
        self.token_requests = 0

    def get_native_price(self, chain: Chain) -> Decimal | None:
        _ = chain
        self.native_requests += 1
        return self._native_price

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        _ = chain
        self.token_requests += 1
        return {
            address.lower(): self._token_prices[address.lower()]
            for address in contract_addresses
            if address.lower() in self._token_prices
        }


def _address(index: int) -> str:
    return f"0x{index:040x}"


def _token_balance(
    index: int,
    symbol: str,
    balance: str,
    verified: bool = True,
    name: str | None = None,
) -> TokenBalance:
    return TokenBalance(
        token=Token(
            contract_address=_address(index),
            symbol=symbol,
            name=name or f"Token {symbol}",
            decimals=18,
            is_verified=verified,
        ),
        balance_decimal=Decimal(balance),
    )
