"""Test contract ABI parsing and source handling.

The cases cover tuples, calls, events, custom errors, user imports, verified imports, and invalid payloads.
They confirm source trust and content hashes remain deterministic.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from eth_abi.abi import encode as abi_encode

from oracle41_open.core.models import (
    Chain,
    ContractABIRecord,
    DecodeStatus,
    SignatureProvenance,
    SignatureSourceKind,
    ValidationError,
)
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
from oracle41_open.core.services.contract_abi_service import (
    ContractABIService,
    VerifiedABIResult,
    parse_contract_abi,
)

_CONTRACT = "0x1111111111111111111111111111111111111111"
_CALLER = "0x2222222222222222222222222222222222222222"
_IMPORTED_AT = datetime(2026, 8, 12, tzinfo=UTC)
_ABI = """
[
  {
    "type": "function",
    "name": "configure",
    "inputs": [
      {
        "name": "config",
        "type": "tuple",
        "components": [
          {"name": "threshold", "type": "uint256"},
          {"name": "owner", "type": "address"}
        ]
      }
    ]
  },
  {
    "type": "event",
    "name": "Configured",
    "anonymous": false,
    "inputs": [
      {"name": "owner", "type": "address", "indexed": true},
      {"name": "threshold", "type": "uint256", "indexed": false}
    ]
  },
  {
    "type": "error",
    "name": "Unauthorized",
    "inputs": [{"name": "caller", "type": "address"}]
  }
]
"""


def test_parse_contract_abi_builds_canonical_tuple_and_error_signatures() -> None:
    provenance = _provenance()

    parsed = parse_contract_abi(_ABI, provenance, "Vault")

    assert parsed.function_count == 1
    assert parsed.event_count == 1
    assert parsed.error_count == 1
    function = next(iter(parsed.registry.functions_by_selector.values()))[0]
    error = next(iter(parsed.registry.errors_by_selector.values()))[0]
    assert function.canonical_signature == "configure((uint256,address))"
    assert error.canonical_signature == "Unauthorized(address)"
    assert function.provenance == provenance


def test_contract_registry_decodes_custom_call_and_error() -> None:
    registry = parse_contract_abi(_ABI, _provenance(), "Vault").registry
    function = next(iter(registry.functions_by_selector.values()))[0]
    custom_error = next(iter(registry.errors_by_selector.values()))[0]
    call_data = function.selector + abi_encode(
        ("(uint256,address)",),
        ((25, _CALLER),),
    ).hex()
    revert_data = custom_error.selector + abi_encode(("address",), (_CALLER,)).hex()
    decoder = StandardABIDecoder()

    call = decoder.decode_call(call_data, registry)
    revert = decoder.decode_revert(revert_data, registry)

    assert call.status is DecodeStatus.DECODED
    assert call.canonical_signature == "configure((uint256,address))"
    assert call.arguments[0].value == f"[25, {_CALLER}]"
    assert revert.status is DecodeStatus.DECODED
    assert revert.canonical_signature == "Unauthorized(address)"
    assert revert.arguments[0].value == _CALLER
    assert revert.raw_data == revert_data


@pytest.mark.parametrize(
    ("selector", "types", "values", "signature", "argument_value"),
    [
        ("0x08c379a0", ("string",), ("not allowed",), "Error(string)", "not allowed"),
        ("0x4e487b71", ("uint256",), (17,), "Panic(uint256)", "17"),
    ],
)
def test_decodes_solidity_builtin_errors(
    selector: str,
    types: tuple[str, ...],
    values: tuple[object, ...],
    signature: str,
    argument_value: str,
) -> None:
    raw_data = selector + abi_encode(types, values).hex()

    decoded = StandardABIDecoder().decode_revert(raw_data)

    assert decoded.status is DecodeStatus.DECODED
    assert decoded.canonical_signature == signature
    assert decoded.arguments[0].value == argument_value
    assert decoded.provenance is not None
    assert decoded.provenance.is_verified


def test_contract_abi_service_distinguishes_user_and_verified_sources() -> None:
    store = _MemoryABIStore()
    service = ContractABIService(store)

    user_record = service.import_user_abi(
        Chain.ETHEREUM,
        _CONTRACT,
        _ABI,
        _IMPORTED_AT,
        contract_name="Vault",
    )
    verified_record = service.import_verified_abi(
        Chain.ETHEREUM,
        _CONTRACT,
        _ABI,
        _IMPORTED_AT,
        source_name="Explorer verified source",
        reference="https://example.invalid/address/contract",
        source_version="2026-08-12",
        contract_name="Vault",
    )

    assert user_record.provenance.source_kind is SignatureSourceKind.USER_ABI
    assert not user_record.provenance.is_verified
    assert verified_record.provenance.source_kind is SignatureSourceKind.VERIFIED_ABI
    assert verified_record.provenance.is_verified
    assert service.registry_for(Chain.ETHEREUM, _CONTRACT) is not None


def test_contract_abi_rejects_invalid_payloads_and_unattributed_verified_source() -> None:
    service = ContractABIService(_MemoryABIStore())

    with pytest.raises(ValidationError, match="valid JSON"):
        service.import_user_abi(Chain.ETHEREUM, _CONTRACT, "not-json", _IMPORTED_AT)
    with pytest.raises(ValidationError, match="require a source"):
        service.import_verified_abi(
            Chain.ETHEREUM,
            _CONTRACT,
            _ABI,
            _IMPORTED_AT,
            source_name="",
            reference="",
            source_version="",
        )


def test_contract_abi_service_imports_verified_provider_result() -> None:
    store = _MemoryABIStore()
    service = ContractABIService(store, verified_abi_provider=_VerifiedABIProvider())

    record = service.fetch_verified_abi(Chain.ETHEREUM, _CONTRACT, _IMPORTED_AT)

    assert record is not None
    assert record.contract_name == "Vault"
    assert record.provenance.source_kind is SignatureSourceKind.VERIFIED_ABI
    assert record.provenance.source_name == "Verified explorer"
    assert record.provenance.reference == "https://example.invalid/address/vault"
    assert store.get_contract_abi(Chain.ETHEREUM, _CONTRACT) == record


def _provenance() -> SignatureProvenance:
    return SignatureProvenance(
        source_id="test:verified:vault",
        source_name="Verified test ABI",
        source_kind=SignatureSourceKind.VERIFIED_ABI,
        version="1",
        is_verified=True,
        reference="https://example.invalid/vault",
    )


class _MemoryABIStore:
    def __init__(self) -> None:
        self.records: dict[tuple[Chain, str], ContractABIRecord] = {}

    def upsert_contract_abi(self, record: ContractABIRecord) -> None:
        self.records[(record.chain, record.contract_address)] = record

    def get_contract_abi(
        self,
        chain: Chain,
        contract_address: str,
    ) -> ContractABIRecord | None:
        return self.records.get((chain, contract_address.lower()))

    def list_contract_abis(self, chain: Chain | None = None) -> tuple[ContractABIRecord, ...]:
        return tuple(
            record
            for record in self.records.values()
            if chain is None or record.chain is chain
        )

    def delete_contract_abi(self, chain: Chain, contract_address: str) -> bool:
        return self.records.pop((chain, contract_address.lower()), None) is not None


class _VerifiedABIProvider:
    def fetch_verified_abi(
        self,
        chain: Chain,
        contract_address: str,
    ) -> VerifiedABIResult | None:
        _ = chain, contract_address
        return VerifiedABIResult(
            abi_json=_ABI,
            contract_name="Vault",
            source_name="Verified explorer",
            source_version="api-v2",
            reference="https://example.invalid/address/vault",
        )
