"""Browse and export saved corporate actions and multiplier observations.

The browser loads SQLite history independently of wallet activity. Refresh uses the public issuer
catalog in the background and keeps saved records visible when the network fails. Selecting a row
shows its full source details, and exports include published local records for that deployment.
xStocks sync can pause or resume at saved page boundaries, with pending progress shown separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
)

from oracle41_open.core.models import (
    Chain,
    ProviderError,
    RealWorldAssetIdentity,
    StockTokenAuditContext,
)
from oracle41_open.core.models.xstocks_audit import XStocksAuditContext, XStocksSyncProgress
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.exports.rwa_audit_export import RWAAuditReport, write_rwa_audit
from oracle41_open.gui.task_runner import BackgroundTaskRunner

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


@dataclass(frozen=True)
class _HistoryResult:
    history: StockTokenAuditContext | XStocksAuditContext
    note: str


class RWAAuditBrowser(QGroupBox):
    """Provide local browsing, explicit refresh, and audit exports for a selected token."""

    sync_progress = Signal(object)

    def __init__(self, container: AppContainer) -> None:
        super().__init__("RWA Audit History")
        self._container = container
        self._runner = BackgroundTaskRunner(self)
        self._runner.result.connect(self._loaded)
        self._runner.error.connect(self._failed)
        self._chain = Chain.ETHEREUM
        self._address = ""
        self._identity: RealWorldAssetIdentity | None = None
        self._history: StockTokenAuditContext | XStocksAuditContext = StockTokenAuditContext((), ())
        self._busy = False
        self._syncing = False
        self._cancel_sync = Event()
        self.sync_progress.connect(self._on_sync_progress)
        self._load_button = QPushButton("Load Saved History")
        self._refresh_button = QPushButton("Refresh Audit History")
        self._csv_button = QPushButton("Export Audit CSV")
        self._json_button = QPushButton("Export Audit JSON")
        self._restart_button = QPushButton("Start New Sync")
        self._pause_button = QPushButton("Pause Sync")
        self._load_button.clicked.connect(self.load_saved)
        self._refresh_button.clicked.connect(lambda: self._start(refresh=True))
        self._csv_button.clicked.connect(lambda: self._export(as_json=False))
        self._json_button.clicked.connect(lambda: self._export(as_json=True))
        self._restart_button.clicked.connect(lambda: self._start(refresh=True, restart=True))
        self._pause_button.clicked.connect(self._pause_sync)
        buttons = QHBoxLayout()
        for button in (
            self._load_button,
            self._refresh_button,
            self._restart_button,
            self._pause_button,
        ):
            buttons.addWidget(button)
        exports = QHBoxLayout()
        exports.addWidget(self._csv_button)
        exports.addWidget(self._json_button)
        exports.addStretch()
        self._status = QLabel("Select a token contract to browse saved issuer audit history.")
        self._status.setWordWrap(True)
        self._actions = QListWidget()
        self._multipliers = QListWidget()
        self._tabs = QTabWidget()
        self._tabs.addTab(self._actions, "Corporate Actions")
        self._tabs.addTab(self._multipliers, "Multiplier Observations")
        self._details = QTextEdit()
        self._details.setReadOnly(True)
        self._details.setMaximumHeight(100)
        self._actions.currentRowChanged.connect(self._show_action)
        self._multipliers.currentRowChanged.connect(self._show_multiplier)
        self._tabs.currentChanged.connect(self._show_selected_record)
        layout = QVBoxLayout(self)
        layout.addLayout(buttons)
        layout.addLayout(exports)
        layout.addWidget(self._status)
        layout.addWidget(self._tabs)
        layout.addWidget(self._details)
        self.setMaximumHeight(320)
        self.setVisible(False)
        self._update_buttons()

    def set_scope(
        self,
        chain: Chain,
        address: str,
        identity: RealWorldAssetIdentity | None = None,
    ) -> None:
        """Discard late results and clear rows when the selected deployment changes."""
        address = AddressValidator.normalized(address)
        if (chain, address) != (self._chain, self._address):
            self._cancel_sync.set()
            self._runner.cancel_all()
            self._busy = False
            self._history = StockTokenAuditContext((), ())
            self._actions.clear()
            self._multipliers.clear()
            self._details.clear()
            self._status.setText("Load saved history or refresh the selected contract.")
        self._chain, self._address, self._identity = chain, address, identity
        self.setVisible(True)
        self._update_buttons()

    def load_saved(self) -> None:
        """Read all stored rows without requiring a wallet or live provider."""
        self._start(refresh=False)

    def _start(self, *, refresh: bool, restart: bool = False) -> None:
        if self._busy:
            return
        if AddressValidator.validation_error(self._address) is not None:
            return
        address = self._address
        chain = self._chain
        self._cancel_sync = Event()
        cancel_sync = self._cancel_sync
        self._busy = True
        self._syncing = refresh and chain is not Chain.ROBINHOOD
        self._update_buttons()
        self._status.setText(
            "Refreshing audit history..." if refresh else "Loading saved history..."
        )

        def operation() -> _HistoryResult:
            if chain is not Chain.ROBINHOOD:
                xstocks_service = self._container.xstocks_audit_service
                note = "Showing locally saved xStocks issuer history."
                if refresh:
                    try:
                        history = xstocks_service.refresh(
                            chain,
                            address,
                            restart=restart,
                            cancel_requested=cancel_sync.is_set,
                            on_progress=lambda progress: self.sync_progress.emit(
                                (cancel_sync, progress)
                            ),
                        )
                        note = (
                            "xStocks sync progress saved."
                            if history is not None
                            else "Contract is not in the xStocks catalog."
                        )
                    except (ProviderError, ValueError) as error:
                        note = f"Refresh unavailable: {error}. Showing saved records."
                history = xstocks_service.load(chain, address)
                return _HistoryResult(history or StockTokenAuditContext((), ()), note)
            service = self._container.stock_token_audit_service
            note = "Showing all locally saved records."
            if refresh:
                try:
                    metadata = (
                        self._container.stock_token_pricing_provider.get_stock_token_metadata(
                            address
                        )
                    )
                    if metadata is None:
                        note = "Contract absent from the current catalog. Showing saved records."
                    else:
                        service.refresh(metadata, limit=None)
                        note = "Audit refreshed. Public catalog cache windows still apply."
                except (ProviderError, ValueError) as error:
                    note = f"Refresh unavailable: {error}. Showing saved records."
            return _HistoryResult(service.load(address, limit=None), note)

        self._runner.start(operation)

    def _loaded(self, result: object) -> None:
        if not isinstance(result, _HistoryResult):
            self._failed(ValueError("Invalid audit history result."))
            return
        self._history = result.history
        self._details.clear()
        if isinstance(self._history, XStocksAuditContext):
            self._tabs.setTabText(0, "Action Revisions")
            self._tabs.setTabText(1, "Issuer Multiplier History")
            self._identity = self._history.identity
            self._actions.clear()
            self._multipliers.clear()
            for revision in self._history.corporate_actions:
                self._actions.addItem(
                    f"{revision.effective_at or 'Time not published'} | {revision.action_type} | "
                    f"{revision.status} | revision {revision.version}"
                )
            for change in self._history.multiplier_history:
                self._multipliers.addItem(
                    f"{change.effective_at.isoformat()} | {change.previous_multiplier} "
                    f"to {change.multiplier} | {change.reason}"
                )
            self._status.setText(
                f"{result.note} {_sync_description(self._history)} "
                "Published records are shown below; staged records remain separate."
            )
            self._busy = False
            self._update_buttons()
            return
        self._tabs.setTabText(0, "Corporate Actions")
        self._tabs.setTabText(1, "Multiplier Observations")
        self._actions.clear()
        self._multipliers.clear()
        for stored in self._history.corporate_actions:
            action = stored.action
            self._actions.addItem(
                f"{action.process_date or 'Not scheduled'} | "
                f"{action.action_type.value.replace('_', ' ')} | {action.status.value}"
            )
        for item in self._history.multiplier_history:
            self._multipliers.addItem(
                f"{item.observed_at.isoformat()} | current={item.current_multiplier} | "
                f"pending={item.pending_multiplier if item.pending_multiplier is not None else 'none'}"
            )
        self._status.setText(
            f"{result.note} Actions: {len(self._history.corporate_actions)}; "
            f"multiplier observations: {len(self._history.multiplier_history)}."
        )
        self._busy = False
        self._update_buttons()

    def _failed(self, error: object) -> None:
        self._status.setText(f"Could not load audit history: {error}")
        self._busy = False
        self._update_buttons()

    def _update_buttons(self) -> None:
        valid = AddressValidator.validation_error(self._address) is None
        self._load_button.setEnabled(valid and not self._busy)
        self._refresh_button.setEnabled(
            valid and not self._busy and self._container.uses_live_providers
        )
        has_records = bool(self._history.corporate_actions or self._history.multiplier_history)
        pending = (
            self._history.sync_progress if isinstance(self._history, XStocksAuditContext) else None
        )
        self._refresh_button.setText("Resume Audit Sync" if pending else "Refresh Audit History")
        self._restart_button.setVisible(self._chain is not Chain.ROBINHOOD)
        self._pause_button.setVisible(self._chain is not Chain.ROBINHOOD)
        self._restart_button.setEnabled(
            bool(pending) and not self._busy and self._container.uses_live_providers
        )
        self._pause_button.setEnabled(self._busy and self._syncing)
        has_records = has_records or pending is not None
        self._csv_button.setEnabled(has_records and not self._busy)
        self._json_button.setEnabled(has_records and not self._busy)

    def _show_action(self, row: int) -> None:
        if row < 0 or row >= len(self._history.corporate_actions):
            return
        if isinstance(self._history, XStocksAuditContext):
            revision = self._history.corporate_actions[row]
            self._details.setPlainText(
                "\n".join(
                    [
                        f"Issuer event ID: {revision.event_id}; revision: {revision.version}",
                        f"Created: {revision.created_at.isoformat()}",
                        f"Source: {self._history.identity.source_name}",
                        *(f"{key}: {value}" for key, value in revision.details),
                    ]
                )
            )
            return
        stored = self._history.corporate_actions[row]
        action = stored.action
        self._details.setPlainText(
            "\n".join(
                [
                    f"Issuer action ID: {action.action_id}",
                    f"Contract: {action.contract_address}",
                    f"Source: {action.source_provider}",
                    f"First observed: {stored.first_seen_at.isoformat()}",
                    f"Last observed: {stored.last_seen_at.isoformat()}",
                    f"Details ({action.detail_kind}):",
                    *(f"{key}: {value}" for key, value in action.details),
                ]
            )
        )

    def _show_selected_record(self, tab: int) -> None:
        self._details.clear()
        if tab == 0:
            self._show_action(self._actions.currentRow())
        else:
            self._show_multiplier(self._multipliers.currentRow())

    def _show_multiplier(self, row: int) -> None:
        if row < 0 or row >= len(self._history.multiplier_history):
            return
        if isinstance(self._history, XStocksAuditContext):
            change = self._history.multiplier_history[row]
            self._details.setPlainText(
                "\n".join(
                    [
                        f"Issuer change ID: {change.change_id}",
                        f"Previous multiplier: {change.previous_multiplier}",
                        f"Multiplier: {change.multiplier}",
                        f"Reason: {change.reason}",
                        f"Issuer activation time: {change.effective_at.isoformat()}",
                        f"Source: {self._history.identity.source_name}",
                    ]
                )
            )
            return
        item = self._history.multiplier_history[row]
        self._details.setPlainText(
            "\n".join(
                [
                    f"Issuer asset ID: {item.asset_id}",
                    f"Contract: {item.contract_address}",
                    f"Status: {item.status.value}",
                    f"Current multiplier: {item.current_multiplier}",
                    f"Pending multiplier: {item.pending_multiplier}",
                    f"Pending effective time: {item.pending_multiplier_effective_at}",
                    f"Observed: {item.observed_at.isoformat()}",
                    f"Source: {item.source_provider}",
                ]
            )
        )

    def _export(self, *, as_json: bool) -> None:
        pending = (
            isinstance(self._history, XStocksAuditContext)
            and self._history.sync_progress is not None
        )
        if self._busy or not (
            self._history.corporate_actions or self._history.multiplier_history or pending
        ):
            return
        suffix = "json" if as_json else "csv"
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export RWA Audit History",
            f"rwa-audit-{self._address}.{suffix}",
            f"{suffix.upper()} Files (*.{suffix})",
        )
        if not name:
            return
        try:
            report = RWAAuditReport(
                self._chain,
                self._address,
                self._history,
                datetime.now(tz=UTC),
                self._identity,
            )
            path = write_rwa_audit(report, Path(name), as_json=as_json)
        except (OSError, ValueError) as error:
            self._status.setText(f"Could not export audit history: {error}")
        else:
            self._status.setText(f"Audit export saved: {path}")

    def _pause_sync(self) -> None:
        self._cancel_sync.set()
        self._status.setText("Pausing after the current page; committed progress will be kept.")
        self._pause_button.setEnabled(False)

    def _on_sync_progress(self, message: object) -> None:
        if not isinstance(message, tuple) or len(message) != 2:
            return
        cancellation, progress = message
        if (
            cancellation is self._cancel_sync
            and not cancellation.is_set()
            and isinstance(progress, XStocksSyncProgress)
        ):
            self._status.setText(_progress_description(progress))


def _progress_description(progress: XStocksSyncProgress) -> str:
    actions = "finished" if progress.actions_done else f"next page {progress.actions_next_page}"
    multipliers = (
        "finished" if progress.multipliers_done else f"next page {progress.multipliers_next_page}"
    )
    return (
        f"Sync {progress.state}. Actions: {actions}, {progress.actions_staged} staged records. "
        f"Multipliers: {multipliers}, {progress.multipliers_staged} staged records. "
        f"Pages saved: {progress.actions_pages + progress.multipliers_pages}."
    )


def _sync_description(history: XStocksAuditContext) -> str:
    last = history.fetched_at.isoformat() if history.fetched_at is not None else "none yet"
    status = (
        _progress_description(history.sync_progress)
        if history.sync_progress
        else "Both feed traversals finished."
    )
    return f"{status} Last completed sync: {last}."
