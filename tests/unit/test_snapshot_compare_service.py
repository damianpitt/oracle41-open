from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from oracle41_open.core.models import Chain, ValidationError
from oracle41_open.core.services.snapshot_compare_service import SnapshotCompareService
from oracle41_open.storage.db.models import WalletSnapshot


def test_snapshot_compare_service_builds_balance_and_value_deltas() -> None:
    service = SnapshotCompareService()
    first = _snapshot(
        snapshot_id=1,
        captured_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        native_balance=Decimal("1"),
        native_price_usd=Decimal("2000"),
        total_usd=Decimal("2120"),
        token_count=2,
        payload_balances=[
            _token_payload(
                contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                symbol="USDC",
                balance_decimal="100",
                balance_usd="100",
            ),
            _token_payload(
                contract="0xdac17f958d2ee523a2206206994597c13d831ec7",
                symbol="USDT",
                balance_decimal="20",
                balance_usd="20",
            ),
        ],
    )
    second = _snapshot(
        snapshot_id=2,
        captured_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
        native_balance=Decimal("1.25"),
        native_price_usd=Decimal("2200"),
        total_usd=Decimal("2940"),
        token_count=2,
        payload_balances=[
            _token_payload(
                contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                symbol="USDC",
                balance_decimal="150",
                balance_usd="150",
            ),
            _token_payload(
                contract="0x6b175474e89094c44da98b954eedeac495271d0f",
                symbol="DAI",
                balance_decimal="40",
                balance_usd="40",
            ),
        ],
    )

    comparison = service.compare_snapshots(first, second)

    assert comparison.older_snapshot.id == 1
    assert comparison.newer_snapshot.id == 2
    assert comparison.native_balance_delta == Decimal("0.25")
    assert comparison.native_usd_delta == Decimal("750")
    assert comparison.total_usd_delta == Decimal("820")
    assert comparison.token_count_delta == 0
    assert comparison.added_token_count == 1
    assert comparison.removed_token_count == 1
    assert comparison.changed_token_count == 1

    by_symbol = {delta.symbol: delta for delta in comparison.token_deltas}
    assert by_symbol["USDC"].change_type == "changed"
    assert by_symbol["USDC"].balance_delta == Decimal("50")
    assert by_symbol["USDC"].usd_delta == Decimal("50")
    assert by_symbol["DAI"].change_type == "added"
    assert by_symbol["DAI"].before_balance == Decimal("0")
    assert by_symbol["USDT"].change_type == "removed"
    assert by_symbol["USDT"].after_balance == Decimal("0")


def test_snapshot_compare_service_can_include_unchanged_tokens() -> None:
    service = SnapshotCompareService()
    first = _snapshot(
        snapshot_id=1,
        captured_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        native_balance=Decimal("1"),
        native_price_usd=Decimal("2000"),
        total_usd=Decimal("2100"),
        token_count=1,
        payload_balances=[
            _token_payload(
                contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                symbol="USDC",
                balance_decimal="100",
                balance_usd="100",
            )
        ],
    )
    second = _snapshot(
        snapshot_id=2,
        captured_at=datetime(2026, 3, 1, 10, 5, tzinfo=UTC),
        native_balance=Decimal("1"),
        native_price_usd=Decimal("2000"),
        total_usd=Decimal("2100"),
        token_count=1,
        payload_balances=[
            _token_payload(
                contract="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                symbol="USDC",
                balance_decimal="100",
                balance_usd="100",
            )
        ],
    )

    hidden_unchanged = service.compare_snapshots(first, second, include_unchanged_tokens=False)
    included_unchanged = service.compare_snapshots(first, second, include_unchanged_tokens=True)

    assert hidden_unchanged.token_deltas == []
    assert len(included_unchanged.token_deltas) == 1
    assert included_unchanged.token_deltas[0].change_type == "unchanged"


def test_snapshot_compare_service_rejects_cross_wallet_or_chain_compare() -> None:
    service = SnapshotCompareService()
    first = _snapshot(
        snapshot_id=1,
        captured_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        native_balance=Decimal("1"),
        native_price_usd=Decimal("2000"),
        total_usd=Decimal("2100"),
        token_count=0,
        payload_balances=[],
    )
    second = _snapshot(
        snapshot_id=2,
        captured_at=datetime(2026, 3, 1, 10, 5, tzinfo=UTC),
        native_balance=Decimal("1"),
        native_price_usd=Decimal("2000"),
        total_usd=Decimal("2100"),
        token_count=0,
        payload_balances=[],
        address="0x9999999999999999999999999999999999999999",
    )

    with pytest.raises(ValidationError):
        service.compare_snapshots(first, second)


def _snapshot(
    snapshot_id: int,
    captured_at: datetime,
    native_balance: Decimal,
    native_price_usd: Decimal | None,
    total_usd: Decimal | None,
    token_count: int,
    payload_balances: list[dict[str, object]],
    address: str = "0x1111111111111111111111111111111111111111",
    chain: Chain = Chain.ETHEREUM,
) -> WalletSnapshot:
    return WalletSnapshot(
        id=snapshot_id,
        address=address,
        chain=chain,
        label=None,
        captured_at=captured_at,
        native_balance=native_balance,
        native_price_usd=native_price_usd,
        total_usd=total_usd,
        token_count=token_count,
        payload={
            "token_balances": payload_balances,
        },
    )


def _token_payload(
    contract: str,
    symbol: str,
    balance_decimal: str,
    balance_usd: str,
) -> dict[str, object]:
    return {
        "contract_address": contract,
        "symbol": symbol,
        "balance_decimal": balance_decimal,
        "balance_usd": balance_usd,
    }
