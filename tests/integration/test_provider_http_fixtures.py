"""Test provider adapters against recorded HTTP fixtures.

The cases verify real response shapes without making live network requests.
Fixture tests protect pagination and response mapping across provider changes.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from oracle41_open.core.models import (
    ActivityCategory,
    Chain,
    ProviderAuthError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    TransactionInspection,
    WalletActionKind,
)
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
from oracle41_open.core.services.action_normalizer import WalletActionNormalizer
from oracle41_open.providers.alchemy import AlchemyProvider
from oracle41_open.providers.ankr import AnkrProvider
from oracle41_open.providers.evm_rpc import EVMJSONRPCProvider
from oracle41_open.providers.http_client import (
    HTTPClientTimeoutError,
    HTTPRequest,
    HTTPResponse,
)
from oracle41_open.providers.jsonrpc import JSONRPCClient

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "providers"


def test_alchemy_http_fixture_parses_native_balance() -> None:
    http_client = _FixtureHTTPClient(
        events=[_fixture_response("alchemy/eth_getBalance_success.json")]
    )
    provider = AlchemyProvider(
        api_key="alchemy-key",
        rpc_client=JSONRPCClient(http_client=http_client),
    )

    value = provider.get_native_balance(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert value == Decimal("1")
    assert len(http_client.requests) == 1
    assert _request_method(http_client.requests[0]) == "eth_getBalance"


def test_alchemy_http_fixture_retries_http_429_then_succeeds() -> None:
    http_client = _FixtureHTTPClient(
        events=[
            HTTPResponse(
                status_code=429,
                data=b"{}",
                headers={"content-type": "application/json"},
            ),
            _fixture_response("alchemy/eth_getBalance_success.json"),
        ]
    )
    delays: list[float] = []
    provider = AlchemyProvider(
        api_key="alchemy-key",
        rpc_client=JSONRPCClient(http_client=http_client),
        retry_attempts=2,
        retry_initial_delay_seconds=0.01,
        sleep_func=delays.append,
    )

    value = provider.get_native_balance(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert value == Decimal("1")
    assert len(http_client.requests) == 2
    assert delays == [pytest.approx(0.01)]


def test_alchemy_http_fixture_maps_remote_auth_error() -> None:
    http_client = _FixtureHTTPClient(
        events=[_fixture_response("alchemy/eth_getBalance_auth_error.json")]
    )
    provider = AlchemyProvider(
        api_key="alchemy-key",
        rpc_client=JSONRPCClient(http_client=http_client),
        retry_attempts=1,
    )

    with pytest.raises(ProviderAuthError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_alchemy_http_fixture_parses_erc721_and_erc1155_token_transfers() -> None:
    token_address = "0x1111111111111111111111111111111111111111"
    http_client = _FixtureHTTPClient(
        events=[
            _fixture_response("alchemy/alchemy_getAssetTransfers_token_outgoing.json"),
            _fixture_response("alchemy/alchemy_getAssetTransfers_token_incoming_empty.json"),
        ]
    )
    provider = AlchemyProvider(
        api_key="alchemy-key",
        rpc_client=JSONRPCClient(http_client=http_client),
    )

    page = provider.get_token_transfers(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address=token_address,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )

    assert len(page.items) == 2
    by_hash = {item.tx_hash: item for item in page.items}
    assert by_hash["0xnft721"].category is ActivityCategory.ERC721
    assert by_hash["0xnft721"].value_decimal == Decimal("1")
    assert by_hash["0xnft1155"].category is ActivityCategory.ERC1155
    assert by_hash["0xnft1155"].value_decimal == Decimal("3")
    first_payload = _request_payload(http_client.requests[0])
    assert first_payload["contractAddresses"] == [token_address]
    assert first_payload["category"] == ["erc20", "erc721", "erc1155"]


def test_ankr_http_fixture_parses_token_balances_page() -> None:
    http_client = _FixtureHTTPClient(
        events=[_fixture_response("ankr/ankr_getAccountBalance_page.json")]
    )
    provider = AnkrProvider(
        api_key="ankr-key",
        rpc_client=JSONRPCClient(http_client=http_client),
    )

    page = provider.get_token_balances(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert page.next_page_key == "p2"
    assert len(page.balances) == 1
    assert page.balances[0].token.symbol == "USDC"
    assert page.balances[0].balance_decimal == Decimal("1.23")
    assert _request_method(http_client.requests[0]) == "ankr_getAccountBalance"


def test_ankr_http_fixture_parses_activity_categories_and_cursor() -> None:
    http_client = _FixtureHTTPClient(
        events=[_fixture_response("ankr/ankr_getTokenTransfers_page.json")]
    )
    provider = AnkrProvider(
        api_key="ankr-key",
        rpc_client=JSONRPCClient(http_client=http_client),
    )

    page = provider.get_activity(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert page.next_cursor == "next-2"
    assert len(page.items) == 2
    assert page.items[0].category is ActivityCategory.ERC721
    assert page.items[1].category is ActivityCategory.ERC20


def test_ankr_http_fixture_maps_timeout_error() -> None:
    http_client = _FixtureHTTPClient(
        events=[HTTPClientTimeoutError("simulated timeout")]
    )
    provider = AnkrProvider(
        api_key="ankr-key",
        rpc_client=JSONRPCClient(http_client=http_client),
        retry_attempts=1,
    )

    with pytest.raises(ProviderTimeoutError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_ankr_http_fixture_maps_remote_auth_error() -> None:
    http_client = _FixtureHTTPClient(
        events=[_fixture_response("ankr/eth_getBalance_auth_error.json")]
    )
    provider = AnkrProvider(
        api_key="ankr-key",
        rpc_client=JSONRPCClient(http_client=http_client),
        retry_attempts=1,
    )

    with pytest.raises(ProviderAuthError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_ankr_http_fixture_includes_approval_events_on_first_token_page() -> None:
    token_address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    http_client = _FixtureHTTPClient(
        events=[
            _fixture_response("ankr/ankr_getTokenTransfers_empty.json"),
            _fixture_response("ankr/eth_call_decimals_success.json"),
            _fixture_response("ankr/eth_blockNumber_success.json"),
            _fixture_response("ankr/eth_getLogs_approval_owner.json"),
            _fixture_response("ankr/eth_getLogs_empty.json"),
            _fixture_response("ankr/eth_getLogs_approval_for_all_owner.json"),
            _fixture_response("ankr/eth_getLogs_empty.json"),
            _fixture_response("ankr/eth_getBlockByNumber_success.json"),
        ]
    )
    provider = AnkrProvider(
        api_key="ankr-key",
        rpc_client=JSONRPCClient(http_client=http_client),
    )

    page = provider.get_token_transfers(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address=token_address,
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )

    assert len(page.items) == 2
    by_hash = {item.tx_hash: item for item in page.items}
    assert by_hash["0xapproval1"].category is ActivityCategory.APPROVAL
    assert by_hash["0xapproval1"].value_decimal == Decimal("1")
    assert by_hash["0xapproval2"].category is ActivityCategory.APPROVAL
    assert by_hash["0xapproval2"].value_decimal == Decimal("1")
    assert len([request for request in http_client.requests if _request_method(request) == "eth_getLogs"]) == 4


def test_alchemy_http_fixture_maps_429_to_rate_limit_error_when_not_retried() -> None:
    http_client = _FixtureHTTPClient(
        events=[
            HTTPResponse(
                status_code=429,
                data=b"{}",
                headers={"content-type": "application/json"},
            )
        ]
    )
    provider = AlchemyProvider(
        api_key="alchemy-key",
        rpc_client=JSONRPCClient(http_client=http_client),
        retry_attempts=1,
    )

    with pytest.raises(ProviderRateLimitError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_shared_receipt_fixture_decodes_identically_across_providers() -> None:
    alchemy = _inspection_from_fixtures("alchemy")
    ankr = _inspection_from_fixtures("ankr")

    # Provider and fetch metadata are provenance, not decoding inputs.
    assert replace(
        alchemy,
        source_provider="provider",
        fetched_at=ankr.fetched_at,
    ) == replace(ankr, source_provider="provider")

    decoder = StandardABIDecoder()
    alchemy_decoding = decoder.decode(alchemy)
    ankr_decoding = decoder.decode(ankr)
    assert alchemy_decoding == ankr_decoding

    normalizer = WalletActionNormalizer()
    alchemy_actions = normalizer.normalize(alchemy, alchemy_decoding, None)
    ankr_actions = normalizer.normalize(ankr, ankr_decoding, None)
    assert alchemy_actions == ankr_actions
    assert len(alchemy_actions) == 1
    assert alchemy_actions[0].kind is WalletActionKind.TRANSFER
    assert alchemy_actions[0].evidence[0].reference == "log:3"


def _inspection_from_fixtures(provider_name: str) -> TransactionInspection:
    http_client = _FixtureHTTPClient(
        events=[
            _fixture_response(f"{provider_name}/eth_getTransactionByHash_shared.json"),
            _fixture_response(f"{provider_name}/eth_getTransactionReceipt_shared.json"),
        ]
    )
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://fixture.invalid"},
        source_name=provider_name,
        rpc_client=JSONRPCClient(http_client=http_client),
    )
    return provider.get_transaction_inspection("0x" + "ab" * 32, Chain.ETHEREUM)


class _FixtureHTTPClient:
    def __init__(self, events: list[HTTPResponse | Exception]) -> None:
        self._events = events
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self._events:
            raise AssertionError("No fixture HTTP events left for request.")
        next_event = self._events.pop(0)
        if isinstance(next_event, Exception):
            raise next_event
        return next_event


def _fixture_response(relative_path: str) -> HTTPResponse:
    payload = (_FIXTURE_ROOT / relative_path).read_bytes()
    return HTTPResponse(
        status_code=200,
        data=payload,
        headers={"content-type": "application/json"},
    )


def _request_method(request: HTTPRequest) -> str:
    if request.json is None:
        raise AssertionError("Expected JSON-RPC payload.")
    raw_method = request.json.get("method")
    if not isinstance(raw_method, str):
        raise AssertionError("Expected JSON-RPC method string.")
    return raw_method


def _request_payload(request: HTTPRequest) -> dict[str, Any]:
    if request.json is None:
        raise AssertionError("Expected JSON-RPC payload.")
    raw_params = request.json.get("params")
    if not isinstance(raw_params, list) or not raw_params:
        raise AssertionError("Expected JSON-RPC params list with one payload entry.")
    first = raw_params[0]
    if not isinstance(first, dict):
        raise AssertionError("Expected JSON-RPC payload object.")
    return first
