"""Test Moralis REST normalization, pagination, and safe failure mapping.

The tests use recorded HTTP responses and never contact Moralis.
They cover key headers, all supported chains, active approvals, cursor ownership, and retries.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from decimal import Decimal
from urllib.parse import parse_qs, urlparse

import pytest

from oracle41_open.core.models import (
    ActivityCategory,
    Chain,
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
)
from oracle41_open.providers.http_client import (
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
    HTTPResponse,
)
from oracle41_open.providers.moralis import MoralisProvider

_WALLET = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_TOKEN = "0x9999999999999999999999999999999999999999"


class _HTTPClient:
    def __init__(self, responses: Iterable[HTTPResponse | Exception]) -> None:
        self.responses = list(responses)
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("No HTTP response remains.")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _response(payload: object, status: int = 200) -> HTTPResponse:
    return HTTPResponse(
        status_code=status,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )


@pytest.mark.parametrize(
    ("chain", "expected_code"),
    [
        (Chain.ETHEREUM, "eth"),
        (Chain.OPTIMISM, "optimism"),
        (Chain.POLYGON, "polygon"),
        (Chain.BASE, "base"),
        (Chain.ARBITRUM, "arbitrum"),
    ],
)
def test_moralis_uses_documented_chain_identifiers(
    chain: Chain,
    expected_code: str,
) -> None:
    client = _HTTPClient(
        [
            _response({"balance": "1000000000000000000"}),
            _response({"cursor": None, "result": []}),
        ]
    )
    provider = MoralisProvider(api_key="secret-key", http_client=client)

    assert provider.get_native_balance(_WALLET, chain) == Decimal("1")
    provider.get_token_balances(_WALLET, chain)

    native_query = parse_qs(urlparse(client.requests[0].url).query)
    balance_query = parse_qs(urlparse(client.requests[1].url).query)
    assert native_query["chain"] == [expected_code]
    assert balance_query["chain"] == [expected_code]
    assert client.requests[0].headers == {
        "Accept": "application/json",
        "X-API-Key": "secret-key",
    }
    assert "secret-key" not in client.requests[0].url


def test_moralis_maps_active_approval_for_requested_token() -> None:
    client = _HTTPClient(
        [
            _response({"cursor": None, "result": []}),
            _response({"cursor": None, "result": []}),
            _response(
                {
                    "cursor": None,
                    "result": [
                        {
                            "block_number": 123,
                            "block_timestamp": "2026-01-20T10:03:00Z",
                            "transaction_hash": "0xapproval",
                            "value": "1000000",
                            "token": {
                                "address": _TOKEN,
                                "symbol": "USDC",
                                "verified_contract": True,
                            },
                            "spender": {
                                "address": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                            },
                        }
                    ],
                }
            ),
        ]
    )
    provider = MoralisProvider(api_key="fixture-key", http_client=client)

    page = provider.get_token_transfers(
        _WALLET,
        _TOKEN,
        Chain.ETHEREUM,
        include_approvals=True,
    )

    assert len(page.items) == 1
    assert page.items[0].category is ActivityCategory.APPROVAL
    assert page.items[0].raw_value == "1000000"
    assert page.items[0].to_address == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_moralis_token_cursor_continues_only_unfinished_resources() -> None:
    first_client = _HTTPClient(
        [
            _response({"cursor": "erc20-page-2", "result": []}),
            _response({"cursor": None, "result": []}),
        ]
    )
    provider = MoralisProvider(api_key="fixture-key", http_client=first_client)

    first_page = provider.get_token_transfers(_WALLET, _TOKEN, Chain.ETHEREUM)

    assert first_page.next_cursor is not None
    second_client = _HTTPClient([_response({"cursor": None, "result": []})])
    provider = MoralisProvider(api_key="fixture-key", http_client=second_client)
    second_page = provider.get_token_transfers(
        _WALLET,
        _TOKEN,
        Chain.ETHEREUM,
        cursor=first_page.next_cursor,
    )

    assert second_page.next_cursor is None
    assert len(second_client.requests) == 1
    request = second_client.requests[0]
    assert urlparse(request.url).path.endswith("/erc20/transfers")
    assert parse_qs(urlparse(request.url).query)["cursor"] == ["erc20-page-2"]


def test_moralis_rejects_cursor_when_approval_mode_changes() -> None:
    client = _HTTPClient(
        [
            _response({"cursor": "erc20-page-2", "result": []}),
            _response({"cursor": None, "result": []}),
        ]
    )
    provider = MoralisProvider(api_key="fixture-key", http_client=client)
    cursor = provider.get_token_transfers(
        _WALLET,
        _TOKEN,
        Chain.ETHEREUM,
    ).next_cursor

    with pytest.raises(ProviderResponseError, match="does not match"):
        provider.get_token_transfers(
            _WALLET,
            _TOKEN,
            Chain.ETHEREUM,
            cursor=cursor,
            include_approvals=True,
        )


def test_moralis_maps_authentication_without_retrying() -> None:
    client = _HTTPClient([_response({}, status=401)])
    provider = MoralisProvider(api_key="bad-key", http_client=client)

    with pytest.raises(ProviderAuthError, match="not authorized"):
        provider.get_native_balance(_WALLET, Chain.ETHEREUM)

    assert len(client.requests) == 1


def test_moralis_retries_rate_limit_then_succeeds() -> None:
    delays: list[float] = []
    client = _HTTPClient(
        [
            _response({}, status=429),
            _response({"balance": "1000000000000000000"}),
        ]
    )
    provider = MoralisProvider(
        api_key="fixture-key",
        http_client=client,
        retry_attempts=2,
        retry_initial_delay_seconds=0.1,
        sleep_func=delays.append,
    )

    assert provider.get_native_balance(_WALLET, Chain.ETHEREUM) == Decimal("1")
    assert delays == [0.1]


def test_moralis_raises_rate_limit_after_retry_budget() -> None:
    client = _HTTPClient([_response({}, status=429)])
    provider = MoralisProvider(
        api_key="fixture-key",
        http_client=client,
        retry_attempts=1,
    )

    with pytest.raises(ProviderRateLimitError, match="rate-limited"):
        provider.get_native_balance(_WALLET, Chain.ETHEREUM)


@pytest.mark.parametrize(
    ("transport_error", "expected_error"),
    [
        (HTTPClientTimeoutError("timeout"), ProviderTimeoutError),
        (HTTPClientNetworkError("network"), ProviderNetworkError),
    ],
)
def test_moralis_maps_transport_errors(
    transport_error: Exception,
    expected_error: type[Exception],
) -> None:
    provider = MoralisProvider(
        api_key="fixture-key",
        http_client=_HTTPClient([transport_error]),
        retry_attempts=1,
    )

    with pytest.raises(expected_error):
        provider.get_native_balance(_WALLET, Chain.ETHEREUM)


def test_moralis_rejects_malformed_json() -> None:
    client = _HTTPClient(
        [HTTPResponse(status_code=200, data=b"not-json", headers={})]
    )
    provider = MoralisProvider(api_key="fixture-key", http_client=client)

    with pytest.raises(ProviderResponseError, match="invalid JSON"):
        provider.get_native_balance(_WALLET, Chain.ETHEREUM)
