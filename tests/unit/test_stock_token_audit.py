"""Test Stock Token audit persistence, schema migration, and refresh behavior.

The cases protect action upserts, first-seen evidence, material-change-only multiplier history, and
service round trips using deterministic provider data.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from oracle41_open.core.models import (
    Chain,
    CorporateActionStatus,
    CorporateActionType,
    ProviderNetworkError,
    StockTokenCorporateAction,
    StockTokenMetadata,
    StockTokenStatus,
)
from oracle41_open.core.services.stock_token_audit_service import StockTokenAuditService
from oracle41_open.exports.rwa_audit_export import (
    RWAAuditReport,
    rwa_audit_csv_text,
    rwa_audit_json_bytes,
    write_rwa_audit,
)
from oracle41_open.gui.views.rwa_audit_browser import RWAAuditBrowser
from oracle41_open.storage.db import SQLiteDatabase, StockTokenAuditRepository

_CONTRACT = "0x1111111111111111111111111111111111111111"
_FIRST = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
_SECOND = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


def test_schema_v12_adds_stock_token_audit_tables(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")

    with database.connection() as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert version is not None and version["value"] == "12"
    assert {
        "stock_token_corporate_actions",
        "stock_token_multiplier_observations",
    }.issubset(tables)


def test_multiplier_history_only_appends_material_changes(tmp_path: Path) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    metadata = _metadata()

    repository.record_multiplier(metadata, _FIRST, "robinhood-stock-token-api")
    repository.record_multiplier(metadata, _SECOND, "robinhood-stock-token-api")
    repository.record_multiplier(
        replace(metadata, current_multiplier=Decimal("2"), pending_multiplier=None),
        _SECOND,
        "robinhood-stock-token-api",
    )

    history = repository.list_multiplier_history(Chain.ROBINHOOD, _CONTRACT)
    assert len(history) == 2
    assert history[0].current_multiplier == Decimal("2")
    assert history[1].current_multiplier == Decimal("1")
    assert history[1].observed_at == _FIRST


def test_corporate_action_status_update_preserves_first_seen(tmp_path: Path) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    pending = _action(CorporateActionStatus.IN_PROGRESS)
    completed = replace(pending, status=CorporateActionStatus.COMPLETED)

    repository.save_corporate_actions((pending,), _FIRST)
    repository.save_corporate_actions((completed,), _SECOND)

    stored = repository.list_corporate_actions(Chain.ROBINHOOD, _CONTRACT)
    assert len(stored) == 1
    assert stored[0].action.status is CorporateActionStatus.COMPLETED
    assert stored[0].first_seen_at == _FIRST
    assert stored[0].last_seen_at == _SECOND


def test_service_refresh_persists_provider_actions_and_multiplier(tmp_path: Path) -> None:
    provider = _AuditProvider((_action(CorporateActionStatus.COMPLETED),))
    service = StockTokenAuditService(
        provider,
        StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3")),
        now_func=lambda: _FIRST,
    )

    context = service.refresh(_metadata())

    assert len(context.corporate_actions) == 1
    assert len(context.multiplier_history) == 1
    assert provider.contracts == [_CONTRACT]


def test_existing_v11_database_migrates_forward(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '11');
            """
        )

    database = SQLiteDatabase(path)

    with database.connection() as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert version is not None and version["value"] == "12"


def test_concurrent_multiplier_refreshes_write_one_observation(tmp_path: Path) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda _: repository.record_multiplier(_metadata(), _FIRST, "issuer"),
                range(24),
            )
        )
    assert len(repository.list_multiplier_history(Chain.ROBINHOOD, _CONTRACT)) == 1


