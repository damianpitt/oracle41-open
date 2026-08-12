"""Manage wallet notes, tags, and saved filter views.

The view edits local metadata and lets users reuse filter settings across analysis screens.
All records are stored through SQLite repositories in the application container.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
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

from oracle41_open.core.models import ActivityCategory, Chain, ValidationError
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.exports import SnapshotExportTemplate, write_snapshot_csv, write_snapshot_json
from oracle41_open.storage.db import SavedView, WalletNote, WalletSnapshot

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


class NotesSavedViewsView(QWidget):
    def __init__(
        self,
        container: AppContainer,
        open_activity_with_filters: Callable[[Chain, dict[str, object]], None],
        open_token_detail_with_filters: Callable[[Chain, dict[str, object]], None],
    ) -> None:
        super().__init__()
        self._container = container
        self._open_activity_with_filters = open_activity_with_filters
        self._open_token_detail_with_filters = open_token_detail_with_filters

        self._note_chain_combo = QComboBox(self)
        for chain in Chain:
            self._note_chain_combo.addItem(chain.display_name, chain.value)
        self._note_address_input = QLineEdit(self)
        self._note_address_input.setPlaceholderText("0x…")
        self._note_body_input = QTextEdit(self)
        self._note_body_input.setPlaceholderText("Wallet note")
        self._note_body_input.setFixedHeight(90)
        self._note_tags_input = QLineEdit(self)
        self._note_tags_input.setPlaceholderText("comma,separated,tags")

        self._save_note_button = QPushButton("Save Note", self)
        self._save_note_button.clicked.connect(self._on_save_note_clicked)
        self._load_note_button = QPushButton("Load Note", self)
        self._load_note_button.clicked.connect(self._on_load_note_clicked)
        self._delete_note_button = QPushButton("Delete Note", self)
        self._delete_note_button.clicked.connect(self._on_delete_note_clicked)
        self._delete_note_button.setEnabled(False)
        self._refresh_notes_button = QPushButton("Refresh Notes", self)
        self._refresh_notes_button.clicked.connect(self._on_refresh_notes_clicked)

        self._notes_list = QListWidget(self)
        self._notes_list.itemSelectionChanged.connect(self._on_notes_selection_changed)

        self._view_name_input = QLineEdit(self)
        self._view_name_input.setPlaceholderText("Saved view name")
        self._view_chain_combo = QComboBox(self)
        for chain in Chain:
            self._view_chain_combo.addItem(chain.display_name, chain.value)
        self._view_wallet_address_input = QLineEdit(self)
        self._view_wallet_address_input.setPlaceholderText("Optional wallet address (0x...)")
        self._view_token_address_input = QLineEdit(self)
        self._view_token_address_input.setPlaceholderText("Optional token contract (0x...)")
        self._view_category_combo = QComboBox(self)
        self._view_category_combo.addItem("All", "all")
        self._view_category_combo.addItem("ERC20", ActivityCategory.ERC20.value)
        self._view_category_combo.addItem("ERC721", ActivityCategory.ERC721.value)
        self._view_category_combo.addItem("ERC1155", ActivityCategory.ERC1155.value)
        self._view_category_combo.addItem("External", ActivityCategory.EXTERNAL.value)
        self._view_category_combo.addItem("Internal Transfer", ActivityCategory.INTERNAL_TRANSFER.value)
        self._view_category_combo.addItem("Approval", ActivityCategory.APPROVAL.value)
        self._view_direction_combo = QComboBox(self)
        self._view_direction_combo.addItem("Any", "any")
        self._view_direction_combo.addItem("Inbound", "inbound")
        self._view_direction_combo.addItem("Outbound", "outbound")
        self._view_min_usd_input = QLineEdit(self)
        self._view_min_usd_input.setPlaceholderText("Optional minimum USD value")
        self._view_from_block_input = QLineEdit(self)
        self._view_from_block_input.setPlaceholderText("Optional start block")
        self._view_asset_query_input = QLineEdit(self)
        self._view_asset_query_input.setPlaceholderText("Optional symbol/address search")
        self._view_verified_only = QCheckBox("Verified only", self)
        self._view_include_approvals = QCheckBox("Include approvals", self)
        self._view_include_approvals.setChecked(True)

        self._save_view_button = QPushButton("Save View", self)
        self._save_view_button.clicked.connect(self._on_save_view_clicked)
        self._delete_view_button = QPushButton("Delete View", self)
        self._delete_view_button.clicked.connect(self._on_delete_view_clicked)
        self._delete_view_button.setEnabled(False)
        self._refresh_views_button = QPushButton("Refresh Views", self)
        self._refresh_views_button.clicked.connect(self._on_refresh_views_clicked)
        self._apply_activity_button = QPushButton("Apply To Activity", self)
        self._apply_activity_button.clicked.connect(self._on_apply_activity_clicked)
        self._apply_token_detail_button = QPushButton("Apply To Token Detail", self)
        self._apply_token_detail_button.clicked.connect(self._on_apply_token_detail_clicked)

        self._views_list = QListWidget(self)
        self._views_list.itemSelectionChanged.connect(self._on_views_selection_changed)

        self._snapshot_chain_combo = QComboBox(self)
        for chain in Chain:
            self._snapshot_chain_combo.addItem(chain.display_name, chain.value)
        self._snapshot_address_input = QLineEdit(self)
        self._snapshot_address_input.setPlaceholderText("0x…")
        self._snapshot_limit_input = QLineEdit(self)
        self._snapshot_limit_input.setText("20")
        self._snapshot_limit_input.setPlaceholderText("1-200")
        self._snapshot_template_combo = QComboBox(self)
        self._snapshot_template_combo.addItem("Summary", SnapshotExportTemplate.SUMMARY.value)
        self._snapshot_template_combo.addItem("Detailed", SnapshotExportTemplate.DETAILED.value)
        self._export_snapshots_csv_button = QPushButton("Export Snapshots CSV", self)
        self._export_snapshots_csv_button.clicked.connect(self._on_export_snapshots_csv_clicked)
        self._export_snapshots_json_button = QPushButton("Export Snapshots JSON", self)
        self._export_snapshots_json_button.clicked.connect(self._on_export_snapshots_json_clicked)

        self._status_label = QLabel("Notes and saved views ready.", self)
        self._status_label.setWordWrap(True)

        self._init_layout()
        self._reload_notes()
        self._reload_views()

    def _init_layout(self) -> None:
        notes_box = QGroupBox("Wallet Notes")
        notes_form = QFormLayout()
        notes_form.addRow("Chain", self._note_chain_combo)
        notes_form.addRow("Wallet Address", self._note_address_input)
        notes_form.addRow("Note", self._note_body_input)
        notes_form.addRow("Tags", self._note_tags_input)

        notes_buttons = QHBoxLayout()
        notes_buttons.addWidget(self._save_note_button)
        notes_buttons.addWidget(self._load_note_button)
        notes_buttons.addWidget(self._delete_note_button)
        notes_buttons.addWidget(self._refresh_notes_button)
        notes_buttons.addStretch(1)
        notes_form.addRow("", notes_buttons)
        notes_box.setLayout(notes_form)

        views_box = QGroupBox("Saved Views / Quick Filters")
        views_form = QFormLayout()
        views_form.addRow("Name", self._view_name_input)
        views_form.addRow("Chain", self._view_chain_combo)
        views_form.addRow("Wallet", self._view_wallet_address_input)
        views_form.addRow("Token", self._view_token_address_input)
        views_form.addRow("Category", self._view_category_combo)
        views_form.addRow("Direction", self._view_direction_combo)
        views_form.addRow("Min USD", self._view_min_usd_input)
        views_form.addRow("From Block", self._view_from_block_input)
        views_form.addRow("Asset Filter", self._view_asset_query_input)
        views_form.addRow("", self._view_verified_only)
        views_form.addRow("", self._view_include_approvals)

        views_buttons = QHBoxLayout()
        views_buttons.addWidget(self._save_view_button)
        views_buttons.addWidget(self._delete_view_button)
        views_buttons.addWidget(self._refresh_views_button)
        views_buttons.addStretch(1)
        views_form.addRow("", views_buttons)

        apply_buttons = QHBoxLayout()
        apply_buttons.addWidget(self._apply_activity_button)
        apply_buttons.addWidget(self._apply_token_detail_button)
        apply_buttons.addStretch(1)
        views_form.addRow("", apply_buttons)
        views_box.setLayout(views_form)

        snapshots_box = QGroupBox("Snapshot Export")
        snapshots_form = QFormLayout()
        snapshots_form.addRow("Chain", self._snapshot_chain_combo)
        snapshots_form.addRow("Wallet Address", self._snapshot_address_input)
        snapshots_form.addRow("Limit", self._snapshot_limit_input)
        snapshots_form.addRow("Template", self._snapshot_template_combo)
        snapshot_buttons = QHBoxLayout()
        snapshot_buttons.addWidget(self._export_snapshots_csv_button)
        snapshot_buttons.addWidget(self._export_snapshots_json_button)
        snapshot_buttons.addStretch(1)
        snapshots_form.addRow("", snapshot_buttons)
        snapshots_box.setLayout(snapshots_form)

        root = QVBoxLayout()
        root.addWidget(notes_box)
        root.addWidget(self._notes_list)
        root.addWidget(views_box)
        root.addWidget(self._views_list)
        root.addWidget(snapshots_box)
        root.addWidget(self._status_label)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(root)

    def _selected_note_chain(self) -> Chain:
        raw = self._note_chain_combo.currentData()
        if not isinstance(raw, str):
            return Chain.ETHEREUM
        try:
            return Chain(raw)
        except ValueError:
            return Chain.ETHEREUM

    def _selected_view_chain(self) -> Chain:
        raw = self._view_chain_combo.currentData()
        if not isinstance(raw, str):
            return Chain.ETHEREUM
        try:
            return Chain(raw)
        except ValueError:
            return Chain.ETHEREUM

    def _selected_snapshot_chain(self) -> Chain:
        raw = self._snapshot_chain_combo.currentData()
        if not isinstance(raw, str):
            return Chain.ETHEREUM
        try:
            return Chain(raw)
        except ValueError:
            return Chain.ETHEREUM

    def _selected_snapshot_template(self) -> SnapshotExportTemplate:
        raw = self._snapshot_template_combo.currentData()
        if not isinstance(raw, str):
            return SnapshotExportTemplate.SUMMARY
        try:
            return SnapshotExportTemplate(raw)
        except ValueError:
            return SnapshotExportTemplate.SUMMARY

    def _on_save_note_clicked(self) -> None:
        try:
            address = _normalize_required_address(self._note_address_input.text())
            note = self._container.wallet_notes_repository.upsert_note(
                address=address,
                chain=self._selected_note_chain(),
                note=self._note_body_input.toPlainText(),
                tags=_parse_tags_csv(self._note_tags_input.text()),
            )
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not save note: {error}")
            return
        self._status_label.setText("Wallet note saved.")
        self._reload_notes(selected_note_id=note.id)

    def _on_load_note_clicked(self) -> None:
        try:
            address = _normalize_required_address(self._note_address_input.text())
            note = self._container.wallet_notes_repository.get_note(
                address=address,
                chain=self._selected_note_chain(),
            )
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not load note: {error}")
            return
        if note is None:
            self._status_label.setText("No note found for this wallet/chain.")
            return
        self._apply_note_to_form(note)
        self._status_label.setText("Wallet note loaded.")

    def _on_delete_note_clicked(self) -> None:
        selected = self._selected_note()
        if selected is not None:
            address = selected.address
            chain = selected.chain
        else:
            try:
                address = _normalize_required_address(self._note_address_input.text())
            except ValidationError as error:
                self._status_label.setText(str(error))
                return
            chain = self._selected_note_chain()

        removed = self._container.wallet_notes_repository.delete_note(address=address, chain=chain)
        if removed:
            self._status_label.setText("Wallet note deleted.")
        else:
            self._status_label.setText("Wallet note not found.")
        self._reload_notes()

    def _on_refresh_notes_clicked(self) -> None:
        self._reload_notes()
        self._status_label.setText("Notes list refreshed.")

    def _selected_note(self) -> WalletNote | None:
        current = self._notes_list.currentItem()
        if current is None:
            return None
        raw_note = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw_note, WalletNote):
            return None
        return raw_note

    def _reload_notes(self, selected_note_id: int | None = None) -> None:
        if selected_note_id is None:
            current = self._selected_note()
            selected_note_id = current.id if current is not None else None

        notes = self._container.wallet_notes_repository.list_notes()
        self._notes_list.clear()
        selected_item: QListWidgetItem | None = None
        for note in notes:
            item = QListWidgetItem(_note_display_text(note))
            item.setData(Qt.ItemDataRole.UserRole, note)
            self._notes_list.addItem(item)
            if selected_note_id is not None and note.id == selected_note_id:
                selected_item = item

        if selected_item is not None:
            self._notes_list.setCurrentItem(selected_item)
        self._delete_note_button.setEnabled(self._selected_note() is not None)

    def _on_notes_selection_changed(self) -> None:
        note = self._selected_note()
        self._delete_note_button.setEnabled(note is not None)
        if note is None:
            return
        self._apply_note_to_form(note)

    def _apply_note_to_form(self, note: WalletNote) -> None:
        chain_index = self._note_chain_combo.findData(note.chain.value)
        if chain_index >= 0:
            self._note_chain_combo.setCurrentIndex(chain_index)
        self._note_address_input.setText(note.address)
        self._note_body_input.setPlainText(note.note)
        self._note_tags_input.setText(", ".join(note.tags))

    def _on_save_view_clicked(self) -> None:
        try:
            view = self._container.saved_views_repository.upsert_view(
                name=self._view_name_input.text(),
                chain=self._selected_view_chain(),
                filters=self._build_filters_from_inputs(),
            )
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not save view: {error}")
            return
        self._status_label.setText("Saved view updated.")
        self._reload_views(selected_view_id=view.id)

    def _on_delete_view_clicked(self) -> None:
        selected = self._selected_saved_view()
        name = selected.name if selected is not None else self._view_name_input.text().strip()
        if not name:
            self._status_label.setText("Enter or select a saved view name.")
            return
        removed = self._container.saved_views_repository.delete_view(name)
        if removed:
            self._status_label.setText("Saved view deleted.")
        else:
            self._status_label.setText("Saved view not found.")
        self._reload_views()

    def _on_refresh_views_clicked(self) -> None:
        self._reload_views()
        self._status_label.setText("Saved views refreshed.")

    def _on_apply_activity_clicked(self) -> None:
        try:
            chain, filters = self._selected_or_staged_filters()
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        self._open_activity_with_filters(chain, filters)
        self._status_label.setText("Quick filters sent to Activity.")

    def _on_apply_token_detail_clicked(self) -> None:
        try:
            chain, filters = self._selected_or_staged_filters()
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        self._open_token_detail_with_filters(chain, filters)
        self._status_label.setText("Quick filters sent to Token Detail.")

    def _on_export_snapshots_csv_clicked(self) -> None:
        try:
            snapshots = self._load_snapshots_for_export()
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not load snapshots: {error}")
            return
        if not snapshots:
            self._status_label.setText("No snapshots found for this wallet/chain.")
            return
        chain = self._selected_snapshot_chain()
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Snapshots CSV",
            f"snapshots-{chain.value}.csv",
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_name:
            return
        path = write_snapshot_csv(
            snapshots,
            Path(file_name),
            template=self._selected_snapshot_template(),
        )
        self._status_label.setText(f"Snapshot CSV export saved: {path}")

    def _on_export_snapshots_json_clicked(self) -> None:
        try:
            snapshots = self._load_snapshots_for_export()
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        except Exception as error:
            self._status_label.setText(f"Could not load snapshots: {error}")
            return
        if not snapshots:
            self._status_label.setText("No snapshots found for this wallet/chain.")
            return
        chain = self._selected_snapshot_chain()
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Snapshots JSON",
            f"snapshots-{chain.value}.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return
        path = write_snapshot_json(
            snapshots,
            Path(file_name),
            template=self._selected_snapshot_template(),
        )
        self._status_label.setText(f"Snapshot JSON export saved: {path}")

    def _selected_or_staged_filters(self) -> tuple[Chain, dict[str, object]]:
        selected = self._selected_saved_view()
        if selected is None:
            return self._selected_view_chain(), self._build_filters_from_inputs()
        if not isinstance(selected.filters, dict):
            return selected.chain, {}
        return selected.chain, selected.filters

    def _load_snapshots_for_export(self) -> list[WalletSnapshot]:
        normalized_address = AddressValidator.normalized(self._snapshot_address_input.text())
        if not normalized_address:
            raise ValidationError("Wallet address cannot be empty.")
        validation_error = AddressValidator.validation_error(normalized_address)
        if validation_error is not None:
            raise ValidationError(validation_error)
        return self._container.snapshots_repository.list_snapshots(
            address=normalized_address,
            chain=self._selected_snapshot_chain(),
            limit=self._parse_snapshot_limit(),
        )

    def _parse_snapshot_limit(self) -> int:
        raw = self._snapshot_limit_input.text().strip()
        if not raw:
            return 20
        if not raw.isdigit():
            raise ValidationError("Snapshot limit must be a positive integer.")
        parsed = int(raw)
        if parsed <= 0:
            raise ValidationError("Snapshot limit must be a positive integer.")
        return min(parsed, 200)

    def _build_filters_from_inputs(self) -> dict[str, object]:
        filters: dict[str, object] = {}

        wallet_address = _normalize_optional_address(self._view_wallet_address_input.text())
        if wallet_address is not None:
            filters["wallet_address"] = wallet_address

        token_address = _normalize_optional_address(
            self._view_token_address_input.text(),
            invalid_message="Invalid token contract address. Expected 0x + 40 hex characters.",
        )
        if token_address is not None:
            filters["token_address"] = token_address

        raw_category = self._view_category_combo.currentData()
        if isinstance(raw_category, str) and raw_category != "all":
            filters["categories"] = [raw_category]

        raw_direction = self._view_direction_combo.currentData()
        if isinstance(raw_direction, str) and raw_direction in {"inbound", "outbound"}:
            filters["direction"] = raw_direction

        min_usd_raw = self._view_min_usd_input.text().strip()
        if min_usd_raw:
            try:
                min_usd = Decimal(min_usd_raw)
            except InvalidOperation as error:
                raise ValidationError("Invalid Min USD value. Enter a numeric value.") from error
            if min_usd < 0:
                raise ValidationError("Min USD cannot be negative.")
            filters["min_value_usd"] = str(min_usd)

        from_block_raw = self._view_from_block_input.text().strip()
        if from_block_raw:
            if not from_block_raw.isdigit() or int(from_block_raw) <= 0:
                raise ValidationError("From block must be a positive integer.")
            filters["from_block"] = int(from_block_raw)

        asset_query = self._view_asset_query_input.text().strip()
        if asset_query:
            filters["asset_query"] = asset_query

        filters["verified_only"] = self._view_verified_only.isChecked()
        filters["include_approvals"] = self._view_include_approvals.isChecked()
        return filters

    def _selected_saved_view(self) -> SavedView | None:
        current = self._views_list.currentItem()
        if current is None:
            return None
        raw_view = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw_view, SavedView):
            return None
        return raw_view

    def _reload_views(self, selected_view_id: int | None = None) -> None:
        if selected_view_id is None:
            current = self._selected_saved_view()
            selected_view_id = current.id if current is not None else None

        views = self._container.saved_views_repository.list_views()
        self._views_list.clear()
        selected_item: QListWidgetItem | None = None
        for view in views:
            item = QListWidgetItem(_saved_view_display_text(view))
            item.setData(Qt.ItemDataRole.UserRole, view)
            self._views_list.addItem(item)
            if selected_view_id is not None and view.id == selected_view_id:
                selected_item = item

        if selected_item is not None:
            self._views_list.setCurrentItem(selected_item)
        self._set_view_actions_enabled(self._selected_saved_view() is not None)

    def _on_views_selection_changed(self) -> None:
        view = self._selected_saved_view()
        self._set_view_actions_enabled(view is not None)
        if view is None:
            return
        self._apply_view_to_form(view)

    def _set_view_actions_enabled(self, has_selection: bool) -> None:
        self._delete_view_button.setEnabled(has_selection)
        self._apply_activity_button.setEnabled(True)
        self._apply_token_detail_button.setEnabled(True)

    def _apply_view_to_form(self, view: SavedView) -> None:
        self._view_name_input.setText(view.name)

        chain_index = self._view_chain_combo.findData(view.chain.value)
        if chain_index >= 0:
            self._view_chain_combo.setCurrentIndex(chain_index)

        filters = view.filters if isinstance(view.filters, dict) else {}
        wallet_address = filters.get("wallet_address")
        self._view_wallet_address_input.setText(wallet_address if isinstance(wallet_address, str) else "")

        token_address = filters.get("token_address")
        self._view_token_address_input.setText(token_address if isinstance(token_address, str) else "")

        raw_category = "all"
        categories = filters.get("categories")
        if isinstance(categories, list):
            for category in categories:
                if isinstance(category, str) and self._view_category_combo.findData(category) >= 0:
                    raw_category = category
                    break
        elif isinstance(categories, str) and self._view_category_combo.findData(categories) >= 0:
            raw_category = categories
        category_index = self._view_category_combo.findData(raw_category)
        if category_index >= 0:
            self._view_category_combo.setCurrentIndex(category_index)

        raw_direction = filters.get("direction")
        direction_value = "any"
        if isinstance(raw_direction, str) and self._view_direction_combo.findData(raw_direction) >= 0:
            direction_value = raw_direction
        direction_index = self._view_direction_combo.findData(direction_value)
        if direction_index >= 0:
            self._view_direction_combo.setCurrentIndex(direction_index)

        self._view_min_usd_input.setText(_quick_filter_text(filters.get("min_value_usd")))
        self._view_from_block_input.setText(_quick_filter_block_text(filters.get("from_block")))
        asset_query = filters.get("asset_query")
        self._view_asset_query_input.setText(asset_query if isinstance(asset_query, str) else "")

        include_approvals = filters.get("include_approvals")
        if isinstance(include_approvals, bool):
            self._view_include_approvals.setChecked(include_approvals)
        else:
            self._view_include_approvals.setChecked(True)

        verified_only = filters.get("verified_only")
        if isinstance(verified_only, bool):
            self._view_verified_only.setChecked(verified_only)
        else:
            self._view_verified_only.setChecked(False)


def _quick_filter_text(raw: object) -> str:
    if isinstance(raw, str):
        return raw.strip()
    if isinstance(raw, bool):
        return ""
    if isinstance(raw, (int, float, Decimal)):
        return str(raw)
    return ""


def _quick_filter_block_text(raw: object) -> str:
    if isinstance(raw, bool):
        return ""
    if isinstance(raw, int):
        if raw > 0:
            return str(raw)
        return ""
    if isinstance(raw, str):
        trimmed = raw.strip()
        if trimmed.isdigit() and int(trimmed) > 0:
            return trimmed
    return ""


def _normalize_required_address(address: str) -> str:
    normalized = AddressValidator.normalized(address)
    if not normalized:
        raise ValidationError("Wallet address cannot be empty.")
    if not AddressValidator.is_valid(normalized):
        raise ValidationError("Invalid wallet address. Expected 0x + 40 hex characters.")
    return normalized


def _normalize_optional_address(
    address: str,
    invalid_message: str = "Invalid wallet address. Expected 0x + 40 hex characters.",
) -> str | None:
    normalized = AddressValidator.normalized(address)
    if not normalized:
        return None
    if not AddressValidator.is_valid(normalized):
        raise ValidationError(invalid_message)
    return normalized


def _parse_tags_csv(raw: str) -> list[str]:
    tags: list[str] = []
    seen: set[str] = set()
    for candidate in raw.split(","):
        trimmed = candidate.strip()
        if not trimmed:
            continue
        key = trimmed.casefold()
        if key in seen:
            continue
        seen.add(key)
        tags.append(trimmed)
    return tags


def _note_display_text(note: WalletNote) -> str:
    summary = note.note.replace("\n", " ").strip()
    if len(summary) > 72:
        summary = f"{summary[:69]}..."
    tags = ", ".join(note.tags) if note.tags else "no-tags"
    return f"[{note.chain.display_name}] {note.address} | {tags} | {summary}"


def _saved_view_display_text(view: SavedView) -> str:
    filters = view.filters if isinstance(view.filters, dict) else {}
    categories = filters.get("categories")
    if isinstance(categories, list):
        category_text = ",".join(category for category in categories if isinstance(category, str))
    elif isinstance(categories, str):
        category_text = categories
    else:
        category_text = "all"

    min_usd = _quick_filter_text(filters.get("min_value_usd")) or "none"
    from_block = _quick_filter_block_text(filters.get("from_block")) or "none"
    direction = filters.get("direction")
    direction_text = direction if isinstance(direction, str) else "any"
    return (
        f"[{view.chain.display_name}] {view.name} | cat={category_text} | "
        f"dir={direction_text} | min_usd={min_usd} | from_block={from_block}"
    )
