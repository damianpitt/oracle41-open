"""Test the versioned protocol-adapter contract and fixture format.

The same conformance path covers a matched reference protocol and an unknown protocol.
Tests confirm deterministic positions, explicit capabilities, safe registry selection, and complete evidence passthrough.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    DecodedArgument,
    DecodedEvent,
    DecodeStatus,
    ProtocolAdapterCapabilities,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
    ProtocolAdapterStatus,
    ProtocolContract,
    ProtocolEvidenceValue,
    ProtocolRawEvidence,
    Token,
    TokenBalance,
)
from oracle41_open.core.protocols import (
    ProtocolAdapter,
    ProtocolAdapterRegistry,
    ReferenceLendingAdapter,
    UnknownProtocolAdapter,
)

_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "protocols"


@pytest.mark.parametrize(
    "fixture_name",
    ("reference_lending_v1.json", "unknown_protocol_v1.json"),
)
def test_protocol_fixture_conformance(fixture_name: str) -> None:
    fixture = _load_fixture(fixture_name)
    context = _context_from_fixture(fixture)
    registry = ProtocolAdapterRegistry((ReferenceLendingAdapter(),))

    first = registry.analyze(context)
    second = registry.analyze(context)

    assert first == second
    assert _result_snapshot(first) == fixture["expected"]
    assert first.source_balances == context.token_balances
    assert first.source_events == context.decoded_events
    assert first.raw_evidence == context.raw_evidence


def test_reference_adapter_exposes_explicit_capabilities() -> None:
    adapter = ReferenceLendingAdapter()

    assert isinstance(adapter, ProtocolAdapter)
    assert adapter.capabilities.adapter_version == "1"
    assert adapter.capabilities.chains == frozenset({Chain.ETHEREUM})
    assert {item.value for item in adapter.capabilities.position_kinds} == {
        "supplied",
        "debt",
        "reward",
    }
    assert adapter.capabilities.contracts == (
        ProtocolContract(
            chain=Chain.ETHEREUM,
            address="0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
    )


def test_fixture_schema_is_published_with_format_version_one() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "schemas"
        / "protocol-adapter-fixture-v1.schema.json"
    )
    schema = json_loads(schema_path.read_bytes())

    assert isinstance(schema, dict)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["format_version"]["const"] == 1
    assert schema["properties"]["format"]["const"] == (
        "oracle41-protocol-adapter-fixture"
    )


def test_matching_contract_without_snapshot_returns_partial_result() -> None:
    context = _context_from_fixture(_load_fixture("reference_lending_v1.json"))
    context = replace(context, raw_evidence=())

    result = ProtocolAdapterRegistry((ReferenceLendingAdapter(),)).analyze(context)

    assert result.status.value == "partial"
    assert result.positions == ()
    assert "snapshot is missing" in result.warnings[0]


def test_registry_rejects_duplicate_adapter_ids() -> None:
    adapter = ReferenceLendingAdapter()

    with pytest.raises(ValueError, match="Duplicate protocol adapter ID"):
        ProtocolAdapterRegistry((adapter, adapter))


def test_registry_rejects_overlapping_contract_claims() -> None:
    adapter = ReferenceLendingAdapter()
    conflicting = _AdapterStub(
        capabilities=replace(
            adapter.capabilities,
            adapter_id="oracle41.conflicting-adapter",
        )
    )

    with pytest.raises(ValueError, match="claimed by both"):
        ProtocolAdapterRegistry((adapter, conflicting))


def test_registry_rejects_multiple_runtime_matches() -> None:
    context = _context_from_fixture(_load_fixture("reference_lending_v1.json"))
    first = _AdapterStub(
        capabilities=replace(
            ReferenceLendingAdapter().capabilities,
            contracts=(),
        ),
        matches=True,
    )
    second = _AdapterStub(
        capabilities=replace(
            ReferenceLendingAdapter().capabilities,
            adapter_id="oracle41.second-matching-adapter",
            contracts=(),
        ),
        matches=True,
    )

    with pytest.raises(ValueError, match="Multiple protocol adapters matched"):
        ProtocolAdapterRegistry((first, second)).analyze(context)


def test_registry_rejects_result_identity_mismatch() -> None:
    context = _context_from_fixture(_load_fixture("unknown_protocol_v1.json"))
    fallback_result = UnknownProtocolAdapter().analyze(context)
    adapter = _AdapterStub(
        capabilities=replace(
            ReferenceLendingAdapter().capabilities,
            contracts=(),
        ),
        matches=True,
        result=replace(
            fallback_result,
            status=ProtocolAdapterStatus.PARTIAL,
            adapter_id="oracle41.wrong-adapter",
            protocol_id="reference-lending",
            protocol_name="Reference Lending",
        ),
    )

    with pytest.raises(ValueError, match="mismatched identity metadata"):
        ProtocolAdapterRegistry((adapter,)).analyze(context)


@dataclass(frozen=True)
class _AdapterStub:
    capabilities: ProtocolAdapterCapabilities
    matches: bool = False
    result: ProtocolAdapterResult | None = None

    def supports(self, context: ProtocolAdapterContext) -> bool:
        _ = context
        return self.matches

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        if self.result is None:
            raise AssertionError(f"Stub should not analyze {context.chain.value}.")
        return self.result


def _load_fixture(name: str) -> dict[str, Any]:
    payload = json_loads((_FIXTURE_ROOT / name).read_bytes())
    if not isinstance(payload, dict):
        raise AssertionError("Protocol fixture must be a JSON object.")
    fixture = cast(dict[str, Any], payload)
    assert fixture["format"] == "oracle41-protocol-adapter-fixture"
    assert fixture["format_version"] == 1
    return fixture


def _context_from_fixture(fixture: dict[str, Any]) -> ProtocolAdapterContext:
    raw_input = cast(dict[str, Any], fixture["input"])
    return ProtocolAdapterContext(
        wallet_address=str(raw_input["wallet_address"]),
        chain=Chain(str(raw_input["chain"])),
        block_number=int(raw_input["block_number"]),
        contract_addresses=tuple(cast(list[str], raw_input["contract_addresses"])),
        actions=(),
        token_balances=tuple(
            _token_balance(cast(dict[str, Any], item))
            for item in cast(list[dict[str, Any]], raw_input["token_balances"])
        ),
        decoded_events=tuple(
            _decoded_event(cast(dict[str, Any], item))
            for item in cast(list[dict[str, Any]], raw_input["decoded_events"])
        ),
        raw_evidence=tuple(
            _raw_evidence(cast(dict[str, Any], item))
            for item in cast(list[dict[str, Any]], raw_input["raw_evidence"])
        ),
        source_provider=str(raw_input["source_provider"]),
        observed_at=datetime.fromisoformat(str(raw_input["observed_at"])),
    )


def _token_balance(payload: dict[str, Any]) -> TokenBalance:
    token = Token(
        contract_address=str(payload["contract_address"]),
        symbol=str(payload["symbol"]),
        name=str(payload["name"]),
        decimals=int(payload["decimals"]),
        is_verified=bool(payload["is_verified"]),
    )
    return TokenBalance(token=token, balance_decimal=Decimal(str(payload["balance_decimal"])))


def _decoded_event(payload: dict[str, Any]) -> DecodedEvent:
    arguments = tuple(
        DecodedArgument(
            name=str(item["name"]),
            abi_type=str(item["abi_type"]),
            value=str(item["value"]),
            indexed=bool(item.get("indexed", False)),
        )
        for item in cast(list[dict[str, Any]], payload["arguments"])
    )
    return DecodedEvent(
        status=DecodeStatus(str(payload["status"])),
        log_index=int(payload["log_index"]),
        topic0=str(payload["topic0"]),
        name=str(payload["name"]),
        canonical_signature=str(payload["canonical_signature"]),
        standard=str(payload["standard"]),
        arguments=arguments,
        provenance=None,
    )


def _raw_evidence(payload: dict[str, Any]) -> ProtocolRawEvidence:
    values = cast(dict[str, Any], payload["values"])
    return ProtocolRawEvidence(
        kind=str(payload["kind"]),
        reference=str(payload["reference"]),
        contract_address=str(payload["contract_address"]),
        tx_hash=str(payload["tx_hash"]),
        signature=str(payload["signature"]),
        values=tuple(
            ProtocolEvidenceValue(name=name, value=str(value))
            for name, value in values.items()
        ),
    )


def _result_snapshot(result: ProtocolAdapterResult) -> dict[str, Any]:
    return {
        "status": result.status.value,
        "adapter_id": result.adapter_id,
        "protocol_id": result.protocol_id,
        "position_kinds": [position.kind.value for position in result.positions],
        "raw_amounts": [position.assets[0].raw_amount for position in result.positions],
        "source_balance_count": len(result.source_balances),
        "source_event_count": len(result.source_events),
        "raw_evidence_count": len(result.raw_evidence),
    }
