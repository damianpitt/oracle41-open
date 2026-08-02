from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from typing import TypeVar

from oracle41_open.core.models import ActivityPage, Chain, ProviderError, TokenBalancePage
from oracle41_open.providers.data_provider import DataProvider

_T = TypeVar("_T")


class FailoverDataProvider:
    def __init__(self, primary: DataProvider, fallback: DataProvider) -> None:
        self._primary = primary
        self._fallback = fallback

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        return self._run_with_failover(lambda provider: provider.get_native_balance(address, chain))

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        return self._run_with_failover(
            lambda provider: provider.get_token_balances(address, chain, page_key=page_key)
        )

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        return self._run_with_failover(
            lambda provider: provider.get_activity(
                address,
                chain,
                cursor=cursor,
                from_block=from_block,
            )
        )

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        return self._run_with_failover(
            lambda provider: provider.get_token_transfers(
                address,
                token_address,
                chain,
                cursor=cursor,
                include_approvals=include_approvals,
            )
        )

    def _run_with_failover(self, operation: Callable[[DataProvider], _T]) -> _T:
        try:
            return operation(self._primary)
        except ProviderError as primary_error:
            try:
                return operation(self._fallback)
            except ProviderError as fallback_error:
                raise ProviderError(
                    f"Primary and fallback providers failed: {primary_error!s}; {fallback_error!s}"
                ) from fallback_error
