"""Smoke-test the desktop interface offscreen.

The test creates the main window and verifies that all expected tabs can be constructed.
It catches missing runtime wiring without opening a visible window.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QTabWidget

from oracle41_open.app.bootstrap import build_container
from oracle41_open.gui.main_window import MainWindow
from oracle41_open.gui.views.portfolio_view import PortfolioView
from oracle41_open.storage.secrets import SecretStore


def test_main_window_constructs_all_primary_tabs(
    qt_application: QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("ORACLE41_ALCHEMY_API_KEY", raising=False)
    monkeypatch.delenv("ORACLE41_ANKR_API_KEY", raising=False)
    monkeypatch.setattr(SecretStore, "get_secret", lambda self, key: None)

    window = MainWindow(container=build_container())
    qt_application.processEvents()

    tabs = window.findChild(QTabWidget)
    assert tabs is not None
    assert [tabs.tabText(index) for index in range(tabs.count())] == [
        "Overview",
        "Activity",
        "Token Detail",
        "Watchlist",
        "Portfolio",
        "Notes & Views",
        "Snapshots",
        "Settings",
    ]

    portfolio = window.findChild(PortfolioView)
    assert portfolio is not None
    assert portfolio._export_template_combo.findData("protocol_positions") >= 0
    assert not portfolio._protocol_block_combo.isEnabled()
    portfolio._protocol_snapshot_mode_combo.setCurrentIndex(1)
    qt_application.processEvents()
    assert portfolio._protocol_block_combo.isEnabled()
    assert portfolio._refresh_protocol_button.isEnabled()
    portfolio._on_protocol_history_clicked()
    assert portfolio._status_label.text() == (
        "No stored protocol snapshots were found in this scope."
    )

    window.close()
