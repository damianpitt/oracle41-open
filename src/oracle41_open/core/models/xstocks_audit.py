"""Describe issuer-published xStocks action revisions and multiplier changes.

Corporate-action revisions retain their event ID, version, and original status. Historical multiplier
changes keep the issuer's activation time separate from the local fetch time. These records describe
issuer evidence and are never applied again to EVM token balances, which already reflect rebasing.
Page and checkpoint models keep pending sync progress separate from published history.
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
    fetched_at: datetime | None
    sync_progress: XStocksSyncProgress | None = None


@dataclass(frozen=True)
class XStocksHistoryPage:
    """One validated issuer page, with the continuation belonging to its feed."""

    feed: str
    page: int
    next_page: int | None
    corporate_actions: tuple[XStocksCorporateAction, ...] = ()
    multiplier_history: tuple[XStocksMultiplierChange, ...] = ()


@dataclass(frozen=True)
class XStocksSyncProgress:
    """Durable staging progress; its revision protects concurrent page writers."""

    identity: RealWorldAssetIdentity
    run_id: str
    revision: int
    state: str
    actions_next_page: int
    multipliers_next_page: int
    actions_done: bool
    multipliers_done: bool
    actions_pages: int
    multipliers_pages: int
    actions_staged: int
    multipliers_staged: int
    started_at: datetime
    updated_at: datetime
