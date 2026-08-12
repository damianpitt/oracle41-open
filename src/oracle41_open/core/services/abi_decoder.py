from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar

from eth_abi.abi import decode as abi_decode
from eth_abi.exceptions import DecodingError
from eth_utils.crypto import keccak

from oracle41_open.core.models import RawTransactionLog, TransactionInspection
from oracle41_open.core.models.decoding import (
    ABIArgumentDefinition,
    DecodedArgument,
    DecodedCall,
    DecodedEvent,
    DecodedRevert,
    DecodeStatus,
    ErrorSignatureDefinition,
    EventSignatureDefinition,
    FunctionSignatureDefinition,
    SignatureProvenance,
    SignatureSourceKind,
    TransactionDecoding,
)

DECODER_VERSION = "2"

_Definition = TypeVar("_Definition")

_BUNDLED_SOURCE = SignatureProvenance(
    source_id="oracle41.bundled.evm-standards",
    source_name="Oracle41 bundled EVM standards",
    source_kind=SignatureSourceKind.BUNDLED_STANDARD,
    version="1",
    is_verified=True,
    reference="https://eips.ethereum.org/",
)

_SOLIDITY_SOURCE = SignatureProvenance(
    source_id="solidity.builtin.errors",
    source_name="Solidity built-in errors",
    source_kind=SignatureSourceKind.BUNDLED_STANDARD,
    version="1",
    is_verified=True,
    reference="https://docs.soliditylang.org/en/latest/control-structures.html#error-handling-assert-require-revert-and-exceptions",
)


@dataclass(frozen=True)
class SignatureRegistry:
    functions_by_selector: dict[str, tuple[FunctionSignatureDefinition, ...]]
    events_by_topic: dict[str, tuple[EventSignatureDefinition, ...]]
    errors_by_selector: dict[str, tuple[ErrorSignatureDefinition, ...]]

    @property
    def fingerprint(self) -> str:
        signatures = sorted(
            (
                type(definition).__name__,
                definition.canonical_signature,
                definition.provenance.source_id,
                definition.provenance.version,
            )
            for definitions_by_key in (
                self.functions_by_selector,
                self.events_by_topic,
                self.errors_by_selector,
            )
            for definitions in definitions_by_key.values()
            for definition in definitions
        )
        payload = repr(signatures).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @classmethod
    def bundled(cls) -> SignatureRegistry:
        functions: defaultdict[str, list[FunctionSignatureDefinition]] = defaultdict(list)
        for name, inputs in _BUNDLED_FUNCTIONS:
            signature = _canonical_signature(name, inputs)
            selector = "0x" + keccak(text=signature)[:4].hex()
            functions[selector].append(
                FunctionSignatureDefinition(
                    selector=selector,
                    name=name,
                    canonical_signature=signature,
                    inputs=inputs,
                    provenance=_BUNDLED_SOURCE,
                )
            )

        events: defaultdict[str, list[EventSignatureDefinition]] = defaultdict(list)
        for standard, name, inputs in _BUNDLED_EVENTS:
            signature = _canonical_signature(name, inputs)
            topic0 = "0x" + keccak(text=signature).hex()
            events[topic0].append(
                EventSignatureDefinition(
                    topic0=topic0,
                    name=name,
                    canonical_signature=signature,
                    inputs=inputs,
                    provenance=_BUNDLED_SOURCE,
                    standard=standard,
                )
            )

        errors: defaultdict[str, list[ErrorSignatureDefinition]] = defaultdict(list)
        for name, inputs in _BUILTIN_ERRORS:
            signature = _canonical_signature(name, inputs)
            selector = "0x" + keccak(text=signature)[:4].hex()
            errors[selector].append(
                ErrorSignatureDefinition(
                    selector=selector,
                    name=name,
                    canonical_signature=signature,
                    inputs=inputs,
                    provenance=_SOLIDITY_SOURCE,
                )
            )

        return cls(
            functions_by_selector={key: tuple(value) for key, value in functions.items()},
            events_by_topic={key: tuple(value) for key, value in events.items()},
            errors_by_selector={key: tuple(value) for key, value in errors.items()},
        )

    @classmethod
    def combine(cls, *registries: SignatureRegistry) -> SignatureRegistry:
        return cls(
            functions_by_selector=_combine_definition_maps(
                *(registry.functions_by_selector for registry in registries)
            ),
            events_by_topic=_combine_definition_maps(
                *(registry.events_by_topic for registry in registries)
            ),
            errors_by_selector=_combine_definition_maps(
                *(registry.errors_by_selector for registry in registries)
            ),
        )


