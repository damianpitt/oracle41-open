"""Test safe provider credential status and validation-time persistence.

The cases use fake key presence and never store credential values in settings or assertions.
They cover keyring priority, environment fallback, source changes, successful checks, and clearing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from oracle41_open.core.services.provider_credential_diagnostics_service import (
    ProviderCredentialDiagnosticsService,
)
from oracle41_open.providers.capabilities import WalletDataProviderId
from oracle41_open.storage.secrets import SecretStore
from oracle41_open.storage.settings import (
    CredentialSource,
    CredentialValidationState,
    SettingsStore,
)


class _SecretStore:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = values

    def get_secret(self, key: str) -> str | None:
        return self.values.get(key)


def _service(
    tmp_path: Path,
    secrets: dict[str, str] | None = None,
    environment: dict[str, str] | None = None,
) -> ProviderCredentialDiagnosticsService:
    return ProviderCredentialDiagnosticsService(
        settings_store=SettingsStore(tmp_path / "settings.json"),
        secret_store=cast(SecretStore, _SecretStore(secrets or {})),
        environment=environment or {},
    )


def test_credential_status_prefers_keyring_without_exposing_value(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        secrets={"alchemy_api_key": "stored-secret"},
        environment={"ORACLE41_ALCHEMY_API_KEY": "environment-secret"},
    )

    status = service.status(WalletDataProviderId.ALCHEMY)

    assert status.source is CredentialSource.KEYRING
    assert status.state is CredentialValidationState.NOT_CHECKED
    assert status.validated_at is None
    assert "secret" not in repr(status)


def test_successful_status_roundtrips_and_source_change_invalidates_it(
    tmp_path: Path,
) -> None:
    secrets = {"moralis_api_key": "stored-secret"}
    service = _service(tmp_path, secrets=secrets)
    checked_at = datetime(2026, 8, 26, 9, 30, tzinfo=UTC)

    status = service.record_success(
        WalletDataProviderId.MORALIS,
        validated_at=checked_at,
    )

    assert status.state is CredentialValidationState.VALID
    assert status.validated_at == checked_at
    persisted = SettingsStore(tmp_path / "settings.json").load()
    assert persisted.credential_diagnostic(WalletDataProviderId.MORALIS).validated_at == checked_at
    assert "stored-secret" not in (tmp_path / "settings.json").read_text(encoding="utf-8")

    secrets.clear()
    environment_service = _service(
        tmp_path,
        environment={"ORACLE41_MORALIS_API_KEY": "different-secret"},
    )
    changed = environment_service.status(WalletDataProviderId.MORALIS)
    assert changed.source is CredentialSource.ENVIRONMENT
    assert changed.state is CredentialValidationState.NOT_CHECKED
    assert changed.validated_at is None


def test_clearing_validation_reports_current_environment_source(tmp_path: Path) -> None:
    service = _service(
        tmp_path,
        environment={"ORACLE41_GOLDRUSH_API_KEY": "environment-secret"},
    )
    service.record_success(
        WalletDataProviderId.GOLDRUSH,
        source=CredentialSource.ENVIRONMENT,
    )

    status = service.clear(WalletDataProviderId.GOLDRUSH)

    assert status.source is CredentialSource.ENVIRONMENT
    assert status.state is CredentialValidationState.NOT_CHECKED
    assert status.validated_at is None
