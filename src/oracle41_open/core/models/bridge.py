"""Describe evidence-backed cross-chain bridge activity.

Bridge observations record direction, lifecycle stage, route, assets, participants, and the exact call or event that supports the classification.
They do not infer that a transfer completed on the other chain unless a matching finalization event is present.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from oracle41_open.core.models.chain import Chain


class BridgeDirection(str, Enum):
    L1_TO_L2 = "l1_to_l2"
    L2_TO_L1 = "l2_to_l1"


class BridgeStage(str, Enum):
    INITIATED = "initiated"
    FINALIZED = "finalized"


class BridgeTransferKind(str, Enum):
    TOKEN = "token"
    MESSAGE = "message"


@dataclass(frozen=True)
class BridgeObservation:
    """One bridge lifecycle fact derived from a known contract and signature."""

    chain: Chain
    tx_hash: str
    bridge_id: str
    bridge_name: str
    direction: BridgeDirection
    stage: BridgeStage
    transfer_kind: BridgeTransferKind
    source_chain: Chain
    destination_chain: Chain
    contract_address: str
    sender_address: str | None
    recipient_address: str | None
    token_address: str | None
    raw_amount: str | None
    message_id: str | None
    evidence_reference: str
    evidence_signature: str

