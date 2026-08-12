"""Test price loading and stale-cache policy.

The cases cover fresh prices, cache reuse, stale fallback, expiry, and provider errors.
They keep price quality visible to calling services.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from oracle41_open.core.models import Chain, ProviderError
from oracle41_open.core.services.pricing_service import PricingService
from oracle41_open.providers.pricing_provider import PricingProvider


def test_pricing_service_falls_back_to_cached_native_quote_on_provider_error() -> None:
    provider = _FakePricingProvider(
        native_plan=[Decimal("3200"), ProviderError("provider unavailable")],
        token_plan=[],
        simple_price_plan=[],
    )
    now_holder = [datetime(2026, 3, 3, 10, 0, tzinfo=UTC)]
    service = PricingService(
        pricing_provider=provider,
        cache_store=_MemoryCache(),
        max_stale_age_seconds=300,
        now_func=lambda: now_holder[0],
    )

    first = service.get_native_price(Chain.ETHEREUM)
    now_holder[0] = now_holder[0] + timedelta(seconds=30)
    second = service.get_native_price(Chain.ETHEREUM)

    assert first == Decimal("3200")
    assert second == Decimal("3200")


def test_pricing_service_returns_none_when_cached_native_quote_is_too_stale() -> None:
    provider = _FakePricingProvider(
        native_plan=[Decimal("3000"), ProviderError("provider unavailable")],
        token_plan=[],
        simple_price_plan=[],
    )
    now_holder = [datetime(2026, 3, 3, 10, 0, tzinfo=UTC)]
    service = PricingService(
        pricing_provider=provider,
        cache_store=_MemoryCache(),
        max_stale_age_seconds=60,
        now_func=lambda: now_holder[0],
    )

    assert service.get_native_price(Chain.ETHEREUM) == Decimal("3000")
    now_holder[0] = now_holder[0] + timedelta(seconds=120)
    assert service.get_native_price(Chain.ETHEREUM) is None


def test_pricing_service_reuses_cached_token_quotes_for_missing_live_entries() -> None:
    addr1 = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    addr2 = "0xdac17f958d2ee523a2206206994597c13d831ec7"
    provider = _FakePricingProvider(
        native_plan=[],
        token_plan=[
            {addr1: Decimal("1.00")},
            ProviderError("provider unavailable"),
        ],
        simple_price_plan=[],
    )
    service = PricingService(
        pricing_provider=provider,
        cache_store=_MemoryCache(),
        max_stale_age_seconds=300,
    )

    first = service.get_token_prices(Chain.ETHEREUM, [addr1, addr2])
    second = service.get_token_prices(Chain.ETHEREUM, [addr1, addr2])

    assert first == {addr1: Decimal("1.00")}
    assert second == {addr1: Decimal("1.00")}


def test_pricing_service_uses_symbol_fallback_for_polygon_native_price() -> None:
    provider = _FakePricingProvider(
        native_plan=[None],
        token_plan=[],
        simple_price_plan=[{"POL": Decimal("0.75")}],
    )
    service = PricingService(
        pricing_provider=provider,
        cache_store=_MemoryCache(),
        max_stale_age_seconds=300,
    )

    price = service.get_native_price(Chain.POLYGON)

    assert price == Decimal("0.75")
    assert provider.simple_price_requests == [["MATIC", "POL"]]


def test_pricing_service_does_not_hide_unexpected_provider_errors() -> None:
    provider = _FakePricingProvider(
        native_plan=[RuntimeError("implementation defect")],
        token_plan=[],
        simple_price_plan=[],
    )
    service = PricingService(pricing_provider=provider)

    with pytest.raises(RuntimeError, match="implementation defect"):
        service.get_native_price(Chain.ETHEREUM)


class _MemoryCache:
    def __init__(self) -> None:
        self._storage: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._storage.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        _ = ttl_seconds
        self._storage[key] = value


class _FakePricingProvider(PricingProvider):
    def __init__(
        self,
        native_plan: list[Decimal | None | Exception],
        token_plan: list[dict[str, Decimal] | Exception],
        simple_price_plan: list[dict[str, Decimal] | Exception],
    ) -> None:
        self._native_plan = native_plan
        self._token_plan = token_plan
        self._simple_price_plan = simple_price_plan
        self.simple_price_requests: list[list[str]] = []

    def get_native_price(self, chain: Chain) -> Decimal | None:
        _ = chain
        if not self._native_plan:
            return None
        next_item = self._native_plan.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        _ = chain
        _ = contract_addresses
        if not self._token_plan:
            return {}
        next_item = self._token_plan.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item

    def get_simple_prices(self, ids: list[str]) -> dict[str, Decimal]:
        self.simple_price_requests.append(ids)
        if not self._simple_price_plan:
            return {}
        next_item = self._simple_price_plan.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item
