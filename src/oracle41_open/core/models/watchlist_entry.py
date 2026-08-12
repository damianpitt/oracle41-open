"""Describe a saved watchlist wallet.

Each entry keeps an address, chain, optional label, and creation time.
Watchlists are read-only analysis targets and never contain private keys.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from oracle41_open.core.models.chain import Chain


@dataclass(frozen=True)
class WatchlistEntry:
    id: int
    address: str
    chain: Chain
    label: str | None
    created_at: datetime
