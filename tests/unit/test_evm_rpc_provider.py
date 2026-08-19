"""Test standard EVM JSON-RPC transaction inspection.

The cases cover receipts, fees, proxy resolution, revert replay, failover, capabilities, and safe error mapping.
All RPC responses are local fixtures.
"""

from __future__ import annotations

from typing import Any

import pytest

from oracle41_open.core.models import (
    Chain,
    ProviderAuthError,
    ProviderError,
    ProviderResponseError,
    ProxyKind,
    ProxyResolutionStatus,
    TraceDialect,
    TraceStatus,
)
from oracle41_open.providers.evm_rpc import EVMJSONRPCProvider, FailoverTransactionDataProvider
from oracle41_open.providers.jsonrpc import JSONRPCHTTPError, JSONRPCRemoteError

_TX_HASH = "0x" + "ab" * 32
_BLOCK_HASH = "0x" + "cd" * 32
_FROM = "0x1111111111111111111111111111111111111111"
_TO = "0x2222222222222222222222222222222222222222"
_LOG_ADDRESS = "0x3333333333333333333333333333333333333333"
_BEACON = "0x4444444444444444444444444444444444444444"


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
    assert provider.capabilities(Chain.ARBITRUM).archive_queries is None
    assert not provider.capabilities(Chain.ETHEREUM).receipts
    assert provider.capabilities(Chain.ETHEREUM).archive_queries is False


def test_evm_rpc_provider_resolves_eip1167_minimal_proxy() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["eth_getCode"] = (
        "0x363d3d373d3d3d363d73" + _LOG_ADDRESS[2:] + "5af43d82803e903d91602b57fd5bf3"
    )
    provider = EVMJSONRPCProvider({Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc)

    result = provider.resolve_proxy(_TO, Chain.ETHEREUM, 24_000_000)

    assert result.status is ProxyResolutionStatus.RESOLVED
    assert result.proxy_kind is ProxyKind.EIP_1167
    assert result.implementation_address == _LOG_ADDRESS
    assert [call[0] for call in rpc.calls] == ["eth_getCode"]
    assert provider.capabilities(Chain.ETHEREUM).archive_queries is True


def test_evm_rpc_provider_learns_when_historical_state_is_pruned() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.errors_by_method["eth_getCode"] = JSONRPCRemoteError(
        "missing trie node for requested block",
        code=-32000,
    )
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example"},
        rpc_client=rpc,
        retry_attempts=1,
    )

    with pytest.raises(ProviderResponseError, match="missing trie node"):
        provider.resolve_proxy(_TO, Chain.ETHEREUM, 1)

    assert provider.capabilities(Chain.ETHEREUM).archive_queries is False


def test_pruned_storage_read_overrides_successful_historical_code_read() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["eth_getCode"] = "0x6000"
    rpc.errors_by_method["eth_getStorageAt"] = JSONRPCRemoteError(
        "historical state is not available",
        code=-32000,
    )
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example"},
        rpc_client=rpc,
        retry_attempts=1,
    )

    with pytest.raises(ProviderResponseError, match="historical state"):
        provider.resolve_proxy(_TO, Chain.ETHEREUM, 1)

    assert provider.capabilities(Chain.ETHEREUM).archive_queries is False


