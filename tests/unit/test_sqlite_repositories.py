"""Test notes, saved views, snapshots, and watchlist repositories.

The cases cover normal CRUD behavior, uniqueness, ordering, validation, and related records.
They protect the local metadata schema used by the desktop views.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from oracle41_open.core.models import Chain, Token, TokenBalance, WalletOverviewResult
from oracle41_open.storage.db import (
    SavedViewsRepository,
    SnapshotsRepository,
    SQLiteDatabase,
    WalletNotesRepository,
    WatchlistRepository,
)


def test_watchlist_repository_upsert_list_and_remove(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    repository = WatchlistRepository(database)
    address = "0x1111111111111111111111111111111111111111"

    first = repository.upsert_entry(address=address.upper(), chain=Chain.ETHEREUM, label="Main")
    second = repository.upsert_entry(address=address, chain=Chain.ETHEREUM, label="Treasury")

    assert first.address == address
    assert second.id == first.id
    assert second.label == "Treasury"
    assert repository.get_entry(address=address, chain=Chain.ETHEREUM) is not None
    assert [entry.address for entry in repository.list_entries(chain=Chain.ETHEREUM)] == [address]

    assert repository.remove_entry(address=address, chain=Chain.ETHEREUM)
    assert not repository.remove_entry(address=address, chain=Chain.ETHEREUM)
    assert repository.get_entry(address=address, chain=Chain.ETHEREUM) is None


def test_wallet_notes_repository_persists_note_and_tags(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    repository = WalletNotesRepository(database)
    address = "0x2222222222222222222222222222222222222222"

    first = repository.upsert_note(
        address=address,
        chain=Chain.BASE,
        note="  monitor approval spikes  ",
        tags=["Risk", "whale", "risk", "  "],
    )
    second = repository.upsert_note(
        address=address,
        chain=Chain.BASE,
        note="monitor approval + transfer spikes",
        tags=["alerts", "ops"],
    )

    assert first.note == "monitor approval spikes"
    assert first.tags == ["Risk", "whale"]
    assert second.id == first.id
    assert second.note == "monitor approval + transfer spikes"
    assert second.tags == ["alerts", "ops"]

    loaded = repository.get_note(address=address, chain=Chain.BASE)
    assert loaded is not None
    assert loaded.tags == ["alerts", "ops"]
    assert len(repository.list_notes(chain=Chain.BASE)) == 1

    assert repository.delete_note(address=address, chain=Chain.BASE)
    assert not repository.delete_note(address=address, chain=Chain.BASE)
    assert repository.get_note(address=address, chain=Chain.BASE) is None


def test_saved_views_repository_upsert_get_list_delete(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    repository = SavedViewsRepository(database)

    first = repository.upsert_view(
        name="High Value Outflows",
        chain=Chain.ETHEREUM,
        filters={"categories": ["external"], "min_value_usd": "1000"},
    )
    second = repository.upsert_view(
        name="High Value Outflows",
        chain=Chain.ARBITRUM,
        filters={"categories": ["external", "erc20"], "min_value_usd": "500"},
    )

    assert first.id == second.id
    assert second.chain is Chain.ARBITRUM
    assert second.filters["min_value_usd"] == "500"
    loaded = repository.get_view("  High Value Outflows ")
    assert loaded is not None
    assert loaded.filters["categories"] == ["external", "erc20"]
    assert len(repository.list_views()) == 1

    assert repository.delete_view("High Value Outflows")
    assert not repository.delete_view("High Value Outflows")
    assert repository.get_view("High Value Outflows") is None


def test_snapshots_repository_create_list_get_delete_and_prune(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    repository = SnapshotsRepository(database)
    address = "0x3333333333333333333333333333333333333333"
    overview = _sample_overview()

    first = repository.create_snapshot(
        address=address,
        chain=Chain.ETHEREUM,
        overview=overview,
        label="before",
        captured_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
    )
    second = repository.create_snapshot(
        address=address,
        chain=Chain.ETHEREUM,
        overview=overview,
        label="after",
        captured_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
    )
    third = repository.create_snapshot(
        address=address,
        chain=Chain.ETHEREUM,
        overview=overview,
        label="latest",
        captured_at=datetime(2026, 3, 1, 13, 0, tzinfo=UTC),
    )

    listed = repository.list_snapshots(address=address, chain=Chain.ETHEREUM, limit=10)
    assert [snapshot.id for snapshot in listed] == [third.id, second.id, first.id]
    assert listed[0].token_count == len(overview.token_balances)
    assert listed[0].payload["token_balances_truncated"] is False

    loaded = repository.get_snapshot(second.id)
    assert loaded is not None
    assert loaded.label == "after"
    assert loaded.total_usd == overview.total_usd

    removed_by_prune = repository.prune_snapshots(address=address, chain=Chain.ETHEREUM, keep_latest=2)
    assert removed_by_prune == 1
    remaining = repository.list_snapshots(address=address, chain=Chain.ETHEREUM, limit=10)
    assert [snapshot.id for snapshot in remaining] == [third.id, second.id]

    assert repository.delete_snapshot(third.id)
    assert not repository.delete_snapshot(third.id)
    assert repository.get_snapshot(third.id) is None


def _sample_overview() -> WalletOverviewResult:
    return WalletOverviewResult(
        native_balance=Decimal("1.5"),
        native_price_usd=Decimal("2500"),
        token_balances=[
            TokenBalance(
                token=Token(
                    contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    symbol="USDC",
                    name="USD Coin",
                    decimals=6,
                    is_verified=True,
                ),
                balance_decimal=Decimal("42"),
                price_usd=Decimal("1"),
            ),
            TokenBalance(
                token=Token(
                    contract_address="0xdac17f958d2ee523a2206206994597c13d831ec7",
                    symbol="USDT",
                    name="Tether USD",
                    decimals=6,
                    is_verified=True,
                ),
                balance_decimal=Decimal("10"),
                price_usd=Decimal("1"),
            ),
        ],
        total_usd=Decimal("3802"),
        updated_at=datetime(2026, 3, 1, 9, 59, tzinfo=UTC),
        token_balance_page_count=1,
        token_balances_truncated=False,
    )
