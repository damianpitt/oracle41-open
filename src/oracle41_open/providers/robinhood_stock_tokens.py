"""Load Robinhood Stock Token metadata and multiplier-aware USD prices.

The public Robinhood endpoints require no credential. Contract addresses must appear in both the asset catalog and quote
deployment before a price is accepted. Raw underlier bid and ask values are multiplied by the current shares-per-token
multiplier, while pending corporate-action multipliers are preserved but never applied early.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import quote

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    CorporateActionStatus,
    CorporateActionType,
    ProviderError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    StockTokenCorporateAction,
    StockTokenMetadata,
    StockTokenQuote,
    StockTokenStatus,
)
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
)
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.providers.retry import retry_with_backoff

_BASE_URL = "https://api.robinhood.com/rhj"
_SOURCE_NAME = "robinhood-stock-token-api"
_ASSET_CACHE_KEY = "robinhood.stock.assets.v1"
_CORPORATE_ACTION_CACHE_KEY = "robinhood.stock.corporate-actions.v1"
_ROBINHOOD_CHAIN_ID = 4663


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...


class RobinhoodStockTokenPricingProvider(PricingProvider):
    """Supplement an existing provider with official Robinhood Stock Token prices."""

    def __init__(
        self,
        fallback_provider: PricingProvider,
        http_client: HTTPClient | None = None,
        cache_store: CacheStore | None = None,
        base_url: str = _BASE_URL,
        asset_cache_ttl_seconds: int = 3_600,
        quote_cache_ttl_seconds: int = 15,
        quote_max_age_seconds: int = 300,
        quote_max_future_skew_seconds: int = 60,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 0.25,
        retry_backoff_multiplier: float = 2.0,
        retry_max_delay_seconds: float = 2.0,
        sleep_func: Callable[[float], None] | None = None,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._fallback_provider = fallback_provider
        self._http_client = http_client or HTTPClient()
        self._cache_store = cache_store
        self._base_url = base_url.rstrip("/")
        self._asset_cache_ttl_seconds = max(0, asset_cache_ttl_seconds)
        self._quote_cache_ttl_seconds = max(0, quote_cache_ttl_seconds)
        self._quote_max_age_seconds = max(0, quote_max_age_seconds)
        self._quote_max_future_skew_seconds = max(0, quote_max_future_skew_seconds)
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep
        self._now = now_func or (lambda: datetime.now(tz=UTC))

    def get_native_price(self, chain: Chain) -> Decimal | None:
        """Keep native-asset pricing with the configured primary provider."""

        return self._fallback_provider.get_native_price(chain)

    def get_token_prices(
        self,
        chain: Chain,
        contract_addresses: list[str],
    ) -> dict[str, Decimal]:
        normalized_addresses = _normalized_addresses(contract_addresses)
        if not normalized_addresses:
            return {}
        if chain is not Chain.ROBINHOOD:
            return self._fallback_provider.get_token_prices(chain, normalized_addresses)

        metadata_by_address = self._metadata_by_address()
        result: dict[str, Decimal] = {}
        for address in normalized_addresses:
            metadata = metadata_by_address.get(address)
            if metadata is None or metadata.status is not StockTokenStatus.ACTIVE:
                continue
            current_quote = self._quote_for_metadata(metadata)
            if current_quote is not None:
                result[address] = current_quote.token_midpoint_usd

        # Unknown Robinhood contracts remain eligible for another provider if one gains support later.
        missing = [
            address
            for address in normalized_addresses
            if address not in result and address not in metadata_by_address
        ]
        if missing:
            try:
                fallback_quotes = self._fallback_provider.get_token_prices(chain, missing)
            except ProviderError:
                if not result:
                    raise
            else:
                result.update(
                    {
                        address.lower(): value
                        for address, value in fallback_quotes.items()
                        if address.lower() in missing
                    }
                )
        return result

    def get_simple_prices(self, ids: list[str]) -> dict[str, Decimal]:
        """Preserve symbol-price support used by the core native-price fallback."""

        method = getattr(self._fallback_provider, "get_simple_prices", None)
        if not callable(method):
            return {}
        result = method(ids)
        return result if isinstance(result, dict) else {}

    def get_stock_token_metadata(self, contract_address: str) -> StockTokenMetadata | None:
        """Return catalog metadata only after exact Robinhood deployment matching."""

        normalized = _normalized_address(contract_address)
        if normalized is None:
            return None
        return self._metadata_by_address().get(normalized)

    def get_stock_token_catalog(self) -> tuple[StockTokenMetadata, ...]:
        """Return every exact Robinhood Chain deployment from the official catalog."""

        return tuple(
            sorted(
                self._metadata_by_address().values(),
                key=lambda item: (item.symbol, item.contract_address),
            )
        )

    def get_corporate_actions(
        self,
        contract_address: str | None = None,
    ) -> tuple[StockTokenCorporateAction, ...]:
        """Return processed actions, optionally limited to one exact deployment."""

        normalized = None
        if contract_address is not None:
            normalized = _normalized_address(contract_address)
            if normalized is None:
                return ()
        payload = self._cached_payload(
            _CORPORATE_ACTION_CACHE_KEY,
            f"{self._base_url}/corporate-actions",
            3_600,
        )
        raw_actions = payload.get("corpActions")
        if not isinstance(raw_actions, list):
            raise ProviderResponseError(
                "Robinhood Stock Token corporate-action response has no corpActions list."
            )
        actions = tuple(
            action
            for raw_action in raw_actions
            if isinstance(raw_action, dict)
            for action in _parse_corporate_action(raw_action)
            if normalized is None or action.contract_address == normalized
        )
        seen: dict[tuple[str, str], StockTokenCorporateAction] = {}
        for action in actions:
            key = (action.action_id, action.contract_address)
            if key in seen and seen[key] != action:
                raise ProviderResponseError("Conflicting corporate-action records.")
            seen[key] = action
        return tuple(
            sorted(
                seen.values(),
                key=lambda item: (item.process_date or date.min, item.action_id),
                reverse=True,
            )
        )

    def get_stock_token_quote(self, contract_address: str) -> StockTokenQuote | None:
        """Return the complete current quote for an active Stock Token deployment."""

        metadata = self.get_stock_token_metadata(contract_address)
        if metadata is None or metadata.status is not StockTokenStatus.ACTIVE:
            return None
        return self._quote_for_metadata(metadata)

    def _metadata_by_address(self) -> dict[str, StockTokenMetadata]:
        payload = self._cached_payload(
            _ASSET_CACHE_KEY,
            f"{self._base_url}/assets",
            self._asset_cache_ttl_seconds,
        )
        raw_assets = payload.get("assets")
        if not isinstance(raw_assets, list):
            raise ProviderResponseError("Robinhood Stock Token asset response has no assets list.")

        result: dict[str, StockTokenMetadata] = {}
        for raw_asset in raw_assets:
            if not isinstance(raw_asset, dict):
                continue
            parsed_assets = _parse_asset(raw_asset)
            for metadata in parsed_assets:
                existing = result.get(metadata.contract_address)
                if existing is not None and existing != metadata:
                    raise ProviderResponseError("Conflicting Robinhood asset deployments.")
                result[metadata.contract_address] = metadata
        return result

    def _quote_for_metadata(self, metadata: StockTokenMetadata) -> StockTokenQuote | None:
        symbol_path = quote(metadata.symbol, safe="")
        payload = self._cached_payload(
            f"robinhood.stock.quote.v1.{metadata.symbol.upper()}",
            f"{self._base_url}/prices/{symbol_path}",
            self._quote_cache_ttl_seconds,
            allow_not_found=True,
        )
        if not payload:
            return None
        raw_quotes = payload.get("quotes")
        if not isinstance(raw_quotes, list):
            raise ProviderResponseError("Robinhood Stock Token price response has no quotes list.")
        for raw_quote in raw_quotes:
            parsed = _parse_quote(raw_quote, metadata)
            if parsed is not None and self._quote_is_current(parsed.generated_at):
                return parsed
        return None

    def _quote_is_current(self, generated_at: datetime) -> bool:
        now = self._now().astimezone(UTC)
        return (
            now - timedelta(seconds=self._quote_max_age_seconds)
            <= generated_at
            <= now + timedelta(seconds=self._quote_max_future_skew_seconds)
        )

    def _cached_payload(
        self,
        cache_key: str,
        url: str,
        ttl_seconds: int,
        *,
        allow_not_found: bool = False,
    ) -> dict[str, Any]:
        if self._cache_store is not None:
            cached = self._cache_store.get(cache_key)
            if isinstance(cached, dict) and cached.get("version") == 1:
                payload = cached.get("payload")
                if isinstance(payload, dict):
                    return payload

        payload = self._send_json(url, allow_not_found=allow_not_found)
        if self._cache_store is not None and payload:
            self._cache_store.set(
                cache_key,
                {"version": 1, "payload": payload},
                ttl_seconds=ttl_seconds,
            )
        return payload

    def _send_json(self, url: str, *, allow_not_found: bool) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            try:
                response = self._http_client.send(
                    HTTPRequest(url=url, headers={"accept": "application/json"})
                )
            except HTTPClientTimeoutError as error:
                raise ProviderTimeoutError("Robinhood Stock Token request timed out.") from error
            except HTTPClientNetworkError as error:
                raise ProviderNetworkError(
                    f"Robinhood Stock Token network failure: {error}"
                ) from error

            if response.status_code == 404 and allow_not_found:
                return {}
            if response.status_code == 429:
                raise ProviderRateLimitError(
                    "Robinhood Stock Token request was rate-limited (HTTP 429)."
                )
            if response.status_code < 200 or response.status_code >= 300:
                raise ProviderResponseError(
                    f"Robinhood Stock Token request failed with HTTP {response.status_code}."
                )
            try:
                decoded = json_loads(response.data)
            except ValueError as error:
                raise ProviderResponseError(
                    "Invalid Robinhood Stock Token response format."
                ) from error
            if not isinstance(decoded, dict):
                raise ProviderResponseError("Invalid Robinhood Stock Token response format.")
            return decoded

        return retry_with_backoff(
            operation=operation,
            should_retry=_is_retryable_error,
            attempts=self._retry_attempts,
            initial_delay_seconds=self._retry_initial_delay_seconds,
            backoff_multiplier=self._retry_backoff_multiplier,
            max_delay_seconds=self._retry_max_delay_seconds,
            sleep_func=self._sleep_func,
        )


def _parse_asset(raw: dict[str, Any]) -> tuple[StockTokenMetadata, ...]:
    asset_id = raw.get("id")
    symbol = raw.get("tokenSymbol")
    name = raw.get("tokenName")
    multiplier = _positive_decimal(raw.get("currentMultiplier"))
    if (
        not isinstance(asset_id, str)
        or not asset_id.startswith("0x")
        or len(asset_id) != 66
        or not isinstance(symbol, str)
        or not symbol.strip()
        or not isinstance(name, str)
        or not name.strip()
        or multiplier is None
    ):
        return ()

    status = _status(raw.get("status"))
    pending_multiplier = _optional_positive_decimal(raw.get("pendingMultiplier"))
    pending_at = _parse_datetime(raw.get("pendingMultiplierEffectiveTime"))
    logo_url = raw.get("logoUrl") if isinstance(raw.get("logoUrl"), str) else None
    capabilities = raw.get("tradingCapabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    deployments = raw.get("deployments")
    if not isinstance(deployments, list):
        return ()

    result: list[StockTokenMetadata] = []
    for deployment in deployments:
        if not isinstance(deployment, dict) or deployment.get("chainId") != _ROBINHOOD_CHAIN_ID:
            continue
        address = _normalized_address(deployment.get("contractAddress"))
        if address is None:
            continue
        result.append(
            StockTokenMetadata(
                asset_id=asset_id.lower(),
                symbol=symbol.strip().upper(),
                name=name.strip(),
                contract_address=address,
                chain_id=_ROBINHOOD_CHAIN_ID,
                current_multiplier=multiplier,
                pending_multiplier=pending_multiplier,
                pending_multiplier_effective_at=pending_at,
                logo_url=logo_url,
                status=status,
                fractional_tradability=_optional_string(
                    capabilities.get("fractionalTradability")
                ),
                all_day_tradability=_optional_string(capabilities.get("allDayTradability")),
                extended_hours_fractional_tradability=_optional_bool(
                    capabilities.get("extendedHoursFractionalTradability")
                ),
            )
        )
    return tuple(result)


def _parse_quote(raw: Any, metadata: StockTokenMetadata) -> StockTokenQuote | None:
    if not isinstance(raw, dict):
        return None
    symbol = raw.get("tokenSymbol")
    currency = raw.get("currency")
    if (
        not isinstance(symbol, str)
        or symbol.upper() != metadata.symbol
        or not isinstance(currency, str)
        or currency.upper() != "USD"
        or not _has_deployment(raw.get("deployments"), metadata.contract_address)
    ):
        return None
    bid = _positive_decimal(raw.get("bid"))
    ask = _positive_decimal(raw.get("ask"))
    generated_at = _parse_datetime(raw.get("generatedAt"))
    is_trading_halt = raw.get("isTradingHalt")
    if bid is None or ask is None or ask < bid or generated_at is None or not isinstance(is_trading_halt, bool):
        return None

    token_bid = bid * metadata.current_multiplier
    token_ask = ask * metadata.current_multiplier
    volume = _nonnegative_decimal(raw.get("dailyTradingVolume"))
    return StockTokenQuote(
        metadata=metadata,
        underlying_bid_usd=bid,
        underlying_ask_usd=ask,
        token_bid_usd=token_bid,
        token_ask_usd=token_ask,
        token_midpoint_usd=(token_bid + token_ask) / Decimal("2"),
        daily_trading_volume=volume,
        is_trading_halt=is_trading_halt,
        generated_at=generated_at,
        source_provider=_SOURCE_NAME,
    )


def _parse_corporate_action(
    raw: dict[str, Any],
) -> tuple[StockTokenCorporateAction, ...]:
    action_id = raw.get("id")
    symbol = raw.get("tokenSymbol")
    if (
        not isinstance(action_id, str)
        or not action_id.startswith("0x")
        or len(action_id) != 66
        or not isinstance(symbol, str)
        or not symbol.strip()
    ):
        return ()
    action_type = _corporate_action_type(raw.get("type"))
    status = _corporate_action_status(raw.get("status"))
    detail = _corporate_action_details(raw.get("details"))
    if detail is None:
        return ()
    process_date = _process_date(raw.get("processDate"))
    deployments = raw.get("deployments")
    if not isinstance(deployments, list):
        return ()

    detail_kind, detail_values = detail
    if action_type is not CorporateActionType.UNSPECIFIED:
        expected_kind = action_type.value.split("_")
        expected_key = expected_kind[0] + "".join(word.title() for word in expected_kind[1:])
        if detail_kind != expected_key:
            raise ProviderResponseError("Corporate-action type and details disagree.")
    for key, value in detail_values:
        if key == "rate" or key.endswith("Rate"):
            if _nonnegative_decimal(value) is None:
                raise ProviderResponseError("Corporate-action rate is invalid.")
    if raw.get("processDate") is not None and process_date is None:
        raise ProviderResponseError("Corporate-action process date is invalid.")
    result: list[StockTokenCorporateAction] = []
    for deployment in deployments:
        if not isinstance(deployment, dict) or deployment.get("chainId") != _ROBINHOOD_CHAIN_ID:
            continue
        address = _normalized_address(deployment.get("contractAddress"))
        if address is None:
            continue
        result.append(
            StockTokenCorporateAction(
                action_id=action_id.lower(),
                action_type=action_type,
                status=status,
                process_date=process_date,
                token_symbol=symbol.strip().upper(),
                chain=Chain.ROBINHOOD,
                contract_address=address,
                detail_kind=detail_kind,
                details=detail_values,
                source_provider=_SOURCE_NAME,
            )
        )
    return tuple(result)


def _has_deployment(raw: Any, contract_address: str) -> bool:
    if not isinstance(raw, list):
        return False
    return any(
        isinstance(deployment, dict)
        and deployment.get("chainId") == _ROBINHOOD_CHAIN_ID
        and _normalized_address(deployment.get("contractAddress")) == contract_address
        for deployment in raw
    )


def _normalized_addresses(values: list[str]) -> list[str]:
    return sorted(
        {
            normalized
            for value in values
            if (normalized := _normalized_address(value)) is not None
        }
    )


def _normalized_address(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    normalized = raw.strip().lower()
    if not normalized.startswith("0x") or len(normalized) != 42:
        return None
    try:
        int(normalized[2:], 16)
    except ValueError:
        return None
    return normalized


def _positive_decimal(raw: Any) -> Decimal | None:
    value = _decimal(raw)
    if value is None or value <= 0:
        return None
    return value


def _optional_positive_decimal(raw: Any) -> Decimal | None:
    if raw is None or raw == "":
        return None
    return _positive_decimal(raw)


def _nonnegative_decimal(raw: Any) -> Decimal | None:
    value = _decimal(raw)
    if value is None or value < 0:
        return None
    return value


def _decimal(raw: Any) -> Decimal | None:
    try:
        value = Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None


def _parse_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    normalized = raw.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _status(raw: Any) -> StockTokenStatus:
    if raw == "ASSET_STATUS_ACTIVE":
        return StockTokenStatus.ACTIVE
    if raw == "ASSET_STATUS_INACTIVE":
        return StockTokenStatus.INACTIVE
    return StockTokenStatus.UNSPECIFIED


def _corporate_action_type(raw: Any) -> CorporateActionType:
    prefix = "CORPORATE_ACTION_TYPE_"
    if not isinstance(raw, str) or not raw.startswith(prefix):
        return CorporateActionType.UNSPECIFIED
    try:
        return CorporateActionType(raw.removeprefix(prefix).lower())
    except ValueError:
        return CorporateActionType.UNSPECIFIED


def _corporate_action_status(raw: Any) -> CorporateActionStatus:
    prefix = "CORPORATE_ACTION_STATUS_"
    if not isinstance(raw, str) or not raw.startswith(prefix):
        return CorporateActionStatus.UNSPECIFIED
    try:
        return CorporateActionStatus(raw.removeprefix(prefix).lower())
    except ValueError:
        return CorporateActionStatus.UNSPECIFIED


def _corporate_action_details(
    raw: Any,
) -> tuple[str, tuple[tuple[str, str], ...]] | None:
    if not isinstance(raw, dict) or len(raw) != 1:
        return None
    detail_kind, detail_payload = next(iter(raw.items()))
    if not isinstance(detail_kind, str) or not isinstance(detail_payload, dict):
        return None
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in detail_payload.items()):
        raise ProviderResponseError("Corporate-action details must contain text fields.")
    values = tuple(
        sorted(
            (key, str(value))
            for key, value in detail_payload.items()
            if isinstance(key, str) and isinstance(value, (str, int, float))
        )
    )
    return detail_kind, values


def _process_date(raw: Any) -> date | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        return None
    year = raw.get("year")
    month = raw.get("month")
    day = raw.get("day")
    if (
        not isinstance(year, int)
        or isinstance(year, bool)
        or not isinstance(month, int)
        or isinstance(month, bool)
        or not isinstance(day, int)
        or isinstance(day, bool)
    ):
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _optional_string(raw: Any) -> str | None:
    return raw if isinstance(raw, str) else None


def _optional_bool(raw: Any) -> bool | None:
    return raw if isinstance(raw, bool) else None


def _is_retryable_error(error: Exception) -> bool:
    return isinstance(
        error,
        (ProviderRateLimitError, ProviderTimeoutError, ProviderNetworkError),
    )
