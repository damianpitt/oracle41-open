"""Browse, fetch, and export daily price evidence without changing wallet valuations.

All network and database work runs through the shared task runner. Saved ranges remain readable
without a key. Scope changes discard late results, and failed refreshes retain the displayed fetch.
Point lookup shows a prior-or-exact source observation and never substitutes a current quote.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from oracle41_open.core.models import Chain, ValidationError
from oracle41_open.core.models.historical_price import (
    HistoricalPriceBatch,
    HistoricalPriceQuery,
    HistoricalPriceSelection,
)
from oracle41_open.exports.historical_price_export import (
    historical_price_csv_text,
    historical_price_json_bytes,
)
from oracle41_open.gui.task_runner import BackgroundTaskRunner

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


class HistoricalPricesView(QWidget):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self._service = container.historical_price_service
        self._runner = BackgroundTaskRunner(self)
        self._runner.result.connect(self._loaded)
        self._runner.error.connect(self._failed)
        self._batch: HistoricalPriceBatch | None = None
        self._busy = False
        self._chain = QComboBox()
        for chain in Chain:
            self._chain.addItem(chain.display_name, chain.value)
        self._address = QLineEdit()
        self._address.setPlaceholderText("Exact token contract, not a wallet or ticker")
        yesterday = datetime.now(tz=UTC).date() - timedelta(days=1)
        self._start = QDateEdit(QDate(yesterday.year, yesterday.month, yesterday.day).addDays(-6))
        self._end = QDateEdit(QDate(yesterday.year, yesterday.month, yesterday.day))
        for widget in (self._start, self._end):
            widget.setCalendarPopup(True)
            widget.setDisplayFormat("yyyy-MM-dd")
        self._target = QLineEdit()
        self._target.setPlaceholderText("UTC or offset ISO time, e.g. 2026-11-01T12:00:00Z")
        self._age = QSpinBox()
        self._age.setRange(0, 604_800)
        self._age.setValue(86_400)
        self._age.setSuffix(" seconds")
        self._saved = QPushButton("Load Saved Range")
        self._fetch = QPushButton("Fetch Daily Prices")
        self._lookup = QPushButton("Find Saved Price at Time")
        self._csv = QPushButton("Export CSV")
        self._json = QPushButton("Export JSON")
        self._saved.clicked.connect(lambda: self._load(False))
        self._fetch.clicked.connect(lambda: self._load(True))
        self._lookup.clicked.connect(self._select)
        self._csv.clicked.connect(lambda: self._export(False))
        self._json.clicked.connect(lambda: self._export(True))
        self._status = QLabel("Daily price evidence only. Current portfolio values are unchanged.")
        self._status.setWordWrap(True)
        self._table = QTableWidget(0, 2)
        self._table.setHorizontalHeaderLabels(["Provider timestamp (UTC)", "USD price"])
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.horizontalHeader().setStretchLastSection(True)
        form = QFormLayout()
        form.addRow("Network", self._chain)
        form.addRow("Token contract", self._address)
        form.addRow("Start day (UTC)", self._start)
        form.addRow("End day (UTC, included)", self._end)
        form.addRow("Lookup time", self._target)
        form.addRow("Maximum prior-price age", self._age)
        actions = QHBoxLayout()
        for button in (self._saved, self._fetch, self._lookup, self._csv, self._json):
            actions.addWidget(button)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addLayout(actions)
        layout.addWidget(self._status)
        layout.addWidget(self._table)
        note = QLabel(
            "Fetching requires an enabled Alchemy key; saved history works offline. Daily samples are not trade prices. Missing days remain gaps; no interpolation, stablecoin peg, or current-price fallback is applied."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self._chain.currentIndexChanged.connect(self._scope_changed)
        self._address.textChanged.connect(self._scope_changed)
        self._start.dateChanged.connect(self._scope_changed)
        self._end.dateChanged.connect(self._scope_changed)
        self._update_controls()

    def _query(self) -> HistoricalPriceQuery:
        start_day = self._start.date()
        end_day = self._end.date()
        start = datetime(start_day.year(), start_day.month(), start_day.day(), tzinfo=UTC)
        end = datetime(end_day.year(), end_day.month(), end_day.day(), tzinfo=UTC) + timedelta(
            days=1, microseconds=-1
        )
        return HistoricalPriceQuery(
            Chain(self._chain.currentData()), self._address.text(), start, end
        )

    def _scope_changed(self) -> None:
        self._runner.cancel_all()
        self._busy = False
        self._batch = None
        self._table.setRowCount(0)
        self._status.setText(
            "Load a saved range or explicitly fetch up to 30 days of daily prices."
        )
        self._update_controls()

    def _load(self, refresh: bool) -> None:
        if self._busy:
            return
        try:
            query = self._query()
        except ValidationError as error:
            self._failed(error)
            return
        self._busy = True
        self._update_controls()
        self._status.setText("Fetching daily prices..." if refresh else "Reading saved prices...")
        self._runner.start(
            lambda: self._service.refresh(query) if refresh else self._service.load_saved(query)
        )

    def _select(self) -> None:
        if self._busy:
            return
        try:
            chain = Chain(self._chain.currentData())
            address = self._address.text()
            at = datetime.fromisoformat(self._target.text().strip())
            age = self._age.value()
        except ValueError:
            self._failed(
                ValidationError("Enter an ISO lookup time with Z or an explicit time-zone offset.")
            )
            return
        self._busy = True
        self._update_controls()
        self._runner.start(lambda: self._service.select(chain, address, at, max_age_seconds=age))

    def _loaded(self, result: object) -> None:
        self._busy = False
        if isinstance(result, HistoricalPriceSelection):
            detail = "No saved price at or before this time."
            if result.point is not None:
                detail = (
                    f"USD {result.point.value_usd} at {result.point.timestamp.isoformat()}; "
                    f"fetch {result.fetch_id}. "
                    + (
                        "Too old to use."
                        if result.status == "stale"
                        else "Within the selected age limit."
                    )
                )
            self._status.setText(f"Lookup: {result.status}. {detail}")
        elif result is None:
            self._batch = None
            self._table.setRowCount(0)
            self._status.setText(
                "No saved fetch matches this exact network, token, and date range."
            )
        elif isinstance(result, HistoricalPriceBatch):
            self._batch = result
            self._table.setRowCount(len(result.points))
            for row, point in enumerate(result.points):
                self._table.setItem(row, 0, QTableWidgetItem(point.timestamp.isoformat()))
                self._table.setItem(row, 1, QTableWidgetItem(str(point.value_usd)))
            self._table.resizeColumnToContents(0)
            self._status.setText(
                f"{len(result.points)} daily observations. Source: {result.source_provider}. "
                f"Fetched: {result.fetched_at.isoformat()}. Fetch ID: {result.fetch_id}. "
                "Gaps and empty responses do not mean a zero price."
            )
        else:
            self._failed(ValidationError("Invalid saved historical price result."))
            return
        self._update_controls()

    def _failed(self, error: object) -> None:
        self._busy = False
        self._status.setText(
            f"Historical prices unavailable: {error}. Any displayed saved fetch is unchanged."
        )
        self._update_controls()

    def _update_controls(self) -> None:
        for widget in (
            self._chain,
            self._address,
            self._start,
            self._end,
            self._target,
            self._age,
            self._saved,
            self._lookup,
        ):
            widget.setEnabled(not self._busy)
        chain = Chain(self._chain.currentData())
        self._fetch.setEnabled(
            not self._busy
            and self._service.can_refresh
            and chain.network.alchemy_pricing_network_path is not None
        )
        self._csv.setEnabled(not self._busy and self._batch is not None)
        self._json.setEnabled(not self._busy and self._batch is not None)

    def _export(self, as_json: bool) -> None:
        if self._batch is None:
            return
        suffix = "json" if as_json else "csv"
        name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Historical Prices",
            f"historical-prices.{suffix}",
            f"{suffix.upper()} Files (*.{suffix})",
        )
        if not name:
            return
        try:
            data = (
                historical_price_json_bytes(self._batch)
                if as_json
                else historical_price_csv_text(self._batch).encode("utf-8")
            )
            Path(name).write_bytes(data)
        except OSError as error:
            self._status.setText(f"Could not export historical prices: {error}")
        else:
            self._status.setText(f"Saved historical price evidence to {name}")
