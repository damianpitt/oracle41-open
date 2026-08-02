from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    contract_address: str
    symbol: str
    name: str
    decimals: int
    is_verified: bool
