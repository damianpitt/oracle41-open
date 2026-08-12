"""Test provider failover rules.

The cases verify fallback after expected provider failures and no fallback after programming errors.
They keep partial availability from hiding real defects.
"""

from decimal import Decimal

import pytest

from oracle41_open.core.models import ActivityPage, Chain, ProviderError, TokenBalancePage
from oracle41_open.providers.failover import FailoverDataProvider


class _Provider:
    def __init__(self, result: Decimal | Exception) -> None:
        self.result = result
        self.calls = 0

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        _ = address
        _ = chain
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        raise NotImplementedError

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        raise NotImplementedError

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        raise NotImplementedError


def test_failover_uses_secondary_provider_for_provider_errors() -> None:
    primary = _Provider(ProviderError("primary unavailable"))
    fallback = _Provider(Decimal("2.5"))
    provider = FailoverDataProvider(primary=primary, fallback=fallback)

    result = provider.get_native_balance("0x" + "1" * 40, Chain.ETHEREUM)

    assert result == Decimal("2.5")
    assert primary.calls == 1
    assert fallback.calls == 1


def test_failover_does_not_hide_programming_errors() -> None:
    primary = _Provider(RuntimeError("implementation defect"))
    fallback = _Provider(Decimal("2.5"))
    provider = FailoverDataProvider(primary=primary, fallback=fallback)

    with pytest.raises(RuntimeError, match="implementation defect"):
        provider.get_native_balance("0x" + "1" * 40, Chain.ETHEREUM)

    assert fallback.calls == 0
