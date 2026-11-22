"""Exercise historical holdings inputs without real credentials or live requests.

Fixtures cover exact-block balance anchors, transfer directions, duplicate/conflicting logs,
coverage gaps, native fees, pinned price revisions, storage failures, and offline reports.
No case treats an endpoint match as proof of complete event or accounting history.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import StringIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from eth_abi.abi import encode
from PySide6.QtCore import QEventLoop, QTimer

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    CompletenessState,
    ContractReadResult,
    DataProvenance,
    FeeRecord,
    ProviderResponseError,
    ProviderTimeoutError,
    ValidationError,
)
from oracle41_open.core.models.historical_holdings import HistoricalHoldingsRequest
from oracle41_open.core.models.historical_price import (
    HistoricalPriceBatch,
    HistoricalPricePoint,
    HistoricalPriceQuery,
)
from oracle41_open.core.services.historical_holdings_service import (
    HistoricalHoldingsService,
    analyze_holdings_inputs,
    token_units,
)
from oracle41_open.core.services.historical_price_service import HistoricalPriceService
from oracle41_open.core.services.protocol_collection import call_data
from oracle41_open.exports.historical_holdings_export import (
    historical_holdings_csv_text,
    historical_holdings_json_bytes,
)
from oracle41_open.gui.views.historical_holdings_view import HistoricalHoldingsView
from oracle41_open.storage.db import EventLedgerRepository, SQLiteDatabase
from oracle41_open.storage.db.historical_holdings_repository import HistoricalHoldingsRepository
from oracle41_open.storage.db.historical_price_repository import HistoricalPriceRepository

_WALLET = "0x" + "11" * 20
_TOKEN = "0x" + "22" * 20
_OTHER = "0x" + "33" * 20
_NOW = datetime(2026, 11, 22, tzinfo=UTC)
_TIME = datetime(2026, 11, 1, tzinfo=UTC)
_REQUEST = HistoricalHoldingsRequest(_WALLET, Chain.ETHEREUM, _TOKEN, 100, 110, _TIME)


class _Reader:
    def __init__(self, opening: int = 10000, closing: int = 13000) -> None:
        self.balances = {100: opening, 110: closing}
        self.calls: list = []
        self.error: str | None = None
        self.wrong: str | None = None
        self.decimals = {100: 2, 110: 2}

    def read_contract(self, contract_address, data, chain, block_number):
        self.calls.append((contract_address, data, chain, block_number))
        if self.error:
            raise ProviderTimeoutError("Synthetic unavailable historical read")
        value = (
            self.balances[block_number]
            if data.startswith(call_data("balanceOf(address)")[:10])
            else self.decimals[block_number]
        )
        kind = "uint256" if data.startswith(call_data("balanceOf(address)")[:10]) else "uint8"
        result = ContractReadResult(
            chain,
            contract_address,
            block_number,
            "0x" + encode((kind,), (value,)).hex(),
            "fixture-rpc",
            _NOW,
        )
        if self.wrong and block_number == 110:
            changes = {
                "provider": {"source_provider": "other"},
                "block": {"block_number": 111},
                "chain": {"chain": Chain.BASE},
                "contract": {"contract_address": _OTHER},
                "data": {"data": "0x12"},
            }
            result = replace(result, **changes[self.wrong])
        return result


def _event(
    index: int,
    raw: str,
    *,
    sender: str = _OTHER,
    recipient: str = _WALLET,
    block: int | None = 105,
    log: str = "0x0",
    category=ActivityCategory.ERC20,
    token=_TOKEN,
) -> ActivityItem:
    return ActivityItem(
        block,
        "0x" + f"{index:064x}",
        log,
        _TIME,
        sender,
        recipient,
        "TEST",
        token,
        raw,
        Decimal(int(raw, 16) if raw.startswith("0x") else raw).scaleb(-2),
        None,
        True,
        category,
        Chain.ETHEREUM,
    )


def _setup(tmp_path: Path, events: list | None = None, reader: _Reader | None = None):
    database = SQLiteDatabase(tmp_path / "state.sqlite3")
    ledger = EventLedgerRepository(database)
    if events is None:
        events = [_event(1, "5000"), _event(2, "2000", sender=_WALLET, recipient=_OTHER, block=106)]
    ledger.persist_page(
        _WALLET,
        Chain.ETHEREUM,
        "activity:from=100",
        ActivityPage(events, None),
        DataProvenance("fixture-indexer", _NOW, query_from_block=100, query_to_block=110),
        CompletenessState.COMPLETE,
    )
    prices = HistoricalPriceRepository(database)
    query = HistoricalPriceQuery(Chain.ETHEREUM, _TOKEN, _TIME, _TIME + timedelta(days=1))
    prices.save(
        HistoricalPriceBatch(
            "original-price",
            query,
            "alchemy",
            "https://example.com/public-price-reference",
            _NOW,
            (HistoricalPricePoint(_TIME, Decimal("2.5")),),
        )
    )
    repository = HistoricalHoldingsRepository(database)
    service = HistoricalHoldingsService(
        reader or _Reader(), repository, HistoricalPriceService(prices), now_func=lambda: _NOW
    )
    return service, repository, database, ledger, prices


def test_endpoints_replay_and_price_inputs_are_exact_and_reproducible(tmp_path: Path) -> None:
    reader = _Reader()
    service, repository, _, _, _ = _setup(tmp_path, reader=reader)
    report = service.collect(_REQUEST)
    payload = report.document
    result = payload["result"]
    assert result["projected_closing_raw_balance"] == "13000"
    assert result["observed_closing_amount"] == "130.00"
    assert result["reconciliation_status"] == "matches_saved_transfers"
    assert result["closing_balance_estimate_usd"] == "325.000"
    assert result["is_complete_history"] is False
    assert result["known_native_fee_total_raw"] is None
    assert payload["inputs"]["price"]["fetch_id"] == "original-price"
    assert analyze_holdings_inputs(payload["inputs"]) == result
    assert repository.get(report.report_id) == report
    assert {call[3] for call in reader.calls} == {100, 110} and len(reader.calls) == 4
    assert "block_reference" in payload["inputs"]["opening"]


def test_opening_block_is_excluded_closing_block_included(tmp_path: Path) -> None:
    events = [
        _event(1, "9000", block=100),
        _event(2, "3000", block=110),
        _event(3, "1000", block=111),
    ]
    service, _, _, _, _ = _setup(tmp_path, events)
    result = service.collect(_REQUEST).document["result"]
    assert result["projected_closing_raw_balance"] == "13000"
    assert len(result["applied_movements"]) == 1


def test_multiple_scopes_and_hex_decimal_log_aliases_do_not_double_count(tmp_path: Path) -> None:
    events = [_event(1, "3000"), _event(1, "0xbb8", log="0")]
    service, _, _, ledger, _ = _setup(tmp_path, events)
    ledger.persist_page(
        _WALLET,
        Chain.ETHEREUM,
        "token:detail",
        ActivityPage(events, None),
        DataProvenance("fixture-indexer", _NOW),
        CompletenessState.COMPLETE,
    )
    result = service.collect(_REQUEST).document["result"]
    assert result["duplicate_log_rows"] == 1
    assert result["projected_closing_raw_balance"] == "13000"
    assert len(result["applied_movements"]) == 1


def test_self_transfer_approval_and_nft_are_not_deposits(tmp_path: Path) -> None:
    events = [
        _event(1, "3000"),
        _event(2, "9000", sender=_WALLET),
        _event(3, "99000", category=ActivityCategory.APPROVAL),
        _event(4, "88000", category=ActivityCategory.ERC721),
    ]
    service, _, _, _, _ = _setup(tmp_path, events)
    result = service.collect(_REQUEST).document["result"]
    assert result["projected_closing_raw_balance"] == "13000"
    assert result["ignored_non_erc20_rows"] == 2
    assert any(
        row["direction"] == "self" and row["signed_raw_delta"] == "0"
        for row in result["applied_movements"]
    )


def test_mint_and_burn_directions_use_zero_counterparty(tmp_path: Path) -> None:
    zero = "0x" + "00" * 20
    events = [_event(1, "5000", sender=zero), _event(2, "2000", sender=_WALLET, recipient=zero)]
    service, _, _, _, _ = _setup(tmp_path, events)
    assert service.collect(_REQUEST).document["result"]["projected_closing_raw_balance"] == "13000"


def test_conflicting_log_aliases_are_all_excluded_even_when_balances_match(tmp_path: Path) -> None:
    reader = _Reader(closing=10000)
    service, _, _, _, _ = _setup(tmp_path, [_event(1, "3000"), _event(1, "4000", log="0")], reader)
    result = service.collect(_REQUEST).document["result"]
    assert len(result["excluded_events"]) == 2
    assert result["applied_movements"] == []
    assert result["reconciliation_difference_raw"] == "0"
    assert result["reconciliation_status"] == "unreconciled"


def test_missing_blocks_and_receipt_failures_are_visible_exclusions(tmp_path: Path) -> None:
    events = [_event(1, "3000", block=None), _event(2, "3000")]
    service, _, database, _, _ = _setup(tmp_path, events)
    _receipt(database, events[1], False)
    result = service.collect(_REQUEST).document["result"]
    assert {row["reason"] for row in result["excluded_events"]} == {
        "missing_block",
        "failed_receipt",
    }
    assert result["projected_closing_raw_balance"] == "10000"


def _receipt(database, event, success=True):
    with database.connection() as conn:
        conn.execute(
            """INSERT INTO ledger_transaction_receipts(chain,tx_hash,block_number,block_hash,
            transaction_index,from_address,status,gas_used,cumulative_gas_used,effective_gas_price,
            logs_bloom,source_provider,fetched_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                "ethereum",
                event.tx_hash,
                event.block_number,
                "0x" + "aa" * 32,
                0,
                _WALLET,
                int(success),
                21000,
                21000,
                "1",
                "0x",
                "fixture-rpc",
                _NOW.isoformat(),
            ),
        )


