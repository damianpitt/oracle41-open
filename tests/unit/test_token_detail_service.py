"""Test token-specific history and approval loading.

The cases cover pagination, bounded scans, cache behavior, ledger reuse, and partial completeness.
They ensure approval values are not treated as token transfers.
"""

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
    CompletenessState,
    ValidationError,
)
from oracle41_open.core.services.token_detail_service import TokenDetailService
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.storage.cache_store import DiskCacheStore
from oracle41_open.storage.db import EventLedgerRepository, SQLiteDatabase


def test_token_detail_service_enriches_token_prices() -> None:
    data_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _item(
                        tx_hash="0x1",
                        log_index="0x0",
                        category=ActivityCategory.ERC20,
                        value_decimal=Decimal("10"),
                    )
                ],
                next_cursor=None,
            )
        }
    )
    pricing_provider = _MockPricingProvider(
        token_prices={"0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48": Decimal("1.25")}
    )
    service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=pricing_provider,
    )

    result = service.load_token_activity(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )

    assert not result.is_cached
    assert len(result.page.items) == 1
    assert result.page.items[0].value_usd == Decimal("12.50")
    assert data_provider.calls == [(None, True)]


def test_token_detail_service_uses_cache_for_same_cursor(tmp_path: Path) -> None:
    data_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(
                items=[_item(tx_hash="0x2", log_index="0x0", category=ActivityCategory.ERC20, value_decimal=Decimal("2"))],
                next_cursor=None,
            )
        }
    )
    service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
        cache_store=DiskCacheStore(file_path=tmp_path / "cache.json"),
        cache_ttl_seconds=60,
    )

    first = service.load_token_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
    )
    second = service.load_token_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
    )

    assert not first.is_cached
    assert second.is_cached
    assert first.updated_at == second.updated_at
    assert data_provider.calls == [(None, True)]


def test_token_detail_service_force_refresh_bypasses_cache(tmp_path: Path) -> None:
    data_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(
                items=[_item(tx_hash="0x2f", log_index="0x0", category=ActivityCategory.ERC20, value_decimal=Decimal("4"))],
                next_cursor=None,
            )
        }
    )
    service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
        cache_store=DiskCacheStore(file_path=tmp_path / "token-detail-force-refresh.json"),
        cache_ttl_seconds=60,
    )

    service.load_token_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
    )
    refreshed = service.load_token_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
        force_refresh=True,
    )

    assert not refreshed.is_cached
    assert data_provider.calls == [(None, True), (None, True)]


def test_token_detail_service_clear_cache_removes_cached_first_page(tmp_path: Path) -> None:
    data_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(
                items=[_item(tx_hash="0x2c", log_index="0x0", category=ActivityCategory.ERC20, value_decimal=Decimal("5"))],
                next_cursor=None,
            )
        }
    )
    service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
        cache_store=DiskCacheStore(file_path=tmp_path / "token-detail-clear-cache.json"),
        cache_ttl_seconds=60,
    )

    address = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    first = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
    )
    second = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
    )
    assert not first.is_cached
    assert second.is_cached

    assert service.clear_cached_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
    )
    third = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
    )

    assert not third.is_cached
    assert data_provider.calls == [(None, True), (None, True)]


def test_token_detail_service_passes_cursor_and_approvals_flag() -> None:
    data_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(items=[], next_cursor="cursor-2"),
            "cursor-2": ActivityPage(
                items=[_item(tx_hash="0x3", log_index="0x0", category=ActivityCategory.ERC20, value_decimal=Decimal("3"))],
                next_cursor=None,
            ),
        }
    )
    service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
    )

    first = service.load_token_activity(
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )
    second = service.load_token_activity(
        address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
        cursor=first.page.next_cursor,
        include_approvals=False,
    )

    assert first.page.next_cursor == "cursor-2"
    assert second.page.next_cursor is None
    assert data_provider.calls == [(None, False), ("cursor-2", False)]


def test_token_detail_service_validates_token_address() -> None:
    service = TokenDetailService(
        data_provider=_MockTokenDataProvider(pages={None: ActivityPage(items=[], next_cursor=None)}),
        pricing_provider=_MockPricingProvider(token_prices={}),
    )

    with pytest.raises(ValidationError):
        service.load_token_activity(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            token_address="0x1234",
            chain=Chain.ETHEREUM,
        )


