from __future__ import annotations

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
from oracle41_open.providers.ankr import AnkrProvider
from oracle41_open.providers.jsonrpc import (
    JSONRPCHTTPError,
    JSONRPCRemoteError,
    JSONRPCTimeoutError,
)


def test_ankr_provider_parses_native_balance() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["eth_getBalance"] = "0xde0b6b3a7640000"
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

    result = provider.get_native_balance(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert result == Decimal("1")
    assert rpc_client.calls[0]["method"] == "eth_getBalance"
    assert "rpc.ankr.com/eth/ankr-key" in rpc_client.calls[0]["url"]


def test_ankr_provider_does_not_map_unexpected_rpc_client_errors() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.errors["eth_getBalance"] = RuntimeError("implementation defect")
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

    with pytest.raises(RuntimeError, match="implementation defect"):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_ankr_provider_parses_token_balances_page() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["ankr_getAccountBalance"] = {
        "assets": [
            {
                "contractAddress": "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                "tokenSymbol": "USDC",
                "tokenName": "USD Coin",
                "tokenDecimals": 6,
                "balanceRawInteger": "1230000",
                "isSpam": False,
            }
        ],
        "nextPageToken": "p2",
    }
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

    page = provider.get_token_balances(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert page.next_page_key == "p2"
    assert len(page.balances) == 1
    assert page.balances[0].token.symbol == "USDC"
    assert page.balances[0].balance_decimal == Decimal("1.23")
    payload = rpc_client.calls[0]["params"][0]
    assert payload["walletAddress"] == "0x742d35cc6634c0532925a3b844bc454e4438f44e"
    assert payload["blockchain"] == ["eth"]


def test_ankr_provider_parses_activity_page() -> None:
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["ankr_getTokenTransfers"] = {
        "transfers": [
            {
                "transactionHash": "0xabc",
                "logIndex": "0x1",
                "fromAddress": "0x1111111111111111111111111111111111111111",
                "toAddress": "0x2222222222222222222222222222222222222222",
                "blockHeight": 19_100_000,
                "blockTimestamp": "2026-01-20T10:00:00Z",
                "value": "25.5",
                "valueRawInteger": "25500000",
                "tokenSymbol": "USDC",
                "tokenDecimals": 6,
                "contractAddress": token,
                "isSpam": False,
            }
        ],
        "nextPageToken": "c2",
    }
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

    page = provider.get_activity(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert page.next_cursor == "c2"
    assert len(page.items) == 1
    assert page.items[0].category is ActivityCategory.ERC20
    assert page.items[0].contract_address == token
    assert page.items[0].value_decimal == Decimal("25.5")


def test_ankr_provider_maps_nft_transfer_categories() -> None:
    token = "0x9999999999999999999999999999999999999999"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["ankr_getTokenTransfers"] = {
        "transfers": [
            {
                "transactionHash": "0x721",
                "logIndex": "0x0",
                "fromAddress": "0x1111111111111111111111111111111111111111",
                "toAddress": "0x2222222222222222222222222222222222222222",
                "blockHeight": 19_100_001,
                "blockTimestamp": "2026-01-20T10:01:00Z",
                "tokenType": "ERC721",
                "tokenId": "123",
                "tokenSymbol": "NFT",
                "tokenDecimals": 0,
                "contractAddress": token,
            }
        ],
        "nextPageToken": None,
    }
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

    page = provider.get_activity(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert len(page.items) == 1
    assert page.items[0].category is ActivityCategory.ERC721
    assert page.items[0].value_decimal == Decimal("1")


def test_ankr_provider_token_transfer_uses_contract_filter() -> None:
    token = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    rpc_client = _FakeRPCClient()
    rpc_client.responses["ankr_getTokenTransfers"] = {"transfers": [], "nextPageToken": None}
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

    page = provider.get_token_transfers(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        token_address=token,
        chain=Chain.ETHEREUM,
        include_approvals=False,
    )

    assert page.next_cursor is None
    payload = rpc_client.calls[0]["params"][0]
    assert payload["contractAddress"] == token


def test_ankr_provider_token_transfers_include_approval_logs() -> None:
    wallet = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    token_address = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
    wallet_topic = "0x000000000000000000000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    approval_topic = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"
    approval_for_all_topic = "0x17307eab39ab6107e8899845ad3d59bd9653f200f220920489ca2b5937696c31"

    rpc_client = _FakeRPCClient()
    rpc_client.responses["ankr_getTokenTransfers"] = {"transfers": [], "nextPageToken": None}
    rpc_client.responses["eth_call"] = "0x6"
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
    rpc_client.responses["eth_getBlockByNumber"] = [{"timestamp": "0x65aa0f00"}]
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

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


def test_ankr_provider_reports_incomplete_approval_history() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["ankr_getTokenTransfers"] = {
        "transfers": [],
        "nextPageToken": None,
    }
    rpc_client.responses["eth_call"] = "0x6"
    rpc_client.responses["eth_blockNumber"] = "0x20000"
    rpc_client.errors["eth_getLogs"] = JSONRPCRemoteError("unauthorized")
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

    with pytest.raises(ProviderError, match="approval history"):
        provider.get_token_transfers(
            address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            token_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            chain=Chain.ETHEREUM,
            include_approvals=True,
        )


def test_ankr_provider_pages_approval_history_back_to_genesis() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["ankr_getTokenTransfers"] = {
        "transfers": [],
        "nextPageToken": None,
    }
    rpc_client.responses["eth_call"] = "0x6"
    rpc_client.responses["eth_blockNumber"] = "0x30d3f"
    rpc_client.responses["eth_getLogs"] = [[] for _ in range(8)]
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client)

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
        call for call in rpc_client.calls if call["method"] == "ankr_getTokenTransfers"
    ]
    assert len(transfer_calls) == 1
    log_calls = [call for call in rpc_client.calls if call["method"] == "eth_getLogs"]
    assert log_calls[0]["params"][0]["fromBlock"] == "0x186a0"
    assert log_calls[0]["params"][0]["toBlock"] == "0x30d3f"
    assert log_calls[4]["params"][0]["fromBlock"] == "0x0"
    assert log_calls[4]["params"][0]["toBlock"] == "0x1869f"


def test_ankr_provider_rejects_invalid_token_address() -> None:
    provider = AnkrProvider(api_key="ankr-key", rpc_client=_FakeRPCClient())
    with pytest.raises(ProviderError):
        provider.get_token_transfers(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            token_address="0x1234",
            chain=Chain.ETHEREUM,
        )


def test_ankr_provider_retries_rate_limited_rpc_then_succeeds() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.responses["eth_getBalance"] = [
        JSONRPCHTTPError(429, "rate limit"),
        "0xde0b6b3a7640000",
    ]
    delays: list[float] = []
    provider = AnkrProvider(
        api_key="ankr-key",
        rpc_client=rpc_client,
        retry_attempts=2,
        retry_initial_delay_seconds=0.01,
        sleep_func=delays.append,
    )

    result = provider.get_native_balance(
        address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
        chain=Chain.ETHEREUM,
    )

    assert result == Decimal("1")
    assert len([call for call in rpc_client.calls if call["method"] == "eth_getBalance"]) == 2
    assert delays == [pytest.approx(0.01)]


def test_ankr_provider_maps_rpc_timeout_to_provider_timeout() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.errors["eth_getBalance"] = JSONRPCTimeoutError("timed out")
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client, retry_attempts=1)

    with pytest.raises(ProviderTimeoutError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_ankr_provider_maps_rpc_auth_errors() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.errors["eth_getBalance"] = JSONRPCRemoteError("invalid api key")
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client, retry_attempts=1)

    with pytest.raises(ProviderAuthError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


def test_ankr_provider_maps_rpc_429_to_rate_limit_error() -> None:
    rpc_client = _FakeRPCClient()
    rpc_client.errors["eth_getBalance"] = JSONRPCHTTPError(429, "too many requests")
    provider = AnkrProvider(api_key="ankr-key", rpc_client=rpc_client, retry_attempts=1)

    with pytest.raises(ProviderRateLimitError):
        provider.get_native_balance(
            address="0x742d35cc6634c0532925a3b844bc454e4438f44e",
            chain=Chain.ETHEREUM,
        )


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
