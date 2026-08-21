"""Describe token balances and paginated balance results.

The models preserve raw integer values while also exposing decimal and optional price information.
Paginated results keep their source provider and cursor so services can report incomplete results safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from oracle41_open.core.models.token import Token


@dataclass(frozen=True)
class TokenBalance:
    token: Token
    balance_decimal: Decimal
    price_usd: Decimal | None = None

    @property
    def balance_usd(self) -> Decimal | None:
        if self.price_usd is None:
            return None
        return self.balance_decimal * self.price_usd


@dataclass(frozen=True)
class TokenBalancePage:
    balances: list[TokenBalance]
    next_page_key: str | None
    source_provider: str | None = None
