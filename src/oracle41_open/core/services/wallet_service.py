from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from oracle41_open.core.models import (
    Chain,
    Token,
    TokenBalance,
    ValidationError,
    WalletOverviewResult,
)
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.token_filter_service import TokenFilterService, TokenFilterSettings
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.pricing_provider import PricingProvider


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...


class WalletService:
    def __init__(
        self,
        data_provider: DataProvider,
        pricing_provider: PricingProvider,
        cache_store: CacheStore | None = None,
        cache_ttl_seconds: int = 300,
        max_token_balance_pages: int = 20,
        token_filter_service: TokenFilterService | None = None,
    ) -> None:
        self._data_provider = data_provider
        self._pricing_provider = pricing_provider
        self._cache_store = cache_store
        self._cache_ttl_seconds = max(0, cache_ttl_seconds)
        self._max_token_balance_pages = max(1, max_token_balance_pages)
        self._token_filter_service = token_filter_service or TokenFilterService()

    def load_wallet_overview(
        self,
        address: str,
        chain: Chain,
        hide_unverified: bool = True,
        hide_dust: bool = False,
        dust_threshold_usd: str | Decimal = "1",
        force_refresh: bool = False,
    ) -> WalletOverviewResult:
        normalized_address = AddressValidator.normalized(address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError(
                AddressValidator.validation_error(normalized_address) or "Invalid wallet address."
            )
        filter_settings = TokenFilterSettings(
            hide_unverified=hide_unverified,
            hide_dust=hide_dust,
            dust_threshold_usd=self._parse_dust_threshold(dust_threshold_usd),
        )

        cache_key = self._cache_key(normalized_address, chain)
        if not force_refresh:
            cached = self._load_cached_result(cache_key)
            if cached is not None:
                return self._apply_token_filters(cached, settings=filter_settings)

        native_balance = self._data_provider.get_native_balance(normalized_address, chain)
        token_balances, page_count, is_truncated = self._load_all_token_balances(normalized_address, chain)
        native_price = self._pricing_provider.get_native_price(chain)
        token_prices = self._pricing_provider.get_token_prices(
            chain=chain,
            contract_addresses=[balance.token.contract_address for balance in token_balances],
        )

        enriched_balances = []
        for balance in token_balances:
            price = token_prices.get(balance.token.contract_address.lower())
            enriched_balances.append(
                balance if price is None else TokenBalance(
                    token=balance.token,
                    balance_decimal=balance.balance_decimal,
                    price_usd=price,
                )
            )

        token_total = sum(
            (balance.balance_usd for balance in enriched_balances if balance.balance_usd is not None),
            start=Decimal("0"),
        )
        native_total = (
            native_balance * native_price if native_price is not None else Decimal("0")
        )
        total_usd: Decimal | None = None
        if native_price is not None or any(balance.balance_usd is not None for balance in enriched_balances):
            total_usd = token_total + native_total

        result = WalletOverviewResult.now(
            native_balance=native_balance,
            native_price_usd=native_price,
            token_balances=enriched_balances,
            total_usd=total_usd,
            token_balance_page_count=page_count,
            token_balances_truncated=is_truncated,
        )
        self._save_cached_result(cache_key, result)
        return self._apply_token_filters(result, settings=filter_settings)

    def has_fresh_overview(self, address: str, chain: Chain) -> bool:
        normalized_address = AddressValidator.normalized(address)
        if not AddressValidator.is_valid(normalized_address):
            return False
        cache_key = self._cache_key(normalized_address, chain)
        return self._load_cached_result(cache_key) is not None

    def clear_cached_overview(self, address: str, chain: Chain) -> bool:
        normalized_address = AddressValidator.normalized(address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError(
                AddressValidator.validation_error(normalized_address) or "Invalid wallet address."
            )
        cache_key = self._cache_key(normalized_address, chain)
        return self._clear_cache_entry(cache_key)

    def _load_all_token_balances(
        self,
        address: str,
        chain: Chain,
    ) -> tuple[list[TokenBalance], int, bool]:
        results: list[TokenBalance] = []
        page_key: str | None = None
        page_count = 0
        is_truncated = False

        while True:
            page = self._data_provider.get_token_balances(address, chain, page_key=page_key)
            results.extend(page.balances)
            page_count += 1

            next_page_key = page.next_page_key
            if next_page_key is None:
                break
            if next_page_key == page_key:
                break
            if page_count >= self._max_token_balance_pages:
                is_truncated = True
                break
            page_key = next_page_key

        return results, page_count, is_truncated

    def _cache_key(self, address: str, chain: Chain) -> str:
        return f"wallet.overview.v1.{chain.value}.{address.lower()}.tp{self._max_token_balance_pages}"

    def _load_cached_result(self, cache_key: str) -> WalletOverviewResult | None:
        if self._cache_store is None:
            return None
        payload = self._cache_store.get(cache_key)
        return self._decode_cached_result(payload)

    def _save_cached_result(self, cache_key: str, result: WalletOverviewResult) -> None:
        if self._cache_store is None:
            return
        self._cache_store.set(
            cache_key,
            self._encode_cached_result(result),
            ttl_seconds=self._cache_ttl_seconds,
        )

    def _clear_cache_entry(self, cache_key: str) -> bool:
        if self._cache_store is None:
            return False
        remove = getattr(self._cache_store, "remove", None)
        if callable(remove):
            remove(cache_key)
            return True
        self._cache_store.set(cache_key, None, ttl_seconds=1)
        return True

    def _encode_cached_result(self, result: WalletOverviewResult) -> dict[str, object]:
        return {
            "version": 1,
            "native_balance": str(result.native_balance),
            "native_price_usd": str(result.native_price_usd) if result.native_price_usd is not None else None,
            "total_usd": str(result.total_usd) if result.total_usd is not None else None,
            "updated_at": result.updated_at.isoformat(),
            "token_balance_page_count": result.token_balance_page_count,
            "token_balances_truncated": result.token_balances_truncated,
            "token_balances": [self._encode_token_balance(balance) for balance in result.token_balances],
        }

    def _encode_token_balance(self, balance: TokenBalance) -> dict[str, object]:
        return {
            "contract_address": balance.token.contract_address,
            "symbol": balance.token.symbol,
            "name": balance.token.name,
            "decimals": balance.token.decimals,
            "is_verified": balance.token.is_verified,
            "balance_decimal": str(balance.balance_decimal),
            "price_usd": str(balance.price_usd) if balance.price_usd is not None else None,
        }

    def _decode_cached_result(self, raw: Any) -> WalletOverviewResult | None:
        if not isinstance(raw, dict):
            return None
        if raw.get("version") != 1:
            return None

        native_balance = _parse_decimal(raw.get("native_balance"))
        updated_at = _parse_datetime(raw.get("updated_at"))
        if native_balance is None or updated_at is None:
            return None

        token_balances_raw = raw.get("token_balances")
        if not isinstance(token_balances_raw, list):
            return None

        token_balances: list[TokenBalance] = []
        for entry in token_balances_raw:
            parsed = _decode_token_balance(entry)
            if parsed is None:
                return None
            token_balances.append(parsed)

        native_price_usd = _parse_optional_decimal(raw.get("native_price_usd"))
        total_usd = _parse_optional_decimal(raw.get("total_usd"))

        page_count_raw = raw.get("token_balance_page_count")
        if not isinstance(page_count_raw, int) or page_count_raw < 0:
            return None
        truncated_raw = raw.get("token_balances_truncated")
        if not isinstance(truncated_raw, bool):
            return None

        return WalletOverviewResult(
            native_balance=native_balance,
            native_price_usd=native_price_usd,
            token_balances=token_balances,
            total_usd=total_usd,
            updated_at=updated_at,
            token_balance_page_count=page_count_raw,
            token_balances_truncated=truncated_raw,
        )

    def _apply_token_filters(
        self,
        result: WalletOverviewResult,
        settings: TokenFilterSettings,
    ) -> WalletOverviewResult:
        filtered_balances = self._token_filter_service.filter_balances(
            balances=result.token_balances,
            settings=settings,
        )
        token_total = sum(
            (balance.balance_usd for balance in filtered_balances if balance.balance_usd is not None),
            start=Decimal("0"),
        )
        filtered_total: Decimal | None = None
        if result.native_price_usd is not None or any(balance.balance_usd is not None for balance in filtered_balances):
            native_total = (
                result.native_balance * result.native_price_usd
                if result.native_price_usd is not None
                else Decimal("0")
            )
            filtered_total = token_total + native_total
        return WalletOverviewResult(
            native_balance=result.native_balance,
            native_price_usd=result.native_price_usd,
            token_balances=filtered_balances,
            total_usd=filtered_total,
            updated_at=result.updated_at,
            token_balance_page_count=result.token_balance_page_count,
            token_balances_truncated=result.token_balances_truncated,
        )

    def _parse_dust_threshold(self, raw: str | Decimal) -> Decimal:
        try:
            threshold = Decimal(str(raw).strip())
        except (InvalidOperation, ValueError) as error:
            raise ValidationError("Invalid dust threshold USD value. Enter a numeric value.") from error
        if threshold < 0:
            raise ValidationError("Dust threshold USD cannot be negative.")
        return threshold


def _decode_token_balance(raw: Any) -> TokenBalance | None:
    if not isinstance(raw, dict):
        return None
    contract_address = raw.get("contract_address")
    symbol = raw.get("symbol")
    name = raw.get("name")
    decimals = raw.get("decimals")
    is_verified = raw.get("is_verified")
    balance_decimal = _parse_decimal(raw.get("balance_decimal"))
    price_usd = _parse_optional_decimal(raw.get("price_usd"))
    if not isinstance(contract_address, str):
        return None
    if not isinstance(symbol, str):
        return None
    if not isinstance(name, str):
        return None
    if not isinstance(decimals, int) or decimals < 0:
        return None
    if not isinstance(is_verified, bool):
        return None
    if balance_decimal is None:
        return None
    if raw.get("price_usd") is not None and price_usd is None:
        return None

    token = Token(
        contract_address=contract_address,
        symbol=symbol,
        name=name,
        decimals=decimals,
        is_verified=is_verified,
    )
    return TokenBalance(
        token=token,
        balance_decimal=balance_decimal,
        price_usd=price_usd,
    )


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


def _parse_optional_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    return _parse_decimal(raw)


def _parse_decimal(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
