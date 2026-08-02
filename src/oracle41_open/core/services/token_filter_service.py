from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from oracle41_open.core.models import TokenBalance

_LOW_SIGNAL_SYMBOLS = {"UNKNOWN", "UNKWN", "SPAM"}
_LOW_SIGNAL_NAMES = {"unknown", "unknown token", "spam"}


@dataclass(frozen=True)
class TokenFilterSettings:
    hide_unverified: bool
    hide_dust: bool
    dust_threshold_usd: Decimal


class TokenFilterService:
    def filter_balances(
        self,
        balances: list[TokenBalance],
        settings: TokenFilterSettings,
    ) -> list[TokenBalance]:
        filtered: list[TokenBalance] = []
        for balance in balances:
            if balance.balance_decimal <= Decimal("0"):
                continue
            if settings.hide_unverified and self._is_unverified_or_low_signal(balance):
                continue
            if settings.hide_dust and self._is_dust(balance, threshold=settings.dust_threshold_usd):
                continue
            filtered.append(balance)
        return filtered

    def _is_unverified_or_low_signal(self, balance: TokenBalance) -> bool:
        if not balance.token.is_verified:
            return True

        symbol = balance.token.symbol.strip().upper()
        if symbol in _LOW_SIGNAL_SYMBOLS:
            return True

        name = balance.token.name.strip().lower()
        return name in _LOW_SIGNAL_NAMES

    def _is_dust(self, balance: TokenBalance, threshold: Decimal) -> bool:
        if threshold <= Decimal("0"):
            return False
        value_usd = balance.balance_usd
        if value_usd is None:
            return False
        return abs(value_usd) < threshold
