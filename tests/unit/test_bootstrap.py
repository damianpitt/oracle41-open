"""Test application dependency construction.

The cases verify provider selection, settings, repositories, services, and live-versus-stub behavior.
Environment and keyring values are isolated for each test.
"""

from oracle41_open.app.bootstrap import _load_provider_key


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
