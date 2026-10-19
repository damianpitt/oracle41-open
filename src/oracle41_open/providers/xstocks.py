"""Load verified deployments and issuer audit history from the public xStocks API.

The public catalog does not require a key. Oracle41 accepts only EVM deployments on networks it
already supports and indexes every published token or wrapper contract by chain and address.
Catalog results are cached because identity data changes much less often than wallet activity.
Action revisions and historical multiplier changes use separate validated pagination sequences.
Numeric multiplier responses are parsed as Decimal before they enter storage or exports.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import quote, urlencode
from uuid import UUID

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
from oracle41_open.core.models.xstocks_audit import (
    XStocksAuditContext,
    XStocksCorporateAction,
    XStocksHistoryPage,
    XStocksMultiplierChange,
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
    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None: ...


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
        identities = _parse_assets(assets)
        if self._cache_store is not None:
            self._cache_store.set(
                _CACHE_KEY,
                {"version": 1, "assets": assets},
                ttl_seconds=self._cache_ttl_seconds,
            )
        return identities

    def get_audit_history(self, chain: Chain, contract_address: str) -> XStocksAuditContext | None:
        """Fetch complete bounded history only after an exact issuer deployment match."""
        identity = self.get_asset_identity(chain, contract_address)
        if identity is None:
            return None
        network = next(name for name, candidate in _NETWORKS.items() if candidate is chain)
        action_nodes = self._history_pages(
            "/public/corporate-actions/history",
            {"symbol": identity.symbol, "sortBy": "createdTimeUtc", "sortOrder": "asc"},
            first_page=1,
        )
        multiplier_nodes = self._history_pages(
            f"/public/assets/{quote(identity.symbol, safe='')}/multiplier/history",
            {"network": network},
            first_page=0,
        )
        actions: dict[tuple[str, int], XStocksCorporateAction] = {}
        for raw in action_nodes:
            action = _parse_action(raw, identity.symbol)
            key = (action.event_id, action.version)
            if key in actions and actions[key] != action:
                raise ProviderResponseError("Conflicting xStocks action revisions.")
            actions[key] = action
        changes: dict[str, XStocksMultiplierChange] = {}
        for raw in multiplier_nodes:
            change = _parse_multiplier_change(raw)
            if change.change_id in changes and changes[change.change_id] != change:
                raise ProviderResponseError("Conflicting xStocks multiplier changes.")
            changes[change.change_id] = change
        return XStocksAuditContext(
            identity=identity,
            corporate_actions=tuple(
                sorted(
                    actions.values(), key=lambda item: (item.created_at, item.version), reverse=True
                )
            ),
            multiplier_history=tuple(
                sorted(changes.values(), key=lambda item: item.effective_at, reverse=True)
            ),
            fetched_at=datetime.now(tz=UTC),
        )

    def _history_pages(
        self,
        path: str,
        filters: dict[str, str],
        *,
        first_page: int,
    ) -> list[dict[str, Any]]:
        """Validate each continuation and refuse to save a truncated history as complete."""
        nodes: list[dict[str, Any]] = []
        for number in range(first_page, first_page + self._max_pages):
            query = urlencode({**filters, "page": number, "pageSize": 100})
            payload = self._send_json(f"{self._base_url}{path}?{query}", preserve_decimals=True)
            raw_nodes, has_next = _validated_page(payload, number)
            nodes.extend(raw_nodes)
            if not has_next:
                return nodes
        raise ProviderResponseError(
            "xStocks history exceeded the safe page limit; saved history retained."
        )

    def get_history_page(
        self,
        identity: RealWorldAssetIdentity,
        feed: str,
        page: int,
    ) -> XStocksHistoryPage:
        """Load one feed page; the service verifies catalog identity before starting a run."""
        if identity.source_name != _SOURCE_NAME or identity.chain not in _NETWORKS.values():
            raise ProviderResponseError("Unsupported xStocks history deployment.")
        if feed == "actions" and page >= 1:
            path = "/public/corporate-actions/history"
            filters = {"symbol": identity.symbol, "sortBy": "createdTimeUtc", "sortOrder": "asc"}
        elif feed == "multipliers" and page >= 0:
            path = f"/public/assets/{quote(identity.symbol, safe='')}/multiplier/history"
            filters = {
                "network": next(
                    name for name, chain in _NETWORKS.items() if chain is identity.chain
                )
            }
        else:
            raise ProviderResponseError("Invalid xStocks history feed or page.")
        query = urlencode({**filters, "page": page, "pageSize": 100})
        payload = self._send_json(f"{self._base_url}{path}?{query}", preserve_decimals=True)
        nodes, has_next = _validated_page(payload, page)
        return XStocksHistoryPage(
            feed=feed,
            page=page,
            next_page=page + 1 if has_next else None,
            corporate_actions=tuple(_parse_action(raw, identity.symbol) for raw in nodes)
            if feed == "actions"
            else (),
            multiplier_history=tuple(_parse_multiplier_change(raw) for raw in nodes)
            if feed == "multipliers"
            else (),
        )

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

    def _send_json(self, url: str, *, preserve_decimals: bool = False) -> dict[str, Any]:
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
                # Parse issuer numeric multipliers directly as Decimal, before any float rounding.
                decoded = (
                    json.loads(response.data, parse_float=Decimal)
                    if preserve_decimals
                    else json_loads(response.data)
                )
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
                    symbol=symbol.strip(),
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


def _required_text(raw: object, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ProviderResponseError(f"xStocks history is missing {field}.")
    return raw.strip()


def _validated_page(payload: dict[str, Any], number: int) -> tuple[list[dict[str, Any]], bool]:
    nodes, page = payload.get("nodes"), payload.get("page")
    if (
        not isinstance(nodes, list)
        or any(not isinstance(item, dict) for item in nodes)
        or not isinstance(page, dict)
        or isinstance(page.get("currentPage"), bool)
        or page.get("currentPage") != number
        or not isinstance(page.get("hasNextPage"), bool)
    ):
        raise ProviderResponseError("Invalid xStocks history pagination.")
    if page["hasNextPage"] and not nodes:
        raise ProviderResponseError("xStocks history continuation has no records.")
    return nodes, page["hasNextPage"]


def _timestamp(raw: object, field: str) -> datetime:
    value = _required_text(raw, field)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProviderResponseError(f"Invalid xStocks {field}.") from error
    if parsed.tzinfo is None:
        raise ProviderResponseError(f"xStocks {field} has no timezone.")
    return parsed.astimezone(UTC)


def _positive_decimal(raw: object, field: str) -> Decimal:
    if isinstance(raw, bool) or not isinstance(raw, (str, int, Decimal)):
        raise ProviderResponseError(f"Invalid xStocks {field}.")
    try:
        value = Decimal(raw)
    except (InvalidOperation, ValueError) as error:
        raise ProviderResponseError(f"Invalid xStocks {field}.") from error
    if not value.is_finite() or value <= 0:
        raise ProviderResponseError(f"Invalid xStocks {field}.")
    return value


def _parse_action(raw: dict[str, Any], symbol: str) -> XStocksCorporateAction:
    event_id = _required_text(raw.get("eventId"), "eventId")
    try:
        event_id = str(UUID(event_id))
    except ValueError as error:
        raise ProviderResponseError("Invalid xStocks corporate-action event ID.") from error
    version = raw.get("version")
    if (
        isinstance(version, bool)
        or not isinstance(version, (int, Decimal))
        or not Decimal(version).is_finite()
        or version < 0
        or version != int(version)
    ):
        raise ProviderResponseError("Invalid xStocks corporate-action version.")
    reported_symbol = _required_text(raw.get("xstockSymbol"), "xstockSymbol")
    if reported_symbol.casefold() != symbol.casefold():
        raise ProviderResponseError("xStocks corporate action belongs to another asset.")
    status = _required_text(raw.get("status"), "status")
    if status not in {"Initial", "Corrected", "Cancelled", "Scheduled"}:
        raise ProviderResponseError("Unknown xStocks corporate-action status.")
    details: list[tuple[str, str | None]] = []
    for key, value in raw.items():
        if value is None or isinstance(value, (str, int, Decimal)) and not isinstance(value, bool):
            details.append((key, str(value) if value is not None else None))
        else:
            raise ProviderResponseError("Invalid xStocks corporate-action detail value.")
    return XStocksCorporateAction(
        event_id=event_id,
        version=int(version),
        symbol=reported_symbol,
        action_type=_required_text(raw.get("caType"), "caType"),
        status=status,
        created_at=_timestamp(raw.get("createdTimeUtc"), "createdTimeUtc"),
        effective_at=(
            _timestamp(raw["effectiveTimeUtc"], "effectiveTimeUtc")
            if raw.get("effectiveTimeUtc") is not None
            else None
        ),
        details=tuple(sorted(details)),
    )


def _parse_multiplier_change(raw: dict[str, Any]) -> XStocksMultiplierChange:
    return XStocksMultiplierChange(
        change_id=_required_text(raw.get("id"), "multiplier change ID"),
        reason=_required_text(raw.get("reason"), "multiplier reason"),
        multiplier=_positive_decimal(raw.get("multiplier"), "multiplier"),
        previous_multiplier=_positive_decimal(raw.get("previousMultiplier"), "previousMultiplier"),
        effective_at=_timestamp(raw.get("activationDateTime"), "activationDateTime"),
    )
