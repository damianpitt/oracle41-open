"""Describe token identity and metadata.

The token model stores contract details, decimals, verification state, and token standard information.
Balances and prices are kept in separate models and service results.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Token:
    contract_address: str
    symbol: str
    name: str
    decimals: int
    is_verified: bool
