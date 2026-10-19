"""Test durable xStocks page staging, interruption, and atomic publication.

Synthetic issuer pages exercise histories beyond the old limit without network access. Failure
injection protects page checkpoints, the last published history, independent feed resume, and the
status exported when a first synchronization is still incomplete.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import UUID

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from oracle41_open.core.models import (
    Chain,
    ProviderTimeoutError,
    RealWorldAssetCategory,
    RealWorldAssetIdentity,
)
from oracle41_open.core.models.xstocks_audit import (
    XStocksCorporateAction,
    XStocksHistoryPage,
    XStocksMultiplierChange,
)
from oracle41_open.core.services.xstocks_audit_service import XStocksAuditService
from oracle41_open.exports.rwa_audit_export import (
    RWAAuditReport,
    rwa_audit_csv_text,
    rwa_audit_json_bytes,
)
from oracle41_open.gui.views.rwa_audit_browser import RWAAuditBrowser
from oracle41_open.storage.db import SQLiteDatabase
from oracle41_open.storage.db.xstocks_audit_repository import XStocksAuditRepository
from oracle41_open.storage.db.xstocks_sync_repository import XStocksSyncRepository

_ADDRESS = "0x" + "11" * 20
_NOW = datetime(2026, 10, 19, tzinfo=UTC)
_IDENTITY = RealWorldAssetIdentity(
    "test-asset",
    "TESTx",
    "Test xStock",
    Chain.ARBITRUM,
    _ADDRESS,
    RealWorldAssetCategory.TOKENIZED_EQUITY,
    "Test issuer",
    "xstocks-public-api",
    "https://docs.xstocks.fi/apis/openapi/assets",
)


def _action(index: int) -> XStocksCorporateAction:
    return XStocksCorporateAction(
        str(UUID(int=index)),
        1,
        "TESTx",
        "CashDividend",
        "Initial",
        _NOW + timedelta(seconds=index),
        _NOW,
        (("netCashflowUsd", "0.21"),),
    )


class _Provider:
    def __init__(self, action_pages: int = 2, multiplier_pages: int = 1, per_page: int = 1) -> None:
        self.action_pages = action_pages
        self.multiplier_pages = multiplier_pages
        self.per_page = per_page
        self.requests: list[tuple[str, int]] = []
        self.fail_at: tuple[str, int] | None = None

    def get_asset_identity(
        self, chain: Chain, contract_address: str
    ) -> RealWorldAssetIdentity | None:
        return _IDENTITY if chain is Chain.ARBITRUM and contract_address == _ADDRESS else None

    def get_history_page(
        self,
        identity: RealWorldAssetIdentity,
        feed: str,
        page: int,
    ) -> XStocksHistoryPage:
        assert identity == _IDENTITY
        self.requests.append((feed, page))
        if self.fail_at == (feed, page):
            raise ProviderTimeoutError("Synthetic timeout")
        if feed == "actions":
            start = (page - 1) * self.per_page + 1
            return XStocksHistoryPage(
                feed,
                page,
                page + 1 if page < self.action_pages else None,
                tuple(_action(index) for index in range(start, start + self.per_page)),
            )
        return XStocksHistoryPage(
            feed,
            page,
            page + 1 if page + 1 < self.multiplier_pages else None,
            multiplier_history=(
                XStocksMultiplierChange(
                    f"change-{page}",
                    "Dividend",
                    Decimal("1.000000000000000001"),
                    Decimal(1),
                    _NOW,
                ),
            ),
        )


def _repository(tmp_path: Path) -> XStocksAuditRepository:
    return XStocksAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))


def test_restart_resumes_next_page_without_publishing_staged_rows(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = XStocksAuditService(_Provider(), repository, page_budget=1).refresh(
        Chain.ARBITRUM, _ADDRESS
    )
    assert first is not None and first.sync_progress is not None
    assert first.fetched_at is None and first.corporate_actions == ()
    assert first.sync_progress.actions_next_page == 2
    assert repository.load(Chain.ARBITRUM, _ADDRESS) is None

    provider = _Provider()
    restarted = XStocksAuditService(provider, _repository(tmp_path))
    assert restarted.load(Chain.ARBITRUM, _ADDRESS) == first
    finished = restarted.refresh(Chain.ARBITRUM, _ADDRESS)
    assert provider.requests == [("actions", 2), ("multipliers", 0)]
    assert finished is not None and finished.sync_progress is None
    assert len(finished.corporate_actions) == 2 and len(finished.multiplier_history) == 1
    with repository.database.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM xstocks_sync_records").fetchone()[0] == 0


def test_only_unfinished_multiplier_feed_is_retried(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    provider = _Provider(multiplier_pages=2)
    provider.fail_at = ("multipliers", 1)
    service = XStocksAuditService(provider, repository)
    with pytest.raises(ProviderTimeoutError):
        service.refresh(Chain.ARBITRUM, _ADDRESS)
    saved = service.load(Chain.ARBITRUM, _ADDRESS)
    assert saved is not None and saved.sync_progress is not None
    assert saved.sync_progress.actions_done
    assert saved.sync_progress.multipliers_next_page == 1
    assert saved.sync_progress.state == "failed"

    resumed_provider = _Provider(multiplier_pages=2)
    finished = XStocksAuditService(resumed_provider, _repository(tmp_path)).refresh(
        Chain.ARBITRUM, _ADDRESS
    )
    assert resumed_provider.requests == [("multipliers", 1)]
    assert finished is not None and len(finished.multiplier_history) == 2


def test_history_over_2000_records_finishes_across_bounded_runs(tmp_path: Path) -> None:
    provider = _Provider(action_pages=21, per_page=100)
    service = XStocksAuditService(provider, _repository(tmp_path))
    paused = service.refresh(Chain.ARBITRUM, _ADDRESS)
    assert paused is not None and paused.sync_progress is not None
    assert paused.sync_progress.actions_staged == 2000
    assert paused.sync_progress.actions_next_page == 21
    finished = XStocksAuditService(provider, _repository(tmp_path)).refresh(
        Chain.ARBITRUM, _ADDRESS
    )
    assert finished is not None and finished.sync_progress is None
    assert len(finished.corporate_actions) == 2100
    assert provider.requests == [("actions", page) for page in range(1, 22)] + [("multipliers", 0)]


def test_pause_between_pages_has_exportable_status_without_partial_records(tmp_path: Path) -> None:
    cancel = Event()
    provider = _Provider()
    service = XStocksAuditService(provider, _repository(tmp_path))
    history = service.refresh(
        Chain.ARBITRUM,
        _ADDRESS,
        cancel_requested=cancel.is_set,
        on_progress=lambda progress: cancel.set() if progress.actions_pages else None,
    )
    assert history is not None and history.sync_progress is not None
    assert provider.requests == [("actions", 1)]
    report = RWAAuditReport(Chain.ARBITRUM, _ADDRESS, history, _NOW)
    payload = json.loads(rwa_audit_json_bytes(report))
    assert payload["format_version"] == 3
    assert payload["sync_status"] == "paused"
    assert payload["last_successful_fetch"] is None
    assert payload["actions_staged"] == 1 and payload["items"] == []
    rows = list(csv.DictReader(StringIO(rwa_audit_csv_text(report))))
    assert len(rows) == 1 and rows[0]["record_type"] == "sync_status"
    assert rows[0]["sync_status"] == "paused"


def test_page_write_failure_rolls_back_records_and_checkpoint_together(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    staging = XStocksSyncRepository(repository.database)
    progress = staging.begin(_IDENTITY)
    with repository.database.connection() as conn:
        conn.execute("""CREATE TRIGGER reject_checkpoint BEFORE UPDATE ON xstocks_sync_checkpoints
            BEGIN SELECT RAISE(ABORT, 'checkpoint refused'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        staging.stage_page(progress, _Provider().get_history_page(_IDENTITY, "actions", 1))
    assert staging.progress(Chain.ARBITRUM, _ADDRESS) == progress


def test_export_columns_are_stable_across_pending_and_completed_sync(tmp_path: Path) -> None:
    service = XStocksAuditService(_Provider(), _repository(tmp_path), page_budget=1)
    pending = service.refresh(Chain.ARBITRUM, _ADDRESS)
    assert pending is not None
    pending_report = RWAAuditReport(Chain.ARBITRUM, _ADDRESS, pending, _NOW)
    service.refresh(Chain.ARBITRUM, _ADDRESS)
    completed = service.refresh(Chain.ARBITRUM, _ADDRESS)
    assert completed is not None and completed.sync_progress is None
    completed_report = RWAAuditReport(Chain.ARBITRUM, _ADDRESS, completed, _NOW)
    pending_csv = csv.DictReader(StringIO(rwa_audit_csv_text(pending_report)))
    completed_csv = csv.DictReader(StringIO(rwa_audit_csv_text(completed_report)))
    assert pending_csv.fieldnames == completed_csv.fieldnames
    pending_json = json.loads(rwa_audit_json_bytes(pending_report))
    completed_json = json.loads(rwa_audit_json_bytes(completed_report))
    assert pending_json.keys() == completed_json.keys()
    assert completed_json["sync_status"] == "complete"
    assert completed_json["actions_complete"] is True
    assert completed_json["multipliers_complete"] is True
    assert completed_json["sync_run_id"] is None


def test_finalization_failure_keeps_published_history_and_resume_state(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    service = XStocksAuditService(_Provider(), repository)
    original = service.refresh(Chain.ARBITRUM, _ADDRESS)
    with repository.database.connection() as conn:
        conn.execute("""CREATE TRIGGER reject_publication BEFORE DELETE ON xstocks_sync_checkpoints
            BEGIN SELECT RAISE(ABORT, 'keep checkpoint'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        service.refresh(Chain.ARBITRUM, _ADDRESS)
    assert repository.load(Chain.ARBITRUM, _ADDRESS) == original
    saved = service.load(Chain.ARBITRUM, _ADDRESS)
    assert saved is not None and saved.sync_progress is not None
    assert saved.sync_progress.actions_done and saved.sync_progress.multipliers_done
    with repository.database.connection() as conn:
        conn.execute("DROP TRIGGER reject_publication")
    provider = _Provider()
    finished = XStocksAuditService(provider, repository).refresh(Chain.ARBITRUM, _ADDRESS)
    assert provider.requests == []
    assert finished is not None and finished.sync_progress is None


def test_duplicate_and_conflicting_pages_preserve_checkpoint(tmp_path: Path) -> None:
    staging = XStocksSyncRepository(_repository(tmp_path).database)
    progress = staging.begin(_IDENTITY)
    progress = staging.stage_page(progress, XStocksHistoryPage("actions", 1, 2, (_action(1),)))
    duplicate = XStocksHistoryPage("actions", 2, 3, (_action(1),))
    progress = staging.stage_page(progress, duplicate)
    assert progress.actions_staged == 1 and progress.actions_pages == 2
    conflict = replace(_action(1), status="Cancelled")
    with pytest.raises(ValueError, match="Conflicting"):
        staging.stage_page(progress, XStocksHistoryPage("actions", 3, None, (conflict,)))
    assert staging.progress(Chain.ARBITRUM, _ADDRESS) == progress


def test_concurrent_stale_writers_cannot_advance_same_checkpoint(tmp_path: Path) -> None:
    staging = XStocksSyncRepository(_repository(tmp_path).database)
    progress = staging.begin(_IDENTITY)
    page = XStocksHistoryPage("actions", 1, 2, (_action(1),))

    def write() -> bool:
        try:
            staging.stage_page(progress, page)
        except ValueError:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: write(), range(2)))
    assert sorted(results) == [False, True]
    saved = staging.progress(Chain.ARBITRUM, _ADDRESS)
    assert saved is not None and saved.actions_pages == 1 and saved.actions_staged == 1


def test_explicit_restart_replaces_pending_run_and_rejects_old_writer(tmp_path: Path) -> None:
    staging = XStocksSyncRepository(_repository(tmp_path).database)
    old = staging.begin(_IDENTITY)
    old = staging.stage_page(old, XStocksHistoryPage("actions", 1, 2, (_action(1),)))
    fresh = staging.begin(_IDENTITY, restart=True)
    assert old.run_id != fresh.run_id
    assert fresh.actions_next_page == 1 and fresh.actions_staged == 0
    with pytest.raises(ValueError, match="another task"):
        staging.stage_page(old, XStocksHistoryPage("actions", 2, None, (_action(2),)))


def test_schema_v13_migrates_to_sync_staging(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""CREATE TABLE schema_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '13');""")
    database = SQLiteDatabase(path)
    with database.connection() as conn:
        assert conn.execute("SELECT value FROM schema_meta").fetchone()[0] == "14"
        assert conn.execute("SELECT COUNT(*) FROM xstocks_sync_records").fetchone()[0] == 0


def test_browser_shows_offline_progress_and_resumes_only_unfinished_pages(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    provider = _Provider()
    service = XStocksAuditService(provider, _repository(tmp_path), page_budget=1)
    pending = service.refresh(Chain.ARBITRUM, _ADDRESS)
    assert pending is not None and pending.sync_progress is not None
    container = SimpleNamespace(uses_live_providers=False, xstocks_audit_service=service)
    browser = RWAAuditBrowser(container)  # type: ignore[arg-type]
    browser.set_scope(Chain.ARBITRUM, _ADDRESS)
    browser.load_saved()
    _wait_browser(browser)
    assert browser._actions.count() == 0
    assert browser._json_button.isEnabled()
    assert browser._refresh_button.text() == "Resume Audit Sync"
    assert not browser._refresh_button.isEnabled()
    assert "next page 2" in browser._status.text()
    assert provider.requests == [("actions", 1)]

    container.uses_live_providers = True
    browser._update_buttons()
    browser._refresh_button.click()
    _wait_browser(browser)
    assert "Actions: finished" in browser._status.text()
    browser._refresh_button.click()
    _wait_browser(browser)
    assert browser._actions.count() == 2
    assert browser._refresh_button.text() == "Refresh Audit History"
    assert provider.requests == [("actions", 1), ("actions", 2), ("multipliers", 0)]

    previous_status = browser._status.text()
    old_cancellation = browser._cancel_sync
    browser.set_scope(Chain.BASE, "0x" + "22" * 20)
    browser._on_sync_progress((old_cancellation, pending.sync_progress))
    assert browser._status.text() != previous_status
    assert "next page 2" not in browser._status.text()
    browser.close()


def _wait_browser(browser: RWAAuditBrowser) -> None:
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)

    def poll() -> None:
        if not browser._busy:
            loop.quit()
        else:
            QTimer.singleShot(10, poll)

    QTimer.singleShot(0, poll)
    timeout.start(3000)
    loop.exec()
    assert not browser._busy
