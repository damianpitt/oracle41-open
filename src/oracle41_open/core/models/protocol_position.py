"""Describe protocol positions and the evidence used to build them.

These immutable models represent supplied assets, debt, collateral, liquidity, staking, vesting, and rewards.
Every result keeps its original actions, balances, decoded events, and raw evidence so an adapter cannot hide source data.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from oracle41_open.core.models.chain import Chain
from oracle41_open.core.models.decoding import DecodedEvent
from oracle41_open.core.models.token_balance import TokenBalance
from oracle41_open.core.models.wallet_action import WalletAction


class ProtocolPositionKind(str, Enum):
    SUPPLIED = "supplied"
    DEBT = "debt"
    COLLATERAL = "collateral"
    LIQUIDITY = "liquidity"
    STAKING = "staking"
    VESTING = "vesting"
    REWARD = "reward"


class ProtocolAssetRole(str, Enum):
    SUPPLIED = "supplied"
    BORROWED = "borrowed"
    COLLATERAL = "collateral"
    UNDERLYING = "underlying"
    LP_TOKEN = "lp_token"
    STAKED = "staked"
    VESTING = "vesting"
    REWARD = "reward"


class ProtocolPositionCompleteness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


class ProtocolAdapterStatus(str, Enum):
    MATCHED = "matched"
    PARTIAL = "partial"
    UNKNOWN_PROTOCOL = "unknown_protocol"


class ProtocolRiskState(str, Enum):
    """Describe liquidation-threshold state without giving financial advice."""

    NO_DEBT = "no_debt"
    ABOVE_OR_EQUAL_LIQUIDATION_THRESHOLD = "above_or_equal_liquidation_threshold"
    BELOW_BORROW_COLLATERAL_REQUIREMENT = "below_borrow_collateral_requirement"
    BELOW_LIQUIDATION_THRESHOLD = "below_liquidation_threshold"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProtocolEvidenceValue:
    name: str
    value: str


@dataclass(frozen=True)
class ProtocolRawEvidence:
    kind: str
    reference: str
    contract_address: str | None
    tx_hash: str | None
    signature: str | None
    values: tuple[ProtocolEvidenceValue, ...] = ()

    def value(self, name: str) -> str | None:
        """Return one named value without exposing a mutable mapping."""
        return next((item.value for item in self.values if item.name == name), None)


@dataclass(frozen=True)
class ProtocolAsset:
    role: ProtocolAssetRole
    standard: str
    contract_address: str | None
    symbol: str | None
    token_id: str | None
    raw_amount: str
    decimals: int | None


@dataclass(frozen=True)
class ProtocolPositionProvenance:
    adapter_id: str
    adapter_version: str
    source_provider: str
    source_reference: str
    observed_at: datetime


@dataclass(frozen=True)
class ProtocolRiskSnapshot:
    """Keep protocol-native account risk evidence without inventing common units."""

    wallet_address: str
    chain: Chain
    block_number: int
    protocol_id: str
    total_collateral_base: str | None
    total_debt_base: str | None
    available_borrow_base: str | None
    liquidation_threshold_bps: int | None
    ltv_bps: int | None
    health_factor_wad: str | None
    base_currency_unit: str | None
    state: ProtocolRiskState
    provenance: ProtocolPositionProvenance
    is_borrow_collateralized: bool | None = None
    is_liquidatable: bool | None = None


@dataclass(frozen=True)
class ProtocolPosition:
    schema_version: int
    position_id: str
    wallet_address: str
    chain: Chain
    block_number: int
    protocol_id: str
    protocol_name: str
    kind: ProtocolPositionKind
    label: str
    assets: tuple[ProtocolAsset, ...]
    contract_addresses: tuple[str, ...]
    completeness: ProtocolPositionCompleteness
    warnings: tuple[str, ...]
    provenance: ProtocolPositionProvenance


@dataclass(frozen=True)
class ProtocolContract:
    chain: Chain
    address: str


@dataclass(frozen=True)
class ProtocolAdapterCapabilities:
    adapter_id: str
    adapter_version: str
    protocol_id: str
    protocol_name: str
    chains: frozenset[Chain]
    position_kinds: frozenset[ProtocolPositionKind]
    contracts: tuple[ProtocolContract, ...]


@dataclass(frozen=True)
class ProtocolAdapterContext:
    wallet_address: str
    chain: Chain
    block_number: int
    contract_addresses: tuple[str, ...]
    actions: tuple[WalletAction, ...]
    token_balances: tuple[TokenBalance, ...]
    decoded_events: tuple[DecodedEvent, ...]
    raw_evidence: tuple[ProtocolRawEvidence, ...]
    source_provider: str
    observed_at: datetime


@dataclass(frozen=True)
class ProtocolAdapterResult:
    schema_version: int
    status: ProtocolAdapterStatus
    adapter_id: str
    adapter_version: str
    protocol_id: str | None
    protocol_name: str | None
    positions: tuple[ProtocolPosition, ...]
    source_actions: tuple[WalletAction, ...]
    source_balances: tuple[TokenBalance, ...]
    source_events: tuple[DecodedEvent, ...]
    raw_evidence: tuple[ProtocolRawEvidence, ...]
    warnings: tuple[str, ...]
    risk_snapshot: ProtocolRiskSnapshot | None = None


@dataclass(frozen=True)
class ProtocolCollectionCheckpoint:
    """Keep enough per-item collection state to continue after an interruption."""

    wallet_address: str
    chain: Chain
    protocol_id: str
    block_number: int
    reserves: tuple[tuple[str, str], ...]
    next_reserve_index: int
    raw_evidence: tuple[ProtocolRawEvidence, ...]
    source_provider: str
    observed_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class StoredProtocolSnapshot:
    """Return one durable protocol result with its storage time."""

    wallet_address: str
    chain: Chain
    protocol_id: str
    block_number: int
    result: ProtocolAdapterResult
    source_provider: str
    observed_at: datetime
    saved_at: datetime
