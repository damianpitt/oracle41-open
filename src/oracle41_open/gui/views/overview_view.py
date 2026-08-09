from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
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

from oracle41_open.core.models import Chain, ValidationError, WalletOverviewResult
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.gui.task_runner import BackgroundTaskRunner

if TYPE_CHECKING:
    from oracle41_open.app.bootstrap import AppContainer


@dataclass(frozen=True)
class _OverviewLoadPayload:
    result: WalletOverviewResult
    resolved_address: str
    input_name: str | None


class OverviewView(QWidget):
    def __init__(self, container: AppContainer) -> None:
        super().__init__()
        self._container = container
        self._task_runner = BackgroundTaskRunner(parent=self)
        self._task_runner.result.connect(self._on_wallet_loaded)
        self._task_runner.error.connect(self._on_wallet_load_error)
        self._task_runner.finished.connect(self._on_wallet_load_finished)
        self._is_loading = False
        self._pending_load: tuple[bool, str, Chain] | None = None

        self._chain_combo = QComboBox(self)
        for chain in Chain:
            self._chain_combo.addItem(chain.display_name, chain.value)

        self._address_input = QLineEdit(self)
        self._address_input.setPlaceholderText("0x…")
        self._snapshot_label_input = QLineEdit(self)
        self._snapshot_label_input.setPlaceholderText("Optional snapshot label")

        self._load_button = QPushButton("Load Wallet", self)
        self._load_button.clicked.connect(self._on_load_clicked)
        self._refresh_button = QPushButton("Refresh Wallet", self)
        self._refresh_button.clicked.connect(self._on_refresh_clicked)
        self._clear_cache_button = QPushButton("Clear Overview Cache", self)
        self._clear_cache_button.clicked.connect(self._on_clear_cache_clicked)
        self._save_snapshot_button = QPushButton("Save Snapshot", self)
        self._save_snapshot_button.clicked.connect(self._on_save_snapshot_clicked)

        self._status_label = QLabel(self._ready_status_text(), self)
        self._status_label.setWordWrap(True)

        self._results = QTextEdit(self)
        self._results.setReadOnly(True)
        self._results.setPlaceholderText("Wallet overview output appears here.")
        self._last_result: WalletOverviewResult | None = None
        self._last_loaded_address: str | None = None
        self._last_loaded_chain: Chain | None = None
        self._last_loaded_input: str | None = None

        self._init_layout()

    def _init_layout(self) -> None:
        controls_box = QGroupBox("Wallet Overview")
        form = QFormLayout()
        form.addRow("Chain", self._chain_combo)
        form.addRow("Wallet Address", self._address_input)
        form.addRow("Snapshot Label", self._snapshot_label_input)

        button_row = QHBoxLayout()
        button_row.addWidget(self._load_button)
        button_row.addWidget(self._refresh_button)
        button_row.addWidget(self._clear_cache_button)
        button_row.addWidget(self._save_snapshot_button)
        button_row.addStretch(1)
        form.addRow("", button_row)
        controls_box.setLayout(form)

        root = QVBoxLayout()
        root.addWidget(controls_box)
        root.addWidget(self._status_label)
        root.addWidget(self._results, stretch=1)
        root.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.setLayout(root)

    def _selected_chain(self) -> Chain:
        raw = self._chain_combo.currentData()
        if not isinstance(raw, str):
            return Chain.ETHEREUM
        try:
            return Chain(raw)
        except ValueError:
            return Chain.ETHEREUM

    def _on_load_clicked(self) -> None:
        self._load_wallet(force_refresh=False)

    def _on_refresh_clicked(self) -> None:
        self._load_wallet(force_refresh=True)

    def load_wallet(self, address: str, chain: Chain, force_refresh: bool = False) -> None:
        normalized = AddressValidator.normalized(address)
        self._address_input.setText(normalized)
        chain_index = self._chain_combo.findData(chain.value)
        if chain_index >= 0:
            self._chain_combo.setCurrentIndex(chain_index)
        self._load_wallet(force_refresh=force_refresh)

    def _load_wallet(self, force_refresh: bool) -> None:
        if self._is_loading:
            return

        address = self._address_input.text()
        normalized = AddressValidator.normalized(address)
        validation_error = AddressValidator.validation_error(normalized)
        if validation_error is not None and not _looks_like_ens_name(address):
            self._status_label.setText(validation_error)
            self._results.clear()
            self._last_result = None
            self._last_loaded_address = None
            self._last_loaded_chain = None
            self._last_loaded_input = None
            return

        chain = self._selected_chain()
        settings = self._container.settings_store.load()
        self._pending_load = (force_refresh, address.strip().lower(), chain)
        self._set_loading(True, status_text="Loading wallet overview...")

        def load_payload() -> object:
            resolution = self._container.label_resolution_service.resolve_input(address, chain)
            result = self._container.wallet_service.load_wallet_overview(
                resolution.address,
                chain,
                hide_unverified=settings.hide_unverified,
                hide_dust=settings.hide_dust,
                dust_threshold_usd=settings.dust_threshold_usd,
                force_refresh=force_refresh,
            )
            return _OverviewLoadPayload(
                result=result,
                resolved_address=resolution.address,
                input_name=resolution.input_name,
            )

        self._task_runner.start(load_payload)

    def _on_wallet_loaded(self, raw_result: object) -> None:
        if not isinstance(raw_result, _OverviewLoadPayload) or self._pending_load is None:
            self._on_wallet_load_error(RuntimeError("Wallet overview returned an invalid result."))
            return

        force_refresh, input_value, chain = self._pending_load
        source_label = "live providers" if self._container.uses_live_providers else "local stub providers"
        action_label = "refreshed" if force_refresh else "loaded"
        if raw_result.result.token_balances_truncated:
            self._status_label.setText(
                f"Wallet {action_label} from {source_label}. "
                f"Token scan hit page cap ({raw_result.result.token_balance_page_count})."
            )
        else:
            self._status_label.setText(f"Wallet {action_label} from {source_label}.")
        self._results.setPlainText(self._render_result(raw_result.result))
        self._last_result = raw_result.result
        self._last_loaded_address = raw_result.resolved_address
        self._last_loaded_chain = chain
        self._last_loaded_input = input_value

    def _on_wallet_load_error(self, error: object) -> None:
        message = str(error)
        self._status_label.setText(message if isinstance(error, ValidationError) else f"Could not load wallet overview: {message}")
        self._results.clear()
        self._last_result = None
        self._last_loaded_address = None
        self._last_loaded_chain = None
        self._last_loaded_input = None

    def _on_wallet_load_finished(self) -> None:
        self._pending_load = None
        self._set_loading(False)

    def _set_loading(self, is_loading: bool, status_text: str | None = None) -> None:
        self._is_loading = is_loading
        enabled = not is_loading
        for widget in (
            self._chain_combo,
            self._address_input,
            self._snapshot_label_input,
            self._load_button,
            self._refresh_button,
            self._clear_cache_button,
            self._save_snapshot_button,
        ):
            widget.setEnabled(enabled)
        if status_text is not None:
            self._status_label.setText(status_text)

    def _on_clear_cache_clicked(self) -> None:
        address = self._address_input.text()
        normalized = AddressValidator.normalized(address)
        validation_error = AddressValidator.validation_error(normalized)
        if validation_error is not None:
            if (
                self._last_loaded_input != address.strip().lower()
                or self._last_loaded_address is None
            ):
                self._status_label.setText("Load this ENS name before clearing its overview cache.")
                return
            normalized = self._last_loaded_address

        chain = self._selected_chain()
        cleared = self._container.wallet_service.clear_cached_overview(normalized, chain)
        if cleared:
            self._status_label.setText("Wallet overview cache cleared for this address and chain.")
            return
        self._status_label.setText("Wallet overview cache is unavailable in this runtime.")

    def _on_save_snapshot_clicked(self) -> None:
        if self._last_result is None or self._last_loaded_address is None or self._last_loaded_chain is None:
            self._status_label.setText("Load wallet overview before saving a snapshot.")
            return

        current_input = self._address_input.text().strip().lower()
        address = self._last_loaded_address
        if address is None:
            self._status_label.setText("Load wallet overview before saving a snapshot.")
            return

        chain = self._selected_chain()
        if current_input != self._last_loaded_input or chain != self._last_loaded_chain:
            self._status_label.setText(
                "Current form differs from last loaded wallet. Load wallet first, then save snapshot."
            )
            return

        raw_label = self._snapshot_label_input.text().strip()
        label = raw_label if raw_label else None
        try:
            snapshot = self._container.snapshots_repository.create_snapshot(
                address=address,
                chain=chain,
                overview=self._last_result,
                label=label,
            )
        except Exception as error:
            self._status_label.setText(f"Could not save snapshot: {error}")
            return

        self._status_label.setText(
            f"Snapshot saved (id={snapshot.id}, chain={chain.display_name}, tokens={snapshot.token_count})."
        )

    def _render_result(self, result: WalletOverviewResult) -> str:
        lines = [
            f"Updated at: {result.updated_at.isoformat()}",
            f"Native balance: {result.native_balance}",
            f"Native price (USD): {self._fmt_decimal(result.native_price_usd)}",
            f"Portfolio total (USD): {self._fmt_decimal(result.total_usd)}",
            f"Token page count: {result.token_balance_page_count}",
            f"Token balances truncated: {'yes' if result.token_balances_truncated else 'no'}",
            "",
            "Token Balances:",
        ]
        if not result.token_balances:
            lines.append("- none")
        else:
            for balance in result.token_balances:
                lines.append(
                    f"- {balance.token.symbol} ({balance.token.name}) | "
                    f"balance={balance.balance_decimal} | "
                    f"price={self._fmt_decimal(balance.price_usd)} | "
                    f"value={self._fmt_decimal(balance.balance_usd)}"
                )
        return "\n".join(lines)

    def _fmt_decimal(self, value: Decimal | None) -> str:
        if value is None:
            return "n/a"
        return f"{value:.6f}"

    def _ready_status_text(self) -> str:
        if self._container.uses_live_providers:
            return "Ready. Live Alchemy providers are enabled."
        return "Ready. No Alchemy API key configured; using local stub providers."


def _looks_like_ens_name(value: str) -> bool:
    normalized = value.strip().lower()
    return normalized.endswith(".eth") and " " not in normalized and len(normalized) <= 255
