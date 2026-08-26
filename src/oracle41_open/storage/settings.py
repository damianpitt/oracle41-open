"""Load and validate local application preferences.

Settings are stored as JSON in the platform configuration directory with safe defaults for missing files.
Provider keys and private RPC URLs are not part of this settings file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path

from platformdirs import user_config_dir
from pydantic import BaseModel, Field, field_validator, model_validator

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import Chain
from oracle41_open.providers.capabilities import WalletDataProviderId


class ProviderPreference(BaseModel):
    """Store a wallet-data provider's enabled state and request priority."""

    provider_id: WalletDataProviderId
    enabled: bool
    priority: int = Field(ge=1, le=4)


class CredentialSource(str, Enum):
    """Identify where a credential was found without storing its value."""

    KEYRING = "keyring"
    ENVIRONMENT = "environment"


class CredentialValidationState(str, Enum):
    """Record whether the current credential source has passed a check."""

    NOT_CHECKED = "not_checked"
    VALID = "valid"


class ProviderCredentialDiagnostic(BaseModel):
    """Persist safe validation metadata without credential-derived data."""

    provider_id: WalletDataProviderId
    state: CredentialValidationState = CredentialValidationState.NOT_CHECKED
    source: CredentialSource | None = None
    validated_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_state(self) -> ProviderCredentialDiagnostic:
        if self.state is CredentialValidationState.VALID:
            if self.source is None or self.validated_at is None:
                raise ValueError("A valid credential diagnostic requires source and time.")
            if self.validated_at.tzinfo is None:
                raise ValueError("Credential validation time must include a timezone.")
        elif self.source is not None or self.validated_at is not None:
            raise ValueError("Unchecked credential diagnostics cannot include validation metadata.")
        return self


def _default_provider_preferences() -> list[ProviderPreference]:
    # Optional providers stay disabled until a user adds a key and enables them.
    return [
        ProviderPreference(
            provider_id=WalletDataProviderId.ALCHEMY,
            enabled=True,
            priority=1,
        ),
        ProviderPreference(
            provider_id=WalletDataProviderId.ANKR,
            enabled=True,
            priority=2,
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


def _default_credential_diagnostics() -> list[ProviderCredentialDiagnostic]:
    return [
        ProviderCredentialDiagnostic(provider_id=provider_id)
        for provider_id in WalletDataProviderId
    ]


class AppSettings(BaseModel):
    selected_chain: Chain = Chain.ETHEREUM
    hide_unverified: bool = True
    hide_dust: bool = False
    dust_threshold_usd: str = "1"
    wallet_overview_max_token_pages: int = Field(default=20, ge=1, le=200)
    wallet_overview_cache_ttl_seconds: int = Field(default=300, ge=0, le=86_400)
    activity_cache_ttl_seconds: int = Field(default=120, ge=0, le=86_400)
    token_detail_cache_ttl_seconds: int = Field(default=120, ge=0, le=86_400)
    pricing_max_stale_age_seconds: int = Field(default=86_400, ge=0, le=604_800)
    cache_max_size_mb: int = Field(default=150, ge=10, le=500)
    provider_preferences: list[ProviderPreference] = Field(
        default_factory=_default_provider_preferences
    )
    provider_credential_diagnostics: list[ProviderCredentialDiagnostic] = Field(
        default_factory=_default_credential_diagnostics
    )

    @field_validator("dust_threshold_usd")
    @classmethod
    def _normalize_dust_threshold(cls, value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            return "0"
        try:
            parsed = Decimal(trimmed)
        except InvalidOperation as error:
            raise ValueError("Dust threshold must be numeric.") from error
        if parsed < 0:
            raise ValueError("Dust threshold cannot be negative.")
        normalized = parsed.normalize()
        if normalized == normalized.to_integral():
            return str(normalized.quantize(Decimal("1")))
        return format(normalized, "f")

    @model_validator(mode="after")
    def _validate_provider_preferences(self) -> AppSettings:
        provider_ids = [preference.provider_id for preference in self.provider_preferences]
        priorities = [preference.priority for preference in self.provider_preferences]
        expected_ids = set(WalletDataProviderId)

        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("Provider preferences contain duplicate provider IDs.")
        if set(provider_ids) != expected_ids:
            raise ValueError("Provider preferences must contain every supported provider ID.")
        if len(priorities) != len(set(priorities)):
            raise ValueError("Provider preferences contain duplicate priorities.")

        diagnostic_ids = [
            diagnostic.provider_id
            for diagnostic in self.provider_credential_diagnostics
        ]
        if len(diagnostic_ids) != len(set(diagnostic_ids)):
            raise ValueError("Credential diagnostics contain duplicate provider IDs.")
        if set(diagnostic_ids) != expected_ids:
            raise ValueError("Credential diagnostics must contain every supported provider ID.")
        return self

    def ordered_enabled_provider_ids(self) -> tuple[WalletDataProviderId, ...]:
        """Return enabled wallet providers in the user's chosen order."""

        ordered = sorted(self.provider_preferences, key=lambda preference: preference.priority)
        return tuple(preference.provider_id for preference in ordered if preference.enabled)

    def provider_preference(self, provider_id: WalletDataProviderId) -> ProviderPreference:
        """Return one provider preference from the validated complete set."""

        return next(
            preference
            for preference in self.provider_preferences
            if preference.provider_id == provider_id
        )

    def credential_diagnostic(
        self,
        provider_id: WalletDataProviderId,
    ) -> ProviderCredentialDiagnostic:
        """Return safe validation metadata for one provider."""

        return next(
            diagnostic
            for diagnostic in self.provider_credential_diagnostics
            if diagnostic.provider_id == provider_id
        )


@dataclass
class SettingsStore:
    file_path: Path

    @staticmethod
    def default() -> SettingsStore:
        root = Path(user_config_dir(appname="oracle41-open", appauthor=False))
        return SettingsStore(file_path=root / "settings.json")

    def load(self) -> AppSettings:
        if not self.file_path.exists():
            return AppSettings()
        data = json_loads(self.file_path.read_bytes())
        return AppSettings.model_validate(data)

    def save(self, settings: AppSettings) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = settings.model_dump(mode="json")
        self.file_path.write_bytes(json_dumps(payload, pretty=True))

    def update(self, **changes: object) -> AppSettings:
        current = self.load()
        updated = current.model_copy(update=changes)
        self.save(updated)
        return updated
