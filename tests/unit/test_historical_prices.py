"""Test historical price collection, immutable storage, policy, exports, and offline UI.

Synthetic source responses never use real credentials or live requests. Cases cover precision,
identity, timestamps, retry limits, migration, atomic rollback, source revisions, no-lookahead
selection, missing history, and scope changes while background work is running.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from io import StringIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QDate, QEventLoop, QTimer

from oracle41_open.core.models import (
    Chain,
    ProviderAuthError,
    ProviderNetworkError,
    ProviderRateLimitError,
    ProviderResponseError,
    ProviderTimeoutError,
    ValidationError,
)
from oracle41_open.core.models.historical_price import (
    HistoricalPriceBatch,
    HistoricalPricePoint,
    HistoricalPriceQuery,
)
from oracle41_open.core.services.historical_price_service import HistoricalPriceService
from oracle41_open.exports.historical_price_export import (
    historical_price_csv_text,
    historical_price_json_bytes,
)
from oracle41_open.gui.views.historical_prices_view import HistoricalPricesView
from oracle41_open.providers.alchemy_historical_prices import (
    SOURCE_REFERENCE,
    AlchemyHistoricalPriceProvider,
)
from oracle41_open.providers.http_client import (
    HTTPClientNetworkError,
    HTTPClientTimeoutError,
    HTTPResponse,
)
from oracle41_open.storage.db import SQLiteDatabase
from oracle41_open.storage.db.historical_price_repository import HistoricalPriceRepository

_ADDRESS = "0x" + "11" * 20
_START = datetime(2026, 11, 1, tzinfo=UTC)
_NOW = datetime(2026, 11, 21, tzinfo=UTC)
_QUERY = HistoricalPriceQuery(
    Chain.ETHEREUM, _ADDRESS, _START, _START + timedelta(days=2, microseconds=-1)
)


def _payload() -> dict:
    return {
        "network": "eth-mainnet",
        "address": _ADDRESS,
        "currency": "usd",
        "data": [
            {"timestamp": "2026-11-01T00:00:00Z", "value": "1.12345678901234567890123456789"},
            {"timestamp": "2026-11-02T00:00:00Z", "value": "0"},
        ],
    }


def _response(payload: object | None = None, status: int = 200) -> HTTPResponse:
    return HTTPResponse(status, json.dumps(_payload() if payload is None else payload).encode(), {})


class _HTTP:
    def __init__(self, responses: list) -> None:
        self.responses = list(responses)
        self.requests: list = []

    def send(self, request):
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def _provider(http: _HTTP, sleeps: list | None = None) -> AlchemyHistoricalPriceProvider:
    return AlchemyHistoricalPriceProvider(
        "synthetic-test-key",
        http_client=http,
        sleep_func=(sleeps.append if sleeps is not None else lambda _: None),
    )


def _repository(tmp_path: Path) -> HistoricalPriceRepository:
    return HistoricalPriceRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))


def _service(tmp_path: Path, http: _HTTP) -> HistoricalPriceService:
    return HistoricalPriceService(_repository(tmp_path), _provider(http), now_func=lambda: _NOW)


def test_address_request_and_decimal_precision_are_preserved() -> None:
    payload = _payload()
    payload["data"].reverse()
    payload["data"].append(payload["data"][0])
    http = _HTTP([_response(payload)])
    points = _provider(http).fetch(_QUERY)
    assert points[0].value_usd == Decimal("1.12345678901234567890123456789")
    assert len(points) == 2 and points[1].value_usd == 0
    request = http.requests[0]
    assert request.method == "POST"
    assert request.url.endswith("/tokens/historical")
    assert request.json["network"] == "eth-mainnet"
    assert request.json["address"] == _ADDRESS
    assert request.json["interval"] == "1d"
    assert "symbol" not in request.json


@pytest.mark.parametrize(
    "case",
    [
        "network",
        "address",
        "currency",
        "data",
        "row",
        "negative",
        "nan",
        "null",
        "bool",
        "time",
        "timezone",
        "conflict",
        "huge",
    ],
)
def test_malformed_or_wrong_identity_history_is_rejected(case: str) -> None:
    payload = _payload()
    if case in {"network", "address", "currency"}:
        payload[case] = "wrong"
    elif case == "data":
        del payload["data"]
    elif case == "row":
        payload["data"] = [None]
    elif case in {"negative", "nan", "null", "bool"}:
        payload["data"][0]["value"] = {"negative": "-1", "nan": "NaN", "null": None, "bool": True}[
            case
        ]
    elif case == "time":
        payload["data"][0]["timestamp"] = "2026-10-31T00:00:00Z"
    elif case == "timezone":
        payload["data"][0]["timestamp"] = "2026-11-01T00:00:00"
    elif case == "conflict":
        payload["data"].append({**payload["data"][0], "value": "999"})
    else:
        payload["data"] *= 17
    http = _HTTP([_response(payload)])
    with pytest.raises(ProviderResponseError):
        _provider(http).fetch(_QUERY)
    assert len(http.requests) == 1


@pytest.mark.parametrize(
    ("status", "error", "attempts"),
    [
        (401, ProviderAuthError, 1),
        (403, ProviderAuthError, 1),
        (404, ProviderResponseError, 1),
        (429, ProviderRateLimitError, 3),
        (503, ProviderNetworkError, 3),
    ],
)
def test_structured_errors_and_retry_budget_hide_transport_secrets(status, error, attempts) -> None:
    http = _HTTP([_response(status=status) for _ in range(attempts)])
    sleeps: list = []
    with pytest.raises(error) as raised:
        _provider(http, sleeps).fetch(_QUERY)
    assert "synthetic-test-key" not in str(raised.value)
    assert len(http.requests) == attempts
    assert sleeps == ([0.25, 0.5] if attempts == 3 else [])


@pytest.mark.parametrize(
    ("transport", "expected"),
    [
        (HTTPClientTimeoutError, ProviderTimeoutError),
        (HTTPClientNetworkError, ProviderNetworkError),
    ],
)
def test_transport_failure_is_safe_and_retried(transport, expected) -> None:
    http = _HTTP([transport("private-url-synthetic-test-key") for _ in range(3)])
    with pytest.raises(expected) as raised:
        _provider(http).fetch(_QUERY)
    assert "private-url" not in str(raised.value)
    assert len(http.requests) == 3


def test_transient_failure_can_recover() -> None:
    http = _HTTP([_response(status=429), _response()])
    assert len(_provider(http).fetch(_QUERY)) == 2
    assert len(http.requests) == 2


def test_numeric_json_is_parsed_without_binary_float_rounding() -> None:
    data = (
        json.dumps(_payload())
        .replace('"1.12345678901234567890123456789"', "1.12345678901234567890123456789")
        .encode()
    )
    points = _provider(_HTTP([HTTPResponse(200, data, {})])).fetch(_QUERY)
    assert str(points[0].value_usd) == "1.12345678901234567890123456789"


def test_invalid_batches_are_rejected_before_storage() -> None:
    point = HistoricalPricePoint(_START, Decimal("1"))
    batch = HistoricalPriceBatch("test-fetch", _QUERY, "alchemy", SOURCE_REFERENCE, _NOW, (point,))
    for changes in (
        {"points": (point, point)},
        {"fetched_at": _START - timedelta(seconds=1)},
        {"points": (replace(point, timestamp=_START - timedelta(seconds=1)),)},
    ):
        with pytest.raises(ValidationError):
            replace(batch, **changes)
    with pytest.raises(ValidationError):
        HistoricalPricePoint(_START, Decimal("Infinity"))
    with pytest.raises(ProviderAuthError):
        AlchemyHistoricalPriceProvider(" ")


def test_unknown_network_never_falls_back_to_a_symbol() -> None:
    http = _HTTP([])
    with pytest.raises(ValidationError):
        _provider(http).fetch(replace(_QUERY, chain=Chain.ROBINHOOD))
    assert http.requests == []


@pytest.mark.parametrize("case", ["naive", "range", "empty", "interval", "zero", "badaddress"])
def test_queries_reject_ambiguous_or_unbounded_inputs(case: str) -> None:
    changes = {
        "naive": {"start": _START.replace(tzinfo=None)},
        "range": {"end": _START + timedelta(days=31)},
        "empty": {"end": _START},
        "interval": {"interval": "5m"},
        "zero": {"contract_address": "0x" + "00" * 20},
        "badaddress": {"contract_address": "DAI"},
    }
    with pytest.raises(ValidationError):
        replace(_QUERY, **changes[case])


def test_timezones_normalize_to_one_query() -> None:
    offset = timezone(timedelta(hours=2))
    assert (
        replace(
            _QUERY,
            contract_address=_ADDRESS.upper(),
            start=_QUERY.start.astimezone(offset),
            end=_QUERY.end.astimezone(offset),
        )
        == _QUERY
    )


def test_future_and_disabled_fetches_make_no_requests(tmp_path: Path) -> None:
    http = _HTTP([])
    with pytest.raises(ValidationError):
        _service(tmp_path, http).refresh(replace(_QUERY, start=_NOW, end=_NOW + timedelta(days=1)))
    offline = HistoricalPriceService(_repository(tmp_path))
    assert not offline.can_refresh
    with pytest.raises(ProviderAuthError):
        offline.refresh(_QUERY)
    assert offline.load_saved(_QUERY) is None
    assert http.requests == []


def test_restart_round_trip_and_revisions_preserve_original_evidence(tmp_path: Path) -> None:
    first = _service(tmp_path, _HTTP([_response()])).refresh(_QUERY)
    revised = _payload()
    revised["data"][0]["value"] = "7"
    service = _service(tmp_path, _HTTP([_response(revised)]))
    second = service.refresh(_QUERY)
    assert second.fetch_id != first.fetch_id
    offline = HistoricalPriceService(_repository(tmp_path))
    assert offline.get_fetch(first.fetch_id) == first
    assert offline.load_saved(_QUERY) == second
    assert offline.load_saved(replace(_QUERY, chain=Chain.BASE)) is None
    assert offline.select(Chain.ETHEREUM, _ADDRESS, _START).usable_value_usd == 7


def test_selection_is_prior_only_with_age_and_knowledge_cutoffs(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = _service(tmp_path, _HTTP([_response()])).refresh(_QUERY)
    assert (
        repository.select(Chain.ETHEREUM, _ADDRESS, _START - timedelta(microseconds=1)).status
        == "missing"
    )
    exact = repository.select(Chain.ETHEREUM, _ADDRESS, _START)
    assert exact.status == "exact" and exact.fetch_id == first.fetch_id
    prior = repository.select(Chain.ETHEREUM, _ADDRESS, _START + timedelta(hours=12))
    assert prior.status == "prior" and prior.point.timestamp == _START
    assert prior.usable_value_usd == first.points[0].value_usd
    stale = repository.select(
        Chain.ETHEREUM, _ADDRESS, _START + timedelta(hours=12), max_age_seconds=1
    )
    assert stale.status == "stale" and stale.usable_value_usd is None
    assert stale.point is not None  # Retain evidence without authorizing its use.
    assert (
        repository.select(
            Chain.ETHEREUM, _ADDRESS, _START, known_by=_NOW - timedelta(seconds=1)
        ).status
        == "missing"
    )
    assert repository.select(Chain.BASE, _ADDRESS, _START).status == "missing"
    assert repository.select(Chain.ETHEREUM, "0x" + "22" * 20, _START).status == "missing"
    with pytest.raises(ValidationError):
        repository.select(Chain.ETHEREUM, _ADDRESS, _START, max_age_seconds=-1)


def test_known_by_selects_original_revision_and_zero_remains_usable(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    first = _service(tmp_path, _HTTP([_response()])).refresh(_QUERY)
    second = replace(
        first,
        fetch_id="revised",
        fetched_at=_NOW + timedelta(seconds=1),
        points=(replace(first.points[0], value_usd=Decimal("9")),),
    )
    repository.save(second)
    selected = repository.select(Chain.ETHEREUM, _ADDRESS, _START, known_by=_NOW)
    assert selected.fetch_id == first.fetch_id
    assert repository.select(Chain.ETHEREUM, _ADDRESS, _START).fetch_id == second.fetch_id
    zero = repository.select(Chain.ETHEREUM, _ADDRESS, _START + timedelta(days=1))
    assert zero.usable_value_usd == Decimal("0")


def test_empty_response_and_failure_never_erase_saved_history(tmp_path: Path) -> None:
    first = _service(tmp_path, _HTTP([_response()])).refresh(_QUERY)
    empty_payload = {**_payload(), "data": []}
    empty = _service(tmp_path, _HTTP([_response(empty_payload)])).refresh(_QUERY)
    assert empty.points == ()
    assert _repository(tmp_path).get(first.fetch_id) == first
    assert _repository(tmp_path).latest(_QUERY) == empty
    with pytest.raises(ProviderResponseError):
        _service(tmp_path, _HTTP([HTTPResponse(200, b"invalid", {})])).refresh(_QUERY)
    assert _repository(tmp_path).latest(_QUERY) == empty


def test_fetch_and_points_roll_back_together(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")
    with database.connection() as conn:
        conn.executescript(
            "CREATE TRIGGER refuse_price BEFORE INSERT ON historical_price_points BEGIN SELECT RAISE(ABORT, 'test'); END;"
        )
    with pytest.raises(sqlite3.IntegrityError):
        _service(tmp_path, _HTTP([_response()])).refresh(_QUERY)
    assert HistoricalPriceRepository(database).latest(_QUERY) is None


def test_v14_migration_preserves_existing_data(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    database = SQLiteDatabase(path)
    with database.connection() as conn:
        conn.execute("DROP TABLE historical_price_points")
        conn.execute("DROP TABLE historical_price_fetches")
        conn.execute("UPDATE schema_meta SET value = '14'")
        conn.execute(
            "INSERT INTO watchlist_entries(address, chain, label, created_at) VALUES (?, 'ethereum', 'Saved', ?)",
            (_ADDRESS, _NOW.isoformat()),
        )
    migrated = SQLiteDatabase(path)
    with migrated.connection() as conn:
        assert conn.execute("SELECT value FROM schema_meta").fetchone()[0] == "15"
        assert conn.execute("SELECT label FROM watchlist_entries").fetchone()[0] == "Saved"
    assert HistoricalPriceRepository(migrated).latest(_QUERY) is None


def test_reports_preserve_precision_provenance_and_empty_status(tmp_path: Path) -> None:
    batch = _service(tmp_path, _HTTP([_response()])).refresh(_QUERY)
    payload = json.loads(historical_price_json_bytes(batch))
    assert payload["format_version"] == 1
    assert payload["fetch_id"] == batch.fetch_id
    assert payload["source_reference"] == SOURCE_REFERENCE
    assert payload["items"][0]["value_usd"] == "1.12345678901234567890123456789"
    rows = list(csv.DictReader(StringIO(historical_price_csv_text(batch))))
    assert len(rows) == 2 and rows[1]["value_usd"] == "0"
    assert "synthetic-test-key" not in historical_price_json_bytes(batch).decode()
    empty = replace(batch, points=())
    empty_rows = list(csv.DictReader(StringIO(historical_price_csv_text(empty))))
    assert empty_rows[0]["record_type"] == "fetch_status"
    assert empty_rows[0]["value_usd"] == ""


def _wait(view: HistoricalPricesView) -> None:
    loop = QEventLoop()

    def poll():
        if view._busy:
            QTimer.singleShot(10, poll)
        else:
            loop.quit()

    QTimer.singleShot(0, poll)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert not view._busy


def test_offline_view_browses_saved_fetch_and_lookup(qt_application, tmp_path: Path) -> None:
    batch = _service(tmp_path, _HTTP([_response()])).refresh(_QUERY)
    view = HistoricalPricesView(
        SimpleNamespace(historical_price_service=HistoricalPriceService(_repository(tmp_path)))
    )
    view._address.setText(_ADDRESS)
    view._start.setDate(QDate(2026, 11, 1))
    view._end.setDate(QDate(2026, 11, 2))
    assert not view._fetch.isEnabled()
    view._load(False)
    _wait(view)
    assert view._batch == batch and view._table.rowCount() == 2
    assert view._json.isEnabled()
    view._target.setText("2026-11-01T12:00:00Z")
    view._select()
    _wait(view)
    assert "Lookup: prior" in view._status.text()
    view._address.setText("0x" + "22" * 20)
    assert view._batch is None and not view._json.isEnabled()
    view.close()


def test_failed_view_refresh_keeps_displayed_fetch(qt_application, tmp_path: Path) -> None:
    service = _service(tmp_path, _HTTP([_response(), _response(status=401)]))
    view = HistoricalPricesView(SimpleNamespace(historical_price_service=service))
    view._address.setText(_ADDRESS)
    view._start.setDate(QDate(2026, 11, 1))
    view._end.setDate(QDate(2026, 11, 2))
    view._load(True)
    _wait(view)
    batch = view._batch
    view._load(True)
    _wait(view)
    assert view._batch == batch and view._table.rowCount() == 2
    assert "denied" in view._status.text()
    view._chain.setCurrentIndex(list(Chain).index(Chain.ROBINHOOD))
    assert not view._fetch.isEnabled()
    view.close()


def test_scope_switch_discards_late_ui_results_but_keeps_valid_saved_evidence(
    qt_application, tmp_path: Path
) -> None:
    started, release = Event(), Event()

    class SlowHTTP(_HTTP):
        def send(self, request):
            started.set()
            assert release.wait(2)
            return super().send(request)

    service = _service(tmp_path, SlowHTTP([_response()]))
    view = HistoricalPricesView(SimpleNamespace(historical_price_service=service))
    view._address.setText(_ADDRESS)
    view._start.setDate(QDate(2026, 11, 1))
    view._end.setDate(QDate(2026, 11, 2))
    view._load(True)
    assert started.wait(1)
    view._address.setText("0x" + "22" * 20)
    release.set()
    loop = QEventLoop()

    def poll():
        if view._runner._active_tasks:
            QTimer.singleShot(10, poll)
        else:
            loop.quit()

    QTimer.singleShot(0, poll)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert not view._runner._active_tasks
    assert view._batch is None and view._table.rowCount() == 0
    assert service.load_saved(_QUERY) is not None
    view.close()
