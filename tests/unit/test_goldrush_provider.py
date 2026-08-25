"""Test GoldRush REST normalization, paging, and safe failure mapping.

The tests use local HTTP recordings and never contact GoldRush.
They cover bearer authentication, supported chains, decoded token events, cursors, and retries.
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
from oracle41_open.providers.goldrush import GoldRushProvider
from oracle41_open.providers.http_client import (
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPRequest,
    HTTPResponse,
)

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


def _native_payload() -> dict[str, object]:
    return {
        "data": {
            "items": [
                {
                    "native_token": True,
                    "balance": "1000000000000000000",
                    "contract_decimals": 18,
                }
            ]
        },
        "error": False,
    }


@pytest.mark.parametrize(
    ("chain", "expected_name"),
    [
        (Chain.ETHEREUM, "eth-mainnet"),
        (Chain.OPTIMISM, "optimism-mainnet"),
        (Chain.POLYGON, "matic-mainnet"),
        (Chain.BASE, "base-mainnet"),
        (Chain.ARBITRUM, "arbitrum-mainnet"),
    ],
)
def test_goldrush_uses_documented_chains_and_bearer_authentication(
    chain: Chain,
    expected_name: str,
) -> None:
    client = _HTTPClient([_response(_native_payload())])
    provider = GoldRushProvider(api_key="secret-key", http_client=client)

    assert provider.get_native_balance(_WALLET, chain) == Decimal("1")

    request = client.requests[0]
    assert f"/v1/{expected_name}/" in request.url
    assert request.headers == {
        "Accept": "application/json",
        "Authorization": "Bearer secret-key",
    }
    assert "secret-key" not in request.url


def test_goldrush_maps_balance_metadata_and_has_no_balance_cursor() -> None:
    client = _HTTPClient(
        [
            _response(
                {
                    "data": {
                        "items": [
                            {
                                "contract_address": _TOKEN,
                                "balance": "2500000",
                                "contract_decimals": 6,
                                "contract_name": "USD Coin",
                                "contract_ticker_symbol": "USDC",
                                "is_spam": False,
                            }
                        ]
                    },
                    "error": False,
                }
            )
        ]
    )
    provider = GoldRushProvider(api_key="fixture-key", http_client=client)

    page = provider.get_token_balances(_WALLET, Chain.ETHEREUM)

    assert page.next_page_key is None
    assert page.balances[0].balance_decimal == Decimal("2.5")
    assert page.balances[0].token.is_verified is True
    assert parse_qs(urlparse(client.requests[0].url).query) == {
        "no-spam": ["true"],
        "nft": ["false"],
    }
    with pytest.raises(ProviderResponseError, match="do not use page cursors"):
        provider.get_token_balances(_WALLET, Chain.ETHEREUM, page_key="2")


def test_goldrush_expands_erc1155_batch_and_preserves_approval_revocation() -> None:
    events = [
        {
            "log_offset": 4,
            "sender_address": _TOKEN,
            "sender_contract_ticker_symbol": "GAME",
            "decoded": {
                "name": "TransferBatch",
                "params": [
                    {"name": "from", "value": _WALLET},
                    {"name": "to", "value": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"},
                    {"name": "ids", "value": ["7", "8"]},
                    {"name": "values", "value": "[\"2\", \"3\"]"},
                ],
            },
        },
        {
            "log_offset": 5,
            "sender_address": _TOKEN,
            "sender_contract_ticker_symbol": "GAME",
            "decoded": {
                "name": "ApprovalForAll",
                "params": [
                    {"name": "owner", "value": _WALLET},
                    {"name": "operator", "value": "0xcccccccccccccccccccccccccccccccccccccccc"},
                    {"name": "approved", "value": False},
                ],
            },
        },
    ]
    client = _HTTPClient(
        [
            _response(
                {
                    "data": {
                        "items": [
                            {
                                "tx_hash": "0xbatch",
                                "block_height": 200,
                                "block_signed_at": "2026-01-20T10:00:00Z",
                                "log_events": events,
                            }
                        ],
                        "pagination": {"has_more": True},
                    },
                    "error": False,
                }
            )
        ]
    )
    provider = GoldRushProvider(api_key="fixture-key", http_client=client)

    page = provider.get_token_transfers(
        _WALLET,
        _TOKEN,
        Chain.ETHEREUM,
        include_approvals=True,
    )

    assert page.next_cursor == "1"
    assert [item.category for item in page.items].count(ActivityCategory.ERC1155) == 2
    approval = next(item for item in page.items if item.category is ActivityCategory.APPROVAL)
    assert approval.raw_value == "0"
    assert approval.value_decimal == Decimal("0")
    assert len({item.id for item in page.items}) == 3


def test_goldrush_filters_old_activity_locally_and_uses_page_cursor() -> None:
    client = _HTTPClient(
        [
            _response(
                {
                    "data": {
                        "items": [
                            {
                                "tx_hash": "0xold",
                                "block_height": 99,
                                "block_signed_at": "2026-01-20T10:00:00Z",
                                "value": "1000000000000000000",
                                "log_events": [],
                            }
                        ],
                        "links": {"next": None},
                    },
                    "error": False,
                }
            )
        ]
    )
    provider = GoldRushProvider(api_key="fixture-key", http_client=client)

    page = provider.get_activity(_WALLET, Chain.ETHEREUM, cursor="2", from_block=100)

    assert page.items == []
    assert page.query_from_block == 100
    assert "/page/2/" in client.requests[0].url
    with pytest.raises(ProviderResponseError, match="cursor is invalid"):
        provider.get_activity(_WALLET, Chain.ETHEREUM, cursor="bad")


@pytest.mark.parametrize(
    ("status", "expected_error"),
    [
        (401, ProviderAuthError),
        (402, ProviderResponseError),
        (429, ProviderRateLimitError),
        (504, ProviderTimeoutError),
        (503, ProviderNetworkError),
    ],
)
def test_goldrush_maps_http_failures(
    status: int,
    expected_error: type[Exception],
) -> None:
    provider = GoldRushProvider(
        api_key="fixture-key",
        http_client=_HTTPClient([_response({}, status=status)]),
        retry_attempts=1,
    )

    with pytest.raises(expected_error):
        provider.get_native_balance(_WALLET, Chain.ETHEREUM)


def test_goldrush_retries_rate_limit_then_succeeds() -> None:
    delays: list[float] = []
    client = _HTTPClient([_response({}, status=429), _response(_native_payload())])
    provider = GoldRushProvider(
        api_key="fixture-key",
        http_client=client,
        retry_attempts=2,
        retry_initial_delay_seconds=0.1,
        sleep_func=delays.append,
    )

    assert provider.get_native_balance(_WALLET, Chain.ETHEREUM) == Decimal("1")
    assert delays == [0.1]


@pytest.mark.parametrize(
    ("transport_error", "expected_error"),
    [
        (HTTPClientTimeoutError("timeout"), ProviderTimeoutError),
        (HTTPClientNetworkError("network"), ProviderNetworkError),
    ],
)
def test_goldrush_maps_transport_errors(
    transport_error: Exception,
    expected_error: type[Exception],
) -> None:
    provider = GoldRushProvider(
        api_key="fixture-key",
        http_client=_HTTPClient([transport_error]),
        retry_attempts=1,
    )

    with pytest.raises(expected_error):
        provider.get_native_balance(_WALLET, Chain.ETHEREUM)


def test_goldrush_maps_wrapped_api_errors_and_malformed_json() -> None:
    provider = GoldRushProvider(
        api_key="fixture-key",
        http_client=_HTTPClient([_response({"error": True, "error_code": 401})]),
    )
    with pytest.raises(ProviderAuthError):
        provider.get_native_balance(_WALLET, Chain.ETHEREUM)

    malformed = GoldRushProvider(
        api_key="fixture-key",
        http_client=_HTTPClient([HTTPResponse(status_code=200, data=b"not-json", headers={})]),
    )
    with pytest.raises(ProviderResponseError, match="invalid JSON"):
        malformed.get_native_balance(_WALLET, Chain.ETHEREUM)
