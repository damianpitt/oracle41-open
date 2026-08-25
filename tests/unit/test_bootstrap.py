"""Test application dependency construction.

The cases verify provider selection, settings, repositories, services, and live-versus-stub behavior.
Environment and keyring values are isolated for each test.
"""

from pathlib import Path

import pytest

from oracle41_open.app.bootstrap import _load_provider_key, build_container
from oracle41_open.providers.failover import OrderedDataProviderPool
from oracle41_open.storage.secrets import SecretStore
from oracle41_open.storage.settings import (
    AppSettings,
    ProviderPreference,
    SettingsStore,
    WalletDataProviderId,
)


class _SecretStore:
    def __init__(self, value: str | None) -> None:
        self.value = value

    def get_secret(self, key: str) -> str | None:
        _ = key
        return self.value


def test_provider_key_prefers_keyring_value(monkeypatch: object) -> None:
    monkeypatch.setenv("ORACLE41_TEST_KEY", "environment-key")  # type: ignore[attr-defined]

    result = _load_provider_key(  # type: ignore[arg-type]
        _SecretStore("stored-key"),
        key_name="provider_key",
        environment_name="ORACLE41_TEST_KEY",
    )

    assert result == "stored-key"


def test_provider_key_uses_environment_fallback(monkeypatch: object) -> None:
    monkeypatch.setenv("ORACLE41_TEST_KEY", " environment-key ")  # type: ignore[attr-defined]

    result = _load_provider_key(  # type: ignore[arg-type]
        _SecretStore(None),
        key_name="provider_key",
        environment_name="ORACLE41_TEST_KEY",
    )

    assert result == "environment-key"


def test_bootstrap_uses_enabled_providers_in_saved_priority_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("ORACLE41_ALCHEMY_API_KEY", "alchemy-test-key")
    monkeypatch.setenv("ORACLE41_ANKR_API_KEY", "ankr-test-key")
    monkeypatch.setattr(SecretStore, "get_secret", lambda self, key: None)
    SettingsStore.default().save(
        AppSettings(
            provider_preferences=[
                ProviderPreference(
                    provider_id=WalletDataProviderId.ALCHEMY,
                    enabled=True,
                    priority=2,
                ),
                ProviderPreference(
                    provider_id=WalletDataProviderId.ANKR,
                    enabled=True,
                    priority=1,
                ),
                ProviderPreference(
                    provider_id=WalletDataProviderId.MORALIS,
                    enabled=False,
                    priority=3,
                ),
                ProviderPreference(
                    provider_id=WalletDataProviderId.GOLDRUSH,
                    enabled=False,
                    priority=4,
                ),
            ]
        )
    )

    container = build_container()

    assert isinstance(container.data_provider, OrderedDataProviderPool)
    assert container.data_provider.provider_ids == ("ankr", "alchemy")
    assert container.uses_live_providers


def test_bootstrap_does_not_configure_disabled_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("ORACLE41_ALCHEMY_API_KEY", "alchemy-test-key")
    monkeypatch.setenv("ORACLE41_ANKR_API_KEY", "ankr-test-key")
    monkeypatch.setattr(SecretStore, "get_secret", lambda self, key: None)
    settings = AppSettings()
    settings.provider_preferences[0].enabled = False
    SettingsStore.default().save(settings)

    container = build_container()

    assert isinstance(container.data_provider, OrderedDataProviderPool)
    assert container.data_provider.provider_ids == ("ankr",)


def test_bootstrap_configures_enabled_moralis_in_saved_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("ORACLE41_MORALIS_API_KEY", "moralis-test-key")
    monkeypatch.setattr(SecretStore, "get_secret", lambda self, key: None)
    settings = AppSettings()
    settings.provider_preferences[0].enabled = False
    settings.provider_preferences[1].enabled = False
    settings.provider_preferences[2].enabled = True
    SettingsStore.default().save(settings)

    container = build_container()

    assert isinstance(container.data_provider, OrderedDataProviderPool)
    assert container.data_provider.provider_ids == ("moralis",)
    assert container.uses_live_providers