def test_failed_transaction_fees_stay_native_and_are_counted_once(tmp_path: Path) -> None:
    events = [_event(1, "3000"), _event(2, "500", category=ActivityCategory.APPROVAL)]
    service, _, database, ledger, _ = _setup(tmp_path, events)
    _receipt(database, events[1], False)
    fee = FeeRecord(
        Chain.ETHEREUM, events[1].tx_hash, _WALLET, "21000", Decimal("0.000000000000021"), "ETH"
    )
    for _ in range(2):
        ledger.upsert_fee(fee, DataProvenance("fixture-rpc", _NOW))
    result = service.collect(_REQUEST).document["result"]
    assert result["projected_closing_raw_balance"] == "13000"
    assert result["known_native_fee_total_raw"] == "21000"
    assert len(result["saved_native_fees"]) == 1
    assert result["saved_native_fees"][0]["receipt_status"] == 0


def test_rebase_or_missing_activity_mismatch_is_not_hidden(tmp_path: Path) -> None:
    service, _, _, _, _ = _setup(tmp_path, [], _Reader(closing=12000))
    result = service.collect(_REQUEST).document["result"]
    assert result["projected_closing_raw_balance"] == "10000"
    assert result["reconciliation_difference_raw"] == "2000"
    assert result["reconciliation_status"] == "unreconciled"
    assert result["closing_balance_estimate_usd"] == "300.000"