class StandardABIDecoder:
    version = DECODER_VERSION

    def __init__(self, registry: SignatureRegistry | None = None) -> None:
        self._registry = registry or SignatureRegistry.bundled()

    def version_for(
        self,
        registries_by_address: Mapping[str, SignatureRegistry] | None = None,
    ) -> str:
        fingerprints = sorted(
            (address.lower(), registry.fingerprint)
            for address, registry in (registries_by_address or {}).items()
        )
        if not fingerprints:
            return DECODER_VERSION
        digest = hashlib.sha256(repr(fingerprints).encode("utf-8")).hexdigest()[:16]
        return f"{DECODER_VERSION}:{digest}"

    def decode(
        self,
        inspection: TransactionInspection,
        registries_by_address: Mapping[str, SignatureRegistry] | None = None,
        revert_data: str | None = None,
        implementation_address: str | None = None,
    ) -> TransactionDecoding:
        registries = {
            address.lower(): registry
            for address, registry in (registries_by_address or {}).items()
        }
        target_address = inspection.to_address
        target_registry = registries.get((target_address or "").lower())
        if target_registry is None and implementation_address is not None:
            target_registry = registries.get(implementation_address.lower())
        return TransactionDecoding(
            decoder_version=self.version_for(registries),
            call=self.decode_call(inspection.input_data, target_registry),
            events=tuple(
                self.decode_event(log, registries.get(log.address.lower()))
                for log in inspection.logs
            ),
            contract_address=target_address,
            implementation_address=implementation_address,
            revert=self.decode_revert(revert_data, target_registry) if revert_data else None,
        )

    def decode_call(
        self,
        input_data: str,
        registry: SignatureRegistry | None = None,
    ) -> DecodedCall:
        normalized = input_data.strip().lower()
        if len(normalized) < 10 or not normalized.startswith("0x"):
            return DecodedCall(
                status=DecodeStatus.UNKNOWN,
                selector=None,
                name=None,
                canonical_signature=None,
                arguments=(),
                provenance=None,
                error="No complete method selector is present.",
            )
        selector = normalized[:10]
        definitions = _definitions_for(
            selector,
            registry.functions_by_selector if registry is not None else None,
            self._registry.functions_by_selector,
        )
        if not definitions:
            return DecodedCall(
                status=DecodeStatus.UNKNOWN,
                selector=selector,
                name=None,
                canonical_signature=None,
                arguments=(),
                provenance=None,
                error="Method selector is not in the local signature registry.",
            )
        try:
            payload = _hex_bytes(normalized[10:])
        except ValueError:
            return _malformed_call(definitions[0], selector)

        for definition in definitions:
            try:
                values = _decode_values(
                    tuple(item.abi_type for item in definition.inputs),
                    payload,
                )
            except DecodingError:
                continue
            return DecodedCall(
                status=DecodeStatus.DECODED,
                selector=selector,
                name=definition.name,
                canonical_signature=definition.canonical_signature,
                arguments=_decoded_arguments(definition.inputs, values),
                provenance=definition.provenance,
            )
        return _malformed_call(definitions[0], selector)

    def decode_event(
        self,
        log: RawTransactionLog,
        registry: SignatureRegistry | None = None,
    ) -> DecodedEvent:
        if not log.topics:
            return _unknown_event(log, None, "Event has no signature topic.")
        topic0 = log.topics[0].lower()
        definitions = _definitions_for(
            topic0,
            registry.events_by_topic if registry is not None else None,
            self._registry.events_by_topic,
        )
        if not definitions:
            return _unknown_event(
                log,
                topic0,
                "Event topic is not in the local signature registry.",
            )

        matching_shape = tuple(
            definition
            for definition in definitions
            if sum(argument.indexed for argument in definition.inputs) == len(log.topics) - 1
        )
        if not matching_shape:
            return _malformed_event(log, definitions[0], topic0)
        for definition in matching_shape:
            try:
                arguments = _decode_event_arguments(definition, log)
            except (DecodingError, ValueError):
                continue
            return DecodedEvent(
                status=DecodeStatus.DECODED,
                log_index=log.log_index,
                topic0=topic0,
                name=definition.name,
                canonical_signature=definition.canonical_signature,
                standard=definition.standard,
                arguments=arguments,
                provenance=definition.provenance,
            )
        return _malformed_event(log, matching_shape[0], topic0)

    def decode_revert(
        self,
        revert_data: str,
        registry: SignatureRegistry | None = None,
    ) -> DecodedRevert:
        normalized = revert_data.strip().lower()
        if len(normalized) < 10 or not normalized.startswith("0x"):
            return DecodedRevert(
                status=DecodeStatus.UNKNOWN,
                raw_data=normalized,
                selector=None,
                name=None,
                canonical_signature=None,
                arguments=(),
                provenance=None,
                error="No complete error selector is present.",
            )
        selector = normalized[:10]
        definitions = _definitions_for(
            selector,
            registry.errors_by_selector if registry is not None else None,
            self._registry.errors_by_selector,
        )
        if not definitions:
            return DecodedRevert(
                status=DecodeStatus.UNKNOWN,
                raw_data=normalized,
                selector=selector,
                name=None,
                canonical_signature=None,
                arguments=(),
                provenance=None,
                error="Error selector is not in the applicable ABI registry.",
            )
        try:
            payload = _hex_bytes(normalized[10:])
        except ValueError:
            return _malformed_revert(normalized, selector, definitions[0])
        for definition in definitions:
            try:
                values = _decode_values(
                    tuple(item.abi_type for item in definition.inputs),
                    payload,
                )
            except DecodingError:
                continue
            return DecodedRevert(
                status=DecodeStatus.DECODED,
                raw_data=normalized,
                selector=selector,
                name=definition.name,
                canonical_signature=definition.canonical_signature,
                arguments=_decoded_arguments(definition.inputs, values),
                provenance=definition.provenance,
            )
        return _malformed_revert(normalized, selector, definitions[0])


