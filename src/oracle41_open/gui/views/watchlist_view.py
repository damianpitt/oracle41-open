"""Manage saved wallet addresses and labels.

The view adds, edits, removes, and exports read-only watchlist entries.
Address validation and storage are delegated to the watchlist service.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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
    QVBoxLayout,
    QWidget,
)

from oracle41_open.core.models import Chain, ValidationError, WatchlistEntry
from oracle41_open.exports import write_watchlist_csv, write_watchlist_json

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


class WatchlistView(QWidget):
    def __init__(
        self,
        container: AppContainer,
        open_wallet_in_overview: Callable[[str, Chain], None],
    ) -> None:
        super().__init__()
        self._container = container
        self._open_wallet_in_overview = open_wallet_in_overview
        self._entries: list[WatchlistEntry] = []

        self._entry_chain_combo = QComboBox(self)
        for chain in Chain:
            self._entry_chain_combo.addItem(chain.display_name, chain.value)

        self._filter_chain_combo = QComboBox(self)
        self._filter_chain_combo.addItem("All chains", "all")
        for chain in Chain:
            self._filter_chain_combo.addItem(chain.display_name, chain.value)
        self._filter_chain_combo.currentIndexChanged.connect(self._on_filter_changed)

        self._address_input = QLineEdit(self)
        self._address_input.setPlaceholderText("0x…")
        self._label_input = QLineEdit(self)
        self._label_input.setPlaceholderText("Optional label")

        self._add_button = QPushButton("Add / Update", self)
        self._add_button.clicked.connect(self._on_add_clicked)
        self._remove_button = QPushButton("Remove Selected", self)
        self._remove_button.clicked.connect(self._on_remove_selected_clicked)
        self._remove_button.setEnabled(False)
        self._open_overview_button = QPushButton("Open In Overview", self)
        self._open_overview_button.clicked.connect(self._on_open_in_overview_clicked)
        self._open_overview_button.setEnabled(False)
        self._refresh_button = QPushButton("Refresh List", self)
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        self._export_csv_button = QPushButton("Export CSV", self)
        self._export_csv_button.clicked.connect(self._on_export_csv_clicked)
        self._export_csv_button.setEnabled(False)
        self._export_json_button = QPushButton("Export JSON", self)
        self._export_json_button.clicked.connect(self._on_export_json_clicked)
        self._export_json_button.setEnabled(False)

        self._status_label = QLabel("Watchlist ready.", self)
        self._status_label.setWordWrap(True)

        self._entries_list = QListWidget(self)
        self._entries_list.itemSelectionChanged.connect(self._on_selection_changed)

        self._init_layout()
        self._reload_entries()

    def _init_layout(self) -> None:
        editor_box = QGroupBox("Watchlist Entry")
        editor_form = QFormLayout()
        editor_form.addRow("Chain", self._entry_chain_combo)
        editor_form.addRow("Wallet Address", self._address_input)
        editor_form.addRow("Label", self._label_input)

        editor_buttons = QHBoxLayout()
        editor_buttons.addWidget(self._add_button)
        editor_buttons.addWidget(self._remove_button)
        editor_buttons.addWidget(self._open_overview_button)
        editor_buttons.addStretch(1)
        editor_form.addRow("", editor_buttons)
        editor_box.setLayout(editor_form)

        list_box = QGroupBox("Tracked Wallets")
        list_form = QFormLayout()
        list_form.addRow("Filter", self._filter_chain_combo)
        list_buttons = QHBoxLayout()
        list_buttons.addWidget(self._refresh_button)
        list_buttons.addWidget(self._export_csv_button)
        list_buttons.addWidget(self._export_json_button)
        list_buttons.addStretch(1)
        list_form.addRow("", list_buttons)
        list_box.setLayout(list_form)

        root = QVBoxLayout()
        root.addWidget(editor_box)
        root.addWidget(list_box)
        root.addWidget(self._status_label)
        root.addWidget(self._entries_list, stretch=1)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(root)

    def _selected_chain(self) -> Chain:
        raw_chain = self._entry_chain_combo.currentData()
        if not isinstance(raw_chain, str):
            return Chain.ETHEREUM
        try:
            return Chain(raw_chain)
        except ValueError:
            return Chain.ETHEREUM

    def _selected_filter_chain(self) -> Chain | None:
        raw_chain = self._filter_chain_combo.currentData()
        if not isinstance(raw_chain, str) or raw_chain == "all":
            return None
        try:
            return Chain(raw_chain)
        except ValueError:
            return None

    def _on_filter_changed(self) -> None:
        self._reload_entries()

    def _on_refresh_clicked(self) -> None:
        self._reload_entries()
        self._status_label.setText("Watchlist refreshed.")

    def _on_export_csv_clicked(self) -> None:
        entries = self._entries
        if not entries:
            self._status_label.setText("No watchlist entries available to export.")
            return
        chain = self._selected_filter_chain()
        chain_part = chain.value if chain is not None else "all-chains"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Watchlist CSV",
            f"watchlist-{chain_part}.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_name:
            return
        path = write_watchlist_csv(entries, Path(file_name))
        self._status_label.setText(f"Watchlist CSV export saved: {path}")

    def _on_export_json_clicked(self) -> None:
        entries = self._entries
        if not entries:
            self._status_label.setText("No watchlist entries available to export.")
            return
        chain = self._selected_filter_chain()
        chain_part = chain.value if chain is not None else "all-chains"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Watchlist JSON",
            f"watchlist-{chain_part}.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return
        path = write_watchlist_json(entries, Path(file_name))
        self._status_label.setText(f"Watchlist JSON export saved: {path}")

    def _on_add_clicked(self) -> None:
        try:
            created = self._container.watchlist_service.upsert_entry(
                address=self._address_input.text(),
                chain=self._selected_chain(),
                label=self._label_input.text(),
            )
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not save watchlist entry: {error}")
            return

        self._status_label.setText("Watchlist entry saved.")
        self._reload_entries(selected_entry_id=created.id)

    def _on_remove_selected_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._status_label.setText("Select a watchlist entry first.")
            return
        removed = self._container.watchlist_service.remove_entry(entry.address, entry.chain)
        if removed:
            self._status_label.setText("Watchlist entry removed.")
        else:
            self._status_label.setText("Selected entry was already removed.")
        self._reload_entries()

    def _on_open_in_overview_clicked(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self._status_label.setText("Select a watchlist entry first.")
            return
        self._open_wallet_in_overview(entry.address, entry.chain)
        self._status_label.setText("Opening wallet in Overview.")

    def _on_selection_changed(self) -> None:
        entry = self._selected_entry()
        has_selection = entry is not None
        self._remove_button.setEnabled(has_selection)
        self._open_overview_button.setEnabled(has_selection)
        if entry is None:
            return

        chain_index = self._entry_chain_combo.findData(entry.chain.value)
        if chain_index >= 0:
            self._entry_chain_combo.setCurrentIndex(chain_index)
        self._address_input.setText(entry.address)
        self._label_input.setText(entry.label or "")

    def _selected_entry(self) -> WatchlistEntry | None:
        current = self._entries_list.currentItem()
        if current is None:
            return None
        raw_entry = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw_entry, WatchlistEntry):
            return None
        return raw_entry

    def _reload_entries(self, selected_entry_id: int | None = None) -> None:
        if selected_entry_id is None:
            current = self._selected_entry()
            selected_entry_id = current.id if current is not None else None

        entries = self._container.watchlist_service.list_entries(chain=self._selected_filter_chain())
        self._entries = entries
        self._entries_list.clear()
        selected_item: QListWidgetItem | None = None
        for entry in entries:
            item = QListWidgetItem(_entry_display_text(entry))
            item.setData(Qt.ItemDataRole.UserRole, entry)
            self._entries_list.addItem(item)
            if selected_entry_id is not None and entry.id == selected_entry_id:
                selected_item = item

        if selected_item is not None:
            self._entries_list.setCurrentItem(selected_item)
        has_entries = bool(entries)
        self._export_csv_button.setEnabled(has_entries)
        self._export_json_button.setEnabled(has_entries)
        self._on_selection_changed()


def _entry_display_text(entry: WatchlistEntry) -> str:
    label_text = entry.label or "No label"
    timestamp = entry.created_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"[{entry.chain.display_name}] {entry.address} | {label_text} | added {timestamp}"
