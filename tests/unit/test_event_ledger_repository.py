"""Test canonical event ledger persistence and migration.

The cases cover schema upgrades, atomic writes, checkpoints, completeness, deduplication, and query scopes.
They protect durable history during interrupted synchronization.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    CompletenessState,
    DataProvenance,
    FeeRecord,
    ValidationError,
)
from oracle41_open.storage.db import EventLedgerRepository, SQLiteDatabase

_ADDRESS = "0x1111111111111111111111111111111111111111"
_SCOPE = "activity:from=latest"


def test_database_migrates_v1_state_without_losing_existing_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '1');
            CREATE TABLE watchlist_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                address TEXT NOT NULL,
                chain TEXT NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL,
                UNIQUE(address, chain)
            );
            INSERT INTO watchlist_entries(address, chain, label, created_at)
            VALUES(
                '0x1111111111111111111111111111111111111111',
                'ethereum',
                'Treasury',
                '2026-08-07T00:00:00+00:00'
            );
            """
        )

    SQLiteDatabase(file_path=database_path)

    with sqlite3.connect(database_path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        watchlist_count = conn.execute("SELECT COUNT(*) FROM watchlist_entries").fetchone()
        ledger_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ledger_events'"
        ).fetchone()

    assert version == ("9",)
    assert watchlist_count == (1,)
    assert ledger_table == ("ledger_events",)


