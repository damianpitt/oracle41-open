"""Describe the result of loading a wallet overview.

The result joins native balance, token balances, prices, totals, cache state, and freshness information.
It is ready for presentation without exposing provider response dictionaries.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from oracle41_open.core.models.token_balance import TokenBalance


@dataclass(frozen=True)
class WalletOverviewResult:
    native_balance: Decimal
    native_price_usd: Decimal | None
    token_balances: list[TokenBalance]
    total_usd: Decimal | None
    updated_at: datetime
    token_balance_page_count: int = 0
    token_balances_truncated: bool = False

    @staticmethod
    def now(
        native_balance: Decimal,
        native_price_usd: Decimal | None,
        token_balances: list[TokenBalance],
        total_usd: Decimal | None,
        token_balance_page_count: int = 0,
        token_balances_truncated: bool = False,
    ) -> WalletOverviewResult:
        return WalletOverviewResult(
            native_balance=native_balance,
            native_price_usd=native_price_usd,
            token_balances=token_balances,
            total_usd=total_usd,
            updated_at=datetime.now(tz=UTC),
            token_balance_page_count=token_balance_page_count,
            token_balances_truncated=token_balances_truncated,
        )