def test_token_detail_service_caches_approvals_flag_separately(tmp_path: Path) -> None:
    data_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(
                items=[_item(tx_hash="0xap", log_index="0x0", category=ActivityCategory.ERC20, value_decimal=Decimal("2"))],
                next_cursor=None,
            )
        }
    )
    service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
        cache_store=DiskCacheStore(file_path=tmp_path / "token-detail-approvals-partition.json"),
        cache_ttl_seconds=60,
    )

    address = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"

    first_with_approvals = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )
    second_with_approvals = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )
    first_without_approvals = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )
    second_without_approvals = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )

    assert not first_with_approvals.is_cached
    assert second_with_approvals.is_cached
    assert not first_without_approvals.is_cached
    assert second_without_approvals.is_cached
    assert data_provider.calls == [(None, True), (None, False)]


def test_token_detail_service_clear_cache_is_scoped_by_approvals_flag(tmp_path: Path) -> None:
    data_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(
                items=[_item(tx_hash="0xscope", log_index="0x0", category=ActivityCategory.ERC20, value_decimal=Decimal("1"))],
                next_cursor=None,
            )
        }
    )
    service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
        cache_store=DiskCacheStore(file_path=tmp_path / "token-detail-clear-scope.json"),
        cache_ttl_seconds=60,
    )

    address = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )
    service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )

    assert service.clear_cached_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )

    with_approvals = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )
    without_approvals = service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )

    assert with_approvals.is_cached
    assert not without_approvals.is_cached
    assert data_provider.calls == [(None, True), (None, False), (None, False)]


def test_token_detail_service_restores_ledger_and_resumes_after_restart(tmp_path: Path) -> None:
    address = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    first_provider = _MockTokenDataProvider(
        pages={
            None: ActivityPage(
                items=[
                    _item(
                        tx_hash="0xapproval-new",
                        log_index="0x0",
                        category=ActivityCategory.APPROVAL,
                        value_decimal=Decimal("1"),
                    )
                ],
                next_cursor="older-approvals",
            )
        }
    )
    first_service = TokenDetailService(
        data_provider=first_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
        event_ledger=EventLedgerRepository(database),
    )

    initial = first_service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
    )

    resumed_provider = _MockTokenDataProvider(
        pages={
            "older-approvals": ActivityPage(
                items=[
                    _item(
                        tx_hash="0xapproval-old",
                        log_index="0x1",
                        category=ActivityCategory.APPROVAL,
                        value_decimal=Decimal("2"),
                    )
                ],
                next_cursor=None,
            )
        }
    )
    resumed_service = TokenDetailService(
        data_provider=resumed_provider,
        pricing_provider=_MockPricingProvider(token_prices={}),
        event_ledger=EventLedgerRepository(SQLiteDatabase(file_path=database.file_path)),
    )

    restored = resumed_service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
    )
    completed = resumed_service.load_token_activity(
        address=address,
        token_address=token,
        chain=Chain.ETHEREUM,
        cursor=restored.page.next_cursor,
    )

    assert initial.completeness is CompletenessState.PARTIAL
    assert restored.is_cached and restored.is_persisted
    assert restored.page.next_cursor == "older-approvals"
    assert resumed_provider.calls == [("older-approvals", True)]
    assert {item.tx_hash for item in completed.page.items} == {
        "0xapproval-new",
        "0xapproval-old",
    }
    assert completed.completeness is CompletenessState.COMPLETE


class _MockTokenDataProvider:
    def __init__(self, pages: dict[str | None, ActivityPage]) -> None:
        self._pages = pages
        self.calls: list[tuple[str | None, bool]] = []

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
        raise RuntimeError("Not used in token detail tests")

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        _ = address
        _ = chain
        _ = cursor
        _ = from_block
        raise RuntimeError("Not used in token detail tests")

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        _ = address
        _ = token_address
        _ = chain
        self.calls.append((cursor, include_approvals))
        return self._pages[cursor]


class _MockPricingProvider(PricingProvider):
    def __init__(self, token_prices: dict[str, Decimal]) -> None:
        self._token_prices = {key.lower(): value for key, value in token_prices.items()}

    def get_native_price(self, chain: Chain) -> Decimal | None:
        _ = chain
        return None

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        _ = chain
        return {
            address.lower(): self._token_prices[address.lower()]
            for address in contract_addresses
            if address.lower() in self._token_prices
        }


def _item(
    tx_hash: str,
    log_index: str,
    category: ActivityCategory,
    value_decimal: Decimal,
) -> ActivityItem:
    return ActivityItem(
        block_number=19_000_000,
        tx_hash=tx_hash,
        log_index=log_index,
        timestamp=datetime(2026, 1, 20, 12, 0, tzinfo=UTC),
        from_address="0x1111111111111111111111111111111111111111",
        to_address="0x2222222222222222222222222222222222222222",
        asset_symbol="USDC",
        contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        raw_value="0",
        value_decimal=value_decimal,
        value_usd=None,
        is_verified=True,
        category=category,
        chain=Chain.ETHEREUM,
    )
