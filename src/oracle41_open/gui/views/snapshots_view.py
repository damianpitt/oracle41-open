from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
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

from oracle41_open.core.models import Chain, ValidationError
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.snapshot_compare_service import SnapshotComparisonResult
from oracle41_open.exports import SnapshotExportTemplate, write_snapshot_csv, write_snapshot_json
from oracle41_open.storage.db import WalletSnapshot

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


class SnapshotsView(QWidget):
    def __init__(
        self,
        container: AppContainer,
        open_wallet_in_overview: Callable[[str, Chain], None],
    ) -> None:
        super().__init__()
        self._container = container
        self._open_wallet_in_overview = open_wallet_in_overview
        self._snapshots: list[WalletSnapshot] = []

        self._chain_combo = QComboBox(self)
        for chain in Chain:
            self._chain_combo.addItem(chain.display_name, chain.value)

        self._address_input = QLineEdit(self)
        self._address_input.setPlaceholderText("0x…")
        self._limit_input = QLineEdit(self)
        self._limit_input.setText("20")
        self._limit_input.setPlaceholderText("1-200")
        self._prune_keep_latest_input = QLineEdit(self)
        self._prune_keep_latest_input.setText("20")
        self._prune_keep_latest_input.setPlaceholderText("0-200")

        self._refresh_button = QPushButton("Load Snapshots", self)
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        self._delete_button = QPushButton("Delete Selected", self)
        self._delete_button.clicked.connect(self._on_delete_selected_clicked)
        self._delete_button.setEnabled(False)
        self._prune_button = QPushButton("Prune", self)
        self._prune_button.clicked.connect(self._on_prune_clicked)
        self._open_overview_button = QPushButton("Open In Overview", self)
        self._open_overview_button.clicked.connect(self._on_open_in_overview_clicked)
        self._open_overview_button.setEnabled(False)
        self._compare_button = QPushButton("Compare Selected", self)
        self._compare_button.clicked.connect(self._on_compare_selected_clicked)
        self._compare_button.setEnabled(False)

        self._export_template_combo = QComboBox(self)
        self._export_template_combo.addItem("Summary", SnapshotExportTemplate.SUMMARY.value)
        self._export_template_combo.addItem("Detailed", SnapshotExportTemplate.DETAILED.value)
        self._export_csv_button = QPushButton("Export CSV", self)
        self._export_csv_button.clicked.connect(self._on_export_csv_clicked)
        self._export_csv_button.setEnabled(False)
        self._export_json_button = QPushButton("Export JSON", self)
        self._export_json_button.clicked.connect(self._on_export_json_clicked)
        self._export_json_button.setEnabled(False)

        self._status_label = QLabel("Snapshots manager ready.", self)
        self._status_label.setWordWrap(True)

        self._snapshots_list = QListWidget(self)
        self._snapshots_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._snapshots_list.itemSelectionChanged.connect(self._on_selection_changed)

        self._details = QTextEdit(self)
        self._details.setReadOnly(True)
        self._details.setMinimumHeight(220)
        self._details.setPlaceholderText("Snapshot details and comparisons appear here.")

        self._init_layout()
        self._apply_default_chain()

    def _init_layout(self) -> None:
        controls_box = QGroupBox("Snapshot Manager")
        form = QFormLayout()
        form.addRow("Chain", self._chain_combo)
        form.addRow("Wallet Address", self._address_input)
        form.addRow("List Limit", self._limit_input)

        row_actions = QHBoxLayout()
        row_actions.addWidget(self._refresh_button)
        row_actions.addWidget(self._delete_button)
        row_actions.addWidget(self._open_overview_button)
        row_actions.addWidget(self._compare_button)
        row_actions.addStretch(1)
        form.addRow("", row_actions)

        row_prune = QHBoxLayout()
        row_prune.addWidget(QLabel("Keep latest", self))
        row_prune.addWidget(self._prune_keep_latest_input)
        row_prune.addWidget(self._prune_button)
        row_prune.addStretch(1)
        form.addRow("Prune", row_prune)

        row_export = QHBoxLayout()
        row_export.addWidget(QLabel("Template", self))
        row_export.addWidget(self._export_template_combo)
        row_export.addWidget(self._export_csv_button)
        row_export.addWidget(self._export_json_button)
        row_export.addStretch(1)
        form.addRow("Export", row_export)
        controls_box.setLayout(form)

        root = QVBoxLayout()
        root.addWidget(controls_box)
        root.addWidget(self._status_label)
        root.addWidget(self._snapshots_list, stretch=1)
        root.addWidget(self._details, stretch=1)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(root)

    def _apply_default_chain(self) -> None:
        settings = self._container.settings_store.load()
        index = self._chain_combo.findData(settings.selected_chain.value)
        if index >= 0:
            self._chain_combo.setCurrentIndex(index)

    def _selected_chain(self) -> Chain:
        raw_chain = self._chain_combo.currentData()
        if not isinstance(raw_chain, str):
            return Chain.ETHEREUM
        try:
            return Chain(raw_chain)
        except ValueError:
            return Chain.ETHEREUM

    def _selected_template(self) -> SnapshotExportTemplate:
        raw = self._export_template_combo.currentData()
        if not isinstance(raw, str):
            return SnapshotExportTemplate.SUMMARY
        try:
            return SnapshotExportTemplate(raw)
        except ValueError:
            return SnapshotExportTemplate.SUMMARY

    def _validated_address(self) -> str:
        normalized = AddressValidator.normalized(self._address_input.text())
        if not normalized:
            raise ValidationError("Wallet address cannot be empty.")
        validation_error = AddressValidator.validation_error(normalized)
        if validation_error is not None:
            raise ValidationError(validation_error)
        return normalized

    def _parse_limit(self) -> int:
        raw = self._limit_input.text().strip()
        if not raw:
            return 20
        if not raw.isdigit():
            raise ValidationError("Snapshot list limit must be a positive integer.")
        parsed = int(raw)
        if parsed <= 0:
            raise ValidationError("Snapshot list limit must be a positive integer.")
        return min(parsed, 200)

    def _parse_prune_keep_latest(self) -> int:
        raw = self._prune_keep_latest_input.text().strip()
        if not raw:
            return 20
        if not raw.isdigit():
            raise ValidationError("Prune keep-latest must be 0 or a positive integer.")
        parsed = int(raw)
        if parsed < 0:
            raise ValidationError("Prune keep-latest must be 0 or a positive integer.")
        return min(parsed, 200)

    def _on_refresh_clicked(self) -> None:
        self._reload_snapshots()

    def _on_delete_selected_clicked(self) -> None:
        selected = self._selected_snapshots()
        if not selected:
            self._status_label.setText("Select at least one snapshot first.")
            return
        removed = 0
        for snapshot in selected:
            if self._container.snapshots_repository.delete_snapshot(snapshot.id):
                removed += 1
        self._reload_snapshots(selected_ids=set())
        self._status_label.setText(f"Deleted {removed} snapshot{'s' if removed != 1 else ''}.")

    def _on_prune_clicked(self) -> None:
        try:
            address = self._validated_address()
            removed = self._container.snapshots_repository.prune_snapshots(
                address=address,
                chain=self._selected_chain(),
                keep_latest=self._parse_prune_keep_latest(),
            )
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not prune snapshots: {error}")
            return

        self._reload_snapshots()
        self._status_label.setText(f"Pruned {removed} snapshot{'s' if removed != 1 else ''}.")

    def _on_open_in_overview_clicked(self) -> None:
        selected = self._selected_snapshots()
        if not selected:
            self._status_label.setText("Select a snapshot first.")
            return
        snapshot = selected[0]
        self._open_wallet_in_overview(snapshot.address, snapshot.chain)
        self._status_label.setText("Opening snapshot wallet in Overview.")

    def _on_compare_selected_clicked(self) -> None:
        selected = self._selected_snapshots()
        if len(selected) != 2:
            self._status_label.setText("Select exactly two snapshots to compare.")
            return
        try:
            result = self._container.snapshot_compare_service.compare_snapshots(selected[0], selected[1])
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not compare snapshots: {error}")
            return
        self._details.setPlainText(_render_comparison(result))
        self._status_label.setText(
            f"Compared snapshots {result.older_snapshot.id} -> {result.newer_snapshot.id}."
        )

    def _on_export_csv_clicked(self) -> None:
        if not self._snapshots:
            self._status_label.setText("No snapshots to export.")
            return
        chain = self._selected_chain()
        address = AddressValidator.normalized(self._address_input.text())
        default_name = f"snapshots-{chain.value}-{address[:10] if address else 'wallet'}.csv"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Snapshots CSV",
            default_name,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_name:
            return
        path = write_snapshot_csv(
            self._snapshots,
            Path(file_name),
            template=self._selected_template(),
        )
        self._status_label.setText(f"Snapshot CSV export saved: {path}")

    def _on_export_json_clicked(self) -> None:
        if not self._snapshots:
            self._status_label.setText("No snapshots to export.")
            return
        chain = self._selected_chain()
        address = AddressValidator.normalized(self._address_input.text())
        default_name = f"snapshots-{chain.value}-{address[:10] if address else 'wallet'}.json"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Snapshots JSON",
            default_name,
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return
        path = write_snapshot_json(
            self._snapshots,
            Path(file_name),
            template=self._selected_template(),
        )
        self._status_label.setText(f"Snapshot JSON export saved: {path}")

    def _on_selection_changed(self) -> None:
        selected = self._selected_snapshots()
        selection_count = len(selected)
        self._delete_button.setEnabled(selection_count >= 1)
        self._open_overview_button.setEnabled(selection_count >= 1)
        self._compare_button.setEnabled(selection_count == 2)
        if selection_count == 1:
            self._details.setPlainText(_render_snapshot(selected[0]))
        elif selection_count == 2:
            self._details.setPlainText("Two snapshots selected. Click Compare Selected.")
        else:
            self._details.clear()

    def _selected_snapshots(self) -> list[WalletSnapshot]:
        selected: list[WalletSnapshot] = []
        for item in self._snapshots_list.selectedItems():
            raw = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(raw, WalletSnapshot):
                selected.append(raw)
        return selected

    def _reload_snapshots(self, selected_ids: set[int] | None = None) -> None:
        current_selected_ids = selected_ids
        if current_selected_ids is None:
            current_selected_ids = {snapshot.id for snapshot in self._selected_snapshots()}

        try:
            address = self._validated_address()
            snapshots = self._container.snapshots_repository.list_snapshots(
                address=address,
                chain=self._selected_chain(),
                limit=self._parse_limit(),
            )
        except ValidationError as error:
            self._status_label.setText(str(error))
            self._snapshots_list.clear()
            self._snapshots = []
            self._export_csv_button.setEnabled(False)
            self._export_json_button.setEnabled(False)
            self._delete_button.setEnabled(False)
            self._open_overview_button.setEnabled(False)
            self._compare_button.setEnabled(False)
            self._details.clear()
            return
        except Exception as error:
            self._status_label.setText(f"Could not load snapshots: {error}")
            return

        self._snapshots = snapshots
        self._snapshots_list.clear()
        to_select: list[QListWidgetItem] = []
        for snapshot in snapshots:
            item = QListWidgetItem(_snapshot_list_text(snapshot))
            item.setData(Qt.ItemDataRole.UserRole, snapshot)
            self._snapshots_list.addItem(item)
            if snapshot.id in current_selected_ids:
                to_select.append(item)

        for item in to_select:
            item.setSelected(True)
        self._export_csv_button.setEnabled(bool(snapshots))
        self._export_json_button.setEnabled(bool(snapshots))
        self._on_selection_changed()
        self._status_label.setText(f"Loaded {len(snapshots)} snapshot{'s' if len(snapshots) != 1 else ''}.")


