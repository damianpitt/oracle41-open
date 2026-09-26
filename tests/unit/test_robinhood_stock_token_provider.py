"""Test official Robinhood Stock Token metadata and price mapping.

The cases verify multiplier-aware valuation, exact deployment matching, inactive-asset handling, response caching, fallback
routing, and structured rate-limit retries. All network responses come from local deterministic fixtures.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from oracle41_open.core.models import (
    Chain,
    CorporateActionStatus,
    CorporateActionType,
    StockTokenStatus,
)
from oracle41_open.providers.http_client import HTTPRequest, HTTPResponse
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.providers.robinhood_stock_tokens import (
    RobinhoodStockTokenPricingProvider,
)

_FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures" / "providers" / "robinhood"
_ACTIVE_CONTRACT = "0x1111111111111111111111111111111111111111"
_INACTIVE_CONTRACT = "0x2222222222222222222222222222222222222222"
_NOW = datetime(2026, 9, 24, 1, 2, 10, tzinfo=UTC)


def test_stock_token_price_applies_current_multiplier_and_keeps_evidence() -> None:
    client = _HTTPClient([_fixture("stock_assets.json"), _fixture("stock_price_test.json")])
    provider = RobinhoodStockTokenPricingProvider(
        fallback_provider=_FallbackPricingProvider(),
        http_client=client,  # type: ignore[arg-type]
        cache_store=_MemoryCache(),
        now_func=lambda: _NOW,
    )

    prices = provider.get_token_prices(Chain.ROBINHOOD, [_ACTIVE_CONTRACT])
    quote = provider.get_stock_token_quote(_ACTIVE_CONTRACT)

    assert prices == {_ACTIVE_CONTRACT: Decimal("301.500000000000000000")}
    assert quote is not None
    assert quote.metadata.status is StockTokenStatus.ACTIVE
    assert quote.metadata.current_multiplier == Decimal("1.500000000000000000")
    assert quote.metadata.pending_multiplier == Decimal("2.000000000000000000")
    assert quote.metadata.pending_multiplier_effective_at == datetime(
        2026, 10, 1, tzinfo=UTC
    )
    assert quote.underlying_bid_usd == Decimal("200.00")
    assert quote.underlying_ask_usd == Decimal("202.00")
    assert quote.token_bid_usd == Decimal("300.00000000000000000000")
    assert quote.token_ask_usd == Decimal("303.00000000000000000000")
    assert quote.token_midpoint_usd == Decimal("301.50000000000000000000")
    assert quote.daily_trading_volume == Decimal("123456.75")
    assert quote.generated_at == datetime(2026, 9, 24, 1, 2, 3, tzinfo=UTC)
    assert quote.source_provider == "robinhood-stock-token-api"


def test_stock_token_payloads_are_reused_within_their_cache_windows() -> None:
    client = _HTTPClient([_fixture("stock_assets.json"), _fixture("stock_price_test.json")])
    provider = RobinhoodStockTokenPricingProvider(
        fallback_provider=_FallbackPricingProvider(),
        http_client=client,  # type: ignore[arg-type]
        cache_store=_MemoryCache(),
        now_func=lambda: _NOW,
    )

    first = provider.get_token_prices(Chain.ROBINHOOD, [_ACTIVE_CONTRACT])
    second = provider.get_token_prices(Chain.ROBINHOOD, [_ACTIVE_CONTRACT])

    assert first == second
    assert [request.url for request in client.requests] == [
        "https://api.robinhood.com/rhj/assets",
        "https://api.robinhood.com/rhj/prices/TEST",
    ]


def test_inactive_stock_token_is_identified_but_not_priced() -> None:
    client = _HTTPClient([_fixture("stock_assets.json")])
    provider = RobinhoodStockTokenPricingProvider(
        fallback_provider=_FallbackPricingProvider({_INACTIVE_CONTRACT: Decimal("999")}),
        http_client=client,  # type: ignore[arg-type]
        cache_store=_MemoryCache(),
        now_func=lambda: _NOW,
    )

    metadata = provider.get_stock_token_metadata(_INACTIVE_CONTRACT)
    prices = provider.get_token_prices(Chain.ROBINHOOD, [_INACTIVE_CONTRACT])

    assert metadata is not None
    assert metadata.status is StockTokenStatus.INACTIVE
    assert prices == {}
    assert len(client.requests) == 1
    assert all("/prices/" not in request.url for request in client.requests)


def test_quote_requires_the_same_chain_and_contract_deployment() -> None:
    quote_payload = json.loads(_fixture("stock_price_test.json").data)
    quote_payload["quotes"][0]["deployments"][0]["contractAddress"] = (
        "0x9999999999999999999999999999999999999999"
    )
    client = _HTTPClient([_fixture("stock_assets.json"), _response(quote_payload)])
    provider = RobinhoodStockTokenPricingProvider(
        fallback_provider=_FallbackPricingProvider({_ACTIVE_CONTRACT: Decimal("999")}),
        http_client=client,  # type: ignore[arg-type]
        now_func=lambda: _NOW,
    )

    assert provider.get_token_prices(Chain.ROBINHOOD, [_ACTIVE_CONTRACT]) == {}


def test_non_robinhood_pricing_stays_with_the_configured_provider() -> None:
    fallback = _FallbackPricingProvider({_ACTIVE_CONTRACT: Decimal("12.50")})
    provider = RobinhoodStockTokenPricingProvider(fallback_provider=fallback)

    prices = provider.get_token_prices(Chain.ETHEREUM, [_ACTIVE_CONTRACT])

    assert prices == {_ACTIVE_CONTRACT: Decimal("12.50")}
    assert fallback.token_requests == [(Chain.ETHEREUM, [_ACTIVE_CONTRACT])]


def test_stock_token_rate_limit_is_retried() -> None:
    delays: list[float] = []
    client = _HTTPClient(
        [
            HTTPResponse(status_code=429, data=b"{}", headers={}),
            _fixture("stock_assets.json"),
            _fixture("stock_price_test.json"),
        ]
    )
    provider = RobinhoodStockTokenPricingProvider(
        fallback_provider=_FallbackPricingProvider(),
        http_client=client,  # type: ignore[arg-type]
        retry_attempts=2,
        retry_initial_delay_seconds=0.1,
        sleep_func=delays.append,
        now_func=lambda: _NOW,
    )

    prices = provider.get_token_prices(Chain.ROBINHOOD, [_ACTIVE_CONTRACT])

    assert prices[_ACTIVE_CONTRACT] == Decimal("301.5")
    assert delays == [0.1]


def test_stale_source_quote_is_not_used_for_portfolio_value() -> None:
    client = _HTTPClient([_fixture("stock_assets.json"), _fixture("stock_price_test.json")])
    provider = RobinhoodStockTokenPricingProvider(
        fallback_provider=_FallbackPricingProvider({_ACTIVE_CONTRACT: Decimal("999")}),
        http_client=client,  # type: ignore[arg-type]
        now_func=lambda: datetime(2026, 9, 24, 2, 0, tzinfo=UTC),
    )

    prices = provider.get_token_prices(Chain.ROBINHOOD, [_ACTIVE_CONTRACT])

    assert prices == {}


def test_corporate_actions_are_filtered_by_exact_deployment() -> None:
    client = _HTTPClient([_fixture("corporate_actions.json")])
    provider = RobinhoodStockTokenPricingProvider(
        fallback_provider=_FallbackPricingProvider(),
        http_client=client,  # type: ignore[arg-type]
    )

    actions = provider.get_corporate_actions(_ACTIVE_CONTRACT)

    assert len(actions) == 1
    assert actions[0].action_type is CorporateActionType.FORWARD_SPLIT
    assert actions[0].status is CorporateActionStatus.COMPLETED
    assert actions[0].process_date is not None
    assert dict(actions[0].details) == {
        "newRate": "2",
        "oldRate": "1",
        "underlyingSymbol": "TEST",
    }
    assert client.requests[0].url.endswith("/corporate-actions")


class _FallbackPricingProvider(PricingProvider):
    def __init__(self, token_prices: dict[str, Decimal] | None = None) -> None:
        self._token_prices = token_prices or {}
        self.token_requests: list[tuple[Chain, list[str]]] = []

    def get_native_price(self, chain: Chain) -> Decimal | None:
        _ = chain
        return Decimal("3200")

    def get_token_prices(
        self,
        chain: Chain,
        contract_addresses: list[str],
    ) -> dict[str, Decimal]:
        self.token_requests.append((chain, contract_addresses))
        return {
            address: self._token_prices[address]
            for address in contract_addresses
            if address in self._token_prices
        }


class _HTTPClient:
    def __init__(self, responses: list[HTTPResponse]) -> None:
        self._responses = responses
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError(f"Unexpected request: {request.url}")
        return self._responses.pop(0)


class _MemoryCache:
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._values.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        _ = ttl_seconds
        self._values[key] = value


def _fixture(name: str) -> HTTPResponse:
    return HTTPResponse(
        status_code=200,
        data=(_FIXTURE_ROOT / name).read_bytes(),
        headers={"content-type": "application/json"},
    )


def _response(payload: object) -> HTTPResponse:
    return HTTPResponse(
        status_code=200,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
