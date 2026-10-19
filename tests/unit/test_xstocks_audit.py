"""Test xStocks issuer history ingestion, storage, browsing, and exports.

Fixtures validate the two pagination conventions and preserve decimal precision before float parsing.
Tests cover revision retention, network-failure rollback, offline restart, deployment isolation, and
the audit browser's source details without contacting public services.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from oracle41_open.core.models import Chain, ProviderResponseError
from oracle41_open.core.services.xstocks_audit_service import XStocksAuditService
from oracle41_open.exports.rwa_audit_export import (
    RWAAuditReport,
    rwa_audit_csv_text,
    rwa_audit_json_bytes,
)
from oracle41_open.gui.views.rwa_audit_browser import RWAAuditBrowser
from oracle41_open.providers.http_client import HTTPRequest, HTTPResponse
from oracle41_open.providers.xstocks import XStocksAssetProvider
from oracle41_open.storage.db import SQLiteDatabase
from oracle41_open.storage.db.xstocks_audit_repository import XStocksAuditRepository

_FIXTURES = Path(__file__).parents[1] / "fixtures" / "providers" / "xstocks"
_ADDRESS = "0x" + "11" * 20


class _HTTPClient:
    def __init__(self, responses: list[HTTPResponse]) -> None:
        self.responses = responses
        self.requests: list[HTTPRequest] = []

    def send(self, request: HTTPRequest) -> HTTPResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def _fixture(name: str) -> HTTPResponse:
    return HTTPResponse(200, (_FIXTURES / name).read_bytes(), {})


def _responses() -> list[HTTPResponse]:
    return [
        _fixture(name)
        for name in (
            "assets.json",
            "actions_page1.json",
            "actions_page2.json",
            "multipliers.json",
        )
    ]


def _provider(client: _HTTPClient) -> XStocksAssetProvider:
    return XStocksAssetProvider(http_client=client, retry_attempts=1)  # type: ignore[arg-type]


def test_issuer_history_preserves_revisions_precision_and_page_conventions() -> None:
    client = _HTTPClient(_responses())
    history = _provider(client).get_audit_history(Chain.ARBITRUM, _ADDRESS)
    assert history is not None
    assert [item.status for item in history.corporate_actions] == ["Corrected", "Initial"]
    assert history.multiplier_history[0].multiplier == Decimal("1.002100000000000001")
    assert history.multiplier_history[0].effective_at == datetime(2026, 10, 1, 0, 30, tzinfo=UTC)
    queries = [parse_qs(urlparse(request.url).query) for request in client.requests]
    assert [query["page"] for query in queries] == [["0"], ["1"], ["2"], ["0"]]
    assert queries[-1]["network"] == ["Arbitrum"]
    assert queries[1]["symbol"] == ["AAPLx"]


def test_unknown_deployment_never_requests_asset_history() -> None:
    client = _HTTPClient([_fixture("assets.json")])
    assert _provider(client).get_audit_history(Chain.BASE, _ADDRESS) is None
    assert len(client.requests) == 1


def test_page_budget_stages_progress_without_publishing_partial_evidence(tmp_path: Path) -> None:
    client = _HTTPClient([_fixture("assets.json"), _fixture("actions_page1.json")])
    provider = XStocksAssetProvider(http_client=client, max_pages=1)  # type: ignore[arg-type]
    repository = XStocksAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    result = XStocksAuditService(provider, repository, page_budget=1).refresh(
        Chain.ARBITRUM, _ADDRESS
    )
    assert result is not None and result.sync_progress is not None
    assert result.sync_progress.actions_next_page == 2
    assert result.sync_progress.state == "paused"
    assert repository.load(Chain.ARBITRUM, _ADDRESS) is None


def test_cancelled_revision_keeps_original_issuer_status() -> None:
    responses = _responses()
    payload = json.loads(responses[2].data)
    payload["nodes"][0]["status"] = "Cancelled"
    payload["nodes"][0]["version"] = 2.0
    responses[2] = HTTPResponse(200, json.dumps(payload).encode(), {})
    history = _provider(_HTTPClient(responses)).get_audit_history(Chain.ARBITRUM, _ADDRESS)
    assert history is not None
    assert history.corporate_actions[0].status == "Cancelled"
    assert history.corporate_actions[0].version == 2


@pytest.mark.parametrize(
    "case", ["wrong_symbol", "wrong_page", "missing_flag", "invalid_multiplier"]
)
def test_invalid_history_is_rejected(case: str) -> None:
    responses = _responses()
    index = 3 if case == "invalid_multiplier" else 1
    payload = json.loads(responses[index].data)
    if case == "wrong_symbol":
        payload["nodes"][0]["xstockSymbol"] = "FAKEx"
    elif case == "wrong_page":
        payload["page"]["currentPage"] = 99
    elif case == "missing_flag":
        del payload["page"]["hasNextPage"]
    else:
        payload["nodes"][0]["multiplier"] = "NaN"
    responses[index] = HTTPResponse(200, json.dumps(payload).encode(), {})
    with pytest.raises(ProviderResponseError):
        _provider(_HTTPClient(responses)).get_audit_history(Chain.ARBITRUM, _ADDRESS)


def test_refresh_failure_keeps_saved_history_and_export_is_source_aware(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    repository = XStocksAuditRepository(SQLiteDatabase(path))
    service = XStocksAuditService(_provider(_HTTPClient(_responses())), repository)
    history = service.refresh(Chain.ARBITRUM, _ADDRESS)
    assert history is not None
    service = XStocksAuditService(
        _provider(_HTTPClient([_fixture("assets.json"), HTTPResponse(500, b"{}", {})])),
        XStocksAuditRepository(SQLiteDatabase(path)),
    )
    with pytest.raises(ProviderResponseError):
        service.refresh(Chain.ARBITRUM, _ADDRESS)
    retained = service.load(Chain.ARBITRUM, _ADDRESS)
    assert retained is not None and retained.sync_progress is not None
    assert retained.sync_progress.state == "failed"
    assert replace(retained, sync_progress=None) == history
    assert service.load(Chain.BASE, _ADDRESS) is None
    report = RWAAuditReport(Chain.ARBITRUM, _ADDRESS, history, datetime.now(tz=UTC))
    payload = json.loads(rwa_audit_json_bytes(report))
    rows = list(csv.DictReader(StringIO(rwa_audit_csv_text(report))))
    assert payload["format_version"] == 3
    assert "xstocks" in payload["audit_source_reference"]
    assert len(rows) == 3
    assert payload["items"][-1]["current_multiplier"] == "1.002100000000000001"
    assert rows[-1]["previous_multiplier"] == "1.000000000000000001"


def test_conflicting_revision_rolls_back_refresh_and_keeps_previous_records(tmp_path: Path) -> None:
    repository = XStocksAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    history = _provider(_HTTPClient(_responses())).get_audit_history(Chain.ARBITRUM, _ADDRESS)
    assert history is not None
    repository.save(history)
    repository.save(history)
    conflict = replace(history.corporate_actions[0], status="Cancelled")
    with pytest.raises(ValueError, match="Conflicting"):
        repository.save(replace(history, corporate_actions=(conflict,)))
    assert repository.load(Chain.ARBITRUM, _ADDRESS) == history


def test_xstocks_history_browser_works_offline_after_restart(
    tmp_path: Path,
    qt_application: QApplication,
) -> None:
    repository = XStocksAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    service = XStocksAuditService(_provider(_HTTPClient(_responses())), repository)
    service.refresh(Chain.ARBITRUM, _ADDRESS)
    browser = RWAAuditBrowser(
        SimpleNamespace(
            uses_live_providers=False,
            xstocks_audit_service=service,
        )
    )  # type: ignore[arg-type]
    browser.set_scope(Chain.ARBITRUM, _ADDRESS)
    browser.load_saved()
    loop = QEventLoop()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    browser._runner.result.connect(lambda _: loop.quit())
    timer.start(3000)
    loop.exec()
    assert not browser._busy
    assert browser._actions.count() == 2
    browser._actions.setCurrentRow(0)
    assert "Corrected" in browser._details.toPlainText()
    browser._multipliers.setCurrentRow(0)
    assert "1.002100000000000001" in browser._details.toPlainText()
    assert browser._json_button.isEnabled()
    browser.close()


def test_schema_v12_migrates_to_xstocks_history(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta VALUES ('schema_version', '12');
        """)
    database = SQLiteDatabase(path)
    with database.connection() as conn:
        assert conn.execute("SELECT value FROM schema_meta").fetchone()[0] == "14"
        assert conn.execute("SELECT COUNT(*) FROM xstocks_corporate_actions").fetchone()[0] == 0
