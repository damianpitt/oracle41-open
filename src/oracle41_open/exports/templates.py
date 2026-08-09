from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from oracle41_open.core.models import CompletenessState, DataProvenance

ACTIVITY_EXPORT_FORMAT = "oracle41-activity"
ACTIVITY_EXPORT_FORMAT_VERSION = 2


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
