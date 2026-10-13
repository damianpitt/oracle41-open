"""Describe issuer-published xStocks action revisions and multiplier changes.

Corporate-action revisions retain their event ID, version, and original status. Historical multiplier
changes keep the issuer's activation time separate from the local fetch time. These records describe
issuer evidence and are never applied again to EVM token balances, which already reflect rebasing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from oracle41_open.core.models.real_world_asset import RealWorldAssetIdentity


@dataclass(frozen=True)
class XStocksCorporateAction:
    event_id: str
    version: int
    symbol: str
    action_type: str
    status: str
    created_at: datetime
    effective_at: datetime | None
    details: tuple[tuple[str, str | None], ...]


@dataclass(frozen=True)
class XStocksMultiplierChange:
    change_id: str
    reason: str
    multiplier: Decimal
    previous_multiplier: Decimal
    effective_at: datetime


@dataclass(frozen=True)
class XStocksAuditContext:
    """Bind issuer history to a verified deployment and its last successful fetch."""

    identity: RealWorldAssetIdentity
    corporate_actions: tuple[XStocksCorporateAction, ...]
    multiplier_history: tuple[XStocksMultiplierChange, ...]
    fetched_at: datetime
