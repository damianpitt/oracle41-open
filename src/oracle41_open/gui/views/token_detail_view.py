"""Present token-specific transfers and approval history.

The view supports wallet and token input, pagination, quick filters, background loading, and activity exports.
Historical completeness and approval scan progress remain visible.
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

from oracle41_open.core.models import ActivityCategory, ActivityItem, Chain, ValidationError
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.core.services.token_detail_service import TokenDetailPageResult
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
class _TokenDetailLoadPayload:
    result: TokenDetailPageResult
    labels_by_address: dict[str, str]
    resolved_address: str
    input_name: str | None


class TokenDetailView(QWidget):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self._container = container
        self._task_runner = BackgroundTaskRunner(parent=self)
        self._task_runner.result.connect(self._on_token_activity_loaded)
        self._task_runner.error.connect(self._on_token_activity_load_error)
        self._task_runner.finished.connect(self._on_token_activity_load_finished)
        self._next_cursor: str | None = None
        self._raw_items: list[ActivityItem] = []
        self._visible_items: list[ActivityItem] = []
        self._export_context: ActivityExportContext | None = None
        self._active_wallet_address: str | None = None
        self._active_wallet_input: str | None = None
        self._is_loading = False
        self._retry_request: tuple[str | None, bool, bool] | None = None
        self._pending_load: tuple[str | None, bool, bool, Decimal | None] | None = None

        self._chain_combo = QComboBox(self)
        for chain in Chain:
            self._chain_combo.addItem(chain.display_name, chain.value)

        self._address_input = QLineEdit(self)
        self._address_input.setPlaceholderText("Wallet address (0x...)")
        self._token_address_input = QLineEdit(self)
        self._token_address_input.setPlaceholderText("Token contract address (0x...)")
        self._include_approvals = QCheckBox(
            "Include approval history (loaded in resumable block windows)",
            self,
        )
        self._include_approvals.setChecked(True)

        self._category_combo = QComboBox(self)
        self._category_combo.addItem("All", "all")
        self._category_combo.addItem("ERC20", ActivityCategory.ERC20.value)
        self._category_combo.addItem("ERC721", ActivityCategory.ERC721.value)
        self._category_combo.addItem("ERC1155", ActivityCategory.ERC1155.value)
        self._category_combo.addItem("Approval", ActivityCategory.APPROVAL.value)
        self._direction_combo = QComboBox(self)
        self._direction_combo.addItem("Any", "any")
        self._direction_combo.addItem("Inbound", "inbound")
        self._direction_combo.addItem("Outbound", "outbound")
        self._min_value_usd_input = QLineEdit(self)
        self._min_value_usd_input.setPlaceholderText("Optional minimum USD value")
        self._verified_only_checkbox = QCheckBox("Verified only", self)
        self._asset_query_input = QLineEdit(self)
        self._asset_query_input.setPlaceholderText("Optional symbol/address search")
        self._apply_local_filters_button = QPushButton("Apply Local Filters", self)
        self._apply_local_filters_button.clicked.connect(self._on_apply_local_filters_clicked)

        self._load_button = QPushButton("Load Token Activity", self)
        self._load_button.clicked.connect(self._on_load_clicked)
        self._refresh_button = QPushButton("Refresh Token Activity", self)
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        self._clear_cache_button = QPushButton("Clear Token Cache", self)
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

        self._status_label = QLabel("Token detail ready.", self)
        self._status_label.setWordWrap(True)
        self._labels_by_address: dict[str, str] = {}
        self._items_list = QListWidget(self)
        self._items_list.itemSelectionChanged.connect(self._on_item_selection_changed)
        self._detail_drawer = QTextEdit(self)
        self._detail_drawer.setReadOnly(True)
        self._detail_drawer.setPlaceholderText(
            "Transaction detail drawer. Select a token activity row to inspect details."
        )

        self._init_layout()
        self._apply_default_chain()

    def _init_layout(self) -> None:
        controls_box = QGroupBox("Token Detail")
        form = QFormLayout()
        form.addRow("Chain", self._chain_combo)
        form.addRow("Wallet Address", self._address_input)
        form.addRow("Token Address", self._token_address_input)
        form.addRow("", self._include_approvals)
        form.addRow("Category", self._category_combo)
        form.addRow("Direction", self._direction_combo)
        form.addRow("Min USD", self._min_value_usd_input)
        form.addRow("", self._verified_only_checkbox)
        form.addRow("Asset Filter", self._asset_query_input)
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

    def _selected_category(self) -> ActivityCategory | None:
        raw = self._category_combo.currentData()
        if not isinstance(raw, str) or raw == "all":
            return None
        try:
            return ActivityCategory(raw)
        except ValueError:
            return None

    def apply_quick_filters(self, chain: Chain, filters: dict[str, object]) -> None:
        chain_index = self._chain_combo.findData(chain.value)
        if chain_index >= 0:
            self._chain_combo.setCurrentIndex(chain_index)

        wallet_address = filters.get("wallet_address")
        if isinstance(wallet_address, str):
            self._address_input.setText(AddressValidator.normalized(wallet_address))

        token_address = filters.get("token_address")
        if isinstance(token_address, str):
            self._token_address_input.setText(AddressValidator.normalized(token_address))

        categories = filters.get("categories")
        category_value = "all"
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
        asset_query = filters.get("asset_query")
        self._asset_query_input.setText(asset_query.strip() if isinstance(asset_query, str) else "")

        include_approvals = filters.get("include_approvals")
        if isinstance(include_approvals, bool):
            self._include_approvals.setChecked(include_approvals)

        verified_only = filters.get("verified_only")
        if isinstance(verified_only, bool):
            self._verified_only_checkbox.setChecked(verified_only)

        self._status_label.setText("Quick filters applied. Enter token address if needed, then load.")

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
            self._status_label.setText("No loaded token activity yet. Load Token Activity first.")
            return
        try:
            min_value_usd = self._parse_min_value_usd()
        except ValidationError as error:
            self._status_label.setText(str(error))
            return
        self._refresh_visible_items(min_value_usd=min_value_usd)
        hidden = len(self._raw_items) - len(self._visible_items)
        hidden_note = f" {hidden} hidden by advanced filters." if hidden > 0 else ""
        self._status_label.setText(
            f"Advanced filters applied. Showing {len(self._visible_items)} item(s).{hidden_note}"
        )

    def _on_clear_cache_clicked(self) -> None:
        if self._is_loading:
            return
        wallet = self._address_input.text()
        token_address = self._token_address_input.text()
        normalized_wallet = AddressValidator.normalized(wallet)
        normalized_token = AddressValidator.normalized(token_address)

        wallet_error = AddressValidator.validation_error(normalized_wallet)
        if wallet_error is not None:
            input_key = wallet.strip().lower()
            if self._active_wallet_input != input_key or self._active_wallet_address is None:
                self._status_label.setText("Load this ENS name before clearing its token cache.")
                return
            normalized_wallet = self._active_wallet_address
        token_error = AddressValidator.validation_error(normalized_token)
        if token_error is not None:
            self._status_label.setText("Invalid token contract address. Expected 0x + 40 hex characters.")
            return

        cleared = self._container.token_detail_service.clear_cached_token_activity(
            address=normalized_wallet,
            token_address=normalized_token,
            chain=self._selected_chain(),
            include_approvals=self._include_approvals.isChecked(),
        )
        if cleared:
            self._status_label.setText("Token detail cache cleared for this address/token scope.")
            return
        self._status_label.setText("Token detail cache is unavailable in this runtime.")

    def _load_page(self, cursor: str | None, append: bool, force_refresh: bool = False) -> None:
        if self._is_loading:
            return
        self._set_loading(True, status_text="Loading token activity...")
        wallet = self._address_input.text()
        token_address = self._token_address_input.text()
        normalized_wallet = AddressValidator.normalized(wallet)
        normalized_token = AddressValidator.normalized(token_address)

        wallet_error = AddressValidator.validation_error(normalized_wallet)
        if wallet_error is not None and not _looks_like_ens_name(wallet):
            self._status_label.setText(wallet_error)
            self._set_loading(False)
            return
        token_error = AddressValidator.validation_error(normalized_token)
        if token_error is not None:
            self._status_label.setText("Invalid token contract address. Expected 0x + 40 hex characters.")
            self._set_loading(False)
            return

        try:
            min_value_usd = self._parse_min_value_usd()
        except ValidationError as error:
            self._status_label.setText(str(error))
            self._set_loading(False)
            return
        chain = self._selected_chain()
        include_approvals = self._include_approvals.isChecked()
        self._pending_load = (cursor, append, force_refresh, min_value_usd)
        existing_items = list(self._raw_items)

        def load_payload() -> object:
            resolution = self._container.label_resolution_service.resolve_input(wallet, chain)
            result = self._container.token_detail_service.load_token_activity(
                address=resolution.address,
                token_address=normalized_token,
                chain=chain,
                cursor=cursor,
                include_approvals=include_approvals,
                force_refresh=force_refresh,
            )
            labels = self._resolve_labels_for_items(existing_items + result.page.items)
            if resolution.input_name is not None:
                labels[resolution.address] = resolution.input_name
            return _TokenDetailLoadPayload(
                result=result,
                labels_by_address=labels,
                resolved_address=resolution.address,
                input_name=resolution.input_name,
            )

        self._task_runner.start(load_payload)

    def _on_token_activity_loaded(self, raw_result: object) -> None:
        if not isinstance(raw_result, _TokenDetailLoadPayload) or self._pending_load is None:
            self._on_token_activity_load_error(
                RuntimeError("Token detail service returned an invalid result.")
            )
            return

        result = raw_result.result
        self._labels_by_address = raw_result.labels_by_address
        self._active_wallet_address = raw_result.resolved_address
        self._active_wallet_input = (
            raw_result.input_name or raw_result.resolved_address
        ).lower()
        _cursor, append, _force_refresh, min_value_usd = self._pending_load
        if append:
            self._raw_items = _merge_items(self._raw_items, result.page.items)
        else:
            self._raw_items = result.page.items

        self._next_cursor = result.page.next_cursor
        self._export_context = ActivityExportContext(
            completeness=result.completeness,
            updated_at=result.updated_at,
            provenance=result.provenance,
            is_persisted=result.is_persisted,
        )
        self._refresh_visible_items(min_value_usd=min_value_usd)

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
            f"Loaded {len(result.page.items)} token item(s) from {source}.",
            f"Visible: {len(self._visible_items)}.",
            f"Completeness: {result.completeness.value}.",
        ]
        if not self._visible_items:
            status_parts.append("No token activity items match current filters.")
        if hidden_count > 0:
            status_parts.append(f"Hidden by advanced filters: {hidden_count}.")
        if self._next_cursor is not None:
            status_parts.append("More pages are available; this view is partial until you load next page.")
        self._status_label.setText(" ".join(status_parts))
        self._retry_request = None
        self._retry_button.setEnabled(False)

    def _on_token_activity_load_error(self, error: object) -> None:
        if isinstance(error, ValidationError):
            self._status_label.setText(str(error))
            return
        if self._pending_load is not None:
            cursor, append, force_refresh, _min_value_usd = self._pending_load
            self._retry_request = (cursor, append, force_refresh)
        self._status_label.setText(f"Could not load token detail: {error}. Use Retry Last.")

    def _on_token_activity_load_finished(self) -> None:
        self._pending_load = None
        self._set_loading(False)

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
        self._token_address_input.setEnabled(enabled)
        self._include_approvals.setEnabled(enabled)
        self._category_combo.setEnabled(enabled)
        self._direction_combo.setEnabled(enabled)
        self._min_value_usd_input.setEnabled(enabled)
        self._verified_only_checkbox.setEnabled(enabled)
        self._asset_query_input.setEnabled(enabled)
        self._auto_refresh_checkbox.setEnabled(enabled)
        self._items_list.setEnabled(enabled)
        self._retry_button.setEnabled(enabled and self._retry_request is not None)
        self._cancel_button.setEnabled(is_loading)
        if status_text is not None:
            self._status_label.setText(status_text)

    def _refresh_visible_items(self, min_value_usd: Decimal | None = None) -> None:
        normalized_wallet = self._active_wallet_address or AddressValidator.normalized(
            self._address_input.text()
        )
        self._visible_items = self._apply_advanced_filters(
            self._raw_items,
            wallet_address=normalized_wallet,
            min_value_usd=min_value_usd,
        )
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
            list_item = QListWidgetItem(_render_token_detail_list_row(item, self._labels_by_address))
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
        self._detail_drawer.setPlainText("No token activity items match current filters.")

    def _on_item_selection_changed(self) -> None:
        item = self._selected_item()
        if item is None:
            if self._visible_items:
                self._detail_drawer.setPlainText(
                    "Select a token activity row to inspect transaction details."
                )
            else:
                self._detail_drawer.clear()
            return
        wallet_address = self._active_wallet_address or AddressValidator.normalized(
            self._address_input.text()
        )
        self._detail_drawer.setPlainText(
            _render_token_activity_detail(item, self._labels_by_address, wallet_address=wallet_address)
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
        min_value_usd: Decimal | None,
    ) -> list[ActivityItem]:
        category = self._selected_category()
        direction = self._selected_direction()
        verified_only = self._verified_only_checkbox.isChecked()
        asset_query = self._asset_query_input.text().strip().lower()

        filtered: list[ActivityItem] = []
        for item in items:
            if category is not None and item.category is not category:
                continue
            if verified_only and item.is_verified is not True:
                continue
            if direction == "inbound" and wallet_address and item.to_address != wallet_address:
                continue
            if direction == "outbound" and wallet_address and item.from_address != wallet_address:
                continue
            if min_value_usd is not None:
                if item.value_usd is None or item.value_usd < min_value_usd:
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
            self._status_label.setText("Nothing to export. Load token detail first.")
            return
        chain = self._selected_chain()
        suggested = f"token-detail-{chain.value}.csv"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Token Detail CSV",
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
            self._status_label.setText("Nothing to export. Load token detail first.")
            return
        chain = self._selected_chain()
        suggested = f"token-detail-{chain.value}.json"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Token Detail JSON",
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


def _merge_items(existing: list[ActivityItem], incoming: list[ActivityItem]) -> list[ActivityItem]:
    by_id: dict[str, ActivityItem] = {item.id: item for item in existing}
    for item in incoming:
        current = by_id.get(item.id)
        if current is None or item.timestamp > current.timestamp:
            by_id[item.id] = item
    return sorted(by_id.values(), key=lambda item: item.timestamp, reverse=True)


def _render_token_detail_list_row(item: ActivityItem, labels_by_address: dict[str, str]) -> str:
    from_display = _format_address_with_label(item.from_address, labels_by_address)
    to_display = _format_address_with_label(item.to_address, labels_by_address)
    return (
        f"{item.timestamp.isoformat()} | {item.category.value} | {item.asset_symbol} | "
        f"value={item.value_decimal} | usd={_fmt_decimal(item.value_usd)} | "
        f"from={from_display} | to={to_display} | tx={item.tx_hash}"
    )


def _render_token_activity_detail(
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
        "Token Transaction Detail",
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
