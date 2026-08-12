"""Import, validate, and retrieve contract ABIs.

This service parses functions, events, tuple types, and custom errors while recording user or verified source details.
Only valid ABI entries are stored, and verification requires an attributed external source.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from eth_abi.exceptions import NoEntriesFound
from eth_abi.registry import registry as abi_registry
from eth_utils.crypto import keccak

from oracle41_open.core.models import (
    ABIArgumentDefinition,
    Chain,
    ContractABIRecord,
    ErrorSignatureDefinition,
    EventSignatureDefinition,
    FunctionSignatureDefinition,
    SignatureProvenance,
    SignatureSourceKind,
    ValidationError,
)
from oracle41_open.core.services.abi_decoder import SignatureRegistry
from oracle41_open.core.services.address_validator import AddressValidator


class ContractABIStore(Protocol):
    def upsert_contract_abi(self, record: ContractABIRecord) -> None:
        ...

    def get_contract_abi(
        self,
        chain: Chain,
        contract_address: str,
    ) -> ContractABIRecord | None:
        ...

    def list_contract_abis(self, chain: Chain | None = None) -> tuple[ContractABIRecord, ...]:
        ...

    def delete_contract_abi(self, chain: Chain, contract_address: str) -> bool:
        ...


@dataclass(frozen=True)
class VerifiedABIResult:
    abi_json: str
    contract_name: str | None
    source_name: str
    source_version: str
    reference: str


class VerifiedABIProvider(Protocol):
    def fetch_verified_abi(
        self,
        chain: Chain,
        contract_address: str,
    ) -> VerifiedABIResult | None:
        ...


@dataclass(frozen=True)
class ParsedContractABI:
    normalized_json: str
    content_hash: str
    registry: SignatureRegistry
    function_count: int
    event_count: int
    error_count: int


class ContractABIService:
    def __init__(
        self,
        repository: ContractABIStore,
        verified_abi_provider: VerifiedABIProvider | None = None,
    ) -> None:
        self._repository = repository
        self._verified_abi_provider = verified_abi_provider

    def fetch_verified_abi(
        self,
        chain: Chain,
        contract_address: str,
        imported_at: datetime,
    ) -> ContractABIRecord | None:
        if self._verified_abi_provider is None:
            raise ValidationError("Verified ABI lookup is not configured.")
        normalized_address = AddressValidator.normalized(contract_address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError("Invalid contract address for verified ABI lookup.")
        result = self._verified_abi_provider.fetch_verified_abi(chain, normalized_address)
        if result is None:
            return None
        return self.import_verified_abi(
            chain=chain,
            contract_address=normalized_address,
            abi_json=str(result.abi_json),
            imported_at=imported_at,
            source_name=str(result.source_name),
            reference=str(result.reference),
            source_version=str(result.source_version),
            contract_name=result.contract_name,
        )

    def import_user_abi(
        self,
        chain: Chain,
        contract_address: str,
        abi_json: str,
        imported_at: datetime,
        contract_name: str | None = None,
        reference: str | None = None,
    ) -> ContractABIRecord:
        return self._import_abi(
            chain=chain,
            contract_address=contract_address,
            abi_json=abi_json,
            imported_at=imported_at,
            contract_name=contract_name,
            source_name="User-imported contract ABI",
            source_kind=SignatureSourceKind.USER_ABI,
            is_verified=False,
            reference=reference,
            source_version="1",
        )

    def import_verified_abi(
        self,
        chain: Chain,
        contract_address: str,
        abi_json: str,
        imported_at: datetime,
        source_name: str,
        reference: str,
        source_version: str,
        contract_name: str | None = None,
    ) -> ContractABIRecord:
        if not source_name.strip() or not reference.strip() or not source_version.strip():
            raise ValidationError(
                "Verified ABI imports require a source name, version, and reference."
            )
        return self._import_abi(
            chain=chain,
            contract_address=contract_address,
            abi_json=abi_json,
            imported_at=imported_at,
            contract_name=contract_name,
            source_name=source_name.strip(),
            source_kind=SignatureSourceKind.VERIFIED_ABI,
            is_verified=True,
            reference=reference.strip(),
            source_version=source_version.strip(),
        )

    def registry_for(
        self,
        chain: Chain,
        contract_address: str,
    ) -> SignatureRegistry | None:
        record = self._repository.get_contract_abi(chain, contract_address)
        if record is None:
            return None
        return parse_contract_abi(
            record.abi_json,
            record.provenance,
            record.contract_name,
        ).registry

    def list_contract_abis(self, chain: Chain | None = None) -> tuple[ContractABIRecord, ...]:
        return self._repository.list_contract_abis(chain)

    def delete_contract_abi(self, chain: Chain, contract_address: str) -> bool:
        normalized_address = AddressValidator.normalized(contract_address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError("Invalid contract address for ABI removal.")
        return self._repository.delete_contract_abi(chain, normalized_address)

    def _import_abi(
        self,
        *,
        chain: Chain,
        contract_address: str,
        abi_json: str,
        imported_at: datetime,
        contract_name: str | None,
        source_name: str,
        source_kind: SignatureSourceKind,
        is_verified: bool,
        reference: str | None,
        source_version: str,
    ) -> ContractABIRecord:
        normalized_address = AddressValidator.normalized(contract_address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError("Invalid contract address for ABI import.")
        provisional_source = SignatureProvenance(
            source_id="pending",
            source_name=source_name,
            source_kind=source_kind,
            version=source_version,
            is_verified=is_verified,
            reference=reference,
        )
        parsed = parse_contract_abi(abi_json, provisional_source, contract_name)
        provenance = SignatureProvenance(
            source_id=(
                f"abi:{chain.value}:{normalized_address}:{parsed.content_hash[:16]}"
            ),
            source_name=source_name,
            source_kind=source_kind,
            version=source_version,
            is_verified=is_verified,
            reference=reference,
        )
        record = ContractABIRecord(
            chain=chain,
            contract_address=normalized_address,
            contract_name=_optional_text(contract_name),
            abi_json=parsed.normalized_json,
            content_hash=parsed.content_hash,
            provenance=provenance,
            imported_at=imported_at,
        )
        self._repository.upsert_contract_abi(record)
        return record


def parse_contract_abi(
    abi_json: str,
    provenance: SignatureProvenance,
    contract_name: str | None = None,
) -> ParsedContractABI:
    try:
        payload = json.loads(abi_json)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValidationError("Contract ABI is not valid JSON.") from error
    entries = payload.get("abi") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        raise ValidationError("Contract ABI must be a JSON array or an object with an ABI array.")

    functions: dict[str, list[FunctionSignatureDefinition]] = {}
    events: dict[str, list[EventSignatureDefinition]] = {}
    errors: dict[str, list[ErrorSignatureDefinition]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValidationError("Contract ABI entries must be JSON objects.")
        entry_type = entry.get("type")
        if entry_type not in {"function", "event", "error"}:
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValidationError(f"Contract ABI {entry_type} entry has no name.")
        inputs = _parse_inputs(entry.get("inputs"), event=entry_type == "event")
        signature = _canonical_signature(name.strip(), inputs)
        if entry_type == "function":
            selector = "0x" + keccak(text=signature)[:4].hex()
            functions.setdefault(selector, []).append(
                FunctionSignatureDefinition(
                    selector=selector,
                    name=name.strip(),
                    canonical_signature=signature,
                    inputs=inputs,
                    provenance=provenance,
                )
            )
        elif entry_type == "event":
            if entry.get("anonymous") is True:
                continue
            topic0 = "0x" + keccak(text=signature).hex()
            events.setdefault(topic0, []).append(
                EventSignatureDefinition(
                    topic0=topic0,
                    name=name.strip(),
                    canonical_signature=signature,
                    inputs=inputs,
                    provenance=provenance,
                    standard=_optional_text(contract_name) or "Contract ABI",
                )
            )
        else:
            selector = "0x" + keccak(text=signature)[:4].hex()
            errors.setdefault(selector, []).append(
                ErrorSignatureDefinition(
                    selector=selector,
                    name=name.strip(),
                    canonical_signature=signature,
                    inputs=inputs,
                    provenance=provenance,
                )
            )

    if not functions and not events and not errors:
        raise ValidationError("Contract ABI contains no decodable functions, events, or errors.")
    normalized_json = json.dumps(entries, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    content_hash = hashlib.sha256(normalized_json.encode("utf-8")).hexdigest()
    return ParsedContractABI(
        normalized_json=normalized_json,
        content_hash=content_hash,
        registry=SignatureRegistry(
            functions_by_selector={key: tuple(value) for key, value in functions.items()},
            events_by_topic={key: tuple(value) for key, value in events.items()},
            errors_by_selector={key: tuple(value) for key, value in errors.items()},
        ),
        function_count=sum(len(value) for value in functions.values()),
        event_count=sum(len(value) for value in events.values()),
        error_count=sum(len(value) for value in errors.values()),
    )


def _parse_inputs(raw_inputs: object, *, event: bool) -> tuple[ABIArgumentDefinition, ...]:
    if not isinstance(raw_inputs, list):
        raise ValidationError("Contract ABI inputs must be an array.")
    inputs: list[ABIArgumentDefinition] = []
    for index, raw_input in enumerate(raw_inputs):
        if not isinstance(raw_input, dict):
            raise ValidationError("Contract ABI input entries must be objects.")
        abi_type = _canonical_input_type(raw_input)
        try:
            abi_registry.get_decoder(abi_type)
        except (NoEntriesFound, ValueError) as error:
            raise ValidationError(f"Unsupported ABI input type: {abi_type}.") from error
        raw_name = raw_input.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        inputs.append(
            ABIArgumentDefinition(
                name=name or f"arg{index}",
                abi_type=abi_type,
                indexed=event and raw_input.get("indexed") is True,
            )
        )
    return tuple(inputs)


def _canonical_input_type(raw_input: dict[str, Any]) -> str:
    raw_type = raw_input.get("type")
    if not isinstance(raw_type, str) or not raw_type.strip():
        raise ValidationError("Contract ABI input has no type.")
    abi_type = raw_type.strip()
    if not abi_type.startswith("tuple"):
        return abi_type
    components = raw_input.get("components")
    if not isinstance(components, list):
        raise ValidationError("Tuple ABI input has no components array.")
    component_types = tuple(
        _canonical_input_type(component)
        for component in components
        if isinstance(component, dict)
    )
    if len(component_types) != len(components):
        raise ValidationError("Tuple ABI components must be objects.")
    suffix = abi_type.removeprefix("tuple")
    return f"({','.join(component_types)}){suffix}"


def _canonical_signature(
    name: str,
    inputs: tuple[ABIArgumentDefinition, ...],
) -> str:
    return f"{name}({','.join(item.abi_type for item in inputs)})"


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None
