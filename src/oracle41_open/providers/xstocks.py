"""Load exact tokenized equity and ETF deployments from the public xStocks catalog.

The public catalog does not require a key. Oracle41 accepts only EVM deployments on networks it
already supports and indexes every published token or wrapper contract by chain and address.
Catalog results are cached because identity data changes much less often than wallet activity.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from typing import Any, Protocol
from urllib.parse import urlencode

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    RealWorldAssetCategory,
    RealWorldAssetIdentity,
)
from oracle41_open.providers.http_client import (
    HTTPClient,
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
)
from oracle41_open.providers.retry import retry_with_backoff

_BASE_URL = "https://api.xstocks.fi/api/v2"
_CACHE_KEY = "xstocks.public.assets.v2"
_SOURCE_NAME = "xstocks-public-api"
_SOURCE_REFERENCE = "https://docs.xstocks.fi/apis/openapi/assets"
_NETWORKS = {
    "Ethereum": Chain.ETHEREUM,
    "Optimism": Chain.OPTIMISM,
    "Polygon": Chain.POLYGON,
    "Base": Chain.BASE,
    "Arbitrum": Chain.ARBITRUM,
}


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...


class XStocksAssetProvider:
    """Resolve public xStocks identities without accepting ticker-only matches."""

    def __init__(
        self,
        http_client: HTTPClient | None = None,
        cache_store: CacheStore | None = None,
        base_url: str = _BASE_URL,
        cache_ttl_seconds: int = 3_600,
        max_pages: int = 20,
        retry_attempts: int = 3,
        retry_initial_delay_seconds: float = 0.25,
        retry_backoff_multiplier: float = 2.0,
        retry_max_delay_seconds: float = 2.0,
        sleep_func: Callable[[float], None] | None = None,
    ) -> None:
        self._http_client = http_client or HTTPClient()
        self._cache_store = cache_store
        self._base_url = base_url.rstrip("/")
        self._cache_ttl_seconds = max(0, cache_ttl_seconds)
        self._max_pages = max(1, max_pages)
        self._retry_attempts = max(1, retry_attempts)
        self._retry_initial_delay_seconds = max(0.0, retry_initial_delay_seconds)
        self._retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
        self._retry_max_delay_seconds = max(0.0, retry_max_delay_seconds)
        self._sleep_func = sleep_func or time.sleep

    def get_asset_identity(
        self,
        chain: Chain,
        contract_address: str,
    ) -> RealWorldAssetIdentity | None:
        """Look up one exact deployment from the cached public catalog."""

        normalized = _normalized_address(contract_address)
        if normalized is None:
            return None
        return self._identities().get((chain, normalized))

    def _identities(self) -> dict[tuple[Chain, str], RealWorldAssetIdentity]:
        if self._cache_store is not None:
            cached = self._cache_store.get(_CACHE_KEY)
            if isinstance(cached, dict) and cached.get("version") == 1:
                raw_assets = cached.get("assets")
                if isinstance(raw_assets, list):
                    return _parse_assets(raw_assets)

        assets = self._fetch_all_assets()
        if self._cache_store is not None:
            self._cache_store.set(
                _CACHE_KEY,
                {"version": 1, "assets": assets},
                ttl_seconds=self._cache_ttl_seconds,
            )
        return _parse_assets(assets)

    def _fetch_all_assets(self) -> list[dict[str, Any]]:
        assets: list[dict[str, Any]] = []
        for page_number in range(self._max_pages):
            query = urlencode({"page": page_number, "pageSize": 100})
            payload = self._send_json(f"{self._base_url}/public/assets?{query}")
            raw_nodes = payload.get("nodes")
            if not isinstance(raw_nodes, list):
                raise ProviderResponseError("xStocks asset response has no nodes list.")
            assets.extend(item for item in raw_nodes if isinstance(item, dict))
            page = payload.get("page")
            if not isinstance(page, dict) or page.get("hasNextPage") is not True:
                return assets
        raise ProviderResponseError("xStocks asset catalog exceeded the safe page limit.")

    def _send_json(self, url: str) -> dict[str, Any]:
        def operation() -> dict[str, Any]:
            try:
                response = self._http_client.send(
                    HTTPRequest(url=url, headers={"accept": "application/json"})
                )
            except HTTPClientTimeoutError as error:
                raise ProviderTimeoutError("xStocks catalog request timed out.") from error
            except HTTPClientNetworkError as error:
                raise ProviderNetworkError(f"xStocks catalog network failure: {error}") from error
            if response.status_code == 429:
                raise ProviderRateLimitError("xStocks catalog request was rate-limited (HTTP 429).")
            if response.status_code < 200 or response.status_code >= 300:
                raise ProviderResponseError(
                    f"xStocks catalog request failed with HTTP {response.status_code}."
                )
            try:
                decoded = json_loads(response.data)
            except ValueError as error:
                raise ProviderResponseError("Invalid xStocks catalog response format.") from error
            if not isinstance(decoded, dict):
                raise ProviderResponseError("Invalid xStocks catalog response format.")
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


def _parse_assets(raw_assets: Sequence[object]) -> dict[tuple[Chain, str], RealWorldAssetIdentity]:
    result: dict[tuple[Chain, str], RealWorldAssetIdentity] = {}
    for raw in raw_assets:
        if not isinstance(raw, dict):
            continue
        asset_id = raw.get("id")
        symbol = raw.get("symbol")
        name = raw.get("name")
        deployments = raw.get("deployments")
        if (
            not isinstance(asset_id, str)
            or not asset_id.strip()
            or not isinstance(symbol, str)
            or not symbol.strip()
            or not isinstance(name, str)
            or not name.strip()
        ):
            continue
        if not isinstance(deployments, list):
            continue
        underlying = raw.get("underlying")
        underlying = underlying if isinstance(underlying, dict) else {}
        category = (
            RealWorldAssetCategory.TOKENIZED_ETF
            if underlying.get("type") == "ETF"
            else RealWorldAssetCategory.TOKENIZED_EQUITY
        )
        for deployment in deployments:
            if not isinstance(deployment, dict):
                continue
            network_name = deployment.get("network")
            chain = _NETWORKS.get(network_name) if isinstance(network_name, str) else None
            if chain is None:
                continue
            for field_name in ("address", "wrapperAddress", "wrapperAddressV2"):
                address = _normalized_address(deployment.get(field_name))
                if address is None:
                    continue
                identity = RealWorldAssetIdentity(
                    asset_id=asset_id.strip(),
                    symbol=symbol.strip().upper(),
                    name=name.strip(),
                    chain=chain,
                    contract_address=address,
                    category=category,
                    issuer="Backed Assets",
                    source_name=_SOURCE_NAME,
                    source_reference=_SOURCE_REFERENCE,
                    underlying_symbol=_optional_text(underlying.get("symbol")),
                    underlying_isin=_optional_text(underlying.get("isin")),
                )
                existing = result.get((chain, address))
                if existing is not None and existing != identity:
                    raise ProviderResponseError("Conflicting xStocks deployment identities.")
                result[(chain, address)] = identity
    return result


def _normalized_address(raw: object) -> str | None:
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


def _optional_text(raw: object) -> str | None:
    return raw.strip() if isinstance(raw, str) and raw.strip() else None


def _is_retryable_error(error: Exception) -> bool:
    return isinstance(
        error,
        (ProviderRateLimitError, ProviderTimeoutError, ProviderNetworkError),
    )