def test_database_rejects_a_newer_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "future.sqlite3"
    with sqlite3.connect(database_path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '99');
            """
        )

    with pytest.raises(RuntimeError, match="newer than supported"):
        SQLiteDatabase(file_path=database_path)


def test_event_ledger_deduplicates_updates_and_restores_checkpoint(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "ledger.sqlite3")
    repository = EventLedgerRepository(database)
    fetched_at = datetime(2026, 8, 7, 8, 0, tzinfo=UTC)
    original = _activity_item(value_usd=None)
    enriched = _activity_item(value_usd=Decimal("42.50"))

    repository.persist_page(
        address=_ADDRESS,
        chain=Chain.ETHEREUM,
        scope=_SCOPE,
        page=ActivityPage(items=[original], next_cursor="page-2"),
        provenance=DataProvenance(source_provider="alchemy", fetched_at=fetched_at),
        completeness=CompletenessState.PARTIAL,
    )
    repository.persist_page(
        address=_ADDRESS,
        chain=Chain.ETHEREUM,
        scope=_SCOPE,
        page=ActivityPage(items=[enriched], next_cursor=None),
        provenance=DataProvenance(
            source_provider="alchemy",
            fetched_at=fetched_at,
            request_cursor="page-2",
        ),
        completeness=CompletenessState.COMPLETE,
    )

    restarted = EventLedgerRepository(SQLiteDatabase(file_path=database.file_path))
    page = restarted.load_page(_ADDRESS, Chain.ETHEREUM, _SCOPE)
    checkpoint = restarted.get_checkpoint(_ADDRESS, Chain.ETHEREUM, _SCOPE)

    assert restarted.event_count(_ADDRESS, Chain.ETHEREUM, _SCOPE) == 1
    assert page.next_cursor is None
    assert page.items[0].value_usd == Decimal("42.50")
    assert checkpoint is not None
    assert checkpoint.completeness is CompletenessState.COMPLETE
    assert checkpoint.provenance.request_cursor == "page-2"
    assert checkpoint.provenance.fetched_at == fetched_at
    movements = restarted.list_asset_movements(_ADDRESS, Chain.ETHEREUM, _SCOPE)
    assert len(movements) == 1
    assert movements[0].asset.symbol == "USDC"
    assert restarted.list_approvals(_ADDRESS, Chain.ETHEREUM, _SCOPE) == []

    restarted.upsert_fee(
        FeeRecord(
            chain=Chain.ETHEREUM,
            tx_hash=original.tx_hash,
            payer_address=_ADDRESS,
            raw_value="21000000000000",
            value_decimal=Decimal("0.000021"),
            asset_symbol="ETH",
        ),
        DataProvenance(source_provider="alchemy", fetched_at=fetched_at),
    )
    with database.connection() as conn:
        fee_count = conn.execute("SELECT COUNT(*) AS count FROM ledger_fees").fetchone()
    assert fee_count is not None and fee_count["count"] == 1


def test_event_ledger_projects_approval_events(tmp_path: Path) -> None:
    repository = EventLedgerRepository(SQLiteDatabase(file_path=tmp_path / "ledger.sqlite3"))
    approval = _activity_item(
        value_usd=None,
        category=ActivityCategory.APPROVAL,
    )

    repository.persist_page(
        address=_ADDRESS,
        chain=Chain.ETHEREUM,
        scope=_SCOPE,
        page=ActivityPage(items=[approval], next_cursor=None),
        provenance=DataProvenance(
            source_provider="ankr",
            fetched_at=datetime(2026, 8, 7, tzinfo=UTC),
        ),
        completeness=CompletenessState.COMPLETE,
    )

    approvals = repository.list_approvals(_ADDRESS, Chain.ETHEREUM, _SCOPE)
    assert len(approvals) == 1
    assert approvals[0].owner_address == approval.from_address
    assert approvals[0].spender_address == approval.to_address
    assert approvals[0].asset.contract_address == approval.contract_address
    assert repository.list_asset_movements(_ADDRESS, Chain.ETHEREUM, _SCOPE) == []


def test_repeating_identical_ingestion_keeps_canonical_tables_identical(tmp_path: Path) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "ledger.sqlite3")
    repository = EventLedgerRepository(database)
    page = ActivityPage(items=[_activity_item(value_usd=Decimal("42.5"))], next_cursor=None)
    provenance = DataProvenance(
        source_provider="alchemy",
        fetched_at=datetime(2026, 8, 7, tzinfo=UTC),
    )

    repository.persist_page(
        _ADDRESS,
        Chain.ETHEREUM,
        _SCOPE,
        page,
        provenance,
        CompletenessState.COMPLETE,
    )
    before = _canonical_rows(database)
    repository.persist_page(
        _ADDRESS,
        Chain.ETHEREUM,
        _SCOPE,
        page,
        provenance,
        CompletenessState.COMPLETE,
    )

    assert _canonical_rows(database) == before


def test_failed_ingestion_rolls_back_events_and_checkpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = SQLiteDatabase(file_path=tmp_path / "ledger.sqlite3")
    repository = EventLedgerRepository(database)

    def fail_after_transaction_insert(**_: object) -> None:
        raise RuntimeError("projection failed")

    monkeypatch.setattr(repository, "_upsert_event", fail_after_transaction_insert)

    with pytest.raises(RuntimeError, match="projection failed"):
        repository.persist_page(
            _ADDRESS,
            Chain.ETHEREUM,
            _SCOPE,
            ActivityPage(items=[_activity_item(value_usd=None)], next_cursor="page-2"),
            DataProvenance(
                source_provider="alchemy",
                fetched_at=datetime(2026, 8, 7, tzinfo=UTC),
            ),
            CompletenessState.PARTIAL,
        )

    with database.connection() as conn:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "ledger_transactions",
                "ledger_events",
                "ledger_assets",
                "ledger_asset_movements",
                "ledger_approvals",
                "ledger_event_scopes",
                "sync_checkpoints",
                "ingestion_runs",
            )
        }

    assert all(count == 0 for count in counts.values())


def test_event_ledger_rejects_mixed_chain_page_without_writes(tmp_path: Path) -> None:
    repository = EventLedgerRepository(SQLiteDatabase(file_path=tmp_path / "ledger.sqlite3"))
    wrong_chain_item = _activity_item(value_usd=None, chain=Chain.BASE)

    with pytest.raises(ValidationError, match="different chain"):
        repository.persist_page(
            address=_ADDRESS,
            chain=Chain.ETHEREUM,
            scope=_SCOPE,
            page=ActivityPage(items=[wrong_chain_item], next_cursor=None),
            provenance=DataProvenance(
                source_provider="alchemy",
                fetched_at=datetime(2026, 8, 7, tzinfo=UTC),
            ),
            completeness=CompletenessState.COMPLETE,
        )

    assert repository.event_count(_ADDRESS, Chain.ETHEREUM, _SCOPE) == 0
    assert repository.get_checkpoint(_ADDRESS, Chain.ETHEREUM, _SCOPE) is None


def _activity_item(
    value_usd: Decimal | None,
    chain: Chain = Chain.ETHEREUM,
    category: ActivityCategory = ActivityCategory.ERC20,
) -> ActivityItem:
    return ActivityItem(
        block_number=24_000_000,
        tx_hash="0xabc",
        log_index="0x1",
        timestamp=datetime(2026, 8, 7, 7, 30, tzinfo=UTC),
        from_address=_ADDRESS,
        to_address="0x2222222222222222222222222222222222222222",
        asset_symbol="USDC",
        contract_address="0x3333333333333333333333333333333333333333",
        raw_value="42500000",
        value_decimal=Decimal("42.5"),
        value_usd=value_usd,
        is_verified=True,
        category=category,
        chain=chain,
    )


def _canonical_rows(database: SQLiteDatabase) -> dict[str, list[tuple[object, ...]]]:
    tables = (
        "ledger_transactions",
        "ledger_events",
        "ledger_assets",
        "ledger_asset_movements",
        "ledger_approvals",
        "ledger_event_scopes",
    )
    with database.connection() as conn:
        return {
            table: [tuple(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
            for table in tables
        }
