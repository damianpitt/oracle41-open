"""Read bounded daily token-price history from Alchemy's address endpoint.

Requests use an exact supported network and contract, never a token symbol fallback. Responses
must repeat that identity and USD currency. Prices are parsed as decimals before storage, while
transport and HTTP errors use safe messages that never include the credential-bearing URL.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal, InvalidOperation

from oracle41_open.core.models import (
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ValidationError,
)
from oracle41_open.core.models.historical_price import HistoricalPricePoint, HistoricalPriceQuery
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
)
from oracle41_open.providers.retry import retry_with_backoff

SOURCE_REFERENCE = "https://www.alchemy.com/docs/data/prices-api/prices-api-endpoints/prices-api-endpoints/get-historical-token-prices"


class AlchemyHistoricalPriceProvider:
    source_provider = "alchemy"
    source_reference = SOURCE_REFERENCE

    def __init__(
        self,
        api_key: str,
        http_client: HTTPClient | None = None,
        *,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        if not api_key.strip():
            raise ProviderAuthError("Configure an Alchemy key to fetch historical prices.")
        self._url = f"https://api.g.alchemy.com/prices/v1/{api_key.strip()}/tokens/historical"
        self._http = http_client or HTTPClient()
        self._sleep = sleep_func

    def fetch(self, query: HistoricalPriceQuery) -> tuple[HistoricalPricePoint, ...]:
        network = query.chain.network.alchemy_pricing_network_path
        if network is None:
            raise ValidationError("Historical address pricing is not enabled for this network.")
        request = HTTPRequest(
            self._url,
            method="POST",
            headers={"content-type": "application/json"},
            json={
                "network": network,
                "address": query.contract_address,
                "startTime": query.start.isoformat(),
                "endTime": query.end.isoformat(),
                "interval": query.interval,
            },
        )

        def operation() -> tuple[HistoricalPricePoint, ...]:
            try:
                response = self._http.send(request)
            except HTTPClientTimeoutError:
                raise ProviderTimeoutError("Historical price request timed out.") from None
            except HTTPClientNetworkError:
                raise ProviderNetworkError("Historical price network request failed.") from None
            if response.status_code in {401, 403}:
                raise ProviderAuthError(
                    "Historical price access was denied. Check the Alchemy key and plan."
                )
            if response.status_code == 429:
                raise ProviderRateLimitError(
                    "Historical prices were rate-limited. Try again later."
                )
            if response.status_code >= 500:
                raise ProviderNetworkError("Historical price source is temporarily unavailable.")
            if response.status_code != 200:
                raise ProviderResponseError(
                    f"Historical price request failed (HTTP {response.status_code})."
                )
            if len(response.data) > 2_000_000:
                raise ProviderResponseError("Historical price response exceeded the size limit.")
            try:
                # Numeric JSON prices must not pass through a binary float before validation.
                payload = json.loads(response.data, parse_float=Decimal)
                if (
                    not isinstance(payload, dict)
                    or payload.get("currency") != "usd"
                    or payload.get("network") != network
                    or not isinstance(payload.get("address"), str)
                    or payload["address"].lower() != query.contract_address
                    or not isinstance(payload.get("data"), list)
                    or len(payload["data"]) > 32
                    or payload.get("error") is not None
                ):
                    raise ValueError("Mismatched historical price identity or response shape.")
                points: dict[datetime, HistoricalPricePoint] = {}
                for raw in payload["data"]:
                    if not isinstance(raw, dict) or not isinstance(raw.get("timestamp"), str):
                        raise ValueError("Invalid historical price row.")
                    value = raw.get("value")
                    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
                        raise ValueError("Invalid historical price amount.")
                    point = HistoricalPricePoint(
                        datetime.fromisoformat(raw["timestamp"]), Decimal(value)
                    )
                    if not query.start <= point.timestamp <= query.end:
                        raise ValueError("Historical price outside requested range.")
                    previous = points.get(point.timestamp)
                    if previous is not None and previous.value_usd != point.value_usd:
                        raise ValueError("Conflicting historical price timestamp.")
                    points[point.timestamp] = point
                return tuple(sorted(points.values(), key=lambda point: point.timestamp))
            except (ValueError, InvalidOperation, ValidationError, OverflowError):
                raise ProviderResponseError(
                    "Invalid historical price data; saved history was not changed."
                ) from None

        return retry_with_backoff(
            operation,
            lambda error: isinstance(
                error, (ProviderTimeoutError, ProviderNetworkError, ProviderRateLimitError)
            ),
            attempts=3,
            initial_delay_seconds=0.25,
            backoff_multiplier=2,
            max_delay_seconds=2,
            sleep_func=self._sleep,
        )
