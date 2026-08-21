"""Test ordered wallet-data provider routing.

The cases cover fresh-request failover, programming errors, provider-owned cursors, and invalid continuation data.
They ensure pagination never combines pages from different providers.
"""

from decimal import Decimal

import pytest

from oracle41_open.core.models import (
    ActivityPage,
    Chain,
    ProviderError,
    ProviderRateLimitError,
    ProviderResponseError,
    TokenBalancePage,
)
from oracle41_open.providers.failover import (
    FailoverDataProvider,
    OrderedDataProviderPool,
    ProviderPoolEntry,
)


class _Provider:
    def __init__(
        self,
        native_result: Decimal | Exception = Decimal("1"),
        activity_results: dict[str | None, ActivityPage | Exception] | None = None,
        balance_results: dict[str | None, TokenBalancePage | Exception] | None = None,
    ) -> None:
        self.native_result = native_result
        self.activity_results = activity_results or {}
        self.balance_results = balance_results or {}
        self.native_calls = 0
        self.activity_cursors: list[str | None] = []
        self.balance_cursors: list[str | None] = []

    def get_native_balance(self, address: str, chain: Chain) -> Decimal:
        _ = address
        _ = chain
        self.native_calls += 1
        if isinstance(self.native_result, Exception):
            raise self.native_result
        return self.native_result

    def get_token_balances(
        self,
        address: str,
        chain: Chain,
        page_key: str | None = None,
    ) -> TokenBalancePage:
        _ = address
        _ = chain
        self.balance_cursors.append(page_key)
        result = self.balance_results[page_key]
        if isinstance(result, Exception):
            raise result
        return result

    def get_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
    ) -> ActivityPage:
        _ = address
        _ = chain
        _ = from_block
        self.activity_cursors.append(cursor)
        result = self.activity_results[cursor]
        if isinstance(result, Exception):
            raise result
        return result

    def get_token_transfers(
        self,
        address: str,
        token_address: str,
        chain: Chain,
        cursor: str | None = None,
        include_approvals: bool = False,
    ) -> ActivityPage:
        _ = token_address
        _ = include_approvals
        return self.get_activity(address, chain, cursor=cursor)


def test_failover_uses_secondary_provider_for_provider_errors() -> None:
    primary = _Provider(native_result=ProviderError("primary unavailable"))
    fallback = _Provider(native_result=Decimal("2.5"))
    provider = FailoverDataProvider(primary=primary, fallback=fallback)

    result = provider.get_native_balance("0x" + "1" * 40, Chain.ETHEREUM)

    assert result == Decimal("2.5")
    assert primary.native_calls == 1
    assert fallback.native_calls == 1


def test_ordered_pool_tries_all_entries_in_priority_order() -> None:
    first = _Provider(native_result=ProviderError("first unavailable"))
    second = _Provider(native_result=ProviderError("second unavailable"))
    third = _Provider(native_result=Decimal("3"))
    provider = OrderedDataProviderPool(
        (
            ProviderPoolEntry("alchemy", first),
            ProviderPoolEntry("ankr", second),
            ProviderPoolEntry("future-provider", third),
        )
    )

    assert provider.provider_ids == ("alchemy", "ankr", "future-provider")
    assert provider.get_native_balance("0x" + "1" * 40, Chain.BASE) == Decimal("3")
    assert [first.native_calls, second.native_calls, third.native_calls] == [1, 1, 1]


def test_failover_does_not_hide_programming_errors() -> None:
    primary = _Provider(native_result=RuntimeError("implementation defect"))
    fallback = _Provider(native_result=Decimal("2.5"))
    provider = FailoverDataProvider(primary=primary, fallback=fallback)

    with pytest.raises(RuntimeError, match="implementation defect"):
        provider.get_native_balance("0x" + "1" * 40, Chain.ETHEREUM)

    assert fallback.native_calls == 0


def test_activity_cursor_returns_only_to_the_provider_that_created_it() -> None:
    first = _Provider(activity_results={None: ProviderError("not available")})
    second = _Provider(
        activity_results={
            None: ActivityPage(items=[], next_cursor="vendor-page-2"),
            "vendor-page-2": ActivityPage(items=[], next_cursor=None),
        }
    )
    provider = OrderedDataProviderPool(
        (
            ProviderPoolEntry("alchemy", first),
            ProviderPoolEntry("ankr", second),
        )
    )

    first_page = provider.get_activity("0x" + "1" * 40, Chain.ETHEREUM)
    assert first_page.source_provider == "ankr"
    assert first_page.next_cursor is not None
    assert first_page.next_cursor != "vendor-page-2"

    second_page = provider.get_activity(
        "0x" + "1" * 40,
        Chain.ETHEREUM,
        cursor=first_page.next_cursor,
    )

    assert second_page.source_provider == "ankr"
    assert second_page.next_cursor is None
    assert first.activity_cursors == [None]
    assert second.activity_cursors == [None, "vendor-page-2"]


