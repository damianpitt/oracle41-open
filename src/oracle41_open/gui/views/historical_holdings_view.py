"""Collect, browse, and export single-token historical holdings audit reports.

Explicit collection reads historical contract balances but never starts wallet synchronization or
fetches prices. Saved reports work offline and keep their original inputs. Background tasks and
scope cancellation prevent a late result from appearing under another wallet or token.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from oracle41_open._json import dumps
from oracle41_open.core.models import Chain, ValidationError
from oracle41_open.core.models.block_reference import BlockReferenceCheck
from oracle41_open.core.models.historical_holdings import (
    HistoricalHoldingsReport,
    HistoricalHoldingsReportSummary,
    HistoricalHoldingsRequest,
)
from oracle41_open.exports.historical_holdings_export import (
    block_reference_check_json_bytes,
    historical_holdings_csv_text,
    historical_holdings_json_bytes,
)
from oracle41_open.gui.task_runner import BackgroundTaskRunner

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


@dataclass(frozen=True)
class _SavedReport:
    report: HistoricalHoldingsReport | None
    check: BlockReferenceCheck | None


class HistoricalHoldingsView(QWidget):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self._service = container.historical_holdings_service
        self._runner = BackgroundTaskRunner(self)
        self._runner.result.connect(self._loaded)
        self._runner.error.connect(self._failed)
        self._busy = False
        self._report: HistoricalHoldingsReport | None = None
        self._latest_check: BlockReferenceCheck | None = None
        self._chain = QComboBox()
        for chain in Chain:
            self._chain.addItem(chain.display_name, chain.value)
        self._wallet = QLineEdit()
        self._token = QLineEdit()
        self._wallet.setPlaceholderText("Wallet address")
        self._token.setPlaceholderText("Exact ERC-20 token contract")
        self._opening = QLineEdit()
        self._closing = QLineEdit()
        self._opening.setPlaceholderText("Opening end-of-block balance; never assumed zero")
        self._closing.setPlaceholderText("Closing end-of-block balance")
        self._price_at = QLineEdit()
        self._price_at.setReadOnly(True)
        self._price_at.setPlaceholderText("Derived from closing block on collection")
        self._finalized = QCheckBox(
            "Require closing block to be reported finalized by the endpoint"
        )
        self._age = QSpinBox()
        self._age.setRange(0, 604_800)
        self._age.setValue(86_400)
        self._age.setSuffix(" seconds")
        self._saved = QComboBox()
        self._saved.currentIndexChanged.connect(self._open_saved)
        self._collect = QPushButton("Collect Block Balances and Audit")
        self._list = QPushButton("Find Saved Reports")
        self._json = QPushButton("Export JSON")
        self._csv = QPushButton("Export CSV")
        self._check = QPushButton("Check Saved Block References")
        self._export_check = QPushButton("Export Latest Check JSON")
        self._collect.clicked.connect(self._collect_report)
        self._list.clicked.connect(self._list_saved)
        self._json.clicked.connect(lambda: self._export(True))
        self._csv.clicked.connect(lambda: self._export(False))
        self._check.clicked.connect(self._check_references)
        self._export_check.clicked.connect(self._export_latest_check)
        self._status = QLabel(
            "Single-token inputs only; not complete history, a portfolio total, or P&L."
        )
        self._status.setWordWrap(True)
        self._details = QTextEdit()
        self._details.setReadOnly(True)
        form = QFormLayout()
        for label, widget in (
            ("Network", self._chain),
            ("Wallet", self._wallet),
            ("Token", self._token),
            ("Opening block", self._opening),
            ("Closing block", self._closing),
            ("Price lookup time (UTC)", self._price_at),
            ("Finality rule", self._finalized),
            ("Maximum prior-price age", self._age),
            ("Saved reports", self._saved),
        ):
            form.addRow(label, widget)
        buttons = QHBoxLayout()
        for button in (self._collect, self._list, self._json, self._csv):
            buttons.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(buttons)
        checks = QHBoxLayout()
        checks.addWidget(self._check)
        checks.addWidget(self._export_check)
        layout.addLayout(checks)
        layout.addWidget(self._status)
        layout.addWidget(self._details)
        note = QLabel(
            "Synchronize Activity or Token Detail and save price evidence first. Collection does not fetch missing activity or prices. Saved fees stay in native units and are not deducted from ERC-20 holdings."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self._chain.currentIndexChanged.connect(self._scope_changed)
        for widget in (self._wallet, self._token, self._opening, self._closing):
            widget.textChanged.connect(self._scope_changed)
        self._age.valueChanged.connect(self._scope_changed)
        self._finalized.toggled.connect(self._scope_changed)
        self._update()

    def _request(self) -> HistoricalHoldingsRequest:
        try:
            return HistoricalHoldingsRequest(
                self._wallet.text(),
                Chain(self._chain.currentData()),
                self._token.text(),
                int(self._opening.text()),
                int(self._closing.text()),
                None,
                self._age.value(),
                self._finalized.isChecked(),
            )
        except ValueError:
            raise ValidationError("Enter valid integer block numbers.") from None

    def _scope_changed(self) -> None:
        self._runner.cancel_all()
        self._busy = False
        self._report = None
        self._latest_check = None
        self._price_at.clear()
        self._saved.blockSignals(True)
        self._saved.clear()
        self._saved.blockSignals(False)
        self._details.clear()
        self._status.setText(
            "Collect a new report or find saved reports for this wallet and token."
        )
        self._update()

    def _collect_report(self) -> None:
        if self._busy:
            return
        try:
            request = self._request()
        except ValidationError as error:
            self._failed(error)
            return
        self._busy = True
        self._status.setText("Reading historical endpoint balances and capturing local inputs...")
        self._update()
        self._runner.start(lambda: self._service.collect(request))

    def _list_saved(self) -> None:
        if self._busy:
            return
        wallet, chain, token = (
            self._wallet.text(),
            Chain(self._chain.currentData()),
            self._token.text(),
        )
        self._busy = True
        self._update()
        self._runner.start(lambda: self._service.list_saved(wallet, chain, token))

    def _open_saved(self) -> None:
        if self._busy or self._saved.currentData() is None:
            return
        report_id = str(self._saved.currentData())
        self._busy = True
        self._update()
        self._runner.start(
            lambda: _SavedReport(
                self._service.load(report_id), self._service.latest_check(report_id)
            )
        )

    def _check_references(self) -> None:
        """Save a separate current observation; never replace the displayed report."""
        if self._busy or self._report is None:
            return
        report_id = self._report.report_id
        self._busy = True
        self._status.setText("Comparing saved references with the current endpoint...")
        self._update()
        self._runner.start(lambda: self._service.revalidate(report_id))

    def _loaded(self, result: object) -> None:
        self._busy = False
        if isinstance(result, BlockReferenceCheck):
            if self._report is not None and result.report_id == self._report.report_id:
                self._latest_check = result
                self._show_report()
            return
        if isinstance(result, tuple) and all(
            isinstance(item, HistoricalHoldingsReportSummary) for item in result
        ):
            self._saved.blockSignals(True)
            self._saved.clear()
            for item in result:
                self._saved.addItem(
                    f"{item.created_at.isoformat()} | {item.report_id}", item.report_id
                )
            self._saved.blockSignals(False)
            self._update()
            if result:
                self._open_saved()
            else:
                self._report = None
                self._latest_check = None
                self._price_at.clear()
                self._details.clear()
                self._status.setText("No saved report for this wallet, network, and token.")
                self._update()
            return
        self._latest_check = result.check if isinstance(result, _SavedReport) else None
        report = result.report if isinstance(result, _SavedReport) else result
        if not isinstance(report, HistoricalHoldingsReport):
            self._failed(ValidationError("The selected saved report is unavailable."))
            return
        self._report = report
        self._show_report()

    def _show_report(self) -> None:
        if self._report is None:
            return
        document = self._report.document
        summary = document["result"]
        inputs = document["inputs"]
        self._price_at.setText(inputs["request"]["price_at"])
        timing = (
            "Price time comes from the closing block. "
            f"Finality: {inputs['finality']['status']} (endpoint assertion)."
            if summary["price_time_source"] == "closing_block_header"
            else "Legacy report: price time is user supplied, not block-verified."
        )
        latest = json.loads(self._latest_check.document_json) if self._latest_check else None
        self._status.setText(
            f"Report {self._report.report_id}: {summary['reconciliation_status']}. "
            f"Closing token amount: {summary['observed_closing_amount']}. "
            f"Price: {summary['price_status']}. USD estimate: {summary['closing_balance_estimate_usd'] or 'unknown'}. "
            f"Endpoint agreement is not proof of complete history. {timing} "
            + (f"Latest check: {latest['status']}. Original report unchanged." if latest else "")
        )
        lines = [
            f"Opening raw balance: {summary['opening_raw_balance']}",
            f"Saved-transfer closing projection: {summary['projected_closing_raw_balance']}",
            f"Observed closing raw balance: {summary['observed_closing_raw_balance']}",
            f"Difference (raw units): {summary['reconciliation_difference_raw']}",
            f"Historical read source: {inputs['closing']['source_provider']}",
            f"Price fetch ID: {inputs['price']['fetch_id'] or 'none'}",
            f"Closing block hash: {inputs['closing'].get('header', {}).get('hash', 'not saved in legacy report')}",
            "",
            "Limitations and Warnings:",
            *(f"- {warning}" for warning in summary["warnings"]),
            "",
            "Frozen Inputs and Results (JSON):",
            dumps(document, pretty=True).decode("utf-8"),
        ]
        if latest:
            lines.extend(
                [
                    "",
                    "Latest Reference Check (separate from frozen report):",
                    dumps(latest, pretty=True).decode("utf-8"),
                ]
            )
        self._details.setPlainText("\n".join(lines))
        self._update()

    def _failed(self, error: object) -> None:
        self._busy = False
        self._status.setText(
            f"Historical holdings unavailable: {error}. Any displayed saved report is unchanged."
        )
        self._update()

    def _update(self) -> None:
        for widget in (
            self._chain,
            self._wallet,
            self._token,
            self._opening,
            self._closing,
            self._price_at,
            self._age,
            self._finalized,
            self._saved,
            self._list,
        ):
            widget.setEnabled(not self._busy)
        self._collect.setEnabled(not self._busy and self._service.can_collect)
        self._json.setEnabled(not self._busy and self._report is not None)
        self._csv.setEnabled(not self._busy and self._report is not None)
        self._check.setEnabled(
            not self._busy and self._report is not None and self._service.can_collect
        )
        self._export_check.setEnabled(not self._busy and self._latest_check is not None)

    def _export_latest_check(self) -> None:
        if self._latest_check is None:
            return
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Block Reference Check",
            "block-reference-check.json",
            "JSON Files (*.json)",
        )
        if name:
            try:
                Path(name).write_bytes(block_reference_check_json_bytes(self._latest_check))
            except OSError as error:
                self._status.setText(f"Could not export reference check: {error}")
            else:
                self._status.setText(
                    f"Exported separate reference check to {name}. Original report unchanged."
                )

    def _export(self, as_json: bool) -> None:
        if self._report is None:
            return
        suffix = "json" if as_json else "csv"
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Historical Holdings",
            f"historical-holdings.{suffix}",
            f"{suffix.upper()} Files (*.{suffix})",
        )
        if name:
            try:
                data = (
                    historical_holdings_json_bytes(self._report)
                    if as_json
                    else historical_holdings_csv_text(self._report).encode("utf-8")
                )
                Path(name).write_bytes(data)
            except OSError as error:
                self._status.setText(f"Could not export report: {error}")
            else:
                self._status.setText(f"Exported pinned report {self._report.report_id} to {name}")
