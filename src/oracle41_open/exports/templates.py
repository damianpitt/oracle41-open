"""Define selectable fields for CSV and JSON exports.

Templates give the GUI stable names, labels, defaults, and field order for each report type.
They contain no file-system or provider logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from oracle41_open.core.models import CompletenessState, DataProvenance

ACTIVITY_EXPORT_FORMAT = "oracle41-activity"
ACTIVITY_EXPORT_FORMAT_VERSION = 2
PORTFOLIO_EXPORT_FORMAT = "oracle41-portfolio"
PORTFOLIO_EXPORT_FORMAT_VERSION = 3


@dataclass(frozen=True)
class ActivityExportContext:
    completeness: CompletenessState
    updated_at: datetime
    provenance: DataProvenance | None = None
    is_persisted: bool = False


class ActivityExportTemplate(str, Enum):
    FULL = "full"
    COMPACT = "compact"
    AUDIT = "audit"


class SnapshotExportTemplate(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


class PortfolioExportTemplate(str, Enum):
    SUMMARY = "summary"
    CHAINS = "chains"
    TOKENS = "tokens"
    WALLETS = "wallets"
    PROTOCOL_POSITIONS = "protocol_positions"
    PROTOCOL_RISK = "protocol_risk"
