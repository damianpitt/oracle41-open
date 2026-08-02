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
