"""Describe Robinhood Stock Token identity and current USD quote evidence.

The models keep the chain deployment, corporate-action multiplier, quote timestamp, and trading state together.
Portfolio services consume only the multiplier-adjusted midpoint; richer fields remain available for inspection and future exports.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum


class StockTokenStatus(str, Enum):
    """Represent the lifecycle state published by Robinhood's asset catalog."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    UNSPECIFIED = "unspecified"


@dataclass(frozen=True)
class StockTokenMetadata:
    """Identify one Stock Token deployment and its current UI multiplier."""

    asset_id: str
    symbol: str
    name: str
    contract_address: str
    chain_id: int
    current_multiplier: Decimal
    pending_multiplier: Decimal | None
    pending_multiplier_effective_at: datetime | None
    logo_url: str | None
    status: StockTokenStatus
    fractional_tradability: str | None
    all_day_tradability: str | None
    extended_hours_fractional_tradability: bool | None


@dataclass(frozen=True)
class StockTokenQuote:
    """Keep raw underlier prices beside the multiplier-adjusted token value."""

    metadata: StockTokenMetadata
    underlying_bid_usd: Decimal
    underlying_ask_usd: Decimal
    token_bid_usd: Decimal
    token_ask_usd: Decimal
    token_midpoint_usd: Decimal
    daily_trading_volume: Decimal | None
    is_trading_halt: bool
    generated_at: datetime
    source_provider: str
