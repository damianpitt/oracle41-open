from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from oracle41_open.core.models.activity import ActivityItem
from oracle41_open.core.models.chain import Chain


class CompletenessState(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    CAPPED = "capped"
    STALE = "stale"
    PROVIDER_LIMITED = "provider_limited"


@dataclass(frozen=True)
class DataProvenance:
    source_provider: str
    fetched_at: datetime
    request_cursor: str | None = None
    query_from_block: int | None = None
    query_to_block: int | None = None


@dataclass(frozen=True)
class TransactionRecord:
    chain: Chain
    tx_hash: str
    block_number: int | None
    timestamp: datetime
    from_address: str
    to_address: str
    source_provider: str


@dataclass(frozen=True)
class AssetRecord:
    chain: Chain
    contract_address: str | None
    symbol: str
    category: str
    is_verified: bool | None


@dataclass(frozen=True)
class AssetMovement:
    wallet_address: str
    chain: Chain
    event_id: str
    tx_hash: str
    from_address: str
    to_address: str
    asset: AssetRecord
    raw_value: str
    value_decimal: Decimal


@dataclass(frozen=True)
class ApprovalRecord:
    wallet_address: str
    chain: Chain
    event_id: str
    tx_hash: str
    owner_address: str
    spender_address: str
    asset: AssetRecord
    raw_value: str
    value_decimal: Decimal


@dataclass(frozen=True)
class FeeRecord:
    chain: Chain
    tx_hash: str
    payer_address: str
    raw_value: str
    value_decimal: Decimal
    asset_symbol: str


@dataclass(frozen=True)
class NormalizedEvent:
    wallet_address: str
    activity: ActivityItem
    provenance: DataProvenance


@dataclass(frozen=True)
class LedgerCheckpoint:
    wallet_address: str
    chain: Chain
    scope: str
    next_cursor: str | None
    completeness: CompletenessState
    provenance: DataProvenance
    updated_at: datetime
