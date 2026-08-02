from __future__ import annotations

from decimal import Decimal
from typing import Protocol

from oracle41_open.core.models import ActivityPage, Chain, TokenBalancePage


class DataProvider(Protocol):
    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        ...

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        ...

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        ...

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        ...