def test_evm_rpc_provider_resolves_eip1967_storage_proxy() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["eth_getCode"] = "0x6000"
    rpc.responses_by_method["eth_getStorageAt"] = "0x" + "00" * 12 + _LOG_ADDRESS[2:]
    provider = EVMJSONRPCProvider({Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc)

    result = provider.resolve_proxy(_TO, Chain.ETHEREUM, 24_000_000)

    assert result.status is ProxyResolutionStatus.RESOLVED
    assert result.proxy_kind is ProxyKind.EIP_1967
    assert result.implementation_address == _LOG_ADDRESS
    assert [call[0] for call in rpc.calls] == ["eth_getCode", "eth_getStorageAt"]


def test_evm_rpc_provider_resolves_eip1967_beacon_at_requested_block() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["eth_getCode"] = "0x6000"
    rpc.response_sequences_by_method["eth_getStorageAt"] = [
        "0x" + "00" * 32,
        "0x" + "00" * 12 + _BEACON[2:],
    ]
    rpc.responses_by_method["eth_call"] = "0x" + "00" * 12 + _LOG_ADDRESS[2:]
    provider = EVMJSONRPCProvider({Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc)

    result = provider.resolve_proxy(_TO, Chain.ETHEREUM, 24_000_000)

    assert result.status is ProxyResolutionStatus.RESOLVED
    assert result.proxy_kind is ProxyKind.EIP_1967_BEACON
    assert result.beacon_address == _BEACON
    assert result.implementation_address == _LOG_ADDRESS
    assert [call[0] for call in rpc.calls] == [
        "eth_getCode",
        "eth_getStorageAt",
        "eth_getStorageAt",
        "eth_call",
    ]
    assert rpc.calls[-1][1] == [
        {"to": _BEACON, "data": "0x5c60da1b"},
        hex(24_000_000),
    ]


def test_evm_rpc_provider_keeps_empty_beacon_implementation_unresolved() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["eth_getCode"] = "0x6000"
    rpc.response_sequences_by_method["eth_getStorageAt"] = [
        "0x" + "00" * 32,
        "0x" + "00" * 12 + _BEACON[2:],
    ]
    rpc.responses_by_method["eth_call"] = "0x" + "00" * 32
    provider = EVMJSONRPCProvider({Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc)

    result = provider.resolve_proxy(_TO, Chain.ETHEREUM, 24_000_000)

    assert result.status is ProxyResolutionStatus.UNAVAILABLE
    assert result.proxy_kind is ProxyKind.EIP_1967_BEACON
    assert result.beacon_address == _BEACON
    assert result.implementation_address is None
    assert result.error == "The beacon returned an empty implementation address."


def test_beacon_resolution_failover_continues_after_unavailable_provider() -> None:
    first_rpc = _beacon_rpc()
    first_rpc.errors_by_method["eth_call"] = JSONRPCRemoteError(
        "historical state unavailable",
        code=-32000,
    )
    second_rpc = _beacon_rpc()
    second_rpc.responses_by_method["eth_call"] = "0x" + "00" * 12 + _LOG_ADDRESS[2:]
    provider = FailoverTransactionDataProvider(
        [
            EVMJSONRPCProvider(
                {Chain.ETHEREUM: "https://first.example"},
                source_name="first",
                rpc_client=first_rpc,
            ),
            EVMJSONRPCProvider(
                {Chain.ETHEREUM: "https://second.example"},
                source_name="second",
                rpc_client=second_rpc,
            ),
        ]
    )

    result = provider.resolve_proxy(_TO, Chain.ETHEREUM, 24_000_000)

    assert result.status is ProxyResolutionStatus.RESOLVED
    assert result.source_provider == "second"
    assert result.beacon_address == _BEACON
    assert result.implementation_address == _LOG_ADDRESS


def test_evm_rpc_provider_marks_plain_contract_as_not_proxy() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["eth_getCode"] = "0x6000"
    rpc.responses_by_method["eth_getStorageAt"] = "0x" + "00" * 32
    provider = EVMJSONRPCProvider({Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc)

    result = provider.resolve_proxy(_TO, Chain.ETHEREUM, 24_000_000)

    assert result.status is ProxyResolutionStatus.NOT_PROXY
    assert result.proxy_kind is ProxyKind.NONE
    assert result.implementation_address is None


def test_evm_rpc_provider_extracts_revert_data_from_historical_call() -> None:
    receipt = _receipt_payload()
    receipt["status"] = "0x0"
    rpc = _FakeRPCClient(_transaction_payload(), receipt)
    provider = EVMJSONRPCProvider({Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc)
    inspection = provider.get_transaction_inspection(_TX_HASH, Chain.ETHEREUM)
    revert_data = "0x08c379a0" + "00" * 32
    rpc.errors_by_method["eth_call"] = JSONRPCRemoteError(
        "execution reverted",
        code=3,
        data={"data": revert_data},
    )

    assert provider.get_revert_data(inspection) == revert_data


def test_revert_failover_continues_when_first_provider_returns_no_data() -> None:
    receipt = _receipt_payload()
    receipt["status"] = "0x0"
    first_rpc = _FakeRPCClient(_transaction_payload(), receipt)
    first_rpc.responses_by_method["eth_call"] = None
    second_rpc = _FakeRPCClient(_transaction_payload(), receipt)
    revert_data = "0x4e487b71" + "00" * 32
    second_rpc.errors_by_method["eth_call"] = JSONRPCRemoteError(
        "execution reverted",
        code=3,
        data=revert_data,
    )
    first = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://first.example"}, rpc_client=first_rpc
    )
    second = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://second.example"}, rpc_client=second_rpc
    )
    inspection = first.get_transaction_inspection(_TX_HASH, Chain.ETHEREUM)

    result = FailoverTransactionDataProvider([first, second]).get_revert_data(inspection)

    assert result == revert_data
    assert [call[0] for call in first_rpc.calls].count("eth_call") == 1
    assert [call[0] for call in second_rpc.calls].count("eth_call") == 1


def test_trace_discovery_prefers_debug_call_tracer_and_remembers_support() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["debug_traceTransaction"] = {
        "type": "CALL",
        "from": _FROM,
        "to": _TO,
        "gas": "0x20",
        "gasUsed": "0x10",
        "input": "0x",
        "output": "0x",
    }
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc
    )

    first = provider.get_transaction_trace(_TX_HASH, Chain.ETHEREUM)
    second = provider.get_transaction_trace(_TX_HASH, Chain.ETHEREUM)

    assert first.status is TraceStatus.COMPLETE
    assert first.dialect is TraceDialect.DEBUG_CALL_TRACER
    assert first.calls[0].to_address == _TO
    assert first.raw_json is not None
    assert second.dialect is TraceDialect.DEBUG_CALL_TRACER
    assert provider.capabilities(Chain.ETHEREUM).traces is True
    assert [call[0] for call in rpc.calls] == [
        "debug_traceTransaction",
        "debug_traceTransaction",
    ]


def test_trace_discovery_falls_back_to_parity_trace() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.errors_by_method["debug_traceTransaction"] = JSONRPCRemoteError(
        "method not found", code=-32601
    )
    rpc.responses_by_method["trace_transaction"] = [
        {
            "type": "call",
            "traceAddress": [],
            "action": {
                "callType": "call",
                "from": _FROM,
                "to": _TO,
                "gas": "0x20",
                "input": "0x",
                "value": "0x1",
            },
            "result": {"gasUsed": "0x10", "output": "0x"},
        }
    ]
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc
    )

    result = provider.get_transaction_trace(_TX_HASH, Chain.ETHEREUM)

    assert result.status is TraceStatus.COMPLETE
    assert result.dialect is TraceDialect.PARITY_TRACE
    assert result.calls[0].value_wei == 1
    assert [call[0] for call in rpc.calls] == [
        "debug_traceTransaction",
        "trace_transaction",
    ]


def test_trace_discovery_reports_unsupported_capability() -> None:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.errors_by_method["debug_traceTransaction"] = JSONRPCRemoteError(
        "method not found", code=-32601
    )
    rpc.errors_by_method["trace_transaction"] = JSONRPCRemoteError(
        "method not found", code=-32601
    )
    provider = EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://rpc.example"}, rpc_client=rpc
    )

    first = provider.get_transaction_trace(_TX_HASH, Chain.ETHEREUM)
    second = provider.get_transaction_trace(_TX_HASH, Chain.ETHEREUM)

    assert first.status is TraceStatus.UNSUPPORTED
    assert second.status is TraceStatus.UNSUPPORTED
    assert provider.capabilities(Chain.ETHEREUM).traces is False
    assert len(rpc.calls) == 2


