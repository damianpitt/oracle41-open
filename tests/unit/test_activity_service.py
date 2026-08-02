from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    ValidationError,
)
from oracle41_open.core.services.activity_service import ActivityService
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.storage.cache_store import DiskCacheStore


def test_activity_service_enriches_prices_and_applies_filters() -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0x1",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 20, 10, 0, tzinfo=UTC),
                        category=ActivityCategory.ERC20,
                        symbol="USDC",
                        value_decimal=Decimal("10"),
                        contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    ),
                    _activity_item(
                        tx_hash="0x2",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 20, 9, 0, tzinfo=UTC),
                        category=ActivityCategory.EXTERNAL,
                        symbol="ETH",
                        value_decimal=Decimal("0.01"),
                        contract_address=None,
                    ),
                ],
                next_cursor=None,
            )
        }
    )
    pricing_provider = _MockPricingProvider(
        native_price=Decimal("2000"),
        token_prices={"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": Decimal("1.50")},
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
    )

    result = service.load_activity(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
        categories={ActivityCategory.ERC20},
        min_value_usd=Decimal("12"),
    )

    assert not result.is_cached
    assert len(result.page.items) == 1
    assert result.page.items[0].tx_hash == "0x1"
    assert result.page.items[0].value_usd == Decimal("15")
    assert data_provider.activity_requests == [(None, None)]


def test_activity_service_uses_cache_for_same_page(tmp_path: Path) -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0x3",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 20, 8, 0, tzinfo=UTC),
                        category=ActivityCategory.EXTERNAL,
                        symbol="ETH",
                        value_decimal=Decimal("0.5"),
                        contract_address=None,
                    )
                ],
                next_cursor=None,
            )
        }
    )
    pricing_provider = _MockPricingProvider(native_price=Decimal("2"), token_prices={})
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
        cache_store=DiskCacheStore(file_path=tmp_path / "cache.json"),
        cache_ttl_seconds=60,
    )

    first = service.load_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
    )
    second = service.load_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
    )

    assert not first.is_cached
    assert second.is_cached
    assert data_provider.activity_requests == [(None, None)]
    assert first.updated_at == second.updated_at


def test_activity_service_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0x4",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 20, 8, 30, tzinfo=UTC),
                        category=ActivityCategory.EXTERNAL,
                        symbol="ETH",
                        value_decimal=Decimal("0.2"),
                        contract_address=None,
                    )
                ],
                next_cursor=None,
            )
        }
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(native_price=Decimal("1000"), token_prices={}),
        cache_store=DiskCacheStore(file_path=tmp_path / "activity-force-refresh.json"),
        cache_ttl_seconds=60,
    )

    service.load_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
    )
    refreshed = service.load_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
        force_refresh=True,
    )

    assert not refreshed.is_cached
    assert data_provider.activity_requests == [(None, None), (None, None)]


def test_activity_service_clear_cache_removes_first_page_and_baseline(tmp_path: Path) -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0x6",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 20, 9, 15, tzinfo=UTC),
                        category=ActivityCategory.ERC20,
                        symbol="USDC",
                        value_decimal=Decimal("3"),
                        contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    )
                ],
                next_cursor=None,
            )
        }
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(
            native_price=None,
            token_prices={"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": Decimal("1")},
        ),
        cache_store=DiskCacheStore(file_path=tmp_path / "activity-clear-cache.json"),
        cache_ttl_seconds=60,
    )

    address = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    first = service.load_activity(address=address, chain=Chain.ETHEREUM)
    second = service.load_activity(address=address, chain=Chain.ETHEREUM)
    assert not first.is_cached
    assert second.is_cached

    assert service.clear_cached_activity(address=address, chain=Chain.ETHEREUM)
    third = service.load_activity(address=address, chain=Chain.ETHEREUM)

    assert not third.is_cached
    assert data_provider.activity_requests == [(None, None), (None, None)]


def test_activity_service_passes_cursor_to_provider() -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(items=[], next_cursor="cursor-2"),
            "cursor-2": ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0x5",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 21, 8, 0, tzinfo=UTC),
                        category=ActivityCategory.ERC20,
                        symbol="DAI",
                        value_decimal=Decimal("2"),
                        contract_address="0x6b175474e89094c44da98b954eedeac495271d0f",
                    )
                ],
                next_cursor=None,
            ),
        }
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(
            native_price=None,
            token_prices={"0x6b175474e89094c44da98b954eedeac495271d0f": Decimal("1")},
        ),
    )

    first = service.load_activity(
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        chain=Chain.ETHEREUM,
    )
    second = service.load_activity(
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        chain=Chain.ETHEREUM,
        cursor=first.page.next_cursor,
    )

    assert first.page.next_cursor == "cursor-2"
    assert second.page.next_cursor is None
    assert len(second.page.items) == 1
    assert second.page.items[0].value_usd == Decimal("2")
    assert data_provider.activity_requests == [(None, None), ("cursor-2", None)]


