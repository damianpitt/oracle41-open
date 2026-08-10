from pathlib import Path

import pytest

from oracle41_open.app.bootstrap import build_container
from oracle41_open.core.models import Chain
from oracle41_open.storage.secrets import SecretStore


def test_bootstrap_configures_chain_specific_transaction_provider_from_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("ORACLE41_RPC_BASE_URL", "https://base.example")
    monkeypatch.delenv("ORACLE41_ALCHEMY_API_KEY", raising=False)
    monkeypatch.delenv("ORACLE41_ANKR_API_KEY", raising=False)
    monkeypatch.setattr(SecretStore, "get_secret", lambda self, key: None)

    container = build_container()

    assert container.transaction_inspection_service.capabilities(Chain.BASE).receipts
    assert not container.transaction_inspection_service.capabilities(Chain.ETHEREUM).receipts