def _snapshot_list_text(snapshot: WalletSnapshot) -> str:
    label = snapshot.label or "no-label"
    return (
        f"#{snapshot.id} | {snapshot.captured_at.strftime('%Y-%m-%d %H:%M UTC')} | "
        f"{label} | total={_fmt_decimal(snapshot.total_usd)} | tokens={snapshot.token_count}"
    )


def _render_snapshot(snapshot: WalletSnapshot) -> str:
    lines = [
        f"Snapshot ID: {snapshot.id}",
        f"Captured At: {snapshot.captured_at.isoformat()}",
        f"Address: {snapshot.address}",
        f"Chain: {snapshot.chain.display_name}",
        f"Label: {snapshot.label or 'n/a'}",
        f"Native Balance: {snapshot.native_balance}",
        f"Native Price USD: {_fmt_decimal(snapshot.native_price_usd)}",
        f"Total USD: {_fmt_decimal(snapshot.total_usd)}",
        f"Token Count: {snapshot.token_count}",
    ]
    payload_page_count = snapshot.payload.get("token_balance_page_count")
    payload_truncated = snapshot.payload.get("token_balances_truncated")
    if isinstance(payload_page_count, int):
        lines.append(f"Token Balance Page Count: {payload_page_count}")
    if isinstance(payload_truncated, bool):
        lines.append(f"Token Balances Truncated: {'yes' if payload_truncated else 'no'}")
    return "\n".join(lines)


