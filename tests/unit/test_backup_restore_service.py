from __future__ import annotations

import sqlite3
import zipfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    CompletenessState,
    DataProvenance,
)
from oracle41_open.storage.backup_restore import BackupRestoreError, BackupRestoreService
from oracle41_open.storage.db import EventLedgerRepository, SQLiteDatabase, WatchlistRepository
from oracle41_open.storage.settings import AppSettings, SettingsStore


def test_backup_restore_roundtrip_restores_settings_and_sqlite_state(tmp_path: Path) -> None:
    settings_store = SettingsStore(file_path=tmp_path / "settings.json")
    sqlite_database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    watchlist_repository = WatchlistRepository(sqlite_database)
    event_ledger_repository = EventLedgerRepository(sqlite_database)
    service = BackupRestoreService(settings_store=settings_store, sqlite_database=sqlite_database)

    source_settings = AppSettings(
        selected_chain=Chain.BASE,
        hide_unverified=False,
        hide_dust=True,
        dust_threshold_usd="3.5",
        wallet_overview_max_token_pages=25,
        wallet_overview_cache_ttl_seconds=600,
        activity_cache_ttl_seconds=300,
        token_detail_cache_ttl_seconds=300,
        pricing_max_stale_age_seconds=90_000,
        cache_max_size_mb=180,
    )
    settings_store.save(source_settings)
    source_address = "0x1111111111111111111111111111111111111111"
    changed_address = "0x2222222222222222222222222222222222222222"
    watchlist_repository.upsert_entry(source_address, chain=Chain.ETHEREUM, label="source")
    event_ledger_repository.persist_page(
        address=source_address,
        chain=Chain.ETHEREUM,
        scope="activity:from=latest",
        page=ActivityPage(items=[_ledger_item(source_address)], next_cursor="page-2"),
        provenance=DataProvenance(
            source_provider="alchemy",
            fetched_at=datetime(2026, 8, 7, 10, 0, tzinfo=UTC),
        ),
        completeness=CompletenessState.PARTIAL,
    )

    backup_path = tmp_path / "oracle41-backup.zip"
    service.export_backup(backup_path)

    settings_store.save(AppSettings(selected_chain=Chain.ARBITRUM))
    watchlist_repository.upsert_entry(changed_address, chain=Chain.BASE, label="changed")
    entries_before_restore = watchlist_repository.list_entries()
    assert any(entry.address == changed_address for entry in entries_before_restore)

    restored_settings = service.restore_backup(backup_path)

    loaded_settings = settings_store.load()
    assert restored_settings == source_settings
    assert loaded_settings == source_settings

    restored_entries = watchlist_repository.list_entries()
    assert any(entry.address == source_address for entry in restored_entries)
    assert all(entry.address != changed_address for entry in restored_entries)
    restored_ledger = EventLedgerRepository(sqlite_database)
    restored_page = restored_ledger.load_page(
        source_address,
        Chain.ETHEREUM,
        "activity:from=latest",
    )
    assert [item.tx_hash for item in restored_page.items] == ["0xledger"]
    assert restored_page.next_cursor == "page-2"


def test_export_backup_bundle_contains_manifest_and_payload_files(tmp_path: Path) -> None:
    settings_store = SettingsStore(file_path=tmp_path / "settings.json")
    sqlite_database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    service = BackupRestoreService(settings_store=settings_store, sqlite_database=sqlite_database)

    backup_path = tmp_path / "bundle.zip"
    metadata = service.export_backup(backup_path)

    assert metadata.backup_path == backup_path
    assert backup_path.exists()
    with zipfile.ZipFile(backup_path, mode="r") as bundle:
        file_names = set(bundle.namelist())
        assert "manifest.json" in file_names
        assert "settings.json" in file_names
        assert "state.sqlite3" in file_names


def test_restore_backup_rejects_invalid_bundle(tmp_path: Path) -> None:
    settings_store = SettingsStore(file_path=tmp_path / "settings.json")
    sqlite_database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    service = BackupRestoreService(settings_store=settings_store, sqlite_database=sqlite_database)

    invalid_bundle = tmp_path / "invalid.zip"
    with zipfile.ZipFile(invalid_bundle, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json_dumps({"format": "unknown", "version": 1}, pretty=False))

    with pytest.raises(BackupRestoreError):
        service.restore_backup(invalid_bundle)


def test_restore_backup_rejects_sqlite_payload_missing_required_tables(tmp_path: Path) -> None:
    settings_store = SettingsStore(file_path=tmp_path / "settings.json")
    sqlite_database = SQLiteDatabase(file_path=tmp_path / "state.sqlite3")
    service = BackupRestoreService(settings_store=settings_store, sqlite_database=sqlite_database)

    empty_sqlite = tmp_path / "empty.sqlite3"
    conn = sqlite3.connect(empty_sqlite)
    try:
        conn.execute("CREATE TABLE only_test(id INTEGER PRIMARY KEY);")
        conn.commit()
    finally:
        conn.close()

    invalid_bundle = tmp_path / "invalid-sqlite.zip"
    manifest = {
        "format": "oracle41-open-backup",
        "version": 1,
        "created_at": "2026-03-05T12:00:00+00:00",
        "files": {"settings": "settings.json", "sqlite": "state.sqlite3"},
    }
    settings_payload = AppSettings().model_dump(mode="json")
    with zipfile.ZipFile(invalid_bundle, mode="w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("manifest.json", json_dumps(manifest, pretty=False))
        bundle.writestr("settings.json", json_dumps(settings_payload, pretty=False))
        bundle.write(empty_sqlite, arcname="state.sqlite3")

    with pytest.raises(BackupRestoreError, match="missing required tables"):
        service.restore_backup(invalid_bundle)


def _ledger_item(address: str) -> ActivityItem:
    return ActivityItem(
        block_number=24_000_000,
        tx_hash="0xledger",
        log_index="0x0",
        timestamp=datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
        from_address=address,
        to_address="0x2222222222222222222222222222222222222222",
        asset_symbol="ETH",
        contract_address=None,
        raw_value="1000000000000000000",
        value_decimal=Decimal("1"),
        value_usd=None,
        is_verified=True,
        category=ActivityCategory.EXTERNAL,
        chain=Chain.ETHEREUM,
    )
