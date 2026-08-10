from __future__ import annotations

from typing import Any

import pytest

from oracle41_open.core.models import (
    Chain,
    ProviderAuthError,
    ProviderResponseError,
)
from oracle41_open.providers.evm_rpc import EVMJSONRPCProvider
from oracle41_open.providers.jsonrpc import JSONRPCHTTPError

_TX_HASH = "0x" + "ab" * 32
_BLOCK_HASH = "0x" + "cd" * 32
_FROM = "0x1111111111111111111111111111111111111111"
_TO = "0x2222222222222222222222222222222222222222"
_LOG_ADDRESS = "0x3333333333333333333333333333333333333333"


def test_evm_rpc_provider_maps_transaction_receipt_and_fee() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example/key"},
        source_name="custom-json-rpc",
        rpc_client=rpc,
    )

    result = provider.get_transaction_inspection(_TX_HASH.upper(), Chain.ETHEREUM)

    assert result.tx_hash == _TX_HASH
    assert result.status is True
    assert result.input_data == "0xa9059cbb"
    assert result.gas_used == 21_000
    assert result.effective_gas_price == 2_000_000_000
    assert result.fee_wei == 42_000_000_000_000
    assert str(result.fee_native) == "0.000042"
    assert result.source_provider == "custom-json-rpc"
    assert len(result.logs) == 1
    assert result.logs[0].topics == ("0x" + "01" * 32,)
    assert [call[0] for call in rpc.calls] == [
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
    ]


def test_evm_rpc_provider_preserves_contract_creation_and_revert_status() -> None:
    transaction = _transaction_payload()
    transaction["to"] = None
    receipt = _receipt_payload()
    receipt["to"] = None
    receipt["contractAddress"] = _TO
    receipt["status"] = "0x0"
    provider = EVMJSONRPCProvider(
        {Chain.BASE: "https://base.example"},
        rpc_client=_FakeRPCClient(transaction, receipt),
    )

    result = provider.get_transaction_inspection(_TX_HASH, Chain.BASE)

    assert result.to_address is None
    assert result.contract_address == _TO
    assert result.status is False


def test_evm_rpc_provider_rejects_malformed_logs() -> None:
    receipt = _receipt_payload()
    receipt["logs"] = [{"logIndex": "0x0", "address": _LOG_ADDRESS, "topics": "bad"}]
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example"},
        rpc_client=_FakeRPCClient(_transaction_payload(), receipt),
    )

    with pytest.raises(ProviderResponseError, match="topics"):
        provider.get_transaction_inspection(_TX_HASH, Chain.ETHEREUM)


def test_evm_rpc_provider_maps_auth_errors_without_exposing_endpoint() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.error = JSONRPCHTTPError(401, "secret endpoint failed")
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example/private-key"},
        rpc_client=rpc,
    )

    with pytest.raises(ProviderAuthError) as captured:
        provider.get_transaction_inspection(_TX_HASH, Chain.ETHEREUM)

    assert "private-key" not in str(captured.value)


def test_evm_rpc_capabilities_are_chain_specific() -> None:
    provider = EVMJSONRPCProvider({Chain.ARBITRUM: "https://arb.example"})

    assert provider.capabilities(Chain.ARBITRUM).receipts
    assert not provider.capabilities(Chain.ETHEREUM).receipts


class _FakeRPCClient:
    def __init__(self, transaction: object, receipt: object) -> None:
        self.transaction = transaction
        self.receipt = receipt
        self.error: Exception | None = None
        self.calls: list[tuple[str, list[Any]]] = []

    def call(self, url: str, method: str, params: list[Any] | None = None) -> object:
        _ = url
        safe_params = params or []
        self.calls.append((method, safe_params))
        if self.error is not None:
            raise self.error
        if method == "eth_getTransactionByHash":
            return self.transaction
        if method == "eth_getTransactionReceipt":
            return self.receipt
        raise AssertionError(f"Unexpected method: {method}")


def _transaction_payload() -> dict[str, object]:
    return {
        "hash": _TX_HASH,
        "from": _FROM,
        "to": _TO,
        "nonce": "0x7",
        "value": "0xde0b6b3a7640000",
        "input": "0xa9059cbb",
        "gas": "0x5208",
        "gasPrice": "0x77359400",
        "maxFeePerGas": "0xb2d05e00",
        "maxPriorityFeePerGas": "0x3b9aca00",
    }


def _receipt_payload() -> dict[str, object]:
    return {
        "transactionHash": _TX_HASH,
        "blockNumber": "0x16e3600",
        "blockHash": _BLOCK_HASH,
        "transactionIndex": "0x2",
        "from": _FROM,
        "to": _TO,
        "contractAddress": None,
        "status": "0x1",
        "gasUsed": "0x5208",
        "cumulativeGasUsed": "0x15f90",
        "effectiveGasPrice": "0x77359400",
        "type": "0x2",
        "logsBloom": "0x" + "00" * 256,
        "logs": [
            {
                "logIndex": "0x3",
                "address": _LOG_ADDRESS,
                "topics": ["0x" + "01" * 32],
                "data": "0x",
                "removed": False,
            }
        ],
    }
