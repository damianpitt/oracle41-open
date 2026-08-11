from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DecodeStatus(str, Enum):
    DECODED = "decoded"
    UNKNOWN = "unknown"
    MALFORMED = "malformed"


class SignatureSourceKind(str, Enum):
    BUNDLED_STANDARD = "bundled_standard"
    VERIFIED_ABI = "verified_abi"
    USER_ABI = "user_abi"
    REMOTE_REGISTRY = "remote_registry"
    INFERRED = "inferred"


@dataclass(frozen=True)
class SignatureProvenance:
    source_id: str
    source_name: str
    source_kind: SignatureSourceKind
    version: str
    is_verified: bool
    reference: str | None = None


@dataclass(frozen=True)
class ABIArgumentDefinition:
    name: str
    abi_type: str
    indexed: bool = False


@dataclass(frozen=True)
class FunctionSignatureDefinition:
    selector: str
    name: str
    canonical_signature: str
    inputs: tuple[ABIArgumentDefinition, ...]
    provenance: SignatureProvenance


@dataclass(frozen=True)
class EventSignatureDefinition:
    topic0: str
    name: str
    canonical_signature: str
    inputs: tuple[ABIArgumentDefinition, ...]
    provenance: SignatureProvenance
    standard: str


@dataclass(frozen=True)
class DecodedArgument:
    name: str
    abi_type: str
    value: str
    indexed: bool = False


@dataclass(frozen=True)
class DecodedCall:
    status: DecodeStatus
    selector: str | None
    name: str | None
    canonical_signature: str | None
    arguments: tuple[DecodedArgument, ...]
    provenance: SignatureProvenance | None
    error: str | None = None


@dataclass(frozen=True)
class DecodedEvent:
    status: DecodeStatus
    log_index: int
    topic0: str | None
    name: str | None
    canonical_signature: str | None
    standard: str | None
    arguments: tuple[DecodedArgument, ...]
    provenance: SignatureProvenance | None
    error: str | None = None


@dataclass(frozen=True)
class TransactionDecoding:
    decoder_version: str
    call: DecodedCall
    events: tuple[DecodedEvent, ...]
