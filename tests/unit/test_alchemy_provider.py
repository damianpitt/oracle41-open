from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import pytest

from oracle41_open.core.models import (
    ActivityCategory,
    Chain,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from oracle41_open.providers.alchemy import AlchemyPricingProvider, AlchemyProvider
from oracle41_open.providers.http_client import HTTPRequest, HTTPResponse
from oracle41_open.providers.jsonrpc import (
    JSONRPCHTTPError,
    JSONRPCRemoteError,
    JSONRPCTimeoutError,
)


def test_alchemy_provider_parses_native_balance_hex() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["eth_getBalance"] = "0xde0b6b3a7640000"
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    result = provider.get_native_balance(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert result == Decimal("1")
    assert rpc_client.calls[0]["method"] == "eth_getBalance"
    assert "eth-mainnet.g.alchemy.com/v2/alchemy-key" in rpc_client.calls[0]["url"]


def test_alchemy_provider_does_not_map_unexpected_rpc_client_errors() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.errors["eth_getBalance"] = RuntimeError("implementation defect")
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    with pytest.raises(RuntimeError, match="implementation defect"):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_alchemy_provider_parses_token_balances_page_and_metadata() -> None:
    contract = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getTokenBalances"] = {
        "tokenBalances": [
            {"contractAddress": contract, "tokenBalance": "0x0f4240"},
            {"contractAddress": contract, "tokenBalance": "0x0"},
            {"contractAddress": "bad", "tokenBalance": "0x01"},
        ],
        "pageKey": "next-page",
    }
    rpc_client.responses["alchemy_getTokenMetadata"] = {
        "symbol": "USDC",
        "name": "USD Coin",
        "decimals": "6",
    }
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    page = provider.get_token_balances(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
        page_key="p1",
    )

    assert page.next_page_key == "next-page"
    assert len(page.balances) == 1
    assert page.balances[0].token.symbol == "USDC"
    assert page.balances[0].token.name == "USD Coin"
    assert page.balances[0].token.decimals == 6
    assert page.balances[0].balance_decimal == Decimal("1")


def test_alchemy_provider_uses_unknown_metadata_when_metadata_call_fails() -> None:
    contract = "0x1111111111111111111111111111111111111111"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getTokenBalances"] = {
        "tokenBalances": [{"contractAddress": contract, "tokenBalance": "0x02"}],
        "pageKey": None,
    }
    rpc_client.errors["alchemy_getTokenMetadata"] = JSONRPCRemoteError("metadata unavailable")
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    page = provider.get_token_balances(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert page.next_page_key is None
    assert len(page.balances) == 1
    token = page.balances[0].token
    assert token.symbol == "UNKNOWN"
    assert token.name == "Unknown"
    assert token.decimals == 0
    assert token.is_verified is False
    assert page.balances[0].balance_decimal == Decimal("2")


def test_alchemy_pricing_provider_parses_native_price_from_symbol_endpoint() -> None:
    http_client = _FakeHTTPClient(
        responses=[
            _json_response(
                {
                    "data": [
                        {
                            "symbol": "ETH",
                            "prices": [{"currency": "usd", "value": "3200.12"}],
                        }
                    ]
                }
            )
        ]
    )
    provider = AlchemyPricingProvider(api_key="alchemy-key", http_client=http_client)

    native_price = provider.get_native_price(Chain.ETHEREUM)

    assert native_price == Decimal("3200.12")
    assert "/tokens/by-symbol?" in http_client.requests[0].url
    assert "symbols=ETH" in http_client.requests[0].url


def test_alchemy_pricing_provider_parses_token_prices_from_address_endpoint() -> None:
    addr1 = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    addr2 = "0xdac17f958d2ee523a2206206994597c13d831ec7"
    http_client = _FakeHTTPClient(
        responses=[
            _json_response(
                {
                    "data": [
                        {
                            "address": addr1,
                            "prices": [{"currency": "usd", "value": "1.01"}],
                        },
                        {"address": addr2, "error": "unsupported"},
                    ]
                }
            )
        ]
    )
    provider = AlchemyPricingProvider(api_key="alchemy-key", http_client=http_client)

    prices = provider.get_token_prices(chain=Chain.ETHEREUM, contract_addresses=[addr1, addr2])

    assert prices == {addr1.lower(): Decimal("1.01")}
    assert http_client.requests[0].url.endswith("/tokens/by-address")
    assert http_client.requests[0].method == "POST"
    assert http_client.requests[0].json is not None


def test_alchemy_provider_merges_incoming_and_outgoing_activity_pages() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getAssetTransfers"] = [
        {
            "transfers": [
                {
                    "blockNum": "0x10",
                    "hash": "0xout1",
                    "logIndex": "0x1",
                    "from": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "asset": "USDC",
                    "category": "erc20",
                    "value": "15",
                    "rawContract": {
                        "address": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                        "decimal": "6",
                        "value": "0x00e4e1c0",
                    },
                    "metadata": {"blockTimestamp": "2026-01-20T10:00:00Z"},
                }
            ],
            "pageKey": "from-next",
        },
        {
            "transfers": [
                {
                    "blockNum": "0x0f",
                    "hash": "0xin1",
                    "logIndex": "0x2",
                    "from": "0xcccccccccccccccccccccccccccccccccccccccc",
                    "to": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "asset": "ETH",
                    "category": "external",
                    "value": "0.25",
                    "rawContract": {"decimal": "18", "value": "0x3782dace9d900000"},
                    "metadata": {"blockTimestamp": "2026-01-19T10:00:00Z"},
                }
            ],
            "pageKey": "to-next",
        },
    ]
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    page = provider.get_activity(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        chain=Chain.ETHEREUM,
    )

    assert len(page.items) == 2
    assert page.items[0].tx_hash == "0xout1"
    assert page.items[0].asset_symbol == "USDC"
    assert page.items[0].value_decimal == Decimal("15")
    assert page.items[1].tx_hash == "0xin1"
    assert page.items[1].asset_symbol == "ETH"
    assert page.next_cursor is not None
    decoded_cursor = json.loads(page.next_cursor)
    assert decoded_cursor == {"from": "from-next", "to": "to-next"}