def test_negative_projection_is_not_clamped_and_block_order_is_not_guessed(tmp_path: Path) -> None:
    service, _, _, _, _ = _setup(
        tmp_path, [_event(1, "15000", sender=_WALLET, recipient=_OTHER)], _Reader(closing=0)
    )
    result = service.collect(_REQUEST).document["result"]
    assert result["projected_closing_raw_balance"] == "-5000"
    assert result["negative_block_end_balances"] == [105]
    assert result["reconciliation_status"] == "unreconciled"
    # Transfers in one block have only a block-end check, not a fabricated tx-hash order.
    service, _, _, ledger, _ = _setup(
        tmp_path / "same-block",
        [_event(1, "15000", sender=_WALLET, recipient=_OTHER), _event(2, "15000")],
        _Reader(0, 0),
    )
    assert service.collect(_REQUEST).document["result"]["negative_block_end_balances"] == []


@pytest.mark.parametrize("case", ["missing", "stale", "future", "zero"])
def test_price_gaps_are_not_replaced_with_current_or_later_quotes(
    tmp_path: Path, case: str
) -> None:
    service, _, database, _, prices = _setup(tmp_path)
    with database.connection() as conn:
        conn.execute("DELETE FROM historical_price_points")
        conn.execute("DELETE FROM historical_price_fetches")
    if case != "missing":
        timestamp = (
            _TIME + timedelta(hours=1)
            if case == "future"
            else _TIME - timedelta(days=2)
            if case == "stale"
            else _TIME
        )
        query = HistoricalPriceQuery(
            Chain.ETHEREUM, _TOKEN, timestamp, timestamp + timedelta(days=1)
        )
        prices.save(
            HistoricalPriceBatch(
                "test-price",
                query,
                "alchemy",
                "https://example.com/source",
                _NOW,
                (
                    HistoricalPricePoint(
                        timestamp, Decimal("0") if case == "zero" else Decimal("2")
                    ),
                ),
            )
        )
    result = service.collect(_REQUEST).document["result"]
    assert result["closing_balance_estimate_usd"] == ("0.00" if case == "zero" else None)
    assert result["price_status"] == (
        "exact" if case == "zero" else "stale" if case == "stale" else "missing"
    )


