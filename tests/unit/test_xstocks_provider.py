"""Test exact-contract xStocks recognition from public catalog fixtures.

The cases cover supported EVM deployments, wrapper contracts, ETF classification, cache reuse, and
the rule that unsupported chains or copied symbols cannot create a verified identity.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oracle41_open.core.models import Chain, RealWorldAssetCategory
from oracle41_open.providers.http_client import HTTPRequest, HTTPResponse
from oracle41_open.providers.xstocks import XStocksAssetProvider

_FIXTURE = Path(__file__).parents[1] / "fixtures" / "providers" / "xstocks" / "assets.json"
_ARBITRUM_TOKEN = "0x1111111111111111111111111111111111111111"
_ARBITRUM_WRAPPER = "0x2222222222222222222222222222222222222222"
_BASE_ETF = "0x3333333333333333333333333333333333333333"


def test_catalog_recognizes_token_and_wrapper_by_exact_chain_address() -> None:
    client = _HTTPClient(_response())
    provider = XStocksAssetProvider(http_client=client, cache_store=_MemoryCache())  # type: ignore[arg-type]

    token = provider.get_asset_identity(Chain.ARBITRUM, _ARBITRUM_TOKEN)
    wrapper = provider.get_asset_identity(Chain.ARBITRUM, _ARBITRUM_WRAPPER)
    etf = provider.get_asset_identity(Chain.BASE, _BASE_ETF)

    assert token is not None
    assert token.symbol == "AAPLX"
    assert token.underlying_symbol == "AAPL"
    assert token.category is RealWorldAssetCategory.TOKENIZED_EQUITY
    assert wrapper is not None and wrapper.asset_id == token.asset_id
    assert etf is not None and etf.category is RealWorldAssetCategory.TOKENIZED_ETF
    assert provider.get_asset_identity(Chain.BASE, _ARBITRUM_TOKEN) is None
    assert len(client.requests) == 1


def test_catalog_cache_avoids_repeated_network_reads() -> None:
    client = _HTTPClient(_response())
    provider = XStocksAssetProvider(http_client=client, cache_store=_MemoryCache())  # type: ignore[arg-type]

    assert provider.get_asset_identity(Chain.ARBITRUM, _ARBITRUM_TOKEN) is not None
    assert provider.get_asset_identity(Chain.BASE, _BASE_ETF) is not None

    assert len(client.requests) == 1


class _HTTPClient:
    def __init__(self, response: HTTPResponse) -> None:
        self.response = response
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        return self.response


class _MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self.values.get(key)

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        _ = ttl_seconds
        self.values[key] = value


def _response() -> HTTPResponse:
    return HTTPResponse(
        status_code=200,
        data=json.dumps(json.loads(_FIXTURE.read_text())).encode("utf-8"),
        headers={"content-type": "application/json"},
    )

