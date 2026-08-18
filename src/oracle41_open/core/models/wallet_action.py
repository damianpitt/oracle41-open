"""Describe normalized wallet actions and their source evidence.

Actions summarize transaction intent and effects without replacing receipts, decoded logs, or traces.
Asset direction is relative to the transaction initiator, and every action links back to immutable source data.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from oracle41_open.core.models.chain import Chain
from oracle41_open.core.models.transaction import TraceStatus


class WalletActionKind(str, Enum):
    TRANSFER = "transfer"
    APPROVAL = "approval"
    SWAP = "swap"
    DEPLOYMENT = "deployment"
    CONTRACT_CALL = "contract_call"
    UNKNOWN = "unknown"


class WalletActionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ActionConfidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ActionAssetDirection(str, Enum):
    IN = "in"
    OUT = "out"
    NEUTRAL = "neutral"


class ActionEvidenceKind(str, Enum):
    CALL = "call"
    EVENT = "event"
    TRACE = "trace"
    RECEIPT = "receipt"


class ActionSetCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class ActionParticipant:
    role: str
    address: str


@dataclass(frozen=True)
class ActionAsset:
    direction: ActionAssetDirection
    standard: str
    contract_address: str | None
    symbol: str | None
    token_id: str | None
    raw_amount: str


@dataclass(frozen=True)
class ActionEvidence:
    kind: ActionEvidenceKind
    reference: str
    contract_address: str | None = None
    signature: str | None = None
    source_id: str | None = None


@dataclass(frozen=True)
class WalletAction:
    chain: Chain
    tx_hash: str
    action_index: int
    kind: WalletActionKind
    status: WalletActionStatus
    summary: str
    participants: tuple[ActionParticipant, ...]
    assets: tuple[ActionAsset, ...]
    protocol_hint: str | None
    confidence: ActionConfidence
    evidence: tuple[ActionEvidence, ...]
    normalizer_version: str


@dataclass(frozen=True)
class WalletActionSet:
    chain: Chain
    tx_hash: str
    actions: tuple[WalletAction, ...]
    completeness: ActionSetCompleteness
    trace_status: TraceStatus | None
    missing_evidence: tuple[str, ...]
    normalizer_version: str
