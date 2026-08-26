"""Test local settings persistence.

The cases cover defaults, round trips, invalid files, and supported preference bounds.
They confirm secrets are not part of the settings model.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import Chain
from oracle41_open.storage.settings import (
    AppSettings,
    CredentialSource,
    CredentialValidationState,
    ProviderCredentialDiagnostic,
    ProviderPreference,
    SettingsStore,
    WalletDataProviderId,
)


def test_settings_store_roundtrip(tmp_path: Path) -> None:
    store = SettingsStore(file_path=tmp_path / "settings.json")
    initial = store.load()
    assert initial.selected_chain is Chain.ETHEREUM
    assert initial.wallet_overview_max_token_pages == 20
    assert initial.wallet_overview_cache_ttl_seconds == 300
    assert initial.activity_cache_ttl_seconds == 120
    assert initial.token_detail_cache_ttl_seconds == 120
    assert initial.pricing_max_stale_age_seconds == 86_400
    assert initial.cache_max_size_mb == 150
    assert initial.ordered_enabled_provider_ids() == (
        WalletDataProviderId.ALCHEMY,
        WalletDataProviderId.ANKR,
    )
    assert all(
        item.state is CredentialValidationState.NOT_CHECKED
        for item in initial.provider_credential_diagnostics
    )

    updated = AppSettings(
        selected_chain=Chain.BASE,
        hide_unverified=False,
        hide_dust=True,
        dust_threshold_usd="5",
        wallet_overview_max_token_pages=45,
        wallet_overview_cache_ttl_seconds=900,
        activity_cache_ttl_seconds=180,
        token_detail_cache_ttl_seconds=180,
        pricing_max_stale_age_seconds=172_800,
        cache_max_size_mb=220,
        provider_preferences=[
            ProviderPreference(
                provider_id=WalletDataProviderId.ALCHEMY,
                enabled=False,
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
        ],
        provider_credential_diagnostics=[
            ProviderCredentialDiagnostic(
                provider_id=provider_id,
                state=(
                    CredentialValidationState.VALID
                    if provider_id is WalletDataProviderId.ANKR
                    else CredentialValidationState.NOT_CHECKED
                ),
                source=(
                    CredentialSource.KEYRING
                    if provider_id is WalletDataProviderId.ANKR
                    else None
                ),
                validated_at=(
                    datetime(2026, 8, 26, 10, 0, tzinfo=UTC)
                    if provider_id is WalletDataProviderId.ANKR
                    else None
                ),
            )
            for provider_id in WalletDataProviderId
        ],
    )
    store.save(updated)

    loaded = store.load()
    assert loaded.selected_chain is Chain.BASE
    assert loaded.hide_unverified is False
    assert loaded.hide_dust is True
    assert loaded.dust_threshold_usd == "5"
    assert loaded.wallet_overview_max_token_pages == 45
    assert loaded.wallet_overview_cache_ttl_seconds == 900
    assert loaded.activity_cache_ttl_seconds == 180
    assert loaded.token_detail_cache_ttl_seconds == 180
    assert loaded.pricing_max_stale_age_seconds == 172_800
    assert loaded.cache_max_size_mb == 220
    assert loaded.ordered_enabled_provider_ids() == (WalletDataProviderId.ANKR,)
    assert (
        loaded.credential_diagnostic(WalletDataProviderId.ANKR).state
        is CredentialValidationState.VALID
    )


def test_settings_store_loads_legacy_payload_with_new_defaults(tmp_path: Path) -> None:
    store = SettingsStore(file_path=tmp_path / "settings.json")
    legacy_payload = {
        "selected_chain": "ethereum",
        "hide_unverified": True,
        "hide_dust": False,
        "dust_threshold_usd": "2",
    }
    store.file_path.write_bytes(json_dumps(legacy_payload, pretty=True))

    loaded = store.load()
    assert loaded.dust_threshold_usd == "2"
    assert loaded.wallet_overview_max_token_pages == 20
    assert loaded.wallet_overview_cache_ttl_seconds == 300
    assert loaded.activity_cache_ttl_seconds == 120
    assert loaded.token_detail_cache_ttl_seconds == 120
    assert loaded.pricing_max_stale_age_seconds == 86_400
    assert loaded.cache_max_size_mb == 150
    assert loaded.ordered_enabled_provider_ids() == (
        WalletDataProviderId.ALCHEMY,
        WalletDataProviderId.ANKR,
    )
    assert len(loaded.provider_credential_diagnostics) == 4


def test_settings_reject_duplicate_provider_priorities() -> None:
    raw_preferences = AppSettings().model_dump(mode="json")["provider_preferences"]
    assert isinstance(raw_preferences, list)
    raw_preferences[1]["priority"] = 1

    with pytest.raises(ValidationError, match="duplicate priorities"):
        AppSettings.model_validate({"provider_preferences": raw_preferences})


def test_settings_reject_missing_provider_preferences() -> None:
    raw_preferences = AppSettings().model_dump(mode="json")["provider_preferences"]
    assert isinstance(raw_preferences, list)

    with pytest.raises(ValidationError, match="every supported provider ID"):
        AppSettings.model_validate({"provider_preferences": raw_preferences[:-1]})