def test_export_reads_all_records_and_preserves_decimals_after_restart(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    repository = StockTokenAuditRepository(SQLiteDatabase(path))
    for index in range(25):
        repository.record_multiplier(
            replace(_metadata(), current_multiplier=Decimal("1.000000000000000001") + index),
            _FIRST + timedelta(minutes=index),
            "issuer",
        )
    repository.save_corporate_actions((_action(CorporateActionStatus.COMPLETED),), _SECOND)
    service = StockTokenAuditService(
        _AuditProvider(()), StockTokenAuditRepository(SQLiteDatabase(path))
    )
    history = service.load(_CONTRACT, limit=None)
    report = RWAAuditReport(Chain.ROBINHOOD, _CONTRACT, history, _SECOND)
    payload = json.loads(rwa_audit_json_bytes(report))
    rows = list(csv.DictReader(StringIO(rwa_audit_csv_text(report))))
    assert len(payload["items"]) == len(rows) == 26
    assert payload["history_scope"] == "all_locally_saved_records"
    assert payload["items"][-1]["current_multiplier"] == "1.000000000000000001"
    assert json.loads(rows[0]["details"]) == {"newRate": "2", "oldRate": "1"}
    destination = tmp_path / "audit.json"
    write_rwa_audit(report, destination, as_json=True)
    assert json.loads(destination.read_bytes()) == payload


def test_export_rejects_cross_contract_evidence(tmp_path: Path) -> None:
    service = StockTokenAuditService(
        _AuditProvider((_action(CorporateActionStatus.COMPLETED),)),
        StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3")),
        now_func=lambda: _FIRST,
    )
    history = service.refresh(_metadata())
    report = RWAAuditReport(Chain.ROBINHOOD, "0x" + "22" * 20, history, _SECOND)
    with pytest.raises(ValueError, match="deployment"):
        rwa_audit_json_bytes(report)


def test_older_actions_cannot_regress_completed_state(tmp_path: Path) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    repository.save_corporate_actions((_action(CorporateActionStatus.COMPLETED),), _SECOND)
    repository.save_corporate_actions((_action(CorporateActionStatus.IN_PROGRESS),), _FIRST)
    stored = repository.list_corporate_actions(Chain.ROBINHOOD, _CONTRACT)[0]
    assert stored.action.status is CorporateActionStatus.COMPLETED
    assert stored.last_seen_at == _SECOND


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity"), Decimal("0")])
def test_invalid_multiplier_is_rejected(tmp_path: Path, value: Decimal) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    with pytest.raises(ValueError, match="finite positive"):
        repository.record_multiplier(
            replace(_metadata(), current_multiplier=value), _FIRST, "issuer"
        )


def test_browser_loads_offline_exports_and_keeps_history_on_refresh_failure(
    tmp_path: Path,
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    repository.record_multiplier(_metadata(), _FIRST, "issuer")
    repository.save_corporate_actions((_action(CorporateActionStatus.COMPLETED),), _FIRST)
    container = SimpleNamespace(
        uses_live_providers=False,
        stock_token_audit_service=StockTokenAuditService(_AuditProvider(()), repository),
        stock_token_pricing_provider=_OfflineCatalog(),
    )
    browser = RWAAuditBrowser(container)  # type: ignore[arg-type]
    browser.set_scope(Chain.ROBINHOOD, _CONTRACT)
    assert not browser._refresh_button.isEnabled()
    browser.load_saved()
    _wait_browser(browser)
    assert browser._actions.count() == browser._multipliers.count() == 1
    browser._actions.setCurrentRow(0)
    assert "newRate: 2" in browser._details.toPlainText()
    destination = tmp_path / "gui-audit.json"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(destination), ""))
    browser._json_button.click()
    assert len(json.loads(destination.read_bytes())["items"]) == 2
    container.uses_live_providers = True
    browser._start(refresh=True)
    _wait_browser(browser)
    assert "Refresh unavailable" in browser._status.text()
    assert browser._actions.count() == 1
    browser.set_scope(Chain.ROBINHOOD, "0x" + "22" * 20)
    assert browser._actions.count() == 0
    assert not browser._json_button.isEnabled()
    browser.close()


class _OfflineCatalog:
    def get_stock_token_metadata(self, address: str) -> None:
        raise ProviderNetworkError("Test network unavailable")


def _wait_browser(browser: RWAAuditBrowser) -> None:
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)

    def poll() -> None:
        if browser._busy:
            QTimer.singleShot(10, poll)
        else:
            loop.quit()

    QTimer.singleShot(0, poll)
    timer.start(3000)
    loop.exec()
    assert not browser._busy


class _AuditProvider:
    def __init__(self, actions: tuple[StockTokenCorporateAction, ...]) -> None:
        self.actions = actions
        self.contracts: list[str | None] = []

    def get_corporate_actions(
        self,
        contract_address: str | None = None,
    ) -> tuple[StockTokenCorporateAction, ...]:
        self.contracts.append(contract_address)
        return self.actions


def _metadata() -> StockTokenMetadata:
    return StockTokenMetadata(
        asset_id="0x" + "aa" * 32,
        symbol="TEST",
        name="Test Stock Token",
        contract_address=_CONTRACT,
        chain_id=4663,
        current_multiplier=Decimal("1"),
        pending_multiplier=Decimal("2"),
        pending_multiplier_effective_at=datetime(2026, 10, 1, tzinfo=UTC),
        logo_url=None,
        status=StockTokenStatus.ACTIVE,
        fractional_tradability="tradable",
        all_day_tradability="tradable",
        extended_hours_fractional_tradability=True,
    )


def _action(status: CorporateActionStatus) -> StockTokenCorporateAction:
    return StockTokenCorporateAction(
        action_id="0x" + "bb" * 32,
        action_type=CorporateActionType.FORWARD_SPLIT,
        status=status,
        process_date=date(2026, 9, 20),
        token_symbol="TEST",
        chain=Chain.ROBINHOOD,
        contract_address=_CONTRACT,
        detail_kind="forwardSplit",
        details=(("newRate", "2"), ("oldRate", "1")),
        source_provider="robinhood-stock-token-api",
    )