def _decode_event_arguments(
    definition: EventSignatureDefinition,
    log: RawTransactionLog,
) -> tuple[DecodedArgument, ...]:
    indexed_definitions = tuple(item for item in definition.inputs if item.indexed)
    data_definitions = tuple(item for item in definition.inputs if not item.indexed)
    indexed_values: list[Any] = []
    for argument, topic in zip(indexed_definitions, log.topics[1:], strict=True):
        if _is_dynamic_type(argument.abi_type):
            indexed_values.append(topic.lower())
            continue
        decoded = _decode_values((argument.abi_type,), _topic_bytes(topic))
        indexed_values.append(decoded[0])
    data_values = _decode_values(
        tuple(item.abi_type for item in data_definitions),
        _prefixed_hex_bytes(log.data),
    )
    indexed_iterator = iter(indexed_values)
    data_iterator = iter(data_values)
    values = tuple(
        next(indexed_iterator) if argument.indexed else next(data_iterator)
        for argument in definition.inputs
    )
    return _decoded_arguments(definition.inputs, values)


def _decode_values(types: tuple[str, ...], payload: bytes) -> tuple[Any, ...]:
    if not types:
        if payload:
            raise DecodingError("Unexpected data for an event without non-indexed arguments.")
        return ()
    return tuple(abi_decode(types, payload, strict=True))


