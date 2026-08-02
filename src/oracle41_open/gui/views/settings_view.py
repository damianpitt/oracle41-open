from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

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
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import Chain
from oracle41_open.core.services.provider_key_validation_service import ProviderKeyValidationResult
from oracle41_open.gui.task_runner import BackgroundTaskRunner
from oracle41_open.storage.backup_restore import BackupMetadata
from oracle41_open.storage.settings import AppSettings

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer
    from oracle41_open.storage.cache_store import CacheDiagnostics


@dataclass(frozen=True)
class _KeyValidationPayload:
    alchemy_value: str
    ankr_value: str
    alchemy_result: ProviderKeyValidationResult | None
    ankr_result: ProviderKeyValidationResult | None


@dataclass(frozen=True)
class _BackupExportPayload:
    metadata: BackupMetadata


@dataclass(frozen=True)
class _BackupRestorePayload:
    settings: AppSettings


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

        self._alchemy_key_input = QLineEdit(self)
        self._alchemy_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._alchemy_key_input.setPlaceholderText("Alchemy API Key")
        self._alchemy_key_input.setText(
            self._container.secret_store.get_secret("alchemy_api_key") or ""
        )

        self._ankr_key_input = QLineEdit(self)
        self._ankr_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._ankr_key_input.setPlaceholderText("Ankr API Key")
        self._ankr_key_input.setText(self._container.secret_store.get_secret("ankr_api_key") or "")

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

        keys_box = QGroupBox("Provider Keys")
        keys_form = QFormLayout()
        keys_form.addRow("Alchemy", self._alchemy_key_input)
        keys_form.addRow("Ankr", self._ankr_key_input)

        button_row = QHBoxLayout()
        button_row.addWidget(self._save_keys_button)
        button_row.addStretch(1)
        keys_form.addRow("", button_row)
        keys_box.setLayout(keys_form)

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
        root.addWidget(keys_box)
        root.addWidget(backup_box)
        root.addWidget(cache_box)
        root.addWidget(self._status)
        root.addStretch(1)
        self.setLayout(root)

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
                }
            )
        except PydanticValidationError as error:
            self._status.setText(f"Invalid settings values: {error}")
            return

        self._container.settings_store.save(self._settings)
        self._status.setText(
            "Settings saved. Restart app to apply cache/page/TTL tuning to running services."
        )

    def _save_keys(self) -> None:
        if self._key_validation_in_progress:
            return
        alchemy_value = self._alchemy_key_input.text().strip()
        ankr_value = self._ankr_key_input.text().strip()

        self._set_key_validation_loading(True)

        def validate_keys() -> object:
            validation_service = self._container.provider_key_validation_service
            alchemy_result = (
                validation_service.validate_alchemy_key(alchemy_value) if alchemy_value else None
            )
            ankr_result = validation_service.validate_ankr_key(ankr_value) if ankr_value else None
            return _KeyValidationPayload(
                alchemy_value=alchemy_value,
                ankr_value=ankr_value,
                alchemy_result=alchemy_result,
                ankr_result=ankr_result,
            )

        self._task_runner.start(validate_keys)

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
