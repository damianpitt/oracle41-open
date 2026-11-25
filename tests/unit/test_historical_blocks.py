"""Test historical block identity, hash reads, finality labels, and whole-source failover.

Synthetic JSON-RPC responses cover changing headers and networks, unavailable tags, malformed
fields, and unsupported EIP-1898 calls. No test contacts an endpoint or uses a provider key.
Finality labels are checked as source assertions, never as independent settlement proofs.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from eth_abi.abi import encode

from oracle41_open.core.models import Chain, ProviderResponseError
from oracle41_open.core.models.block_reference import BlockReferenceChangedError
from oracle41_open.core.services.historical_block_service import HistoricalBlockService
from oracle41_open.core.services.protocol_collection import call_data
from oracle41_open.providers.evm_rpc import EVMJSONRPCProvider, FailoverTransactionDataProvider
from oracle41_open.providers.jsonrpc import JSONRPCRemoteError

_NOW = datetime(2026, 11, 25, tzinfo=UTC)
_TIME = datetime(2026, 11, 1, tzinfo=UTC)
_WALLET = "0x" + "11" * 20
_TOKEN = "0x" + "22" * 20


def _hash(number):
    return "0x" + f"{number:064x}"


class _RPC:
    def __init__(self):
        self.calls = []
        self.tags = {"safe": 110, "finalized": 110}
        self.overrides = {}
        self.chain_ids = [1, 1]
        self.hash_supported = True
        self.reorg = False
        self.read_counts = {}

    def call(self, url, method, params):
        self.calls.append((method, params))
        if method == "eth_chainId":
            return hex(self.chain_ids.pop(0) if len(self.chain_ids) > 1 else self.chain_ids[0])
        if method == "eth_getBlockByNumber":
            tag = params[0]
            number = self.tags.get(tag) if tag in {"safe", "finalized"} else int(tag, 16)
            if number is None:
                raise JSONRPCRemoteError("Synthetic unsupported tag: confidential-marker", -32602)
            self.read_counts[number] = self.read_counts.get(number, 0) + 1
            header = {
                "number": hex(number),
                "hash": _hash(number),
                "parentHash": _hash(number - 1),
                "timestamp": hex(int((_TIME + timedelta(seconds=(number - 110) * 12)).timestamp())),
            }
            header.update(self.overrides.get(number, {}))
            if self.reorg and number == 100 and self.read_counts[number] > 1:
                header["hash"] = _hash(999)
            return header
        assert method == "eth_call"
        assert params[1].keys() == {"blockHash", "requireCanonical"}
        assert params[1]["requireCanonical"] is True
        if not self.hash_supported:
            raise JSONRPCRemoteError("Synthetic unsupported block object", -32602)
        number = int(params[1]["blockHash"], 16)
        is_balance = params[0]["data"].startswith(call_data("balanceOf(address)")[:10])
        return (
            "0x" + encode(("uint256",), (10000 if number == 100 else 13000,)).hex()
            if is_balance
            else "0x" + encode(("uint8",), (2,)).hex()
        )


def _provider(rpc, name="fixture-rpc"):
    return EVMJSONRPCProvider(
        {Chain.ETHEREUM: "https://example.com/rpc"}, name, rpc_client=rpc, retry_attempts=1
    )


def _service(*providers):
    return HistoricalBlockService(
        FailoverTransactionDataProvider(list(providers)), now_func=lambda: _NOW
    )


def _collect(service, opening=100, closing=110, require_finalized=False):
    return service.collect(Chain.ETHEREUM, _WALLET, _TOKEN, opening, closing, require_finalized)


def test_headers_and_contract_reads_are_hash_pinned_and_rechecked():
    rpc = _RPC()
    anchors = _collect(_service(_provider(rpc)))
    assert anchors.closing["header"]["hash"] == _hash(110)
    assert datetime.fromisoformat(anchors.closing["header"]["timestamp"]) == _TIME
    assert anchors.opening["raw_balance"] == "10000"
    assert anchors.closing["decimals"] == 2
    assert anchors.finality["status"] == "finalized_by_endpoint"
    assert sum(method == "eth_call" for method, _ in rpc.calls) == 4
    assert sum(method == "eth_chainId" for method, _ in rpc.calls) == 2
    assert rpc.read_counts[100] == 2


@pytest.mark.parametrize(
    ("finalized", "safe", "status"),
    [
        (110, 115, "finalized_by_endpoint"),
        (105, 110, "safe_by_endpoint"),
        (105, 108, "not_reported_safe"),
        (None, None, "unknown"),
        (110, None, "finalized_by_endpoint"),
        (None, 110, "safe_by_endpoint"),
    ],
)
def test_endpoint_finality_is_explicit_and_unavailable_tags_are_not_upgraded(
    finalized, safe, status
):
    rpc = _RPC()
    rpc.tags = {"finalized": finalized, "safe": safe}
    anchors = _collect(_service(_provider(rpc)))
    assert anchors.finality["status"] == status
    assert "confidential-marker" not in str(anchors.finality)
    assert "not_independent" in anchors.finality["meaning"]


def test_required_finality_rejects_safe_only_or_unknown_endpoints():
    rpc = _RPC()
    rpc.tags["finalized"] = None
    with pytest.raises(ProviderResponseError):
        _collect(_service(_provider(rpc)), require_finalized=True)


@pytest.mark.parametrize("ids", [[8453], [1, 8453]])
def test_wrong_chain_before_or_after_reads_is_rejected(ids):
    rpc = _RPC()
    rpc.chain_ids = ids
    with pytest.raises(ProviderResponseError):
        _collect(_service(_provider(rpc)))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("hash", None),
        ("hash", "0x12"),
        ("hash", "0x" + "00" * 32),
        ("parentHash", "invalid"),
        ("timestamp", "0x-1"),
        ("timestamp", "0x" + "f" * 64),
        ("number", "0x6f"),
        ("number", True),
        ("number", None),
    ],
)
def test_invalid_headers_never_reach_contract_collection(field, value):
    rpc = _RPC()
    rpc.overrides[110] = {field: value}
    with pytest.raises(ProviderResponseError):
        _collect(_service(_provider(rpc)))
    assert not any(method == "eth_call" for method, _ in rpc.calls)


def test_reorganization_aborts_without_hiding_it_with_failover():
    first, second = _RPC(), _RPC()
    first.reorg = True
    with pytest.raises(BlockReferenceChangedError):
        _collect(_service(_provider(first, "first"), _provider(second, "second")))
    assert second.calls == []


def test_explicit_noncanonical_call_failure_is_not_hidden_by_failover():
    class RejectedHashRPC(_RPC):
        def call(self, url, method, params):
            if method == "eth_call":
                raise JSONRPCRemoteError("Block is not canonical", -32000)
            return super().call(url, method, params)

    second = _RPC()
    with pytest.raises(BlockReferenceChangedError):
        _collect(_service(_provider(RejectedHashRPC()), _provider(second, "second")))
    assert second.calls == []


def test_unsupported_hash_reads_retry_the_entire_next_source_without_numeric_fallback():
    first, second = _RPC(), _RPC()
    first.hash_supported = False
    anchors = _collect(_service(_provider(first, "first"), _provider(second, "second")))
    assert anchors.opening["source_provider"] == anchors.closing["source_provider"] == "second"
    assert first.calls[-1][0] == "eth_call"
    assert second.calls[0][0] == "eth_chainId"
    assert sum(method == "eth_call" for method, _ in second.calls) == 4
    with pytest.raises(ProviderResponseError):
        _collect(_service(_provider(first)))


@pytest.mark.parametrize(
    "case", ["future", "reversed_time", "parent", "finality_order", "finality_time"]
)
def test_inconsistent_timing_parent_and_finality_are_rejected(case):
    rpc = _RPC()
    if case == "future":
        rpc.overrides[110] = {"timestamp": hex(int((_NOW + timedelta(days=1)).timestamp()))}
    elif case == "reversed_time":
        rpc.overrides[100] = {"timestamp": hex(int((_TIME + timedelta(days=1)).timestamp()))}
    elif case == "parent":
        rpc.overrides[101] = {"parentHash": _hash(999)}
    elif case == "finality_order":
        rpc.tags = {"finalized": 110, "safe": 109}
    else:
        rpc.tags = {"finalized": 111, "safe": None}
        rpc.overrides[111] = {"timestamp": hex(int((_TIME - timedelta(seconds=1)).timestamp()))}
    with pytest.raises(ProviderResponseError):
        _collect(_service(_provider(rpc)), closing=101 if case == "parent" else 110)


def test_same_and_adjacent_blocks_have_valid_identity_and_parent_checks():
    for closing in (100, 101):
        rpc = _RPC()
        result = _collect(_service(_provider(rpc)), closing=closing)
        assert result.closing["header"]["hash"] == _hash(closing)


@pytest.mark.parametrize(
    "field", ["chain", "contract_address", "block_number", "block_hash", "source_provider"]
)
def test_wrong_hash_read_context_is_rejected(field):
    class WrongReadProvider(EVMJSONRPCProvider):
        def read_contract_at_hash(self, *args):
            result = super().read_contract_at_hash(*args)
            value = {
                "chain": Chain.BASE,
                "contract_address": _WALLET,
                "block_number": 999,
                "block_hash": _hash(999),
                "source_provider": "other",
            }[field]
            return replace(result, **{field: value})

    provider = WrongReadProvider({Chain.ETHEREUM: "https://example.com/rpc"}, rpc_client=_RPC())
    with pytest.raises(ProviderResponseError):
        _collect(_service(provider))


def test_recheck_retains_saved_reference_and_records_current_disagreement():
    rpc = _RPC()
    service = _service(_provider(rpc))
    anchors = _collect(service)
    headers = [anchors.opening["header"], anchors.closing["header"]]
    assert service.revalidate(Chain.ETHEREUM, headers)["status"] == "references_match_at_check"
    rpc.overrides[110] = {"hash": _hash(999)}
    result = service.revalidate(Chain.ETHEREUM, headers)
    assert result["status"] == "changed_reference"
    assert result["observations"][1]["current"]["hash"] == _hash(999)
    assert headers[1]["hash"] == _hash(110)
