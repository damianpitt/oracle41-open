"""Load token prices with cache and stale-value support.

Fresh provider prices are cached, while recent saved prices can be returned when a provider is temporarily unavailable.
The result states whether a value is live, cached, stale, or missing.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from oracle41_open.core.models import Chain, ProviderError
from oracle41_open.providers.pricing_provider import PricingProvider


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...


@dataclass(frozen=True)
class _CachedQuote:
    value: Decimal
    updated_at: datetime


class PricingService(PricingProvider):
    def __init__(
        self,
        pricing_provider: PricingProvider,
        cache_store: CacheStore | None = None,
        max_stale_age_seconds: int = 86_400,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._pricing_provider = pricing_provider
        self._cache_store = cache_store
        self._max_stale_age_seconds = max(0, max_stale_age_seconds)
        self._now = now_func or (lambda: datetime.now(tz=UTC))

    def get_native_price(self, chain: Chain) -> Decimal | None:
        cache_key = self._native_cache_key(chain)
        cached = self._load_quote(cache_key)

        live_quote: Decimal | None = None
        try:
            live_quote = self._pricing_provider.get_native_price(chain)
        except ProviderError:
            live_quote = None

        if live_quote is None:
            live_quote = self._load_native_symbol_fallback(chain)
        if live_quote is not None:
            self._save_quote(cache_key, live_quote)
            return live_quote
        return self._stale_or_none(cached)

    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        normalized_addresses = sorted(
            {
                address.strip().lower()
                for address in contract_addresses
                if isinstance(address, str) and address.strip().startswith("0x") and len(address.strip()) == 42
            }
        )
        if not normalized_addresses:
            return {}

        cached_by_address = {
            address: self._load_quote(self._token_cache_key(chain, address))
            for address in normalized_addresses
        }

        live_quotes: dict[str, Decimal] = {}
        try:
            provider_quotes = self._pricing_provider.get_token_prices(chain=chain, contract_addresses=normalized_addresses)
        except ProviderError:
            provider_quotes = {}
        for address, quote in provider_quotes.items():
            normalized = address.strip().lower()
            if normalized not in cached_by_address:
                continue
            live_quotes[normalized] = quote

        result: dict[str, Decimal] = {}
        for address in normalized_addresses:
            live_quote = live_quotes.get(address)
            if live_quote is not None:
                result[address] = live_quote
                self._save_quote(self._token_cache_key(chain, address), live_quote)
                continue

            stale_quote = self._stale_or_none(cached_by_address.get(address))
            if stale_quote is not None:
                result[address] = stale_quote
        return result

    def _load_native_symbol_fallback(self, chain: Chain) -> Decimal | None:
        symbol_client = getattr(self._pricing_provider, "get_simple_prices", None)
        if not callable(symbol_client):
            return None
        fallback_symbols = _native_symbol_candidates(chain)
        if not fallback_symbols:
            return None
        try:
            quotes = symbol_client(fallback_symbols)
        except ProviderError:
            return None
        if not isinstance(quotes, dict):
            return None
        for symbol in fallback_symbols:
            value = quotes.get(symbol.upper())
            if isinstance(value, Decimal):
                return value
        return None

    def _native_cache_key(self, chain: Chain) -> str:
        return f"pricing.native.v1.{chain.value}"

    def _token_cache_key(self, chain: Chain, contract_address: str) -> str:
        return f"pricing.token.v1.{chain.value}.{contract_address.lower()}"

    def _load_quote(self, cache_key: str) -> _CachedQuote | None:
        if self._cache_store is None:
            return None
        payload = self._cache_store.get(cache_key)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != 1:
            return None

        value = _parse_decimal(payload.get("value"))
        updated_at = _parse_datetime(payload.get("updated_at"))
        if value is None or updated_at is None:
            return None
        return _CachedQuote(value=value, updated_at=updated_at)

    def _save_quote(self, cache_key: str, value: Decimal) -> None:
        if self._cache_store is None:
            return
        payload = {
            "version": 1,
            "value": str(value),
            "updated_at": self._now().isoformat(),
        }
        self._cache_store.set(cache_key, payload, ttl_seconds=self._max_stale_age_seconds)

    def _stale_or_none(self, cached: _CachedQuote | None) -> Decimal | None:
        if cached is None:
            return None
        age_seconds = (self._now() - cached.updated_at).total_seconds()
        if age_seconds < 0:
            return cached.value
        if age_seconds > self._max_stale_age_seconds:
            return None
        return cached.value


def _native_symbol_candidates(chain: Chain) -> list[str]:
    if chain is Chain.POLYGON:
        return ["MATIC", "POL"]
    return []


def _parse_decimal(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