def test_frozen_report_survives_ledger_and_price_revisions(tmp_path: Path) -> None:
    service, repository, _, ledger, prices = _setup(tmp_path)
    report = service.collect(_REQUEST)
    ledger.persist_page(
        _WALLET,
        Chain.ETHEREUM,
        "activity:from=100",
        ActivityPage([_event(3, "9999")], None),
        DataProvenance("fixture-indexer", _NOW),
        CompletenessState.COMPLETE,
    )
    prices.save(
        HistoricalPriceBatch(
            "new-price",
            HistoricalPriceQuery(Chain.ETHEREUM, _TOKEN, _TIME, _TIME + timedelta(days=1)),
            "alchemy",
            "https://example.com/source",
            _NOW,
            (HistoricalPricePoint(_TIME, Decimal("999")),),
        )
    )
    reopened = HistoricalHoldingsRepository(SQLiteDatabase(tmp_path / "state.sqlite3")).get(
        report.report_id
    )
    assert reopened == report
    assert analyze_holdings_inputs(reopened.document["inputs"]) == report.document["result"]
    mutated = reopened.document
    mutated["result"]["closing_balance_estimate_usd"] = "bad"
    assert reopened.document["result"]["closing_balance_estimate_usd"] == "325.000"


@pytest.mark.parametrize("wrong", ["provider", "block", "chain", "contract", "data"])
def test_wrong_contract_context_or_malformed_abi_never_publishes(
    tmp_path: Path, wrong: str
) -> None:
    reader = _Reader()
    reader.wrong = wrong
    service, repository, _, _, _ = _setup(tmp_path, reader=reader)
    with pytest.raises(ProviderResponseError):
        service.collect(_REQUEST)
    assert repository.list_reports(_WALLET, Chain.ETHEREUM, _TOKEN) == ()


def test_failed_rpc_and_changed_decimals_leave_saved_reports_untouched(tmp_path: Path) -> None:
    reader = _Reader()
    service, repository, _, _, _ = _setup(tmp_path, reader=reader)
    report = service.collect(_REQUEST)
    reader.error = "timeout"
    with pytest.raises(ProviderTimeoutError):
        service.collect(_REQUEST)
    reader.error = None
    reader.decimals[110] = 18
    with pytest.raises(ProviderResponseError, match="decimals changed"):
        service.collect(_REQUEST)
    assert repository.get(report.report_id) == report


