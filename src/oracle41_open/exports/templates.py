from __future__ import annotations

from enum import Enum


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
