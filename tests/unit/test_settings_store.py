from pathlib import Path

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import Chain
from oracle41_open.storage.settings import AppSettings, SettingsStore


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