def test_alchemy_provider_token_transfers_use_contract_filter() -> None:
    token_address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getAssetTransfers"] = [
        {
            "transfers": [
                {
                    "blockNum": "0x10",
                    "hash": "0xtoken1",
                    "logIndex": "0x0",
                    "from": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "asset": "USDC",
                    "category": "erc20",
                    "value": "42",
                    "rawContract": {
                        "address": token_address,
                        "decimal": "6",
                        "value": "0x028fa6ae",
                    },
                    "metadata": {"blockTimestamp": "2026-01-20T10:00:00Z"},
                }
            ],
            "pageKey": None,
        },
        {"transfers": [], "pageKey": None},
    ]
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    page = provider.get_token_transfers(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address=token_address,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )

    assert len(page.items) == 1
    assert page.items[0].tx_hash == "0xtoken1"
    assert page.items[0].contract_address == token_address
    assert page.items[0].value_decimal == Decimal("42")
    first_call_params = rpc_client.calls[0]["params"]
    assert isinstance(first_call_params, list)
    payload = first_call_params[0]
    assert isinstance(payload, dict)
    assert payload["category"] == ["erc20", "erc721", "erc1155"]
    assert payload["contractAddresses"] == [token_address]


def test_alchemy_provider_token_transfers_include_approval_logs() -> None:
    wallet = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    token_address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    wallet_topic = "0x000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    approval_topic = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
    approval_for_all_topic = "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getAssetTransfers"] = [
        {"transfers": [], "pageKey": None},
        {"transfers": [], "pageKey": None},
    ]
    rpc_client.responses["alchemy_getTokenMetadata"] = {
        "symbol": "USDC",
        "name": "USD Coin",
        "decimals": "6",
    }
    rpc_client.responses["eth_blockNumber"] = "0x20000"
    rpc_client.responses["eth_getLogs"] = [
        [
            {
                "address": token_address,
                "blockNumber": "0x10",
                "transactionHash": "0xapproval1",
                "logIndex": "0x1",
                "topics": [
                    approval_topic,
                    wallet_topic,
                    "0x000000000000000000000000bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                ],
                "data": "0x00000000000000000000000000000000000000000000000000000000000f4240",
            }
        ],
        [],
        [
            {
                "address": token_address,
                "blockNumber": "0x10",
                "transactionHash": "0xapproval2",
                "logIndex": "0x2",
                "topics": [
                    approval_for_all_topic,
                    wallet_topic,
                    "0x000000000000000000000000cccccccccccccccccccccccccccccccccccccccc",
                ],
                "data": "0x0000000000000000000000000000000000000000000000000000000000000001",
            }
        ],
        [],
    ]
    rpc_client.responses["eth_getBlockByNumber"] = [
        {"timestamp": "0x65aa0f00"},
    ]
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    page = provider.get_token_transfers(
        address=wallet,
        token_address=token_address,
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )

    assert len(page.items) == 2
    approvals = {item.tx_hash: item for item in page.items}
    assert approvals["0xapproval1"].category is ActivityCategory.APPROVAL
    assert approvals["0xapproval1"].from_address == wallet
    assert approvals["0xapproval1"].to_address == "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    assert approvals["0xapproval1"].value_decimal == Decimal("1")
    assert approvals["0xapproval2"].category is ActivityCategory.APPROVAL
    assert approvals["0xapproval2"].from_address == wallet
    assert approvals["0xapproval2"].to_address == "0xcccccccccccccccccccccccccccccccccccccccc"
    assert approvals["0xapproval2"].value_decimal == Decimal("1")
    log_calls = [call for call in rpc_client.calls if call["method"] == "eth_getLogs"]
    assert len(log_calls) == 4
    for call in log_calls:
        log_filter = call["params"][0]
        assert log_filter["fromBlock"] == "0x7961"
        assert log_filter["toBlock"] == "0x20000"


def test_alchemy_provider_reports_incomplete_approval_history() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getAssetTransfers"] = [
        {"transfers": [], "pageKey": None},
        {"transfers": [], "pageKey": None},
    ]
    rpc_client.responses["alchemy_getTokenMetadata"] = {
        "symbol": "USDC",
        "name": "USD Coin",
        "decimals": "6",
    }
    rpc_client.responses["eth_blockNumber"] = "0x20000"
    rpc_client.errors["eth_getLogs"] = JSONRPCRemoteError("unauthorized")
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    with pytest.raises(ProviderError, match="approval history"):
        provider.get_token_transfers(
            address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            chain=Chain.ETHEREUM,
            include_approvals=True,
        )