def test_invalid_request_and_future_time_are_rejected(tmp_path: Path) -> None:
    for kwargs in (
        {"opening_block": -1},
        {"closing_block": 99},
        {"opening_block": True},
        {"closing_block": 2**63},
        {"price_at": _TIME.replace(tzinfo=None)},
        {"wallet_address": "bad"},
        {"max_price_age_seconds": -1},
    ):
        with pytest.raises(ValidationError):
            replace(_REQUEST, **kwargs)
    reader = _Reader()
    service, _, _, _, _ = _setup(tmp_path, reader=reader)
    with pytest.raises(ValidationError):
        service.collect(replace(_REQUEST, price_at=_NOW + timedelta(days=1)))
    assert reader.calls == []


def test_uint256_units_and_usd_math_do_not_round_at_default_decimal_precision(
    tmp_path: Path,
) -> None:
    amount = 10**60 + 123
    reader = _Reader(amount, amount)
    service, _, _, _, _ = _setup(tmp_path, [], reader)
    report = service.collect(_REQUEST)
    assert report.document["result"]["observed_closing_amount"] == token_units(amount, 2)
    assert (
        report.document["result"]["closing_balance_estimate_usd"]
        == str(amount * 25)[:-3] + "." + str(amount * 25)[-3:]
    )


