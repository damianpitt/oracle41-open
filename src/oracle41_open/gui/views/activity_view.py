"""Present wallet activity and transaction inspection.

The view handles address input, filters, pagination, exports, background loading, and detailed receipt display.
Provider and decoding work is delegated to core services.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
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

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    Chain,
    DecodedArgument,
    DecodedCall,
    DecodedEvent,
    DecodedRevert,
    ProxyResolution,
    SignatureProvenance,
    TransactionDecoding,
    TransactionInspection,
    ValidationError,
)
from oracle41_open.core.services.activity_service import ActivityPageResult
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.transaction_inspection_service import TransactionInspectionResult
from oracle41_open.exports import (
    ActivityExportContext,
    ActivityExportTemplate,
    write_activity_csv,
    write_activity_json,
)
from oracle41_open.gui.task_runner import BackgroundTaskRunner

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


@dataclass(frozen=True)
class _ActivityLoadPayload:
    result: ActivityPageResult
    labels_by_address: dict[str, str]
    resolved_address: str
    input_name: str | None


@dataclass(frozen=True)
class _TransactionInspectionPayload:
    item_id: str
    result: TransactionInspectionResult


class ActivityView(QWidget):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self._container = container
        self._task_runner = BackgroundTaskRunner(parent=self)
        self._task_runner.result.connect(self._on_activity_loaded)
        self._task_runner.error.connect(self._on_activity_load_error)
        self._task_runner.finished.connect(self._on_activity_load_finished)
        self._transaction_task_runner = BackgroundTaskRunner(parent=self)
        self._transaction_task_runner.result.connect(self._on_transaction_inspected)
        self._transaction_task_runner.error.connect(self._on_transaction_inspection_error)
        self._transaction_task_runner.finished.connect(self._on_transaction_inspection_finished)
        self._next_cursor: str | None = None
        self._raw_items: list[ActivityItem] = []
        self._visible_items: list[ActivityItem] = []
        self._export_context: ActivityExportContext | None = None
        self._active_wallet_address: str | None = None
        self._active_wallet_input: str | None = None
        self._is_loading = False
        self._is_inspecting_transaction = False
        self._retry_request: tuple[str | None, bool, bool] | None = None
        self._pending_load: tuple[str | None, bool, bool] | None = None

        self._chain_combo = QComboBox(self)
        for chain in Chain:
            self._chain_combo.addItem(chain.display_name, chain.value)

        self._address_input = QLineEdit(self)
        self._address_input.setPlaceholderText("0x…")

        self._category_combo = QComboBox(self)
        self._category_combo.addItem("All", "all")
        self._category_combo.addItem("ERC20", ActivityCategory.ERC20.value)
        self._category_combo.addItem("ERC721", ActivityCategory.ERC721.value)
        self._category_combo.addItem("ERC1155", ActivityCategory.ERC1155.value)
        self._category_combo.addItem("External", ActivityCategory.EXTERNAL.value)
        self._category_combo.addItem("Internal Transfer", ActivityCategory.INTERNAL_TRANSFER.value)
        self._category_combo.addItem("Approval", ActivityCategory.APPROVAL.value)

        self._min_value_usd_input = QLineEdit(self)
        self._min_value_usd_input.setPlaceholderText("Optional minimum USD value")
        self._from_block_input = QLineEdit(self)
        self._from_block_input.setPlaceholderText("Optional start block (e.g. 21000000)")
        self._direction_combo = QComboBox(self)
        self._direction_combo.addItem("Any", "any")
        self._direction_combo.addItem("Inbound", "inbound")
        self._direction_combo.addItem("Outbound", "outbound")
        self._asset_query_input = QLineEdit(self)
        self._asset_query_input.setPlaceholderText("Optional symbol/address search")
        self._verified_only_checkbox = QCheckBox("Verified only", self)
        self._apply_local_filters_button = QPushButton("Apply Local Filters", self)
        self._apply_local_filters_button.clicked.connect(self._on_apply_local_filters_clicked)

        self._load_button = QPushButton("Load Activity", self)
        self._load_button.clicked.connect(self._on_load_clicked)
        self._refresh_button = QPushButton("Refresh Activity", self)
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        self._clear_cache_button = QPushButton("Clear Activity Cache", self)
        self._clear_cache_button.clicked.connect(self._on_clear_cache_clicked)
        self._next_button = QPushButton("Load Next Page", self)
        self._next_button.clicked.connect(self._on_next_clicked)
        self._next_button.setEnabled(False)
        self._retry_button = QPushButton("Retry Last", self)
        self._retry_button.clicked.connect(self._on_retry_clicked)
        self._retry_button.setEnabled(False)
        self._cancel_button = QPushButton("Cancel", self)
        self._cancel_button.clicked.connect(self._on_cancel_clicked)
        self._cancel_button.setEnabled(False)
        self._auto_refresh_checkbox = QCheckBox("Auto-refresh first page every 30s", self)
        self._auto_refresh_checkbox.toggled.connect(self._on_auto_refresh_toggled)
        self._auto_refresh_timer = QTimer(self)
        self._auto_refresh_timer.setInterval(30_000)
        self._auto_refresh_timer.timeout.connect(self._on_auto_refresh_timeout)

        self._export_csv_button = QPushButton("Export CSV", self)
        self._export_csv_button.clicked.connect(self._on_export_csv_clicked)
        self._export_csv_button.setEnabled(False)
        self._export_json_button = QPushButton("Export JSON", self)
        self._export_json_button.clicked.connect(self._on_export_json_clicked)
        self._export_json_button.setEnabled(False)
        self._export_template_combo = QComboBox(self)
        self._export_template_combo.addItem("Full", ActivityExportTemplate.FULL.value)
        self._export_template_combo.addItem("Compact", ActivityExportTemplate.COMPACT.value)
        self._export_template_combo.addItem("Audit", ActivityExportTemplate.AUDIT.value)

        self._status_label = QLabel("Activity feed ready.", self)
        self._status_label.setWordWrap(True)
        self._labels_by_address: dict[str, str] = {}

        self._items_list = QListWidget(self)
        self._items_list.itemSelectionChanged.connect(self._on_item_selection_changed)

        self._inspect_transaction_button = QPushButton("Inspect Receipt", self)
        self._inspect_transaction_button.clicked.connect(self._on_inspect_transaction_clicked)
        self._inspect_transaction_button.setEnabled(False)

        self._detail_drawer = QTextEdit(self)
        self._detail_drawer.setReadOnly(True)
        self._detail_drawer.setPlaceholderText(
            "Transaction detail drawer. Select an activity row to inspect details."
        )

        self._init_layout()
        self._apply_default_chain()

    def _init_layout(self) -> None:
        controls_box = QGroupBox("Activity Feed")
        form = QFormLayout()
        form.addRow("Chain", self._chain_combo)
        form.addRow("Wallet Address", self._address_input)
        form.addRow("Category", self._category_combo)
        form.addRow("Min USD", self._min_value_usd_input)
        form.addRow("From Block", self._from_block_input)
        form.addRow("Direction", self._direction_combo)
        form.addRow("Asset Filter", self._asset_query_input)
        form.addRow("", self._verified_only_checkbox)
        form.addRow("", self._apply_local_filters_button)

        button_row = QHBoxLayout()
        button_row.addWidget(self._load_button)
        button_row.addWidget(self._refresh_button)
        button_row.addWidget(self._clear_cache_button)
        button_row.addWidget(self._next_button)
        button_row.addWidget(self._retry_button)
        button_row.addWidget(self._cancel_button)
        button_row.addStretch(1)
        form.addRow("", button_row)
        form.addRow("", self._auto_refresh_checkbox)

        export_row = QHBoxLayout()
        export_row.addWidget(QLabel("Template", self))
        export_row.addWidget(self._export_template_combo)
        export_row.addWidget(self._export_csv_button)
        export_row.addWidget(self._export_json_button)
        export_row.addStretch(1)
        form.addRow("", export_row)

        controls_box.setLayout(form)

        root = QVBoxLayout()
        root.addWidget(controls_box)
        root.addWidget(self._status_label)
        root.addWidget(self._items_list, stretch=1)
        root.addWidget(self._inspect_transaction_button)
        root.addWidget(self._detail_drawer, stretch=1)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(root)

    def _apply_default_chain(self) -> None:
        settings = self._container.settings_store.load()
        chain_index = self._chain_combo.findData(settings.selected_chain.value)
        if chain_index >= 0:
            self._chain_combo.setCurrentIndex(chain_index)

    def _selected_chain(self) -> Chain:
        raw = self._chain_combo.currentData()
        if not isinstance(raw, str):
            return Chain.ETHEREUM
        try:
            return Chain(raw)
        except ValueError:
            return Chain.ETHEREUM

    def _selected_categories(self) -> set[ActivityCategory] | None:
        raw = self._category_combo.currentData()
        if not isinstance(raw, str) or raw == "all":
            return None
        try:
            return {ActivityCategory(raw)}
        except ValueError:
            return None

    def apply_quick_filters(self, chain: Chain, filters: dict[str, object]) -> None:
        chain_index = self._chain_combo.findData(chain.value)
        if chain_index >= 0:
            self._chain_combo.setCurrentIndex(chain_index)

        wallet_address = filters.get("wallet_address")
        if isinstance(wallet_address, str):
            self._address_input.setText(AddressValidator.normalized(wallet_address))

        category_value = "all"
        categories = filters.get("categories")
        if isinstance(categories, list):
            for category in categories:
                if isinstance(category, str) and self._category_combo.findData(category) >= 0:
                    category_value = category
                    break
        elif isinstance(categories, str) and self._category_combo.findData(categories) >= 0:
            category_value = categories
        category_index = self._category_combo.findData(category_value)
        if category_index >= 0:
            self._category_combo.setCurrentIndex(category_index)

        direction = filters.get("direction")
        if isinstance(direction, str):
            direction_index = self._direction_combo.findData(direction)
            if direction_index >= 0:
                self._direction_combo.setCurrentIndex(direction_index)

        self._min_value_usd_input.setText(_quick_filter_text(filters.get("min_value_usd")))
        self._from_block_input.setText(_quick_filter_block_text(filters.get("from_block")))
        asset_query = filters.get("asset_query")
        self._asset_query_input.setText(asset_query.strip() if isinstance(asset_query, str) else "")
        verified_only = filters.get("verified_only")
        if isinstance(verified_only, bool):
            self._verified_only_checkbox.setChecked(verified_only)

        self._status_label.setText("Quick filters applied. Click Load Activity.")

    def _parse_min_value_usd(self) -> Decimal | None:
        raw = self._min_value_usd_input.text().strip()
        if not raw:
            return None
        try:
            value = Decimal(raw)
        except InvalidOperation as error:
            raise ValidationError("Invalid Min USD value. Enter a numeric value.") from error
        if value < 0:
            raise ValidationError("Min USD cannot be negative.")
        return value

    def _parse_from_block(self) -> int | None:
        raw = self._from_block_input.text().strip()
        if not raw:
            return None
        if not raw.isdigit():
            raise ValidationError("From block must be a positive integer.")
        parsed = int(raw)
        if parsed <= 0:
            raise ValidationError("From block must be a positive integer.")
        return parsed

    def _on_load_clicked(self) -> None:
        if self._is_loading:
            return
        self._next_cursor = None
        self._raw_items = []
        self._visible_items = []
        self._export_context = None
        self._active_wallet_address = None
        self._active_wallet_input = None
        self._labels_by_address = {}
        self._items_list.clear()
        self._detail_drawer.clear()
        self._load_page(cursor=None, append=False, force_refresh=False)

    def _on_refresh_clicked(self) -> None:
        if self._is_loading:
            return
        self._next_cursor = None
        self._raw_items = []
        self._visible_items = []
        self._export_context = None
        self._active_wallet_address = None
        self._active_wallet_input = None
        self._labels_by_address = {}
        self._items_list.clear()
        self._detail_drawer.clear()
        self._load_page(cursor=None, append=False, force_refresh=True)

    def _on_next_clicked(self) -> None:
        if self._is_loading:
            return
        if self._next_cursor is None:
            self._status_label.setText("No next page available.")
            return
        self._load_page(cursor=self._next_cursor, append=True, force_refresh=False)

    def _on_retry_clicked(self) -> None:
        if self._is_loading:
            return
        if self._retry_request is None:
            self._status_label.setText("No failed request to retry.")
            return
        cursor, append, force_refresh = self._retry_request
        self._load_page(cursor=cursor, append=append, force_refresh=force_refresh)

    def _on_cancel_clicked(self) -> None:
        if not self._is_loading:
            return
        self._task_runner.cancel_all()
        self._status_label.setText("Cancellation requested. Waiting for the current request to stop.")

    def _on_auto_refresh_toggled(self, enabled: bool) -> None:
        if enabled:
            self._auto_refresh_timer.start()
            self._status_label.setText("Auto-refresh enabled (30s).")
            return
        self._auto_refresh_timer.stop()
        self._status_label.setText("Auto-refresh disabled.")

    def _on_auto_refresh_timeout(self) -> None:
        if self._is_loading:
            return
        if not self._raw_items:
            return
        self._on_refresh_clicked()

    def _on_apply_local_filters_clicked(self) -> None:
        if self._is_loading:
            return
        if not self._raw_items:
            self._status_label.setText("No loaded activity yet. Load Activity first.")
            return
        self._refresh_visible_items()
        hidden = len(self._raw_items) - len(self._visible_items)
        hidden_note = f" {hidden} hidden by advanced filters." if hidden > 0 else ""
        self._status_label.setText(
            f"Advanced filters applied. Showing {len(self._visible_items)} item(s).{hidden_note}"
        )

    def _on_clear_cache_clicked(self) -> None:
        if self._is_loading:
            return
        address = self._address_input.text()
        normalized = AddressValidator.normalized(address)
        validation_error = AddressValidator.validation_error(normalized)
        if validation_error is not None:
            input_key = address.strip().lower()
            if self._active_wallet_input != input_key or self._active_wallet_address is None:
                self._status_label.setText("Load this ENS name before clearing its activity cache.")
                return
            normalized = self._active_wallet_address

        chain = self._selected_chain()
        try:
            from_block = self._parse_from_block()
            cleared = self._container.activity_service.clear_cached_activity(
                address=normalized,
                chain=chain,
                from_block=from_block,
            )
        except ValidationError as error:
            self._status_label.setText(str(error))
            return

        if cleared:
            self._status_label.setText("Activity cache cleared for this address/chain/filter scope.")
            return
        self._status_label.setText("Activity cache is unavailable in this runtime.")

    def _load_page(self, cursor: str | None, append: bool, force_refresh: bool = False) -> None:
        if self._is_loading:
            return
        self._set_loading(True, status_text="Loading activity...")
        address = self._address_input.text()
        normalized = AddressValidator.normalized(address)
        validation_error = AddressValidator.validation_error(normalized)
        if validation_error is not None and not _looks_like_ens_name(address):
            self._status_label.setText(validation_error)
            self._set_loading(False)
            return

        chain = self._selected_chain()
        try:
            min_value_usd = self._parse_min_value_usd()
            from_block = self._parse_from_block()
            categories = self._selected_categories()
        except ValidationError as error:
            self._status_label.setText(str(error))
            self._set_loading(False)
            return

        self._pending_load = (cursor, append, force_refresh)
        existing_items = list(self._raw_items)

        def load_payload() -> object:
            resolution = self._container.label_resolution_service.resolve_input(address, chain)
            result = self._container.activity_service.load_activity(
                address=resolution.address,
                chain=chain,
                cursor=cursor,
                from_block=from_block,
                min_value_usd=min_value_usd,
                categories=categories,
                force_refresh=force_refresh,
            )
            labels = self._resolve_labels_for_items(existing_items + result.page.items)
            if resolution.input_name is not None:
                labels[resolution.address] = resolution.input_name
            return _ActivityLoadPayload(
                result=result,
                labels_by_address=labels,
                resolved_address=resolution.address,
                input_name=resolution.input_name,
            )

        self._task_runner.start(load_payload)

    def _on_activity_loaded(self, raw_result: object) -> None:
        if not isinstance(raw_result, _ActivityLoadPayload) or self._pending_load is None:
            self._on_activity_load_error(RuntimeError("Activity service returned an invalid result."))
            return

        result = raw_result.result
        self._labels_by_address = raw_result.labels_by_address
        self._active_wallet_address = raw_result.resolved_address
        self._active_wallet_input = (
            raw_result.input_name or raw_result.resolved_address
        ).lower()
        _cursor, append, _force_refresh = self._pending_load
        if append:
            self._raw_items = _merge_activity_items(self._raw_items, result.page.items)
        else:
            self._raw_items = result.page.items

        self._next_cursor = result.page.next_cursor
        self._export_context = ActivityExportContext(
            completeness=result.completeness,
            updated_at=result.updated_at,
            provenance=result.provenance,
            is_persisted=result.is_persisted,
        )
        self._refresh_visible_items()

        if result.is_cached and result.is_persisted:
            source = "local ledger"
        elif result.is_cached:
            source = "cache"
        elif result.is_persisted:
            source = "provider and saved to the local ledger"
        else:
            source = "provider"
        hidden_count = len(self._raw_items) - len(self._visible_items)
        status_parts = [
            f"Loaded {len(result.page.items)} item(s) from {source}.",
            f"Visible: {len(self._visible_items)}.",
            f"Completeness: {result.completeness.value}.",
        ]
        if not self._visible_items:
            status_parts.append("No activity items match current filters.")
        if hidden_count > 0:
            status_parts.append(f"Hidden by advanced filters: {hidden_count}.")
        if self._next_cursor is not None:
            status_parts.append("More pages are available; this view is partial until you load next page.")
        self._status_label.setText(" ".join(status_parts))
        self._retry_request = None
        self._retry_button.setEnabled(False)

    def _on_activity_load_error(self, error: object) -> None:
        if isinstance(error, ValidationError):
            self._status_label.setText(str(error))
            return
        if self._pending_load is not None:
            self._retry_request = self._pending_load
        self._status_label.setText(f"Could not load activity feed: {error}. Use Retry Last.")

    def _on_activity_load_finished(self) -> None:
        self._pending_load = None
        self._set_loading(False)

    def _on_inspect_transaction_clicked(self) -> None:
        if self._is_inspecting_transaction:
            return
        item = self._selected_item()
        if item is None:
            self._status_label.setText("Select an activity row before inspecting its receipt.")
            return
        capabilities = self._container.transaction_inspection_service.capabilities(item.chain)
        if not capabilities.receipts:
            self._status_label.setText(
                "No receipt-capable JSON-RPC endpoint is configured for this chain."
            )
            return

        self._is_inspecting_transaction = True
        self._inspect_transaction_button.setEnabled(False)
        self._detail_drawer.setPlainText("Loading transaction and receipt...")

        def inspect_transaction() -> object:
            result = self._container.transaction_inspection_service.inspect(
                item.tx_hash,
                item.chain,
            )
            return _TransactionInspectionPayload(item_id=item.id, result=result)

        self._transaction_task_runner.start(inspect_transaction)

    def _on_transaction_inspected(self, raw_result: object) -> None:
        if not isinstance(raw_result, _TransactionInspectionPayload):
            self._on_transaction_inspection_error(
                RuntimeError("Transaction inspection returned an invalid result.")
            )
            return
        selected = self._selected_item()
        if selected is None or selected.id != raw_result.item_id:
            return
        self._detail_drawer.setPlainText(
            _render_transaction_inspection(
                raw_result.result.inspection,
                raw_result.result.decoding,
                raw_result.result.proxy_resolution,
            )
        )
        source = "local ledger" if raw_result.result.is_cached else "provider and local ledger"
        self._status_label.setText(f"Transaction receipt loaded from {source}.")

    def _on_transaction_inspection_error(self, error: object) -> None:
        self._detail_drawer.setPlainText(f"Transaction inspection failed: {error}")
        self._status_label.setText(f"Could not inspect transaction receipt: {error}")

    def _on_transaction_inspection_finished(self) -> None:
        self._is_inspecting_transaction = False
        self._inspect_transaction_button.setEnabled(
            not self._is_loading and self._selected_item() is not None
        )

    def _set_loading(self, is_loading: bool, status_text: str | None = None) -> None:
        self._is_loading = is_loading
        enabled = not is_loading
        self._load_button.setEnabled(enabled)
        self._refresh_button.setEnabled(enabled)
        self._clear_cache_button.setEnabled(enabled)
        self._next_button.setEnabled(enabled and self._next_cursor is not None)
        self._apply_local_filters_button.setEnabled(enabled)
        self._export_csv_button.setEnabled(enabled and bool(self._visible_items))
        self._export_json_button.setEnabled(enabled and bool(self._visible_items))
        self._export_template_combo.setEnabled(enabled)
        self._chain_combo.setEnabled(enabled)
        self._address_input.setEnabled(enabled)
        self._category_combo.setEnabled(enabled)
        self._min_value_usd_input.setEnabled(enabled)
        self._from_block_input.setEnabled(enabled)
        self._direction_combo.setEnabled(enabled)
        self._asset_query_input.setEnabled(enabled)
        self._verified_only_checkbox.setEnabled(enabled)
        self._auto_refresh_checkbox.setEnabled(enabled)
        self._items_list.setEnabled(enabled)
        self._inspect_transaction_button.setEnabled(
            enabled and not self._is_inspecting_transaction and self._selected_item() is not None
        )
        self._retry_button.setEnabled(enabled and self._retry_request is not None)
        self._cancel_button.setEnabled(is_loading)
        if status_text is not None:
            self._status_label.setText(status_text)

    def _refresh_visible_items(self) -> None:
        normalized = self._active_wallet_address or AddressValidator.normalized(
            self._address_input.text()
        )
        self._visible_items = self._apply_advanced_filters(self._raw_items, wallet_address=normalized)
        has_items = bool(self._visible_items)
        self._export_csv_button.setEnabled(has_items)
        self._export_json_button.setEnabled(has_items)
        self._reload_items_list()

    def _reload_items_list(self) -> None:
        selected_id = None
        selected = self._selected_item()
        if selected is not None:
            selected_id = selected.id

        self._items_list.clear()
        selected_widget_item: QListWidgetItem | None = None
        for item in self._visible_items:
            list_item = QListWidgetItem(_render_activity_list_row(item, self._labels_by_address))
            list_item.setData(Qt.ItemDataRole.UserRole, item)
            self._items_list.addItem(list_item)
            if selected_id is not None and item.id == selected_id:
                selected_widget_item = list_item

        if selected_widget_item is not None:
            self._items_list.setCurrentItem(selected_widget_item)
            return
        if self._items_list.count() > 0:
            self._items_list.setCurrentRow(0)
            return
        self._detail_drawer.setPlainText("No activity items match current filters.")

    def _on_item_selection_changed(self) -> None:
        item = self._selected_item()
        self._inspect_transaction_button.setEnabled(
            item is not None and not self._is_loading and not self._is_inspecting_transaction
        )
        if item is None:
            if self._visible_items:
                self._detail_drawer.setPlainText("Select an activity row to inspect transaction details.")
            else:
                self._detail_drawer.clear()
            return
        wallet_address = self._active_wallet_address or AddressValidator.normalized(
            self._address_input.text()
        )
        self._detail_drawer.setPlainText(
            _render_activity_detail(item, self._labels_by_address, wallet_address=wallet_address)
        )

    def _selected_item(self) -> ActivityItem | None:
        current = self._items_list.currentItem()
        if current is None:
            return None
        raw_item = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(raw_item, ActivityItem):
            return None
        return raw_item

    def _apply_advanced_filters(
        self,
        items: list[ActivityItem],
        wallet_address: str,
    ) -> list[ActivityItem]:
        filtered: list[ActivityItem] = []
        direction = self._selected_direction()
        asset_query = self._asset_query_input.text().strip().lower()
        verified_only = self._verified_only_checkbox.isChecked()

        for item in items:
            if verified_only and item.is_verified is not True:
                continue
            if direction == "inbound" and wallet_address and item.to_address != wallet_address:
                continue
            if direction == "outbound" and wallet_address and item.from_address != wallet_address:
                continue
            if asset_query:
                symbol_match = asset_query in item.asset_symbol.lower()
                contract = item.contract_address.lower() if item.contract_address is not None else ""
                contract_match = asset_query in contract
                if not symbol_match and not contract_match:
                    continue
            filtered.append(item)
        return filtered

    def _selected_direction(self) -> str:
        raw = self._direction_combo.currentData()
        if not isinstance(raw, str):
            return "any"
        if raw in {"any", "inbound", "outbound"}:
            return raw
        return "any"

    def _selected_export_template(self) -> ActivityExportTemplate:
        raw = self._export_template_combo.currentData()
        if not isinstance(raw, str):
            return ActivityExportTemplate.FULL
        try:
            return ActivityExportTemplate(raw)
        except ValueError:
            return ActivityExportTemplate.FULL

    def _resolve_labels_for_items(self, items: list[ActivityItem]) -> dict[str, str]:
        addresses_by_chain: dict[Chain, set[str]] = {}
        for item in items:
            chain_addresses = addresses_by_chain.setdefault(item.chain, set())
            chain_addresses.add(item.from_address)
            chain_addresses.add(item.to_address)

        labels_by_address: dict[str, str] = {}
        for chain, addresses in addresses_by_chain.items():
            try:
                resolved = self._container.label_resolution_service.resolve_labels(sorted(addresses), chain=chain)
            except Exception:
                continue
            labels_by_address.update(resolved)
        return labels_by_address

    def _on_export_csv_clicked(self) -> None:
        if not self._visible_items:
            self._status_label.setText("Nothing to export. Load activity first.")
            return
        chain = self._selected_chain()
        suggested = f"activity-{chain.value}.csv"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Activity CSV",
            suggested,
            "CSV Files (*.csv);;All Files (*)",
        )
        if not file_name:
            return
        path = write_activity_csv(
            self._visible_items,
            Path(file_name),
            template=self._selected_export_template(),
            context=self._export_context,
        )
        self._status_label.setText(f"CSV export saved: {path}")

    def _on_export_json_clicked(self) -> None:
        if not self._visible_items:
            self._status_label.setText("Nothing to export. Load activity first.")
            return
        chain = self._selected_chain()
        suggested = f"activity-{chain.value}.json"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Activity JSON",
            suggested,
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return
        path = write_activity_json(
            self._visible_items,
            Path(file_name),
            template=self._selected_export_template(),
            context=self._export_context,
        )
        self._status_label.setText(f"JSON export saved: {path}")


def _merge_activity_items(existing: list[ActivityItem], incoming: list[ActivityItem]) -> list[ActivityItem]:
    by_id: dict[str, ActivityItem] = {item.id: item for item in existing}
    for item in incoming:
        current = by_id.get(item.id)
        if current is None or item.timestamp > current.timestamp:
            by_id[item.id] = item
    return sorted(by_id.values(), key=lambda item: item.timestamp, reverse=True)


def _render_activity_list_row(item: ActivityItem, labels_by_address: dict[str, str]) -> str:
    from_display = _format_address_with_label(item.from_address, labels_by_address)
    to_display = _format_address_with_label(item.to_address, labels_by_address)
    return (
        f"{item.timestamp.isoformat()} | {item.category.value} | {item.asset_symbol} | "
        f"value={item.value_decimal} | usd={_fmt_decimal(item.value_usd)} | "
        f"from={from_display} | to={to_display} | tx={item.tx_hash}"
    )


def _render_activity_detail(
    item: ActivityItem,
    labels_by_address: dict[str, str],
    wallet_address: str,
) -> str:
    from_display = _format_address_with_label(item.from_address, labels_by_address)
    to_display = _format_address_with_label(item.to_address, labels_by_address)
    direction = "unknown"
    if wallet_address:
        if item.to_address == wallet_address and item.from_address == wallet_address:
            direction = "self-transfer"
        elif item.to_address == wallet_address:
            direction = "inbound"
        elif item.from_address == wallet_address:
            direction = "outbound"
    verified = "n/a" if item.is_verified is None else ("yes" if item.is_verified else "no")
    lines = [
        "Transaction Detail",
        f"- Timestamp: {item.timestamp.isoformat()}",
        f"- Chain: {item.chain.display_name}",
        f"- Category: {item.category.value}",
        f"- Direction (wallet-relative): {direction}",
        f"- Tx Hash: {item.tx_hash}",
        f"- Log Index: {item.log_index}",
        f"- Block: {item.block_number if item.block_number is not None else 'n/a'}",
        f"- Asset: {item.asset_symbol}",
        f"- Contract: {item.contract_address or 'n/a'}",
        f"- Raw Value: {item.raw_value}",
        f"- Value (decimal): {item.value_decimal}",
        f"- Value (USD): {_fmt_decimal(item.value_usd)}",
        f"- Verified: {verified}",
        f"- From: {from_display}",
        f"- To: {to_display}",
    ]
    return "\n".join(lines)


def _render_transaction_inspection(
    inspection: TransactionInspection,
    decoding: TransactionDecoding,
    proxy_resolution: ProxyResolution | None = None,
) -> str:
    if inspection.status is True:
        status = "success"
    elif inspection.status is False:
        status = "reverted"
    else:
        status = "unknown"
    selector = inspection.input_data[:10] if len(inspection.input_data) >= 10 else inspection.input_data
    lines = [
        "Transaction Inspector",
        f"- Chain: {inspection.chain.display_name}",
        f"- Tx Hash: {inspection.tx_hash}",
        f"- Status: {status}",
        f"- Block: {inspection.block_number}",
        f"- Block Hash: {inspection.block_hash}",
        f"- Transaction Index: {inspection.transaction_index}",
        f"- From: {inspection.from_address}",
        f"- To: {inspection.to_address or 'contract creation'}",
        f"- Created Contract: {inspection.contract_address or 'n/a'}",
        f"- Nonce: {inspection.nonce}",
        f"- Native Value (wei): {inspection.value_wei}",
        f"- Method Selector: {selector or 'n/a'}",
        "",
        "Decoded Call",
        *_render_decoded_call(decoding.call),
        "",
        "Contract Context",
        *_render_contract_context(decoding, proxy_resolution),
        "",
        "Revert Details",
        *_render_decoded_revert(decoding.revert, inspection.status),
        "",
        "Raw Transaction Data",
        f"- Input Data: {inspection.input_data}",
        f"- Gas Limit: {inspection.gas_limit}",
        f"- Gas Used: {inspection.gas_used}",
        f"- Effective Gas Price (wei): {inspection.effective_gas_price}",
        f"- Network Fee: {inspection.fee_native} {inspection.chain.native_symbol}",
        f"- Transaction Type: {inspection.transaction_type if inspection.transaction_type is not None else 'n/a'}",
        f"- Raw Logs: {len(inspection.logs)}",
        f"- Source: {inspection.source_provider}",
        f"- Fetched At: {inspection.fetched_at.isoformat()}",
    ]
    decoded_events = {event.log_index: event for event in decoding.events}
    for log in inspection.logs:
        lines.extend(
            (
                "",
                f"Log #{log.log_index}",
                *_render_decoded_event(decoded_events.get(log.log_index)),
                f"- Address: {log.address}",
                f"- Topics: {', '.join(log.topics) if log.topics else 'none'}",
                f"- Data: {log.data}",
                f"- Removed: {'yes' if log.removed else 'no'}",
            )
        )
    return "\n".join(lines)


def _render_contract_context(
    decoding: TransactionDecoding,
    proxy_resolution: ProxyResolution | None,
) -> tuple[str, ...]:
    lines = [f"- Decode Address: {decoding.contract_address or 'n/a'}"]
    if proxy_resolution is None:
        lines.append("- Proxy Resolution: unavailable")
        return tuple(lines)
    lines.extend(
        (
            f"- Proxy Resolution: {proxy_resolution.status.value}",
            f"- Proxy Type: {proxy_resolution.proxy_kind.value}",
            f"- Proxy Address: {proxy_resolution.proxy_address}",
            f"- Implementation: {proxy_resolution.implementation_address or 'n/a'}",
            f"- Resolution Block: {proxy_resolution.block_number}",
            f"- Resolution Source: {proxy_resolution.source_provider}",
        )
    )
    if proxy_resolution.error is not None:
        lines.append(f"- Resolution Note: {proxy_resolution.error}")
    return tuple(lines)


def _render_decoded_revert(
    revert: DecodedRevert | None,
    transaction_status: bool | None,
) -> tuple[str, ...]:
    if revert is None:
        message = "not applicable" if transaction_status is not False else "unavailable"
        return (f"- Decode Status: {message}",)
    lines = [f"- Decode Status: {revert.status.value}"]
    if revert.canonical_signature is not None:
        lines.append(f"- Error: {revert.canonical_signature}")
    lines.extend(_render_decoded_arguments(revert.arguments))
    lines.extend(_render_provenance(revert.provenance))
    if revert.error is not None:
        lines.append(f"- Decode Note: {revert.error}")
    lines.append(f"- Raw Revert Data: {revert.raw_data}")
    return tuple(lines)


def _render_decoded_call(call: DecodedCall) -> tuple[str, ...]:
    lines = [f"- Decode Status: {call.status.value}"]
    if call.canonical_signature is not None:
        lines.append(f"- Signature: {call.canonical_signature}")
    lines.extend(_render_decoded_arguments(call.arguments))
    lines.extend(_render_provenance(call.provenance))
    if call.error is not None:
        lines.append(f"- Decode Note: {call.error}")
    return tuple(lines)


def _render_decoded_event(event: DecodedEvent | None) -> tuple[str, ...]:
    if event is None:
        return ("- Decode Status: unavailable",)
    lines = [f"- Decode Status: {event.status.value}"]
    if event.canonical_signature is not None:
        standard = f" ({event.standard})" if event.standard is not None else ""
        lines.append(f"- Event: {event.canonical_signature}{standard}")
    lines.extend(_render_decoded_arguments(event.arguments))
    lines.extend(_render_provenance(event.provenance))
    if event.error is not None:
        lines.append(f"- Decode Note: {event.error}")
    return tuple(lines)


def _render_decoded_arguments(arguments: tuple[DecodedArgument, ...]) -> tuple[str, ...]:
    return tuple(
        f"- {argument.name} [{argument.abi_type}]: {argument.value}"
        for argument in arguments
    )


def _render_provenance(provenance: SignatureProvenance | None) -> tuple[str, ...]:
    if provenance is None:
        return ()
    verification = "verified" if provenance.is_verified else "unverified"
    return (
        f"- Signature Source: {provenance.source_name} v{provenance.version}",
        f"- Signature Trust: {verification} {provenance.source_kind.value}",
    )


def _fmt_decimal(value: Decimal | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:.6f}"


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


def _format_address_with_label(address: str, labels_by_address: dict[str, str]) -> str:
    normalized = AddressValidator.normalized(address)
    label = labels_by_address.get(normalized)
    if label is None:
        return address
    if label.casefold() == normalized.casefold():
        return address
    return f"{label} ({address})"


def _looks_like_ens_name(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.endswith(".eth") and " " not in normalized and len(normalized) <= 255