def test_trace_failover_does_not_hide_temporary_failure_as_unsupported() -> None:
    unsupported_rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    unsupported_rpc.errors_by_method["debug_traceTransaction"] = JSONRPCRemoteError(
        "method not found", code=-32601
    )
    unsupported_rpc.errors_by_method["trace_transaction"] = JSONRPCRemoteError(
        "method not found", code=-32601
    )
    failing_rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    failing_rpc.errors_by_method["debug_traceTransaction"] = JSONRPCRemoteError(
        "node temporarily busy", code=-32000
    )
    providers = [
        EVMJSONRPCProvider(
            {Chain.ETHEREUM: "https://unsupported.example"},
            source_name="unsupported",
            rpc_client=unsupported_rpc,
        ),
        EVMJSONRPCProvider(
            {Chain.ETHEREUM: "https://busy.example"},
            source_name="busy",
            rpc_client=failing_rpc,
        ),
    ]

    with pytest.raises(ProviderError, match="trace providers failed"):
        FailoverTransactionDataProvider(providers).get_transaction_trace(
            _TX_HASH,
            Chain.ETHEREUM,
        )


class _FakeRPCClient:
    def __init__(self, transaction: object, receipt: object) -> None:
        self.transaction = transaction
        self.receipt = receipt
        self.error: Exception | None = None
        self.errors_by_method: dict[str, Exception] = {}
        self.responses_by_method: dict[str, object] = {}
        self.response_sequences_by_method: dict[str, list[object]] = {}
        self.calls: list[tuple[str, list[Any]]] = []

    def call(self, url: str, method: str, params: list[Any] | None = None) -> object:
        _ = url
        safe_params = params or []
        self.calls.append((method, safe_params))
        if self.error is not None:
            raise self.error
        if method in self.errors_by_method:
            raise self.errors_by_method[method]
        if method in self.response_sequences_by_method:
            responses = self.response_sequences_by_method[method]
            if not responses:
                raise AssertionError(f"No remaining response for method: {method}")
            return responses.pop(0)
        if method in self.responses_by_method:
            return self.responses_by_method[method]
        if method == "eth_getTransactionByHash":
            return self.transaction
        if method == "eth_getTransactionReceipt":
            return self.receipt
        raise AssertionError(f"Unexpected method: {method}")


def _beacon_rpc() -> _FakeRPCClient:
    rpc = _FakeRPCClient(_transaction_payload(), _receipt_payload())
    rpc.responses_by_method["eth_getCode"] = "0x6000"
    rpc.response_sequences_by_method["eth_getStorageAt"] = [
        "0x" + "00" * 32,
        "0x" + "00" * 12 + _BEACON[2:],
    ]
    return rpc


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