def test_activity_service_passes_from_block_and_partitions_cache(tmp_path: Path) -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0x10",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 21, 8, 30, tzinfo=UTC),
                        category=ActivityCategory.EXTERNAL,
                        symbol="ETH",
                        value_decimal=Decimal("0.2"),
                        contract_address=None,
                    )
                ],
                next_cursor=None,
            )
        }
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(native_price=Decimal("1"), token_prices={}),
        cache_store=DiskCacheStore(file_path=tmp_path / "activity-cache.json"),
        cache_ttl_seconds=60,
    )

    first = service.load_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
        from_block=100,
    )
    second = service.load_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
        from_block=100,
    )
    third = service.load_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
        from_block=200,
    )

    assert not first.is_cached
    assert second.is_cached
    assert not third.is_cached
    assert data_provider.activity_requests == [(None, 100), (None, 200)]


def test_activity_service_merges_cached_baseline_into_next_page(tmp_path: Path) -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0xbase",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 21, 12, 0, tzinfo=UTC),
                        category=ActivityCategory.ERC20,
                        symbol="USDC",
                        value_decimal=Decimal("5"),
                        contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    )
                ],
                next_cursor="cursor-2",
            ),
            "cursor-2": ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0xnext",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 21, 11, 30, tzinfo=UTC),
                        category=ActivityCategory.EXTERNAL,
                        symbol="ETH",
                        value_decimal=Decimal("0.1"),
                        contract_address=None,
                    )
                ],
                next_cursor=None,
            ),
        }
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(
            native_price=Decimal("2000"),
            token_prices={"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": Decimal("1")},
        ),
        cache_store=DiskCacheStore(file_path=tmp_path / "activity-cache-baseline.json"),
        cache_ttl_seconds=60,
    )

    first = service.load_activity(
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        chain=Chain.ETHEREUM,
    )
    second = service.load_activity(
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        chain=Chain.ETHEREUM,
        cursor=first.page.next_cursor,
    )

    assert not first.is_cached
    assert not second.is_cached
    assert {item.tx_hash for item in second.page.items} == {"0xbase", "0xnext"}
    assert data_provider.activity_requests == [(None, None), ("cursor-2", None)]


def test_activity_service_rejects_boolean_from_block() -> None:
    data_provider = _MockActivityDataProvider(
        pages={None: ActivityPage(items=[], next_cursor=None)}
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(native_price=None, token_prices={}),
    )

    with pytest.raises(ValidationError):
        service.load_activity(
            address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            chain=Chain.ETHEREUM,
            from_block=True,
        )


def test_activity_service_filtering_preserves_next_cursor() -> None:
    data_provider = _MockActivityDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _activity_item(
                        tx_hash="0xfiltered",
                        log_index="0x0",
                        timestamp=datetime(2026, 1, 21, 9, 0, tzinfo=UTC),
                        category=ActivityCategory.ERC20,
                        symbol="USDC",
                        value_decimal=Decimal("1"),
                        contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    )
                ],
                next_cursor="cursor-2",
            )
        }
    )
    service = ActivityService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(
            native_price=None,
            token_prices={"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": Decimal("1")},
        ),
    )

    result = service.load_activity(
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        chain=Chain.ETHEREUM,
        min_value_usd=Decimal("5"),
    )

    assert result.page.items == []
    assert result.page.next_cursor == "cursor-2"


class _MockActivityDataProvider:
    def __init__(self, pages: dict[str | None, ActivityPage]) -> None:
        self._pages = pages
        self.activity_requests: list[tuple[str | None, int | None]] = []

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        _ = address
        _ = chain
        return Decimal("0")

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> object:
        _ = address
        _ = chain
        _ = page_key
        raise RuntimeError("Not used in activity tests")

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        _ = address
        _ = chain
        self.activity_requests.append((cursor, from_block))
        return self._pages[cursor]


class _MockPricingProvider(PricingProvider):
    def __init__(self, native_price: Decimal | None, token_prices: dict[str, Decimal]) -> None:
        self._native_price = native_price
        self._token_prices = {key.lower(): value for key, value in token_prices.items()}

    def get_native_price(self, chain: Chain) -> Decimal | None:
        _ = chain
        return self._native_price

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        _ = chain
        return {
            address.lower(): self._token_prices[address.lower()]
            for address in contract_addresses
            if address.lower() in self._token_prices
        }


def _activity_item(
    tx_hash: str,
    log_index: str,
    timestamp: datetime,
    category: ActivityCategory,
    symbol: str,
    value_decimal: Decimal,
    contract_address: str | None,
) -> ActivityItem:
    return ActivityItem(
        block_number=19_000_000,
        tx_hash=tx_hash,
        log_index=log_index,
        timestamp=timestamp,
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x2222222222222222222222222222222222222222",
        asset_symbol=symbol,
        contract_address=contract_address,
        raw_value="0",
        value_decimal=value_decimal,
        value_usd=None,
        is_verified=True,
        category=category,
        chain=Chain.ETHEREUM,
    )