def _decoded_arguments(
    definitions: tuple[ABIArgumentDefinition, ...],
    values: tuple[Any, ...],
) -> tuple[DecodedArgument, ...]:
    return tuple(
        DecodedArgument(
            name=definition.name,
            abi_type=definition.abi_type,
            value=_format_abi_value(value),
            indexed=definition.indexed,
        )
        for definition, value in zip(definitions, values, strict=True)
    )


def _format_abi_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, bytes):
        return "0x" + value.hex()
    if isinstance(value, str):
        return value.lower() if value.startswith("0x") else value
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_format_abi_value(item) for item in value) + "]"
    return str(value)


def _canonical_signature(
    name: str,
    inputs: Iterable[ABIArgumentDefinition],
) -> str:
    return f"{name}({','.join(item.abi_type for item in inputs)})"


def _argument(name: str, abi_type: str, indexed: bool = False) -> ABIArgumentDefinition:
    return ABIArgumentDefinition(name=name, abi_type=abi_type, indexed=indexed)


def _is_dynamic_type(abi_type: str) -> bool:
    return abi_type in {"bytes", "string"} or abi_type.endswith("[]")


def _hex_bytes(unprefixed: str) -> bytes:
    if len(unprefixed) % 2 != 0:
        raise ValueError("Hexadecimal payload has an odd length.")
    return bytes.fromhex(unprefixed)


def _prefixed_hex_bytes(value: str) -> bytes:
    normalized = value.strip().lower()
    if not normalized.startswith("0x"):
        raise ValueError("Hexadecimal payload has no prefix.")
    return _hex_bytes(normalized[2:])


def _topic_bytes(value: str) -> bytes:
    decoded = _prefixed_hex_bytes(value)
    if len(decoded) != 32:
        raise ValueError("Indexed event topic is not 32 bytes.")
    return decoded


def _malformed_call(
    definition: FunctionSignatureDefinition,
    selector: str,
) -> DecodedCall:
    return DecodedCall(
        status=DecodeStatus.MALFORMED,
        selector=selector,
        name=definition.name,
        canonical_signature=definition.canonical_signature,
        arguments=(),
        provenance=definition.provenance,
        error="Calldata does not match the registered function signature.",
    )


def _unknown_event(log: RawTransactionLog, topic0: str | None, error: str) -> DecodedEvent:
    return DecodedEvent(
        status=DecodeStatus.UNKNOWN,
        log_index=log.log_index,
        topic0=topic0,
        name=None,
        canonical_signature=None,
        standard=None,
        arguments=(),
        provenance=None,
        error=error,
    )


def _malformed_event(
    log: RawTransactionLog,
    definition: EventSignatureDefinition,
    topic0: str,
) -> DecodedEvent:
    return DecodedEvent(
        status=DecodeStatus.MALFORMED,
        log_index=log.log_index,
        topic0=topic0,
        name=definition.name,
        canonical_signature=definition.canonical_signature,
        standard=definition.standard,
        arguments=(),
        provenance=definition.provenance,
        error="Log topics or data do not match the registered event signature.",
    )


def _malformed_revert(
    raw_data: str,
    selector: str,
    definition: ErrorSignatureDefinition,
) -> DecodedRevert:
    return DecodedRevert(
        status=DecodeStatus.MALFORMED,
        raw_data=raw_data,
        selector=selector,
        name=definition.name,
        canonical_signature=definition.canonical_signature,
        arguments=(),
        provenance=definition.provenance,
        error="Revert data does not match the registered error signature.",
    )


def _definitions_for(
    key: str,
    preferred: Mapping[str, tuple[_Definition, ...]] | None,
    fallback: Mapping[str, tuple[_Definition, ...]],
) -> tuple[_Definition, ...]:
    return _unique_definitions(
        *((preferred.get(key, ()) if preferred is not None else ()), fallback.get(key, ()))
    )


def _combine_definition_maps(
    *maps: Mapping[str, tuple[_Definition, ...]],
) -> dict[str, tuple[_Definition, ...]]:
    keys = {key for definitions_by_key in maps for key in definitions_by_key}
    return {
        key: _unique_definitions(*(definitions_by_key.get(key, ()) for definitions_by_key in maps))
        for key in keys
    }


