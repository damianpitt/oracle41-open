"""Describe ABI signatures and decoded transaction data.

These immutable models keep calls, events, reverts, proxy context, and source provenance together.
Unknown or malformed data remains explicit instead of being dropped.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from oracle41_open.core.models.chain import Chain


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


class ProxyKind(str, Enum):
    NONE = "none"
    EIP_1967 = "eip_1967"
    EIP_1967_BEACON = "eip_1967_beacon"
    EIP_1167 = "eip_1167"


class ProxyResolutionStatus(str, Enum):
    RESOLVED = "resolved"
    NOT_PROXY = "not_proxy"
    UNAVAILABLE = "unavailable"


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
class ErrorSignatureDefinition:
    selector: str
    name: str
    canonical_signature: str
    inputs: tuple[ABIArgumentDefinition, ...]
    provenance: SignatureProvenance


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
class DecodedRevert:
    status: DecodeStatus
    raw_data: str
    selector: str | None
    name: str | None
    canonical_signature: str | None
    arguments: tuple[DecodedArgument, ...]
    provenance: SignatureProvenance | None
    error: str | None = None


@dataclass(frozen=True)
class ContractABIRecord:
    chain: Chain
    contract_address: str
    contract_name: str | None
    abi_json: str
    content_hash: str
    provenance: SignatureProvenance
    imported_at: datetime


@dataclass(frozen=True)
class ProxyResolution:
    chain: Chain
    proxy_address: str
    status: ProxyResolutionStatus
    proxy_kind: ProxyKind
    implementation_address: str | None
    block_number: int
    source_provider: str
    resolved_at: datetime
    error: str | None = None
    beacon_address: str | None = None


@dataclass(frozen=True)
class TransactionDecoding:
    decoder_version: str
    call: DecodedCall
    events: tuple[DecodedEvent, ...]
    contract_address: str | None = None
    implementation_address: str | None = None
    revert: DecodedRevert | None = None
