from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from oracle41_open.core.models import Chain


@dataclass(frozen=True)
class WalletNote:
    id: int
    address: str
    chain: Chain
    note: str
    tags: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class SavedView:
    id: int
    name: str
    chain: Chain
    filters: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class WalletSnapshot:
    id: int
    address: str
    chain: Chain
    label: str | None
    captured_at: datetime
    native_balance: Decimal
    native_price_usd: Decimal | None
    total_usd: Decimal | None
    token_count: int
    payload: dict[str, Any]
