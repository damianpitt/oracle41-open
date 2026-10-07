"""Browse and export saved corporate actions and multiplier observations.

The browser loads SQLite history independently of wallet activity. Refresh uses the public issuer
catalog in the background and keeps saved records visible when the network fails. Selecting a row
shows its full source details, and exports include every loaded local record for that deployment.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

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
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.exports.rwa_audit_export import RWAAuditReport, write_rwa_audit
from oracle41_open.gui.task_runner import BackgroundTaskRunner

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


@dataclass(frozen=True)
class _HistoryResult:
    history: StockTokenAuditContext
    note: str


class RWAAuditBrowser(QGroupBox):
    """Provide local browsing, explicit refresh, and audit exports for a selected token."""

    def __init__(self, container: AppContainer) -> None:
        super().__init__("RWA Audit History")
        self._container = container
        self._runner = BackgroundTaskRunner(self)
        self._runner.result.connect(self._loaded)
        self._runner.error.connect(self._failed)
        self._chain = Chain.ETHEREUM
        self._address = ""
        self._identity: RealWorldAssetIdentity | None = None
        self._history = StockTokenAuditContext((), ())
        self._busy = False
        self._load_button = QPushButton("Load Saved History")
        self._refresh_button = QPushButton("Refresh Audit History")
        self._csv_button = QPushButton("Export Audit CSV")
        self._json_button = QPushButton("Export Audit JSON")
        self._load_button.clicked.connect(self.load_saved)
        self._refresh_button.clicked.connect(lambda: self._start(refresh=True))
        self._csv_button.clicked.connect(lambda: self._export(as_json=False))
        self._json_button.clicked.connect(lambda: self._export(as_json=True))
        buttons = QHBoxLayout()
        for button in (
            self._load_button,
            self._refresh_button,
            self._csv_button,
            self._json_button,
        ):
            buttons.addWidget(button)
        self._status = QLabel("Select a Robinhood Chain contract to browse saved audit history.")
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
            self._runner.cancel_all()
            self._busy = False
            self._history = StockTokenAuditContext((), ())
            self._actions.clear()
            self._multipliers.clear()
            self._details.clear()
            self._status.setText("Load saved history or refresh the selected contract.")
        self._chain, self._address, self._identity = chain, address, identity
        self.setVisible(chain is Chain.ROBINHOOD)
        self._update_buttons()

    def load_saved(self) -> None:
        """Read all stored rows without requiring a wallet or live provider."""
        self._start(refresh=False)

    def _start(self, *, refresh: bool) -> None:
        if self._busy or self._chain is not Chain.ROBINHOOD:
            return
        if AddressValidator.validation_error(self._address) is not None:
            return
        address = self._address
        self._busy = True
        self._update_buttons()
        self._status.setText(
            "Refreshing audit history..." if refresh else "Loading saved history..."
        )

        def operation() -> _HistoryResult:
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
        valid = self._chain is Chain.ROBINHOOD and (
            AddressValidator.validation_error(self._address) is None
        )
        self._load_button.setEnabled(valid and not self._busy)
        self._refresh_button.setEnabled(
            valid and not self._busy and self._container.uses_live_providers
        )
        has_records = bool(self._history.corporate_actions or self._history.multiplier_history)
        self._csv_button.setEnabled(has_records and not self._busy)
        self._json_button.setEnabled(has_records and not self._busy)

    def _show_action(self, row: int) -> None:
        if row < 0 or row >= len(self._history.corporate_actions):
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
        if self._busy or not (self._history.corporate_actions or self._history.multiplier_history):
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