def test_report_insert_failure_is_atomic(tmp_path: Path) -> None:
    service, repository, database, _, _ = _setup(tmp_path)
    with database.connection() as conn:
        conn.execute(
            "CREATE TRIGGER reject_report BEFORE INSERT ON historical_holdings_reports BEGIN SELECT RAISE(ABORT, 'test'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        service.collect(_REQUEST)
    assert repository.list_reports(_WALLET, Chain.ETHEREUM, _TOKEN) == ()


def test_exports_preserve_inputs_exclusions_and_prices(tmp_path: Path) -> None:
    service, _, _, _, _ = _setup(tmp_path)
    report = service.collect(_REQUEST)
    assert json.loads(historical_holdings_json_bytes(report)) == report.document
    rows = list(csv.DictReader(StringIO(historical_holdings_csv_text(report))))
    assert {row["record_type"] for row in rows} >= {
        "summary",
        "opening_balance",
        "closing_balance",
        "price_input",
        "ledger_event",
        "applied_movement",
        "checkpoint_context",
    }
    assert all(row["report_id"] == report.report_id for row in rows)
    price = next(
        json.loads(row["details_json"]) for row in rows if row["record_type"] == "price_input"
    )
    assert price["fetch_id"] == "original-price"


def test_receipt_block_mismatch_and_decimal_metadata_disagreement_are_flagged(
    tmp_path: Path,
) -> None:
    events = [_event(1, "3000")]
    service, _, database, _, _ = _setup(tmp_path, events)
    _receipt(database, events[0])
    with database.connection() as conn:
        conn.execute("UPDATE ledger_transaction_receipts SET block_number=109")
    result = service.collect(_REQUEST).document["result"]
    assert result["excluded_events"][0]["reason"] == "receipt_block_mismatch"
    with database.connection() as conn:
        conn.execute("UPDATE ledger_transaction_receipts SET block_number=105")
        conn.execute("UPDATE ledger_events SET value_decimal='999999'")
    result = service.collect(_REQUEST).document["result"]
    assert result["projected_closing_raw_balance"] == "13000"
    assert result["decimal_metadata_mismatches"] == 1
    assert result["reconciliation_status"] == "unreconciled"


def test_empty_or_partial_ledger_never_proves_complete_history(tmp_path: Path) -> None:
    service, _, database, _, _ = _setup(tmp_path, [], _Reader(700, 700))
    with database.connection() as conn:
        conn.execute("UPDATE sync_checkpoints SET completeness='capped', next_cursor='remaining'")
    result = service.collect(_REQUEST).document["result"]
    assert result["opening_raw_balance"] == "700"
    assert result["reconciliation_status"] == "matches_saved_transfers"
    assert result["is_complete_history"] is False
    assert any("unfinished" in warning for warning in result["warnings"])


def test_report_id_cannot_be_overwritten_and_capture_limit_is_not_silent(
    tmp_path: Path, monkeypatch
) -> None:
    service, repository, _, _, _ = _setup(tmp_path)
    report = service.collect(_REQUEST)
    with pytest.raises(sqlite3.IntegrityError):
        repository.save(report)
    assert repository.get(report.report_id) == report
    monkeypatch.setattr("oracle41_open.storage.db.historical_holdings_repository._MAX_EVENTS", 1)
    with pytest.raises(ValidationError, match="narrower"):
        service.collect(_REQUEST)
    assert len(repository.list_reports(_WALLET, Chain.ETHEREUM, _TOKEN)) == 1


def test_v15_migration_preserves_ledger_and_price_history(tmp_path: Path) -> None:
    service, _, database, _, prices = _setup(tmp_path)
    with database.connection() as conn:
        conn.execute("DROP TABLE historical_holdings_reports")
        conn.execute("UPDATE schema_meta SET value='15'")
    migrated = SQLiteDatabase(database.file_path)
    with migrated.connection() as conn:
        assert conn.execute("SELECT value FROM schema_meta").fetchone()[0] == "16"
        assert conn.execute("SELECT COUNT(*) FROM ledger_events").fetchone()[0] == 2
    assert HistoricalPriceRepository(migrated).get("original-price") is not None
    assert (
        HistoricalHoldingsRepository(migrated).list_reports(_WALLET, Chain.ETHEREUM, _TOKEN) == ()
    )


def test_replay_rechecks_future_price_timestamps_instead_of_trusting_usable_flag(
    tmp_path: Path,
) -> None:
    service, _, _, _, _ = _setup(tmp_path)
    inputs = service.collect(_REQUEST).document["inputs"]
    inputs["price"]["price_at"] = (_TIME + timedelta(seconds=1)).isoformat()
    inputs["price"]["usable"] = True
    result = analyze_holdings_inputs(inputs)
    assert result["closing_balance_estimate_usd"] is None
    assert result["price_eligible"] is False


def test_unusable_numeric_range_does_not_invent_a_usd_value_or_drop_the_report(
    tmp_path: Path,
) -> None:
    service, _, _, _, _ = _setup(tmp_path)
    inputs = service.collect(_REQUEST).document["inputs"]
    inputs["price"]["value_usd"] = "1E+1000000"
    result = analyze_holdings_inputs(inputs)
    assert result["closing_balance_estimate_usd"] is None
    assert result["price_eligible"] is False
    assert any("numeric range" in warning for warning in result["warnings"])


def test_offline_gui_loads_frozen_report_without_block_inputs(
    qt_application, tmp_path: Path
) -> None:
    service, repository, _, _, _ = _setup(tmp_path)
    report = service.collect(_REQUEST)
    service.can_collect = False
    view = HistoricalHoldingsView(SimpleNamespace(historical_holdings_service=service))
    view._wallet.setText(_WALLET)
    view._token.setText(_TOKEN)
    assert not view._collect.isEnabled()
    view._list_saved()
    loop = QEventLoop()

    def poll():
        if view._busy:
            QTimer.singleShot(10, poll)
        else:
            loop.quit()

    QTimer.singleShot(0, poll)
    QTimer.singleShot(3000, loop.quit)
    loop.exec()
    assert view._report == report
    assert view._json.isEnabled()
    assert "matches_saved_transfers" in view._status.text()
    view._token.setText(_OTHER)
    assert view._report is None and not view._json.isEnabled()
    view.close()


def test_gui_discards_late_result_after_wallet_scope_change(qt_application, tmp_path: Path) -> None:
    entered, release = Event(), Event()

    class SlowReader(_Reader):
        def read_contract(self, *args):
            entered.set()
            assert release.wait(3)
            return super().read_contract(*args)

    service, repository, _, _, _ = _setup(tmp_path, reader=SlowReader())
    view = HistoricalHoldingsView(SimpleNamespace(historical_holdings_service=service))
    view._wallet.setText(_WALLET)
    view._token.setText(_TOKEN)
    view._opening.setText("100")
    view._closing.setText("110")
    view._price_at.setText(_TIME.isoformat())
    view._collect_report()
    assert entered.wait(1)
    view._wallet.setText(_OTHER)
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
    assert view._report is None and not view._json.isEnabled()
    assert len(repository.list_reports(_WALLET, Chain.ETHEREUM, _TOKEN)) == 1
    view.close()