def test_continuation_failure_does_not_fall_through_to_another_provider() -> None:
    owner = _Provider(
        activity_results={
            None: ActivityPage(items=[], next_cursor="page-2"),
            "page-2": ProviderRateLimitError("try later"),
        }
    )
    fallback = _Provider(activity_results={None: ActivityPage(items=[], next_cursor=None)})
    provider = OrderedDataProviderPool(
        (
            ProviderPoolEntry("alchemy", owner),
            ProviderPoolEntry("ankr", fallback),
        )
    )
    first_page = provider.get_activity("0x" + "1" * 40, Chain.ETHEREUM)

    with pytest.raises(ProviderRateLimitError, match="could not continue"):
        provider.get_activity(
            "0x" + "1" * 40,
            Chain.ETHEREUM,
            cursor=first_page.next_cursor,
        )

    assert fallback.activity_cursors == []


def test_token_balance_cursor_keeps_source_provider() -> None:
    provider_adapter = _Provider(
        balance_results={
            None: TokenBalancePage(balances=[], next_page_key="next"),
            "next": TokenBalancePage(balances=[], next_page_key=None),
        }
    )
    provider = OrderedDataProviderPool((ProviderPoolEntry("alchemy", provider_adapter),))

    first_page = provider.get_token_balances("0x" + "1" * 40, Chain.POLYGON)
    second_page = provider.get_token_balances(
        "0x" + "1" * 40,
        Chain.POLYGON,
        page_key=first_page.next_page_key,
    )

    assert first_page.source_provider == "alchemy"
    assert second_page.source_provider == "alchemy"
    assert provider_adapter.balance_cursors == [None, "next"]


def test_pool_rejects_legacy_and_cross_operation_cursors() -> None:
    provider_adapter = _Provider(
        activity_results={None: ActivityPage(items=[], next_cursor="page-2")},
        balance_results={},
    )
    provider = OrderedDataProviderPool((ProviderPoolEntry("alchemy", provider_adapter),))

    with pytest.raises(ProviderResponseError, match="predates safe provider routing"):
        provider.get_activity("0x" + "1" * 40, Chain.ETHEREUM, cursor="legacy-cursor")

    activity_page = provider.get_activity("0x" + "1" * 40, Chain.ETHEREUM)
    with pytest.raises(ProviderResponseError, match="different operation"):
        provider.get_token_balances(
            "0x" + "1" * 40,
            Chain.ETHEREUM,
            page_key=activity_page.next_cursor,
        )


def test_pool_rejects_cursor_when_its_provider_is_no_longer_enabled() -> None:
    original_adapter = _Provider(
        activity_results={None: ActivityPage(items=[], next_cursor="page-2")}
    )
    original_pool = OrderedDataProviderPool(
        (ProviderPoolEntry("alchemy", original_adapter),)
    )
    first_page = original_pool.get_activity("0x" + "1" * 40, Chain.ETHEREUM)
    replacement_adapter = _Provider(
        activity_results={None: ActivityPage(items=[], next_cursor=None)}
    )
    replacement_pool = OrderedDataProviderPool(
        (ProviderPoolEntry("ankr", replacement_adapter),)
    )

    with pytest.raises(ProviderResponseError, match="source provider is not enabled"):
        replacement_pool.get_activity(
            "0x" + "1" * 40,
            Chain.ETHEREUM,
            cursor=first_page.next_cursor,
        )

    assert replacement_adapter.activity_cursors == []


def test_pool_rejects_empty_or_duplicate_provider_entries() -> None:
    adapter = _Provider()
    with pytest.raises(ValueError, match="at least one"):
        OrderedDataProviderPool(())
    with pytest.raises(ValueError, match="unique"):
        OrderedDataProviderPool(
            (
                ProviderPoolEntry("alchemy", adapter),
                ProviderPoolEntry("alchemy", adapter),
            )
        )