def _render_comparison(result: SnapshotComparisonResult) -> str:
    lines = [
        f"Snapshot Compare: #{result.older_snapshot.id} -> #{result.newer_snapshot.id}",
        f"Wallet: {result.older_snapshot.address}",
        f"Chain: {result.older_snapshot.chain.display_name}",
        "",
        f"Older Captured At: {result.older_snapshot.captured_at.isoformat()}",
        f"Newer Captured At: {result.newer_snapshot.captured_at.isoformat()}",
        f"Native Delta: {result.native_balance_delta}",
        f"Native USD Delta: {_fmt_decimal(result.native_usd_delta)}",
        f"Total USD Delta: {_fmt_decimal(result.total_usd_delta)}",
        f"Token Count Delta: {result.token_count_delta}",
        f"Added Tokens: {result.added_token_count}",
        f"Removed Tokens: {result.removed_token_count}",
        f"Changed Tokens: {result.changed_token_count}",
        "",
        "Token Deltas:",
    ]
    if not result.token_deltas:
        lines.append("- none")
        return "\n".join(lines)
    for delta in result.token_deltas:
        lines.append(
            f"- [{delta.change_type}] {delta.symbol} | "
            f"before={delta.before_balance} -> after={delta.after_balance} | "
            f"delta={delta.balance_delta} | usd_delta={_fmt_decimal(delta.usd_delta)}"
        )
    return "\n".join(lines)


def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6f}"
