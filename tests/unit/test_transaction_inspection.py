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
    ProviderCapabilities,
    RawTransactionLog,
    TransactionInspection,
    ValidationError,
)
from oracle41_open.core.services.transaction_inspection_service import (
    TransactionInspectionService,
)
from oracle41_open.storage.db import (
    EventLedgerRepository,
    SQLiteDatabase,
    TransactionRepository,
)

_TX_HASH = "0x" + "ab" * 32
_ADDRESS = "0x1111111111111111111111111111111111111111"
_TO = "0x2222222222222222222222222222222222222222"


def test_v2_database_migrates_to_v3_without_losing_ledger_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    database = SQLiteDatabase(database_path)
    ledger = EventLedgerRepository(database)
    ledger.persist_page(
        _ADDRESS,
        Chain.ETHEREUM,
        "activity",
        ActivityPage([_activity_item()], None),
        DataProvenance("alchemy", datetime(2026, 8, 10, tzinfo=UTC)),
        CompletenessState.COMPLETE,
    )
    with database.connection() as conn:
        conn.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
        conn.execute("DROP TABLE ledger_raw_logs")
        conn.execute("DROP TABLE ledger_transaction_receipts")
        conn.execute("DROP TABLE ledger_transaction_details")

    SQLiteDatabase(database_path)

    with sqlite3.connect(database_path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        event_count = conn.execute("SELECT COUNT(*) FROM ledger_events").fetchone()
        receipt_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'ledger_transaction_receipts'"
        ).fetchone()
    assert version == ("3",)
    assert event_count == (1,)
    assert receipt_table == ("ledger_transaction_receipts",)


def test_transaction_repository_roundtrip_and_fee_derivation(tmp_path: Path) -> None:
    database, repository = _repository_with_transaction(tmp_path)
    inspection = _inspection()

    repository.save_inspection(inspection)
    restored = repository.get_inspection(Chain.ETHEREUM, _TX_HASH)

    assert restored == inspection
    with database.connection() as conn:
        fee = conn.execute(
            "SELECT raw_value, value_decimal, asset_symbol FROM ledger_fees"
        ).fetchone()
    assert fee is not None
    assert fee["raw_value"] == "42000000000000"
    assert Decimal(fee["value_decimal"]) == Decimal("0.000042")
    assert fee["asset_symbol"] == "ETH"


def test_transaction_repository_requires_canonical_parent(tmp_path: Path) -> None:
    repository = TransactionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))

    with pytest.raises(ValidationError, match="Synchronize Activity"):
        repository.save_inspection(_inspection())


def test_transaction_repository_rolls_back_duplicate_logs(tmp_path: Path) -> None:
    database, repository = _repository_with_transaction(tmp_path)
    duplicate = RawTransactionLog(3, _TO, ("0x" + "01" * 32,), "0x", False)
    inspection = _inspection(logs=(duplicate, duplicate))

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_inspection(inspection)

    assert repository.get_inspection(Chain.ETHEREUM, _TX_HASH) is None
    with database.connection() as conn:
        fee_count = conn.execute("SELECT COUNT(*) FROM ledger_fees").fetchone()[0]
    assert fee_count == 0


def test_transaction_inspection_service_reuses_persisted_result(tmp_path: Path) -> None:
    _, repository = _repository_with_transaction(tmp_path)
    provider = _FakeTransactionProvider(_inspection())
    service = TransactionInspectionService(provider, repository)

    first = service.inspect(_TX_HASH, Chain.ETHEREUM)
    second = service.inspect(_TX_HASH, Chain.ETHEREUM)

    assert not first.is_cached
    assert second.is_cached
    assert second.inspection == first.inspection
    assert provider.calls == 1


class _FakeTransactionProvider:
    def __init__(self, inspection: TransactionInspection) -> None:
        self.inspection = inspection
        self.calls = 0

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        _ = chain
        return ProviderCapabilities(True, True)

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        _ = tx_hash
        _ = chain
        self.calls += 1
        return self.inspection


def _repository_with_transaction(tmp_path: Path) -> tuple[SQLiteDatabase, TransactionRepository]:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")
    EventLedgerRepository(database).persist_page(
        _ADDRESS,
        Chain.ETHEREUM,
        "activity",
        ActivityPage([_activity_item()], None),
        DataProvenance("alchemy", datetime(2026, 8, 10, tzinfo=UTC)),
        CompletenessState.COMPLETE,
    )
    return database, TransactionRepository(database)


def _activity_item() -> ActivityItem:
    return ActivityItem(
        block_number=24_000_000,
        tx_hash=_TX_HASH,
        log_index="0x0",
        timestamp=datetime(2026, 8, 10, tzinfo=UTC),
        from_address=_ADDRESS,
        to_address=_TO,
        asset_symbol="ETH",
        contract_address=None,
        raw_value="1",
        value_decimal=Decimal("0.000000000000000001"),
        value_usd=None,
        is_verified=True,
        category=ActivityCategory.EXTERNAL,
        chain=Chain.ETHEREUM,
    )


def _inspection(
    logs: tuple[RawTransactionLog, ...] | None = None,
) -> TransactionInspection:
    return TransactionInspection(
        chain=Chain.ETHEREUM,
        tx_hash=_TX_HASH,
        block_number=24_000_000,
        block_hash="0x" + "cd" * 32,
        transaction_index=2,
        from_address=_ADDRESS,
        to_address=_TO,
        contract_address=None,
        nonce=7,
        value_wei=1,
        input_data="0xa9059cbb",
        gas_limit=21_000,
        gas_price=2_000_000_000,
        max_fee_per_gas=3_000_000_000,
        max_priority_fee_per_gas=1_000_000_000,
        status=True,
        gas_used=21_000,
        cumulative_gas_used=90_000,
        effective_gas_price=2_000_000_000,
        transaction_type=2,
        logs_bloom="0x" + "00" * 256,
        logs=logs
        if logs is not None
        else (RawTransactionLog(3, _TO, ("0x" + "01" * 32,), "0x", False),),
        source_provider="json-rpc",
        fetched_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
    )
