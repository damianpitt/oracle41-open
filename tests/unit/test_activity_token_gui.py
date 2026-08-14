"""Test the main Activity, Token Detail, and ABI management workflows.

The cases drive offscreen Qt widgets through loading, filtering, pagination, transaction inspection, and ABI changes.
They also protect cancellation from showing late results.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event

import pytest
from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QFileDialog

from oracle41_open.app.bootstrap import AppContainer, build_container
from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    InternalCall,
    ProviderCapabilities,
    RawTransactionLog,
    TraceDialect,
    TraceStatus,
    TransactionInspection,
    TransactionTrace,
)
from oracle41_open.core.services import AddressResolution
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
from oracle41_open.core.services.action_normalizer import WalletActionNormalizer
from oracle41_open.core.services.activity_service import ActivityPageResult
from oracle41_open.core.services.transaction_inspection_service import TransactionInspectionResult
from oracle41_open.gui.views.activity_view import ActivityView
from oracle41_open.gui.views.settings_view import SettingsView
from oracle41_open.gui.views.token_detail_view import TokenDetailView
from oracle41_open.storage.secrets import SecretStore

_ADDRESS = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_TOKEN = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"


def test_activity_gui_loads_ens_paginates_and_filters(
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    container = _container(monkeypatch, tmp_path)
    container.label_resolution_service = _FakeLabelService()  # type: ignore[assignment]
    view = ActivityView(container)
    view._address_input.setText("alice.eth")

    view._load_button.click()
    _wait_until_idle(view, qt_application)

    assert len(view._raw_items) == 2
    assert view._active_wallet_address == _ADDRESS
    assert view._labels_by_address[_ADDRESS] == "alice.eth"
    assert view._next_button.isEnabled()

    inbound_index = view._direction_combo.findData("inbound")
    view._direction_combo.setCurrentIndex(inbound_index)
    view._apply_local_filters_button.click()
    assert [item.category for item in view._visible_items] == [ActivityCategory.EXTERNAL]

    view._next_button.click()
    _wait_until_idle(view, qt_application)
    assert len(view._raw_items) == 3
    assert view._next_cursor is None
    view.close()


def test_token_detail_gui_loads_approval_history_and_filters(
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    container = _container(monkeypatch, tmp_path)
    container.label_resolution_service = _FakeLabelService()  # type: ignore[assignment]
    view = TokenDetailView(container)
    view._address_input.setText("alice.eth")
    view._token_address_input.setText(_TOKEN)

    view._load_button.click()
    _wait_until_idle(view, qt_application)

    assert len(view._raw_items) == 2
    assert view._active_wallet_address == _ADDRESS
    approval_index = view._category_combo.findData(ActivityCategory.APPROVAL.value)
    view._category_combo.setCurrentIndex(approval_index)
    view._apply_local_filters_button.click()
    assert [item.category for item in view._visible_items] == [ActivityCategory.APPROVAL]
    assert view._visible_items[0].value_usd is None

    view._next_button.click()
    _wait_until_idle(view, qt_application)
    assert len(view._raw_items) == 3
    assert view._next_cursor is None
    view.close()


def test_activity_gui_cancellation_discards_late_result(
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    container = _container(monkeypatch, tmp_path)
    container.label_resolution_service = _FakeLabelService()  # type: ignore[assignment]
    started = Event()
    release = Event()
    container.activity_service = _SlowActivityService(started, release)  # type: ignore[assignment]
    view = ActivityView(container)
    view._address_input.setText(_ADDRESS)

    view._load_button.click()
    _wait_for_event(started, qt_application)
    assert view._cancel_button.isEnabled()
    view._cancel_button.click()
    release.set()
    _wait_until_idle(view, qt_application)

    assert view._raw_items == []
    assert "Cancellation requested" in view._status_label.text()
    view.close()


def test_activity_gui_inspects_selected_transaction(
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    container = _container(monkeypatch, tmp_path)
    container.label_resolution_service = _FakeLabelService()  # type: ignore[assignment]
    container.transaction_inspection_service = _FakeTransactionInspectionService()  # type: ignore[assignment]
    view = ActivityView(container)
    view._address_input.setText(_ADDRESS)

    view._load_button.click()
    _wait_until_idle(view, qt_application)
    assert view._inspect_transaction_button.isEnabled()

    view._inspect_transaction_button.click()
    _wait_until_transaction_idle(view)

    detail = view._detail_drawer.toPlainText()
    assert "Transaction Inspector" in detail
    assert "Status: success" in detail
    assert "Network Fee: 0.000042 ETH" in detail
    assert "Method Selector: 0xa9059cbb" in detail
    assert "Decoded Call" in detail
    assert "Decode Status: malformed" in detail
    assert "Signature: transfer(address,uint256)" in detail
    assert "Raw Transaction Data" in detail
    assert "Raw Logs: 1" in detail
    assert "Internal Execution" in detail
    assert "Completeness: complete" in detail
    assert "CALL" in detail
    assert "Normalized Actions" in detail
    assert "unknown" in detail
    assert view._export_actions_csv_button.isEnabled()
    assert view._export_actions_json_button.isEnabled()
    assert "action=Unknown transaction action" in view._items_list.currentItem().text()
    assert not view._trace_tree.isHidden()
    assert view._trace_tree.topLevelItemCount() == 1
    assert view._trace_tree.topLevelItem(0).text(0) == "CALL"
    view.close()


def test_settings_gui_imports_and_removes_contract_abi(
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _ = qt_application
    container = _container(monkeypatch, tmp_path)
    abi_path = tmp_path / "vault.json"
    abi_path.write_text(
        '[{"type":"error","name":"Denied","inputs":[{"name":"caller","type":"address"}]}]',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        QFileDialog,
        "getOpenFileName",
        lambda *args, **kwargs: (str(abi_path), "JSON Files (*.json)"),
    )
    view = SettingsView(container)
    contract_address = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    view._abi_address_input.setText(contract_address)
    view._abi_name_input.setText("Vault")

    view._import_abi_button.click()

    record = container.contract_abi_repository.get_contract_abi(
        Chain.ETHEREUM, contract_address
    )
    assert record is not None
    assert record.contract_name == "Vault"
    assert not record.provenance.is_verified
    assert view._contract_abi_list.count() == 1
    assert "Imported unverified ABI" in view._status.text()

    view._contract_abi_list.setCurrentRow(0)
    view._remove_abi_button.click()

    assert container.contract_abi_repository.get_contract_abi(
        Chain.ETHEREUM, contract_address
    ) is None
    assert view._contract_abi_list.count() == 0
    view.close()


def _container(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AppContainer:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ORACLE41_ALCHEMY_API_KEY", raising=False)
    monkeypatch.delenv("ORACLE41_ANKR_API_KEY", raising=False)
    monkeypatch.setattr(SecretStore, "get_secret", lambda self, key: None)
    return build_container()


def _wait_until_idle(
    view: ActivityView | TokenDetailView,
    application: QApplication,
) -> None:
    event_loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(event_loop.quit)

    def poll() -> None:
        if not view._is_loading:
            event_loop.quit()
            return
        QTimer.singleShot(10, poll)

    QTimer.singleShot(0, poll)
    timeout.start(3_000)
    event_loop.exec()
    assert not view._is_loading, "GUI operation did not finish before timeout."


def _wait_for_event(event: Event, application: QApplication) -> None:
    _ = application
    event_loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(event_loop.quit)

    def poll() -> None:
        if event.is_set():
            event_loop.quit()
            return
        QTimer.singleShot(10, poll)

    QTimer.singleShot(0, poll)
    timeout.start(3_000)
    event_loop.exec()
    assert event.is_set(), "Background operation did not start before timeout."


def _wait_until_transaction_idle(view: ActivityView) -> None:
    event_loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(event_loop.quit)

    def poll() -> None:
        if not view._is_inspecting_transaction:
            event_loop.quit()
            return
        QTimer.singleShot(10, poll)

    QTimer.singleShot(0, poll)
    timeout.start(3_000)
    event_loop.exec()
    assert not view._is_inspecting_transaction, "Transaction inspection did not finish."


class _FakeLabelService:
    def resolve_input(self, value: str, chain: Chain) -> AddressResolution:
        _ = chain
        input_name = value.strip().lower() if value.strip().lower().endswith(".eth") else None
        return AddressResolution(address=_ADDRESS, input_name=input_name)

    def resolve_labels(self, addresses: list[str], chain: Chain) -> dict[str, str]:
        _ = addresses
        _ = chain
        return {}


class _SlowActivityService:
    def __init__(self, started: Event, release: Event) -> None:
        self._started = started
        self._release = release

    def load_activity(self, **kwargs: object) -> ActivityPageResult:
        _ = kwargs
        self._started.set()
        self._release.wait(timeout=3)
        return ActivityPageResult(
            page=ActivityPage(items=[_activity_item()], next_cursor=None),
            updated_at=datetime(2026, 8, 9, tzinfo=UTC),
            is_cached=False,
        )


class _FakeTransactionInspectionService:
    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        _ = chain
        return ProviderCapabilities(transaction_lookup=True, receipts=True)

    def inspect(
        self,
        tx_hash: str,
        chain: Chain,
        force_refresh: bool = False,
    ) -> TransactionInspectionResult:
        _ = force_refresh
        inspection = TransactionInspection(
            chain=chain,
            tx_hash=tx_hash,
            block_number=1,
            block_hash="0x" + "cd" * 32,
            transaction_index=0,
            from_address=_ADDRESS,
            to_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            contract_address=None,
            nonce=1,
            value_wei=0,
            input_data="0xa9059cbb",
            gas_limit=21_000,
            gas_price=2_000_000_000,
            max_fee_per_gas=None,
            max_priority_fee_per_gas=None,
            status=True,
            gas_used=21_000,
            cumulative_gas_used=21_000,
            effective_gas_price=2_000_000_000,
            transaction_type=2,
            logs_bloom="0x" + "00" * 256,
            logs=(
                RawTransactionLog(
                    log_index=0,
                    address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    topics=(),
                    data="0x",
                    removed=False,
                ),
            ),
            source_provider="test-rpc",
            fetched_at=datetime(2026, 8, 10, tzinfo=UTC),
        )
        decoding = StandardABIDecoder().decode(inspection)
        trace = TransactionTrace(
            chain=chain,
            tx_hash=tx_hash,
            status=TraceStatus.COMPLETE,
            calls=(
                InternalCall(
                    trace_address=(),
                    depth=0,
                    call_type="CALL",
                    from_address=_ADDRESS,
                    to_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                    created_contract=None,
                    value_wei=0,
                    gas_limit=21_000,
                    gas_used=20_000,
                    input_data="0xa9059cbb",
                    output_data="0x",
                ),
            ),
            raw_json='{"type":"CALL"}',
            source_provider="test-rpc",
            fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
            dialect=TraceDialect.DEBUG_CALL_TRACER,
        )
        return TransactionInspectionResult(
            inspection=inspection,
            decoding=decoding,
            is_cached=False,
            trace=trace,
            actions=WalletActionNormalizer().normalize(inspection, decoding, trace),
        )


def _activity_item() -> ActivityItem:
    return ActivityItem(
        block_number=1,
        tx_hash="0xlate",
        log_index="0x0",
        timestamp=datetime(2026, 8, 9, tzinfo=UTC),
        from_address=_ADDRESS,
        to_address="0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        asset_symbol="ETH",
        contract_address=None,
        raw_value="1",
        value_decimal=Decimal("1"),
        value_usd=None,
        is_verified=True,
        category=ActivityCategory.EXTERNAL,
        chain=Chain.ETHEREUM,
    )