def test_alchemy_provider_pages_approval_history_back_to_genesis() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getAssetTransfers"] = [
        {"transfers": [], "pageKey": None},
        {"transfers": [], "pageKey": None},
        {"transfers": [], "pageKey": None},
        {"transfers": [], "pageKey": None},
    ]
    rpc_client.responses["alchemy_getTokenMetadata"] = {
        "symbol": "USDC",
        "name": "USD Coin",
        "decimals": "6",
    }
    rpc_client.responses["eth_blockNumber"] = "0x30d3f"
    rpc_client.responses["eth_getLogs"] = [[] for _ in range(8)]
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

    first = provider.get_token_transfers(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
        include_approvals=True,
    )
    second = provider.get_token_transfers(
        address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
        chain=Chain.ETHEREUM,
        cursor=first.next_cursor,
        include_approvals=True,
    )

    assert first.next_cursor is not None
    assert second.next_cursor is None
    block_number_calls = [call for call in rpc_client.calls if call["method"] == "eth_blockNumber"]
    assert len(block_number_calls) == 1
    transfer_calls = [
        call for call in rpc_client.calls if call["method"] == "alchemy_getAssetTransfers"
    ]
    assert len(transfer_calls) == 2
    log_calls = [call for call in rpc_client.calls if call["method"] == "eth_getLogs"]
    assert log_calls[0]["params"][0]["fromBlock"] == "0x186a0"
    assert log_calls[0]["params"][0]["toBlock"] == "0x30d3f"
    assert log_calls[4]["params"][0]["fromBlock"] == "0x0"
    assert log_calls[4]["params"][0]["toBlock"] == "0x1869f"


def test_alchemy_provider_token_transfers_parse_erc721_and_erc1155_quantities() -> None:
    token_address = "0x1111111111111111111111111111111111111111"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["alchemy_getAssetTransfers"] = [
        {
            "transfers": [
                {
                    "blockNum": "0x20",
                    "hash": "0xnft721",
                    "logIndex": "0x0",
                    "from": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "to": "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    "asset": "NFT721",
                    "category": "erc721",
                    "value": None,
                    "erc721TokenId": "0x01",
                    "rawContract": {"address": token_address, "decimal": "0", "value": None},
                    "metadata": {"blockTimestamp": "2026-01-20T11:00:00Z"},
                },
                {
                    "blockNum": "0x21",
                    "hash": "0xnft1155",
                    "logIndex": "0x1",
                    "from": "0xcccccccccccccccccccccccccccccccccccccccc",
                    "to": "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "asset": "NFT1155",
                    "category": "erc1155",
                    "value": None,
                    "erc1155Metadata": [
                        {"tokenId": "0x01", "value": "0x02"},
                        {"tokenId": "0x02", "value": "0x01"},
                    ],
                    "rawContract": {"address": token_address, "decimal": "0", "value": None},
                    "metadata": {"blockTimestamp": "2026-01-20T10:00:00Z"},
                },
            ],
            "pageKey": None,
        },
        {"transfers": [], "pageKey": None},
    ]
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client)

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


