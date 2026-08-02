from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QTabWidget

from oracle41_open.app.bootstrap import build_container
from oracle41_open.gui.main_window import MainWindow
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

    window.close()