def _unique_definitions(
    *groups: tuple[_Definition, ...],
) -> tuple[_Definition, ...]:
    definitions: list[_Definition] = []
    seen: set[str] = set()
    for definition in (item for group in groups for item in group):
        marker = repr(definition)
        if marker in seen:
            continue
        seen.add(marker)
        definitions.append(definition)
    return tuple(definitions)


_BUNDLED_FUNCTIONS: tuple[tuple[str, tuple[ABIArgumentDefinition, ...]], ...] = (
    ("transfer", (_argument("to", "address"), _argument("value", "uint256"))),
    ("approve", (_argument("spender", "address"), _argument("value", "uint256"))),
    (
        "transferFrom",
        (
            _argument("from", "address"),
            _argument("to", "address"),
            _argument("valueOrTokenId", "uint256"),
        ),
    ),
    (
        "safeTransferFrom",
        (
            _argument("from", "address"),
            _argument("to", "address"),
            _argument("tokenId", "uint256"),
        ),
    ),
    (
        "safeTransferFrom",
        (
            _argument("from", "address"),
            _argument("to", "address"),
            _argument("tokenId", "uint256"),
            _argument("data", "bytes"),
        ),
    ),
    (
        "setApprovalForAll",
        (_argument("operator", "address"), _argument("approved", "bool")),
    ),
    (
        "safeTransferFrom",
        (
            _argument("from", "address"),
            _argument("to", "address"),
            _argument("id", "uint256"),
            _argument("value", "uint256"),
            _argument("data", "bytes"),
        ),
    ),
    (
        "safeBatchTransferFrom",
        (
            _argument("from", "address"),
            _argument("to", "address"),
            _argument("ids", "uint256[]"),
            _argument("values", "uint256[]"),
            _argument("data", "bytes"),
        ),
    ),
)

_BUILTIN_ERRORS: tuple[tuple[str, tuple[ABIArgumentDefinition, ...]], ...] = (
    ("Error", (_argument("reason", "string"),)),
    ("Panic", (_argument("code", "uint256"),)),
)

_BUNDLED_EVENTS: tuple[
    tuple[str, str, tuple[ABIArgumentDefinition, ...]],
    ...,
] = (
    (
        "ERC-20",
        "Transfer",
        (
            _argument("from", "address", indexed=True),
            _argument("to", "address", indexed=True),
            _argument("value", "uint256"),
        ),
    ),
    (
        "ERC-721",
        "Transfer",
        (
            _argument("from", "address", indexed=True),
            _argument("to", "address", indexed=True),
            _argument("tokenId", "uint256", indexed=True),
        ),
    ),
    (
        "ERC-20",
        "Approval",
        (
            _argument("owner", "address", indexed=True),
            _argument("spender", "address", indexed=True),
            _argument("value", "uint256"),
        ),
    ),
    (
        "ERC-721",
        "Approval",
        (
            _argument("owner", "address", indexed=True),
            _argument("approved", "address", indexed=True),
            _argument("tokenId", "uint256", indexed=True),
        ),
    ),
    (
        "ERC-721 / ERC-1155",
        "ApprovalForAll",
        (
            _argument("owner", "address", indexed=True),
            _argument("operator", "address", indexed=True),
            _argument("approved", "bool"),
        ),
    ),
    (
        "ERC-1155",
        "TransferSingle",
        (
            _argument("operator", "address", indexed=True),
            _argument("from", "address", indexed=True),
            _argument("to", "address", indexed=True),
            _argument("id", "uint256"),
            _argument("value", "uint256"),
        ),
    ),
    (
        "ERC-1155",
        "TransferBatch",
        (
            _argument("operator", "address", indexed=True),
            _argument("from", "address", indexed=True),
            _argument("to", "address", indexed=True),
            _argument("ids", "uint256[]"),
            _argument("values", "uint256[]"),
        ),
    ),
    (
        "ERC-1155",
        "URI",
        (
            _argument("value", "string"),
            _argument("id", "uint256", indexed=True),
        ),
    ),
)