def test_alchemy_provider_retries_rate_limited_rpc_then_succeeds() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["eth_getBalance"] = [
        JSONRPCHTTPError(429, "rate limit"),
        "0xde0b6b3a7640000",
    ]
    delays: list[float] = []
    provider = AlchemyProvider(
        api_key="alchemy-key",
        rpc_client=rpc_client,
        retry_attempts=2,
        retry_initial_delay_seconds=0.01,
        retry_backoff_multiplier=2.0,
        retry_max_delay_seconds=1.0,
        sleep_func=delays.append,
    )

    value = provider.get_native_balance(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert value == Decimal("1")
    assert len([call for call in rpc_client.calls if call["method"] == "eth_getBalance"]) == 2
    assert delays == [pytest.approx(0.01)]


def test_alchemy_provider_maps_rpc_timeout_to_provider_timeout() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.errors["eth_getBalance"] = JSONRPCTimeoutError("timed out")
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client, retry_attempts=1)

    with pytest.raises(ProviderTimeoutError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_alchemy_provider_maps_rpc_auth_errors() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.errors["eth_getBalance"] = JSONRPCRemoteError("invalid api key")
    provider = AlchemyProvider(api_key="alchemy-key", rpc_client=rpc_client, retry_attempts=1)

    with pytest.raises(ProviderAuthError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_alchemy_pricing_provider_retries_rate_limited_requests() -> None:
    http_client = _FakeHTTPClient(
        responses=[
            _json_response({"error": "rate limit"}, status_code=429),
            _json_response(
                {
                    "data": [
                        {
                            "symbol": "ETH",
                            "prices": [{"currency": "usd", "value": "2500.00"}],
                        }
                    ]
                }
            ),
        ]
    )
    delays: list[float] = []
    provider = AlchemyPricingProvider(
        api_key="alchemy-key",
        http_client=http_client,
        retry_attempts=2,
        retry_initial_delay_seconds=0.01,
        sleep_func=delays.append,
    )

    price = provider.get_native_price(Chain.ETHEREUM)

    assert price == Decimal("2500.00")
    assert len(http_client.requests) == 2
    assert delays == [pytest.approx(0.01)]


def test_alchemy_pricing_provider_maps_429_to_rate_limit_error() -> None:
    http_client = _FakeHTTPClient(
        responses=[_json_response({"error": "rate limit"}, status_code=429)]
    )
    provider = AlchemyPricingProvider(
        api_key="alchemy-key",
        http_client=http_client,
        retry_attempts=1,
    )

    with pytest.raises(ProviderRateLimitError):
        provider.get_native_price(Chain.ETHEREUM)


class _FakeRPCClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.responses: dict[str, Any] = {}
        self.errors: dict[str, Exception] = {}

    def call(self, url: str, method: str, params: list[Any] | None = None) -> Any:
        self.calls.append({"url": url, "method": method, "params": params or []})
        if method in self.errors:
            raise self.errors[method]
        response = self.responses[method]
        if isinstance(response, list):
            if not response:
                raise AssertionError(f"No more fake responses configured for method {method}.")
            next_item = response.pop(0)
            if isinstance(next_item, Exception):
                raise next_item
            return next_item
        return response


class _FakeHTTPClient:
    def __init__(self, responses: list[HTTPResponse]) -> None:
        self._responses = responses
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        if not self._responses:
            raise AssertionError("No fake HTTP responses left.")
        return self._responses.pop(0)


def _json_response(payload: dict[str, Any], status_code: int = 200) -> HTTPResponse:
    return HTTPResponse(
        status_code=status_code,
        data=json.dumps(payload).encode("utf-8"),
        headers={"content-type": "application/json"},
    )
