"""Manage local preferences, provider access, ABIs, backups, and cache tools.

The view validates keys and verified ABI requests in background tasks while keeping secrets in the system keyring.
Backup files include local SQLite state but exclude credentials and private endpoint URLs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from pydantic import ValidationError as PydanticValidationError
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
    QPushButton,
    QScrollArea,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import Chain, ContractABIRecord, ValidationError
from oracle41_open.core.services.provider_key_validation_service import ProviderKeyValidationResult
from oracle41_open.gui.task_runner import BackgroundTaskRunner
from oracle41_open.providers.capabilities import (
    WalletDataProviderDescriptor,
    WalletDataProviderId,
    provider_descriptor,
)
from oracle41_open.storage.backup_restore import BackupMetadata
from oracle41_open.storage.settings import (
    AppSettings,
    ProviderPreference,
)

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer
    from oracle41_open.storage.cache_store import CacheDiagnostics


@dataclass(frozen=True)
class _KeyValidationPayload:
    alchemy_value: str
    ankr_value: str
    moralis_value: str
    alchemy_result: ProviderKeyValidationResult | None
    ankr_result: ProviderKeyValidationResult | None
    moralis_result: ProviderKeyValidationResult | None


@dataclass(frozen=True)
class _BackupExportPayload:
    metadata: BackupMetadata


@dataclass(frozen=True)
class _BackupRestorePayload:
    settings: AppSettings


@dataclass(frozen=True)
class _VerifiedABIPayload:
    record: ContractABIRecord | None


class SettingsView(QWidget):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self._container = container
        self._task_runner = BackgroundTaskRunner(parent=self)
        self._task_runner.result.connect(self._on_key_validation_result)
        self._task_runner.error.connect(self._on_key_validation_error)
        self._task_runner.finished.connect(self._on_key_validation_finished)
        self._key_validation_in_progress = False
        self._backup_task_runner = BackgroundTaskRunner(parent=self)
        self._backup_task_runner.result.connect(self._on_backup_result)
        self._backup_task_runner.error.connect(self._on_backup_error)
        self._backup_task_runner.finished.connect(self._on_backup_finished)
        self._backup_operation: str | None = None
        self._abi_task_runner = BackgroundTaskRunner(parent=self)
        self._abi_task_runner.result.connect(self._on_verified_abi_result)
        self._abi_task_runner.error.connect(self._on_verified_abi_error)
        self._abi_task_runner.finished.connect(self._on_verified_abi_finished)
        self._abi_fetch_in_progress = False
        self._settings = self._container.settings_store.load()

        self._chain_combo = QComboBox(self)
        for chain in Chain:
            self._chain_combo.addItem(chain.display_name, chain.value)
        self._hide_unverified = QCheckBox("Hide unverified / low-signal tokens", self)
        self._hide_dust = QCheckBox("Hide dust tokens by USD threshold", self)
        self._dust_threshold_input = QLineEdit(self)
        self._dust_threshold_input.setPlaceholderText("USD threshold (e.g. 1)")
        self._overview_page_cap_input = QLineEdit(self)
        self._overview_page_cap_input.setPlaceholderText("1-200")
        self._wallet_cache_ttl_input = QLineEdit(self)
        self._wallet_cache_ttl_input.setPlaceholderText("0-86400")
        self._activity_cache_ttl_input = QLineEdit(self)
        self._activity_cache_ttl_input.setPlaceholderText("0-86400")
        self._token_detail_cache_ttl_input = QLineEdit(self)
        self._token_detail_cache_ttl_input.setPlaceholderText("0-86400")
        self._pricing_stale_age_input = QLineEdit(self)
        self._pricing_stale_age_input.setPlaceholderText("0-604800")
        self._cache_max_size_input = QLineEdit(self)
        self._cache_max_size_input.setPlaceholderText("10-500")

        self._alchemy_enabled = QCheckBox("Enabled", self)
        self._alchemy_priority = _provider_priority_combo(self)
        self._alchemy_capabilities = _provider_capability_label(
            provider_descriptor(WalletDataProviderId.ALCHEMY),
            self,
        )
        self._alchemy_key_input = QLineEdit(self)
        self._alchemy_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._alchemy_key_input.setPlaceholderText("Alchemy API Key")
        self._alchemy_key_input.setText(
            self._container.secret_store.get_secret("alchemy_api_key") or ""
        )

        self._ankr_enabled = QCheckBox("Enabled", self)
        self._ankr_priority = _provider_priority_combo(self)
        self._ankr_capabilities = _provider_capability_label(
            provider_descriptor(WalletDataProviderId.ANKR),
            self,
        )
        self._ankr_key_input = QLineEdit(self)
        self._ankr_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._ankr_key_input.setPlaceholderText("Ankr API Key")
        self._ankr_key_input.setText(self._container.secret_store.get_secret("ankr_api_key") or "")

        self._moralis_enabled = QCheckBox("Enabled", self)
        self._moralis_priority = _provider_priority_combo(self)
        self._moralis_capabilities = _provider_capability_label(
            provider_descriptor(WalletDataProviderId.MORALIS),
            self,
        )
        self._moralis_key_input = QLineEdit(self)
        self._moralis_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._moralis_key_input.setPlaceholderText("Moralis API Key")
        self._moralis_key_input.setText(
            self._container.secret_store.get_secret("moralis_api_key") or ""
        )

        self._rpc_chain_combo = QComboBox(self)
        for chain in Chain:
            self._rpc_chain_combo.addItem(chain.display_name, chain.value)
        self._rpc_chain_combo.currentIndexChanged.connect(self._load_selected_rpc_url)
        self._rpc_url_input = QLineEdit(self)
        self._rpc_url_input.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self._rpc_url_input.setPlaceholderText("https://your-rpc-endpoint.example")
        self._save_rpc_url_button = QPushButton("Save RPC Endpoint", self)
        self._save_rpc_url_button.clicked.connect(self._save_rpc_url)

        self._abi_chain_combo = QComboBox(self)
        for chain in Chain:
            self._abi_chain_combo.addItem(chain.display_name, chain.value)
        self._abi_chain_combo.currentIndexChanged.connect(self._refresh_contract_abis)
        self._abi_address_input = QLineEdit(self)
        self._abi_address_input.setPlaceholderText("0x contract or proxy address")
        self._abi_name_input = QLineEdit(self)
        self._abi_name_input.setPlaceholderText("Optional contract name")
        self._contract_abi_list = QListWidget(self)
        self._contract_abi_list.setMinimumHeight(110)
        self._import_abi_button = QPushButton("Import ABI JSON", self)
        self._import_abi_button.clicked.connect(self._import_contract_abi)
        self._fetch_verified_abi_button = QPushButton("Fetch Verified ABI", self)
        self._fetch_verified_abi_button.clicked.connect(self._fetch_verified_contract_abi)
        self._remove_abi_button = QPushButton("Remove Selected ABI", self)
        self._remove_abi_button.clicked.connect(self._remove_selected_contract_abi)

        self._save_settings_button = QPushButton("Save Settings", self)
        self._save_settings_button.clicked.connect(self._save_settings)

        self._save_keys_button = QPushButton("Save API Keys", self)
        self._save_keys_button.clicked.connect(self._save_keys)

        self._export_backup_button = QPushButton("Export Backup", self)
        self._export_backup_button.clicked.connect(self._export_backup_clicked)
        self._restore_backup_button = QPushButton("Restore Backup", self)
        self._restore_backup_button.clicked.connect(self._restore_backup_clicked)
        self._backup_note = QLabel(
            "Backup includes local settings and SQLite state. Provider API keys are excluded.",
            self,
        )
        self._backup_note.setWordWrap(True)

        self._cache_diagnostics = QTextEdit(self)
        self._cache_diagnostics.setReadOnly(True)
        self._cache_diagnostics.setMinimumHeight(180)
        self._cache_diagnostics.setPlaceholderText("Cache diagnostics will appear here.")

        self._refresh_cache_diagnostics_button = QPushButton("Refresh Diagnostics", self)
        self._refresh_cache_diagnostics_button.clicked.connect(self._refresh_cache_diagnostics_clicked)
        self._reset_cache_telemetry_button = QPushButton("Reset Telemetry", self)
        self._reset_cache_telemetry_button.clicked.connect(self._reset_cache_telemetry_clicked)
        self._clear_cache_button = QPushButton("Clear Cache Store", self)
        self._clear_cache_button.clicked.connect(self._clear_cache_clicked)
        self._purge_expired_button = QPushButton("Purge Expired", self)
        self._purge_expired_button.clicked.connect(self._purge_expired_clicked)
        self._cache_prefix_input = QLineEdit(self)
        self._cache_prefix_input.setPlaceholderText("Cache key prefix (e.g. activity.page)")
        self._clear_prefix_button = QPushButton("Clear Prefix", self)
        self._clear_prefix_button.clicked.connect(self._clear_prefix_clicked)
        self._export_cache_diagnostics_button = QPushButton("Export Diagnostics JSON", self)
        self._export_cache_diagnostics_button.clicked.connect(self._export_cache_diagnostics_clicked)

        self._status = QLabel("Settings are stored locally. API keys are stored via Linux keyring.", self)
        self._status.setWordWrap(True)

        self._init_layout()
        self._apply_settings_to_form(self._settings)
        self._refresh_cache_diagnostics(set_status=False)

    def _init_layout(self) -> None:
        settings_box = QGroupBox("General")
        settings_form = QFormLayout()
        settings_form.addRow("Default Chain", self._chain_combo)
        settings_form.addRow("", self._hide_unverified)
        settings_form.addRow("", self._hide_dust)
        settings_form.addRow("Dust Threshold (USD)", self._dust_threshold_input)
        settings_form.addRow("Overview Max Token Pages", self._overview_page_cap_input)
        settings_form.addRow("Overview Cache TTL (s)", self._wallet_cache_ttl_input)
        settings_form.addRow("Activity Cache TTL (s)", self._activity_cache_ttl_input)
        settings_form.addRow("Token Detail Cache TTL (s)", self._token_detail_cache_ttl_input)
        settings_form.addRow("Pricing Stale Age (s)", self._pricing_stale_age_input)
        settings_form.addRow("Cache Max Size (MB)", self._cache_max_size_input)
        settings_form.addRow("", self._save_settings_button)
        settings_box.setLayout(settings_form)

        providers_box = QGroupBox("Wallet Data Providers")
        providers_form = QFormLayout()
        providers_form.addRow(
            "Alchemy",
            _provider_preference_row(self._alchemy_enabled, self._alchemy_priority),
        )
        providers_form.addRow("", self._alchemy_capabilities)
        providers_form.addRow(
            "Ankr",
            _provider_preference_row(self._ankr_enabled, self._ankr_priority),
        )
        providers_form.addRow("", self._ankr_capabilities)
        providers_form.addRow(
            "Moralis",
            _provider_preference_row(self._moralis_enabled, self._moralis_priority),
        )
        providers_form.addRow("", self._moralis_capabilities)
        provider_note = QLabel(
            "Priority 1 is tried first. Disabled providers receive no automatic requests. "
            "Restart after changing this order.",
            self,
        )
        provider_note.setWordWrap(True)
        providers_form.addRow("", provider_note)
        providers_box.setLayout(providers_form)

        keys_box = QGroupBox("Provider Keys")
        keys_form = QFormLayout()
        keys_form.addRow("Alchemy", self._alchemy_key_input)
        keys_form.addRow("Ankr", self._ankr_key_input)
        keys_form.addRow("Moralis", self._moralis_key_input)

        button_row = QHBoxLayout()
        button_row.addWidget(self._save_keys_button)
        button_row.addStretch(1)
        keys_form.addRow("", button_row)
        keys_box.setLayout(keys_form)

        rpc_box = QGroupBox("Custom JSON-RPC")
        rpc_form = QFormLayout()
        rpc_form.addRow("Chain", self._rpc_chain_combo)
        rpc_form.addRow("Endpoint", self._rpc_url_input)
        rpc_form.addRow("", self._save_rpc_url_button)
        rpc_box.setLayout(rpc_form)

        abi_box = QGroupBox("Contract ABIs")
        abi_form = QFormLayout()
        abi_form.addRow("Chain", self._abi_chain_combo)
        abi_form.addRow("Contract Address", self._abi_address_input)
        abi_form.addRow("Contract Name", self._abi_name_input)
        abi_form.addRow("Local ABIs", self._contract_abi_list)
        abi_buttons = QHBoxLayout()
        abi_buttons.addWidget(self._import_abi_button)
        abi_buttons.addWidget(self._fetch_verified_abi_button)
        abi_buttons.addWidget(self._remove_abi_button)
        abi_buttons.addStretch(1)
        abi_form.addRow("", abi_buttons)
        abi_note = QLabel(
            "Imported files are stored locally and marked unverified. "
            "They decode matching transaction calls, logs, and custom errors.",
            self,
        )
        abi_note.setWordWrap(True)
        abi_form.addRow("", abi_note)
        abi_box.setLayout(abi_form)

        backup_box = QGroupBox("Backup / Restore")
        backup_form = QFormLayout()
        backup_button_row = QHBoxLayout()
        backup_button_row.addWidget(self._export_backup_button)
        backup_button_row.addWidget(self._restore_backup_button)
        backup_button_row.addStretch(1)
        backup_form.addRow("", self._backup_note)
        backup_form.addRow("", backup_button_row)
        backup_box.setLayout(backup_form)

        cache_box = QGroupBox("Cache Diagnostics")
        cache_form = QFormLayout()
        cache_form.addRow("", self._cache_diagnostics)

        cache_button_row = QHBoxLayout()
        cache_button_row.addWidget(self._refresh_cache_diagnostics_button)
        cache_button_row.addWidget(self._reset_cache_telemetry_button)
        cache_button_row.addWidget(self._clear_cache_button)
        cache_button_row.addWidget(self._purge_expired_button)
        cache_button_row.addStretch(1)
        cache_form.addRow("", cache_button_row)

        cache_prefix_row = QHBoxLayout()
        cache_prefix_row.addWidget(self._cache_prefix_input)
        cache_prefix_row.addWidget(self._clear_prefix_button)
        cache_prefix_row.addStretch(1)
        cache_form.addRow("Clear By Prefix", cache_prefix_row)

        export_row = QHBoxLayout()
        export_row.addWidget(self._export_cache_diagnostics_button)
        export_row.addStretch(1)
        cache_form.addRow("", export_row)
        cache_box.setLayout(cache_form)

        root = QVBoxLayout()
        root.addWidget(settings_box)
        root.addWidget(providers_box)
        root.addWidget(keys_box)
        root.addWidget(rpc_box)
        root.addWidget(abi_box)
        root.addWidget(backup_box)
        root.addWidget(cache_box)
        root.addWidget(self._status)
        root.addStretch(1)
        content = QWidget(self)
        content.setLayout(root)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(content)
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)
        self.setLayout(outer)
        self._load_selected_rpc_url()
        self._refresh_contract_abis()

    def _set_chain_selection(self, chain: Chain) -> None:
        index = self._chain_combo.findData(chain.value)
        if index >= 0:
            self._chain_combo.setCurrentIndex(index)

    def _selected_chain(self) -> Chain:
        data = self._chain_combo.currentData()
        if not isinstance(data, str):
            return Chain.ETHEREUM
        try:
            return Chain(data)
        except ValueError:
            return Chain.ETHEREUM

    def _save_settings(self) -> None:
        selected_chain = self._selected_chain()
        provider_preferences = self._provider_preferences_from_form()
        if provider_preferences is None:
            return
        page_cap = _parse_int_input(
            self._overview_page_cap_input.text(),
            minimum=1,
            maximum=200,
        )
        if page_cap is None:
            self._status.setText("Invalid overview max token pages. Enter an integer between 1 and 200.")
            return
        wallet_ttl = _parse_int_input(
            self._wallet_cache_ttl_input.text(),
            minimum=0,
            maximum=86_400,
        )
        if wallet_ttl is None:
            self._status.setText("Invalid overview cache TTL. Enter an integer between 0 and 86400.")
            return
        activity_ttl = _parse_int_input(
            self._activity_cache_ttl_input.text(),
            minimum=0,
            maximum=86_400,
        )
        if activity_ttl is None:
            self._status.setText("Invalid activity cache TTL. Enter an integer between 0 and 86400.")
            return
        token_detail_ttl = _parse_int_input(
            self._token_detail_cache_ttl_input.text(),
            minimum=0,
            maximum=86_400,
        )
        if token_detail_ttl is None:
            self._status.setText("Invalid token detail cache TTL. Enter an integer between 0 and 86400.")
            return
        pricing_stale_age = _parse_int_input(
            self._pricing_stale_age_input.text(),
            minimum=0,
            maximum=604_800,
        )
        if pricing_stale_age is None:
            self._status.setText("Invalid pricing stale age. Enter an integer between 0 and 604800.")
            return
        cache_max_size = _parse_int_input(
            self._cache_max_size_input.text(),
            minimum=10,
            maximum=500,
        )
        if cache_max_size is None:
            self._status.setText("Invalid cache max size. Enter an integer between 10 and 500.")
            return

        try:
            self._settings = AppSettings.model_validate(
                {
                    "selected_chain": selected_chain,
                    "hide_unverified": self._hide_unverified.isChecked(),
                    "hide_dust": self._hide_dust.isChecked(),
                    "dust_threshold_usd": self._dust_threshold_input.text(),
                    "wallet_overview_max_token_pages": page_cap,
                    "wallet_overview_cache_ttl_seconds": wallet_ttl,
                    "activity_cache_ttl_seconds": activity_ttl,
                    "token_detail_cache_ttl_seconds": token_detail_ttl,
                    "pricing_max_stale_age_seconds": pricing_stale_age,
                    "cache_max_size_mb": cache_max_size,
                    "provider_preferences": provider_preferences,
                }
            )
        except PydanticValidationError as error:
            self._status.setText(f"Invalid settings values: {error}")
            return

        self._container.settings_store.save(self._settings)
        self._status.setText(
            "Settings saved. Restart app to apply provider order and runtime tuning."
        )

    def _provider_preferences_from_form(self) -> list[ProviderPreference] | None:
        alchemy_priority = int(self._alchemy_priority.currentData())
        ankr_priority = int(self._ankr_priority.currentData())
        moralis_priority = int(self._moralis_priority.currentData())
        if len({alchemy_priority, ankr_priority, moralis_priority}) != 3:
            self._status.setText("Alchemy, Ankr, and Moralis must use different priorities.")
            return None

        current_by_id = {
            preference.provider_id: preference
            for preference in self._settings.provider_preferences
        }
        return [
            ProviderPreference(
                provider_id=WalletDataProviderId.ALCHEMY,
                enabled=self._alchemy_enabled.isChecked(),
                priority=alchemy_priority,
            ),
            ProviderPreference(
                provider_id=WalletDataProviderId.ANKR,
                enabled=self._ankr_enabled.isChecked(),
                priority=ankr_priority,
            ),
            ProviderPreference(
                provider_id=WalletDataProviderId.MORALIS,
                enabled=self._moralis_enabled.isChecked(),
                priority=moralis_priority,
            ),
            current_by_id[WalletDataProviderId.GOLDRUSH],
        ]

    def _save_keys(self) -> None:
        if self._key_validation_in_progress:
            return
        alchemy_value = self._alchemy_key_input.text().strip()
        ankr_value = self._ankr_key_input.text().strip()
        moralis_value = self._moralis_key_input.text().strip()

        self._set_key_validation_loading(True)

        def validate_keys() -> object:
            validation_service = self._container.provider_key_validation_service
            alchemy_result = (
                validation_service.validate_alchemy_key(alchemy_value) if alchemy_value else None
            )
            ankr_result = validation_service.validate_ankr_key(ankr_value) if ankr_value else None
            moralis_result = (
                validation_service.validate_moralis_key(moralis_value)
                if moralis_value
                else None
            )
            return _KeyValidationPayload(
                alchemy_value=alchemy_value,
                ankr_value=ankr_value,
                moralis_value=moralis_value,
                alchemy_result=alchemy_result,
                ankr_result=ankr_result,
                moralis_result=moralis_result,
            )

        self._task_runner.start(validate_keys)

    def _save_rpc_url(self) -> None:
        chain = self._selected_rpc_chain()
        endpoint = self._rpc_url_input.text().strip()
        if endpoint and not _is_valid_rpc_endpoint(endpoint):
            self._status.setText("Invalid RPC endpoint. Use a complete http:// or https:// URL.")
            return
        if not self._save_key(f"rpc_url_{chain.value}", endpoint):
            self._status.setText("Could not update the RPC endpoint. Check keyring access.")
            return
        action = "saved" if endpoint else "removed"
        self._status.setText(
            f"{chain.display_name} custom RPC endpoint {action}. Restart to apply it."
        )

    def _selected_rpc_chain(self) -> Chain:
        raw = self._rpc_chain_combo.currentData()
        if isinstance(raw, str):
            try:
                return Chain(raw)
            except ValueError:
                pass
        return Chain.ETHEREUM

    def _load_selected_rpc_url(self) -> None:
        chain = self._selected_rpc_chain()
        endpoint = self._container.secret_store.get_secret(f"rpc_url_{chain.value}") or ""
        self._rpc_url_input.setText(endpoint)

    def _selected_abi_chain(self) -> Chain:
        raw = self._abi_chain_combo.currentData()
        if isinstance(raw, str):
            try:
                return Chain(raw)
            except ValueError:
                pass
        return Chain.ETHEREUM

    def _refresh_contract_abis(self) -> None:
        records = self._container.contract_abi_service.list_contract_abis(
            self._selected_abi_chain()
        )
        self._contract_abi_list.clear()
        for record in records:
            label = record.contract_name or "Unnamed contract"
            self._contract_abi_list.addItem(
                f"{label} | {record.contract_address} | "
                f"{record.content_hash[:12]} | {record.provenance.source_kind.value}"
            )
            item = self._contract_abi_list.item(self._contract_abi_list.count() - 1)
            item.setData(256, record.contract_address)

    def _import_contract_abi(self) -> None:
        contract_address = self._abi_address_input.text().strip()
        if not contract_address:
            self._status.setText("Enter the contract or proxy address before importing an ABI.")
            return
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Import Contract ABI",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return
        try:
            abi_json = Path(file_name).read_text(encoding="utf-8")
            record = self._container.contract_abi_service.import_user_abi(
                chain=self._selected_abi_chain(),
                contract_address=contract_address,
                abi_json=abi_json,
                imported_at=datetime.now(tz=UTC),
                contract_name=self._abi_name_input.text().strip() or None,
                reference=Path(file_name).name,
            )
        except (OSError, ValidationError) as error:
            self._status.setText(f"Could not import contract ABI: {error}")
            return
        self._refresh_contract_abis()
        self._status.setText(
            f"Imported unverified ABI for {record.contract_address}. "
            "Reload a transaction to apply it."
        )

    def _fetch_verified_contract_abi(self) -> None:
        if self._abi_fetch_in_progress:
            return
        contract_address = self._abi_address_input.text().strip()
        if not contract_address:
            self._status.setText("Enter the contract or proxy address before fetching an ABI.")
            return
        chain = self._selected_abi_chain()
        self._set_abi_fetch_loading(True)
        self._abi_task_runner.start(
            lambda: _VerifiedABIPayload(
                self._container.contract_abi_service.fetch_verified_abi(
                    chain,
                    contract_address,
                    datetime.now(tz=UTC),
                )
            )
        )

    def _on_verified_abi_result(self, result: object) -> None:
        if not isinstance(result, _VerifiedABIPayload):
            self._status.setText("Verified ABI lookup returned an invalid result.")
            return
        if result.record is None:
            self._status.setText("Blockscout has no verified ABI for this contract and chain.")
            return
        self._refresh_contract_abis()
        self._status.setText(
            f"Stored verified ABI for {result.record.contract_address} from "
            f"{result.record.provenance.source_name}."
        )

    def _on_verified_abi_error(self, error: object) -> None:
        self._status.setText(f"Could not fetch verified contract ABI: {error}")

    def _on_verified_abi_finished(self) -> None:
        self._set_abi_fetch_loading(False)

    def _set_abi_fetch_loading(self, is_loading: bool) -> None:
        self._abi_fetch_in_progress = is_loading
        self._abi_chain_combo.setEnabled(not is_loading)
        self._abi_address_input.setEnabled(not is_loading)
        self._import_abi_button.setEnabled(not is_loading)
        self._fetch_verified_abi_button.setEnabled(not is_loading)
        self._remove_abi_button.setEnabled(not is_loading)
        if is_loading:
            self._status.setText("Fetching verified contract ABI from Blockscout...")

    def _remove_selected_contract_abi(self) -> None:
        selected = self._contract_abi_list.currentItem()
        if selected is None:
            self._status.setText("Select a contract ABI to remove.")
            return
        address = selected.data(256)
        if not isinstance(address, str):
            self._status.setText("The selected contract ABI is invalid.")
            return
        removed = self._container.contract_abi_service.delete_contract_abi(
            self._selected_abi_chain(), address
        )
        self._refresh_contract_abis()
        self._status.setText(
            f"Removed contract ABI for {address}." if removed else "Contract ABI was not found."
        )

    def _on_key_validation_result(self, raw_result: object) -> None:
        if not isinstance(raw_result, _KeyValidationPayload):
            self._on_key_validation_error(RuntimeError("Provider key validation returned an invalid result."))
            return

        outcomes: list[str] = []
        errors: list[str] = []
        self._save_validated_key(
            key_name="alchemy_api_key",
            provider_name="Alchemy",
            value=raw_result.alchemy_value,
            validation=raw_result.alchemy_result,
            outcomes=outcomes,
            errors=errors,
        )
        self._save_validated_key(
            key_name="ankr_api_key",
            provider_name="Ankr",
            value=raw_result.ankr_value,
            validation=raw_result.ankr_result,
            outcomes=outcomes,
            errors=errors,
        )
        self._save_validated_key(
            key_name="moralis_api_key",
            provider_name="Moralis",
            value=raw_result.moralis_value,
            validation=raw_result.moralis_result,
            outcomes=outcomes,
            errors=errors,
        )

        if errors:
            summary = "; ".join(outcomes) if outcomes else "No keys changed."
            self._status.setText(f"{summary} Validation errors: {'; '.join(errors)}")
            return

        if outcomes:
            self._status.setText("; ".join(outcomes) + ".")
            return
        self._status.setText("No key changes.")

    def _save_validated_key(
        self,
        key_name: str,
        provider_name: str,
        value: str,
        validation: ProviderKeyValidationResult | None,
        outcomes: list[str],
        errors: list[str],
    ) -> None:
        if value:
            if validation is None or not validation.is_valid:
                errors.append(validation.message if validation is not None else f"{provider_name} validation failed.")
            elif self._save_key(key_name, value):
                outcomes.append(f"{provider_name} key saved")
            else:
                errors.append(f"Could not save {provider_name} key. Check keyring access.")
            return
        if self._save_key(key_name, ""):
            outcomes.append(f"{provider_name} key removed")
        else:
            errors.append(f"Could not remove {provider_name} key. Check keyring access.")

    def _on_key_validation_error(self, error: object) -> None:
        self._status.setText(f"Provider key validation failed: {error}")

    def _on_key_validation_finished(self) -> None:
        self._set_key_validation_loading(False)

    def _set_key_validation_loading(self, is_loading: bool) -> None:
        self._key_validation_in_progress = is_loading
        self._alchemy_key_input.setEnabled(not is_loading)
        self._ankr_key_input.setEnabled(not is_loading)
        self._moralis_key_input.setEnabled(not is_loading)
        self._save_keys_button.setEnabled(not is_loading)
        if is_loading:
            self._status.setText("Validating provider keys...")

    def _export_backup_clicked(self) -> None:
        timestamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%SZ")
        default_name = f"oracle41-open-backup-{timestamp}.zip"
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Backup",
            default_name,
            "Zip Files (*.zip);;All Files (*)",
        )
        if not file_name:
            return

        output_path = Path(file_name)
        self._set_backup_loading("export")
        self._backup_task_runner.start(
            lambda: _BackupExportPayload(
                metadata=self._container.backup_restore_service.export_backup(output_path)
            )
        )

    def _restore_backup_clicked(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "Restore Backup",
            "",
            "Zip Files (*.zip);;All Files (*)",
        )
        if not file_name:
            return

        input_path = Path(file_name)
        self._set_backup_loading("restore")
        self._backup_task_runner.start(
            lambda: _BackupRestorePayload(
                settings=self._container.backup_restore_service.restore_backup(input_path)
            )
        )

    def _on_backup_result(self, result: object) -> None:
        if isinstance(result, _BackupExportPayload):
            metadata = result.metadata
            self._status.setText(
                f"Backup exported: {metadata.backup_path} "
                f"(created {metadata.created_at.isoformat()})."
            )
            return

        if isinstance(result, _BackupRestorePayload):
            self._settings = result.settings
            self._apply_settings_to_form(result.settings)
            self._refresh_cache_diagnostics(set_status=False)
            self._status.setText(
                "Backup restored (settings + SQLite state). "
                "Restart app to ensure all runtime services reload state."
            )
            return

        self._status.setText("Backup operation returned an unsupported result.")

    def _on_backup_error(self, error: object) -> None:
        operation = self._backup_operation or "backup"
        self._status.setText(f"Could not {operation} backup: {error}")

    def _on_backup_finished(self) -> None:
        self._backup_operation = None
        self._export_backup_button.setEnabled(True)
        self._restore_backup_button.setEnabled(True)

    def _set_backup_loading(self, operation: str) -> None:
        self._backup_operation = operation
        self._export_backup_button.setEnabled(False)
        self._restore_backup_button.setEnabled(False)
        progress_message = "Exporting backup..." if operation == "export" else "Restoring backup..."
        self._status.setText(progress_message)

    def _refresh_cache_diagnostics_clicked(self) -> None:
        self._refresh_cache_diagnostics(set_status=True)

    def _clear_cache_clicked(self) -> None:
        self._container.cache_store.clear()
        self._refresh_cache_diagnostics(set_status=False)
        self._status.setText("Cache store cleared.")

    def _purge_expired_clicked(self) -> None:
        purge_fn = getattr(self._container.cache_store, "purge_expired", None)
        if not callable(purge_fn):
            self._status.setText("Purge expired is unavailable in this runtime.")
            return
        removed = purge_fn()
        self._refresh_cache_diagnostics(set_status=False)
        self._status.setText(f"Purged {removed} expired cache entr{'y' if removed == 1 else 'ies'}.")

    def _clear_prefix_clicked(self) -> None:
        prefix = self._cache_prefix_input.text().strip()
        if not prefix:
            self._status.setText("Enter a cache key prefix first.")
            return
        remove_prefix = getattr(self._container.cache_store, "remove_by_prefix", None)
        if not callable(remove_prefix):
            self._status.setText("Prefix-based cache clear is unavailable in this runtime.")
            return
        removed = remove_prefix(prefix)
        self._refresh_cache_diagnostics(set_status=False)
        self._status.setText(
            f"Removed {removed} cache entr{'y' if removed == 1 else 'ies'} with prefix '{prefix}'."
        )

    def _export_cache_diagnostics_clicked(self) -> None:
        diagnostics_reader = getattr(self._container.cache_store, "diagnostics", None)
        if not callable(diagnostics_reader):
            self._status.setText("Cache diagnostics export is unavailable in this runtime.")
            return
        diagnostics = diagnostics_reader()
        file_name, _ = QFileDialog.getSaveFileName(
            self,
            "Export Cache Diagnostics",
            "cache-diagnostics.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not file_name:
            return
        output_path = Path(file_name)
        output_path.write_bytes(json_dumps(asdict(diagnostics), pretty=True))
        self._status.setText(f"Cache diagnostics export saved: {output_path}")

    def _reset_cache_telemetry_clicked(self) -> None:
        reset_fn = getattr(self._container.cache_store, "reset_telemetry", None)
        if not callable(reset_fn):
            self._status.setText("Cache telemetry reset is unavailable in this runtime.")
            return
        reset_fn()
        self._refresh_cache_diagnostics(set_status=False)
        self._status.setText("Cache telemetry counters reset.")

    def _refresh_cache_diagnostics(self, set_status: bool) -> None:
        diagnostics_reader = getattr(self._container.cache_store, "diagnostics", None)
        if not callable(diagnostics_reader):
            self._cache_diagnostics.setPlainText("Cache diagnostics are unavailable in this runtime.")
            if set_status:
                self._status.setText("Cache diagnostics are unavailable in this runtime.")
            return

        diagnostics = diagnostics_reader()
        self._cache_diagnostics.setPlainText(_format_cache_diagnostics(diagnostics))
        if set_status:
            self._status.setText("Cache diagnostics refreshed.")

    def _save_key(self, key: str, value: str) -> bool:
        trimmed = value.strip()
        if not trimmed:
            return self._container.secret_store.delete_secret(key)
        return self._container.secret_store.set_secret(key, trimmed)

    def _apply_settings_to_form(self, settings: AppSettings) -> None:
        self._set_chain_selection(settings.selected_chain)
        self._hide_unverified.setChecked(settings.hide_unverified)
        self._hide_dust.setChecked(settings.hide_dust)
        self._dust_threshold_input.setText(settings.dust_threshold_usd)
        self._overview_page_cap_input.setText(str(settings.wallet_overview_max_token_pages))
        self._wallet_cache_ttl_input.setText(str(settings.wallet_overview_cache_ttl_seconds))
        self._activity_cache_ttl_input.setText(str(settings.activity_cache_ttl_seconds))
        self._token_detail_cache_ttl_input.setText(str(settings.token_detail_cache_ttl_seconds))
        self._pricing_stale_age_input.setText(str(settings.pricing_max_stale_age_seconds))
        self._cache_max_size_input.setText(str(settings.cache_max_size_mb))
        alchemy = settings.provider_preference(WalletDataProviderId.ALCHEMY)
        ankr = settings.provider_preference(WalletDataProviderId.ANKR)
        moralis = settings.provider_preference(WalletDataProviderId.MORALIS)
        self._alchemy_enabled.setChecked(alchemy.enabled)
        self._ankr_enabled.setChecked(ankr.enabled)
        self._moralis_enabled.setChecked(moralis.enabled)
        self._set_provider_priority(self._alchemy_priority, alchemy.priority)
        self._set_provider_priority(self._ankr_priority, ankr.priority)
        self._set_provider_priority(self._moralis_priority, moralis.priority)

    @staticmethod
    def _set_provider_priority(combo: QComboBox, priority: int) -> None:
        index = combo.findData(priority)
        if index >= 0:
            combo.setCurrentIndex(index)


def _provider_priority_combo(parent: QWidget) -> QComboBox:
    combo = QComboBox(parent)
    combo.addItem("1 (first)", 1)
    combo.addItem("2 (second)", 2)
    combo.addItem("3 (third)", 3)
    return combo


def _provider_preference_row(enabled: QCheckBox, priority: QComboBox) -> QHBoxLayout:
    row = QHBoxLayout()
    row.addWidget(enabled)
    row.addWidget(QLabel("Priority"))
    row.addWidget(priority)
    row.addStretch(1)
    return row


def _provider_capability_label(
    descriptor: WalletDataProviderDescriptor,
    parent: QWidget,
) -> QLabel:
    chains = ", ".join(chain.display_name for chain in descriptor.supported_chains)
    features = ", ".join(feature.display_name for feature in descriptor.features)
    destination = descriptor.validation_destination or "Not available"
    label = QLabel(
        f"Chains: {chains}\n"
        f"Wallet features: {features}\n"
        f"Credential check connects to: {destination}",
        parent,
    )
    label.setWordWrap(True)
    return label


def _format_cache_diagnostics(diagnostics: CacheDiagnostics) -> str:
    lines = [
        f"Cache file: {diagnostics.cache_file}",
        f"Entries: {diagnostics.entry_count}",
        f"Estimated size: {diagnostics.estimated_size_bytes} bytes",
        f"Configured max size: {diagnostics.max_size_bytes} bytes",
        f"Utilization: {diagnostics.utilization_ratio:.2%}",
        f"Overall hit rate: {diagnostics.hit_rate:.2%}",
        "",
        "Totals:",
        f"- gets={diagnostics.gets} hits={diagnostics.hits} misses={diagnostics.misses} "
        f"expired={diagnostics.expired}",
        f"- sets={diagnostics.sets} removes={diagnostics.removes} evictions={diagnostics.evictions}",
        f"- loads_from_disk={diagnostics.loads_from_disk} persistence_writes={diagnostics.persistence_writes}",
    ]
    if diagnostics.categories:
        lines.append("")
        lines.append("By Category:")
        for category in diagnostics.categories:
            lines.append(
                f"- {category.category}: gets={category.gets} hits={category.hits} "
                f"misses={category.misses} hit_rate={category.hit_rate:.2%} "
                f"expired={category.expired} sets={category.sets} "
                f"removes={category.removes} evictions={category.evictions}"
            )
    else:
        lines.append("")
        lines.append("By Category: no telemetry yet.")
    return "\n".join(lines)


def _parse_int_input(raw: str, minimum: int, maximum: int) -> int | None:
    trimmed = raw.strip()
    if not trimmed:
        return None
    if not trimmed.isdigit():
        return None
    parsed = int(trimmed)
    if parsed < minimum or parsed > maximum:
        return None
    return parsed


def _is_valid_rpc_endpoint(endpoint: str) -> bool:
    parsed = urlparse(endpoint)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)
