"""Report provider credential presence and safe validation metadata.

The service checks whether credentials come from the keyring or environment without returning their values.
It persists only a source label, successful validation time, and provider ID in local settings.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from oracle41_open.providers.capabilities import WalletDataProviderId
from oracle41_open.storage.secrets import SecretStore
from oracle41_open.storage.settings import (
    CredentialSource,
    CredentialValidationState,
    ProviderCredentialDiagnostic,
    SettingsStore,
)

_CREDENTIAL_NAMES = {
    WalletDataProviderId.ALCHEMY: ("alchemy_api_key", "ORACLE41_ALCHEMY_API_KEY"),
    WalletDataProviderId.ANKR: ("ankr_api_key", "ORACLE41_ANKR_API_KEY"),
    WalletDataProviderId.MORALIS: ("moralis_api_key", "ORACLE41_MORALIS_API_KEY"),
    WalletDataProviderId.GOLDRUSH: ("goldrush_api_key", "ORACLE41_GOLDRUSH_API_KEY"),
}


@dataclass(frozen=True)
class ProviderCredentialStatus:
    """Describe credential readiness without containing credential material."""

    provider_id: WalletDataProviderId
    source: CredentialSource | None
    state: CredentialValidationState
    validated_at: datetime | None

    @property
    def is_configured(self) -> bool:
        return self.source is not None


class ProviderCredentialDiagnosticsService:
    """Read credential presence and update successful-check metadata."""

    def __init__(
        self,
        settings_store: SettingsStore,
        secret_store: SecretStore,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self._settings_store = settings_store
        self._secret_store = secret_store
        self._environment = environment if environment is not None else os.environ

    def status(self, provider_id: WalletDataProviderId) -> ProviderCredentialStatus:
        """Return current source and applicable saved validation state."""

        source = self._credential_source(provider_id)
        diagnostic = self._settings_store.load().credential_diagnostic(provider_id)
        if source is None or diagnostic.source is not source:
            return ProviderCredentialStatus(
                provider_id=provider_id,
                source=source,
                state=CredentialValidationState.NOT_CHECKED,
                validated_at=None,
            )
        return ProviderCredentialStatus(
            provider_id=provider_id,
            source=source,
            state=diagnostic.state,
            validated_at=diagnostic.validated_at,
        )

    def record_success(
        self,
        provider_id: WalletDataProviderId,
        source: CredentialSource = CredentialSource.KEYRING,
        validated_at: datetime | None = None,
    ) -> ProviderCredentialStatus:
        """Persist a successful check without storing any credential-derived value."""

        checked_at = validated_at or datetime.now(tz=UTC)
        if checked_at.tzinfo is None:
            raise ValueError("Credential validation time must include a timezone.")
        diagnostic = ProviderCredentialDiagnostic(
            provider_id=provider_id,
            state=CredentialValidationState.VALID,
            source=source,
            validated_at=checked_at,
        )
        self._replace_diagnostic(diagnostic)
        return self.status(provider_id)

    def clear(self, provider_id: WalletDataProviderId) -> ProviderCredentialStatus:
        """Forget validation metadata after a credential is removed or replaced."""

        self._replace_diagnostic(ProviderCredentialDiagnostic(provider_id=provider_id))
        return self.status(provider_id)

    def _credential_source(self, provider_id: WalletDataProviderId) -> CredentialSource | None:
        key_name, environment_name = _CREDENTIAL_NAMES[provider_id]
        if (self._secret_store.get_secret(key_name) or "").strip():
            return CredentialSource.KEYRING
        if self._environment.get(environment_name, "").strip():
            return CredentialSource.ENVIRONMENT
        return None

    def _replace_diagnostic(self, replacement: ProviderCredentialDiagnostic) -> None:
        settings = self._settings_store.load()
        diagnostics = [
            replacement if item.provider_id is replacement.provider_id else item
            for item in settings.provider_credential_diagnostics
        ]
        self._settings_store.save(
            settings.model_copy(update={"provider_credential_diagnostics": diagnostics})
        )
