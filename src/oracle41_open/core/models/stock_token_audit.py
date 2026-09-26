"""Describe Stock Token corporate actions and multiplier audit records.

Corporate actions preserve the issuer's generic detail fields so newly introduced action types can
be retained before Oracle41 adds specialized presentation. Multiplier observations are separate,
append-only records and therefore show what the catalog reported at each material change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from oracle41_open.core.models.chain import Chain
from oracle41_open.core.models.stock_token import StockTokenStatus


class CorporateActionType(str, Enum):
    """Corporate-action variants currently documented by Robinhood."""

    UNSPECIFIED = "unspecified"
    FORWARD_SPLIT = "forward_split"
    REVERSE_SPLIT = "reverse_split"
    CASH_DIVIDEND = "cash_dividend"
    STOCK_DIVIDEND = "stock_dividend"
    SPIN_OFF = "spin_off"
    CASH_MERGER = "cash_merger"
    STOCK_MERGER = "stock_merger"
    STOCK_AND_CASH_MERGER = "stock_and_cash_merger"
    REDEMPTION = "redemption"
    NAME_CHANGE = "name_change"
    WORTHLESS_REMOVAL = "worthless_removal"
    RIGHTS_DISTRIBUTION = "rights_distribution"
    UNIT_SPLIT = "unit_split"


class CorporateActionStatus(str, Enum):
    """Lifecycle state reported by the issuer feed."""

    UNSPECIFIED = "unspecified"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


@dataclass(frozen=True)
class StockTokenCorporateAction:
    """Represent one corporate action for one exact token deployment."""

    action_id: str
    action_type: CorporateActionType
    status: CorporateActionStatus
    process_date: date | None
    token_symbol: str
    chain: Chain
    contract_address: str
    detail_kind: str
    details: tuple[tuple[str, str], ...]
    source_provider: str


@dataclass(frozen=True)
class StoredStockTokenCorporateAction:
    """Add local observation times to a corporate action."""

    action: StockTokenCorporateAction
    first_seen_at: datetime
    last_seen_at: datetime


@dataclass(frozen=True)
class StockTokenMultiplierObservation:
    """Capture one material Stock Token multiplier state."""

    asset_id: str
    token_symbol: str
    chain: Chain
    contract_address: str
    status: StockTokenStatus
    current_multiplier: Decimal
    pending_multiplier: Decimal | None
    pending_multiplier_effective_at: datetime | None
    source_provider: str
    observed_at: datetime


@dataclass(frozen=True)
class StockTokenAuditContext:
    """Return recent corporate actions and multiplier changes together."""

    corporate_actions: tuple[StoredStockTokenCorporateAction, ...]
    multiplier_history: tuple[StockTokenMultiplierObservation, ...]

