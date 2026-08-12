"""Present the combined watchlist portfolio.

The view loads wallet aggregates, shows chain and token totals, and offers portfolio CSV or JSON exports.
Partial wallet failures remain visible to the user.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from oracle41_open.core.models import Chain, ValidationError, WatchlistEntry
from oracle41_open.core.services.portfolio_service import PortfolioLoadResult, PortfolioWalletResult
from oracle41_open.exports import PortfolioExportTemplate, write_portfolio_csv, write_portfolio_json
from oracle41_open.gui.task_runner import BackgroundTaskRunner

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


class PortfolioView(QWidget):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self._container = container
        self._task_runner = BackgroundTaskRunner(parent=self)
        self._task_runner.result.connect(self._on_portfolio_loaded)
        self._task_runner.error.connect(self._on_portfolio_load_error)
        self._task_runner.finished.connect(self._on_portfolio_load_finished)
        self._entries: list[WatchlistEntry] = []
        self._last_result: PortfolioLoadResult | None = None
        self._is_initializing = True
        self._is_loading = False
        self._pending_load: bool | None = None

        self._chain_combo = QComboBox(self)
        self._chain_combo.addItem("All chains", "all")
        for chain in Chain:
            self._chain_combo.addItem(chain.display_name, chain.value)
        self._chain_combo.currentIndexChanged.connect(self._on_chain_filter_changed)

        self._hide_unverified_checkbox = QCheckBox("Hide unverified tokens", self)
        self._hide_dust_checkbox = QCheckBox("Hide low-value tokens", self)
        self._dust_threshold_input = QLineEdit(self)
        self._dust_threshold_input.setPlaceholderText("Dust threshold in USD")

        self._refresh_watchlist_button = QPushButton("Refresh Watchlist", self)
        self._refresh_watchlist_button.clicked.connect(self._on_refresh_watchlist_clicked)
        self._select_all_button = QPushButton("Select All", self)
        self._select_all_button.clicked.connect(self._on_select_all_clicked)
        self._clear_selection_button = QPushButton("Clear Selection", self)
        self._clear_selection_button.clicked.connect(self._on_clear_selection_clicked)

        self._load_button = QPushButton("Load Portfolio", self)
        self._load_button.clicked.connect(self._on_load_clicked)
        self._refresh_button = QPushButton("Refresh Portfolio", self)
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        self._export_csv_button = QPushButton("Export CSV", self)
        self._export_csv_button.clicked.connect(self._on_export_csv_clicked)
        self._export_csv_button.setEnabled(False)
        self._export_json_button = QPushButton("Export JSON", self)
        self._export_json_button.clicked.connect(self._on_export_json_clicked)
        self._export_json_button.setEnabled(False)
        self._export_template_combo = QComboBox(self)
        self._export_template_combo.addItem("Summary", PortfolioExportTemplate.SUMMARY.value)
        self._export_template_combo.addItem("Chains", PortfolioExportTemplate.CHAINS.value)
        self._export_template_combo.addItem("Tokens", PortfolioExportTemplate.TOKENS.value)
        self._export_template_combo.addItem("Wallets", PortfolioExportTemplate.WALLETS.value)

        self._status_label = QLabel("Portfolio aggregate ready.", self)
        self._status_label.setWordWrap(True)
        self._selection_hint_label = QLabel("No wallets selected. Loading will include all listed wallets.", self)
        self._selection_hint_label.setWordWrap(True)

        self._entries_list = QListWidget(self)
        self._entries_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._entries_list.itemSelectionChanged.connect(self._on_selection_changed)

        self._results = QTextEdit(self)
        self._results.setReadOnly(True)
        self._results.setPlaceholderText("Portfolio aggregate output appears here.")

        self._init_layout()
        self._apply_default_filters()
        self._reload_entries()
        self._is_initializing = False

    def _init_layout(self) -> None:
        controls_box = QGroupBox("Portfolio Aggregate")
        form = QFormLayout()
        form.addRow("Watchlist Chain Scope", self._chain_combo)
        form.addRow("", self._hide_unverified_checkbox)
        form.addRow("", self._hide_dust_checkbox)
        form.addRow("Dust Threshold (USD)", self._dust_threshold_input)

        watchlist_row = QHBoxLayout()
        watchlist_row.addWidget(self._refresh_watchlist_button)
        watchlist_row.addWidget(self._select_all_button)
        watchlist_row.addWidget(self._clear_selection_button)
        watchlist_row.addStretch(1)
        form.addRow("Wallet Selection", watchlist_row)

        run_row = QHBoxLayout()
        run_row.addWidget(self._load_button)
        run_row.addWidget(self._refresh_button)
        run_row.addStretch(1)
        form.addRow("", run_row)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Template", self))
        export_row.addWidget(self._export_template_combo)
        export_row.addWidget(self._export_csv_button)
        export_row.addWidget(self._export_json_button)
        export_row.addStretch(1)
        form.addRow("Export", export_row)

        controls_box.setLayout(form)

        root = QVBoxLayout()
        root.addWidget(controls_box)
        root.addWidget(self._selection_hint_label)
        root.addWidget(self._status_label)
        root.addWidget(self._entries_list, stretch=1)
        root.addWidget(self._results, stretch=1)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(root)

    def _apply_default_filters(self) -> None:
        settings = self._container.settings_store.load()
        chain_index = self._chain_combo.findData(settings.selected_chain.value)
        if chain_index >= 0:
            self._chain_combo.setCurrentIndex(chain_index)
        self._hide_unverified_checkbox.setChecked(settings.hide_unverified)
        self._hide_dust_checkbox.setChecked(settings.hide_dust)
        self._dust_threshold_input.setText(settings.dust_threshold_usd)

    def _selected_chain(self) -> Chain | None:
        raw_chain = self._chain_combo.currentData()
        if not isinstance(raw_chain, str) or raw_chain == "all":
            return None
        try:
            return Chain(raw_chain)
        except ValueError:
            return None

    def _parse_dust_threshold(self) -> Decimal:
        raw = self._dust_threshold_input.text().strip()
        if not raw:
            return Decimal("1")
        try:
            value = Decimal(raw)
        except InvalidOperation as error:
            raise ValidationError("Dust threshold must be numeric.") from error
        if value < 0:
            raise ValidationError("Dust threshold cannot be negative.")
        return value

    def _selected_export_template(self) -> PortfolioExportTemplate:
        raw = self._export_template_combo.currentData()
        if not isinstance(raw, str):
            return PortfolioExportTemplate.SUMMARY
        try:
            return PortfolioExportTemplate(raw)
        except ValueError:
            return PortfolioExportTemplate.SUMMARY

    def _on_chain_filter_changed(self) -> None:
        self._reload_entries()
        if self._is_initializing:
            return
        self._status_label.setText("Watchlist chain scope updated.")

    def _on_refresh_watchlist_clicked(self) -> None:
        self._reload_entries()
        entry_count = len(self._entries)
        self._status_label.setText(
            f"Watchlist entries refreshed for portfolio scope: {entry_count} wallet(s)."
        )

    def _on_select_all_clicked(self) -> None:
        if not self._entries:
            self._status_label.setText("No watchlist entries available.")
            return
        self._entries_list.selectAll()
        self._status_label.setText("Selected all listed wallets.")

    def _on_clear_selection_clicked(self) -> None:
        self._entries_list.clearSelection()
        self._status_label.setText("Wallet selection cleared. Loading will include all listed wallets.")

    def _on_load_clicked(self) -> None:
        self._load_portfolio(force_refresh=False)

    def _on_refresh_clicked(self) -> None:
        self._load_portfolio(force_refresh=True)

    def _on_selection_changed(self) -> None:
        selected_count = len(self._selected_entries())
        if selected_count == 0:
            self._selection_hint_label.setText(
                "No wallets selected. Loading will include all listed wallets."
            )
            return
        self._selection_hint_label.setText(
            f"{selected_count} wallet(s) selected. Loading will include selected wallets only."
        )

    def _selected_entries(self) -> list[WatchlistEntry]:
        selected: list[WatchlistEntry] = []
        for item in self._entries_list.selectedItems():
            raw_entry = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(raw_entry, WatchlistEntry):
                selected.append(raw_entry)
        return selected

    def _reload_entries(self, selected_entry_ids: set[int] | None = None) -> None:
        current_selected_ids = selected_entry_ids
        if current_selected_ids is None:
            current_selected_ids = {entry.id for entry in self._selected_entries()}

        entries = self._container.watchlist_service.list_entries(chain=self._selected_chain())
        self._entries = entries
        self._entries_list.clear()
        for entry in entries:
            item = QListWidgetItem(_entry_display_text(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._entries_list.addItem(item)
            if entry.id in current_selected_ids:
                item.setSelected(True)

        if not entries:
            self._selection_hint_label.setText(
                "No watchlist entries in this scope. Add wallets in Watchlist first."
            )
        else:
            self._on_selection_changed()

    def _load_portfolio(self, force_refresh: bool) -> None:
        if self._is_loading:
            return
        try:
            dust_threshold = self._parse_dust_threshold()
            selected_entries = self._selected_entries()
            selected_entry_ids = [entry.id for entry in selected_entries] if selected_entries else None
        except ValidationError as error:
            self._status_label.setText(str(error))
            return

        chain = self._selected_chain()
        hide_unverified = self._hide_unverified_checkbox.isChecked()
        hide_dust = self._hide_dust_checkbox.isChecked()
        self._pending_load = force_refresh
        self._set_loading(True, status_text="Loading portfolio aggregate...")
        self._task_runner.start(
            lambda: self._container.portfolio_service.load_portfolio(
                chain=chain,
                selected_entry_ids=selected_entry_ids,
                hide_unverified=hide_unverified,
                hide_dust=hide_dust,
                dust_threshold_usd=dust_threshold,
                force_refresh=force_refresh,
            )
        )

    def _on_portfolio_loaded(self, raw_result: object) -> None:
        if not isinstance(raw_result, PortfolioLoadResult) or self._pending_load is None:
            self._on_portfolio_load_error(RuntimeError("Portfolio service returned an invalid result."))
            return

        force_refresh = self._pending_load
        self._last_result = raw_result
        has_wallets = raw_result.selected_wallet_count > 0
        self._export_csv_button.setEnabled(has_wallets)
        self._export_json_button.setEnabled(has_wallets)
        self._results.setPlainText(_render_result(raw_result))
        if raw_result.selected_wallet_count == 0:
            self._status_label.setText("No wallets in scope. Adjust chain filter or add watchlist entries.")
            return

        action = "refreshed" if force_refresh else "loaded"
        status_parts = [
            f"Portfolio {action}: {raw_result.loaded_wallet_count}/{raw_result.selected_wallet_count} wallet(s) loaded."
        ]
        if raw_result.failed_wallet_count > 0:
            status_parts.append(f"{raw_result.failed_wallet_count} failed.")
        if raw_result.truncated_wallet_count > 0:
            status_parts.append(f"{raw_result.truncated_wallet_count} truncated by token page cap.")
        if raw_result.wallets_missing_total_usd_count > 0:
            status_parts.append(f"{raw_result.wallets_missing_total_usd_count} missing total USD.")
        self._status_label.setText(" ".join(status_parts))

    def _on_portfolio_load_error(self, error: object) -> None:
        message = str(error)
        self._status_label.setText(
            message if isinstance(error, ValidationError) else f"Could not load portfolio aggregate: {message}"
        )

    def _on_portfolio_load_finished(self) -> None:
        self._pending_load = None
        self._set_loading(False)

    def _set_loading(self, is_loading: bool, status_text: str | None = None) -> None:
        self._is_loading = is_loading
        enabled = not is_loading
        for widget in (
            self._chain_combo,
            self._hide_unverified_checkbox,
            self._hide_dust_checkbox,
            self._dust_threshold_input,
            self._refresh_watchlist_button,
            self._select_all_button,
            self._clear_selection_button,
            self._load_button,
            self._refresh_button,
            self._export_template_combo,
            self._entries_list,
        ):
            widget.setEnabled(enabled)
        self._export_csv_button.setEnabled(enabled and self._last_result is not None)
        self._export_json_button.setEnabled(enabled and self._last_result is not None)
        if status_text is not None:
            self._status_label.setText(status_text)

    def _on_export_csv_clicked(self) -> None:
        if self._last_result is None:
            self._status_label.setText("Load portfolio aggregate before exporting.")
            return
        raw_chain = self._chain_combo.currentData()
        chain_part = raw_chain if isinstance(raw_chain, str) else "all"
        suggested = f"portfolio-{chain_part}.csv"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Portfolio CSV",
            suggested,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_name:
            return
        path = write_portfolio_csv(
            self._last_result,
            Path(file_name),
            template=self._selected_export_template(),
        )
        self._status_label.setText(f"Portfolio CSV export saved: {path}")

    def _on_export_json_clicked(self) -> None:
        if self._last_result is None:
            self._status_label.setText("Load portfolio aggregate before exporting.")
            return
        raw_chain = self._chain_combo.currentData()
        chain_part = raw_chain if isinstance(raw_chain, str) else "all"
        suggested = f"portfolio-{chain_part}.json"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Portfolio JSON",
            suggested,
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return
        path = write_portfolio_json(
            self._last_result,
            Path(file_name),
            template=self._selected_export_template(),
        )
        self._status_label.setText(f"Portfolio JSON export saved: {path}")


def _entry_display_text(entry: WatchlistEntry) -> str:
    label_text = entry.label or "No label"
    return f"[{entry.chain.display_name}] {entry.address} | {label_text}"


def _render_result(result: PortfolioLoadResult) -> str:
    lines = [
        "Portfolio Aggregate Summary",
        f"- Selected wallets: {result.selected_wallet_count}",
        f"- Loaded wallets: {result.loaded_wallet_count}",
        f"- Failed wallets: {result.failed_wallet_count}",
        f"- Truncated wallets: {result.truncated_wallet_count}",
        f"- Wallets missing total USD: {result.wallets_missing_total_usd_count}",
        f"- Total USD (complete): {_fmt_decimal(result.total_usd)}",
        f"- Known USD (partial): {_fmt_decimal(result.known_total_usd)}",
        "",
        "Chain Aggregates:",
    ]
    if not result.chain_aggregates:
        lines.append("- none")
    else:
        for chain_aggregate in result.chain_aggregates:
            missing_note = ""
            if chain_aggregate.native_usd_missing_wallet_count > 0:
                missing_note = (
                    f", native USD missing in {chain_aggregate.native_usd_missing_wallet_count} wallet(s)"
                )
            lines.append(
                f"- {chain_aggregate.chain.display_name}: wallets={chain_aggregate.wallet_count}, "
                f"native={chain_aggregate.native_balance_total:.6f} {chain_aggregate.chain.native_symbol}, "
                f"native_usd={chain_aggregate.native_usd_total:.6f}{missing_note}"
            )

    lines.extend(["", "Token Aggregates:"])
    if not result.token_aggregates:
        lines.append("- none")
    else:
        max_rows = 200
        for token_aggregate in result.token_aggregates[:max_rows]:
            missing_note = ""
            if token_aggregate.usd_missing_wallet_count > 0:
                missing_note = f", usd-missing-wallets={token_aggregate.usd_missing_wallet_count}"
            lines.append(
                f"- [{token_aggregate.chain.display_name}] {token_aggregate.symbol} ({token_aggregate.name}) | "
                f"wallets={token_aggregate.wallet_count} | "
                f"balance={token_aggregate.total_balance:.6f} | "
                f"usd={token_aggregate.total_usd:.6f}{missing_note}"
            )
        remaining = len(result.token_aggregates) - max_rows
        if remaining > 0:
            lines.append(f"- ... {remaining} more aggregate token row(s) omitted.")

    lines.extend(["", "Wallet Results:"])
    if not result.wallet_results:
        lines.append("- none")
    else:
        for wallet in result.wallet_results:
            lines.append(_render_wallet_result(wallet))
    return "\n".join(lines)


def _render_wallet_result(wallet: PortfolioWalletResult) -> str:
    label_text = wallet.entry.label or "No label"
    if wallet.error is not None:
        return f"- [{wallet.entry.chain.display_name}] {wallet.entry.address} | {label_text} | ERROR: {wallet.error}"
    if wallet.overview is None:
        return f"- [{wallet.entry.chain.display_name}] {wallet.entry.address} | {label_text} | no overview data"
    truncation_note = " | truncated" if wallet.overview.token_balances_truncated else ""
    return (
        f"- [{wallet.entry.chain.display_name}] {wallet.entry.address} | {label_text} | "
        f"native={wallet.overview.native_balance:.6f} {wallet.entry.chain.native_symbol} | "
        f"total_usd={_fmt_decimal(wallet.overview.total_usd)}{truncation_note}"
    )


def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6f}"
