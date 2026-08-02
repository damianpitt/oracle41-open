from __future__ import annotations

import time
from pathlib import Path

from oracle41_open.storage.cache_store import DiskCacheStore


def test_cache_store_tracks_hit_miss_set_remove_by_category(tmp_path: Path) -> None:
    cache = DiskCacheStore(file_path=tmp_path / "cache.json")
    key = "wallet.overview.v1.ethereum.0xabc"

    assert cache.get(key) is None
    cache.set(key, {"value": 1}, ttl_seconds=60)
    assert cache.get(key) == {"value": 1}
    cache.remove(key)

    diagnostics = cache.diagnostics()
    assert diagnostics.gets == 2
    assert diagnostics.hits == 1
    assert diagnostics.misses == 1
    assert diagnostics.sets == 1
    assert diagnostics.removes == 1
    assert diagnostics.expired == 0

    by_category = {entry.category: entry for entry in diagnostics.categories}
    assert by_category["wallet.overview"].gets == 2
    assert by_category["wallet.overview"].hits == 1
    assert by_category["wallet.overview"].misses == 1
    assert by_category["wallet.overview"].sets == 1
    assert by_category["wallet.overview"].removes == 1


def test_cache_store_tracks_expired_entries_as_misses(tmp_path: Path) -> None:
    cache = DiskCacheStore(file_path=tmp_path / "cache.json")
    key = "activity.page.v1.ethereum.0xabc"

    cache.set(key, {"value": "x"}, ttl_seconds=1)
    time.sleep(1.1)
    assert cache.get(key) is None

    diagnostics = cache.diagnostics()
    assert diagnostics.gets == 1
    assert diagnostics.hits == 0
    assert diagnostics.misses == 1
    assert diagnostics.expired == 1
    assert diagnostics.removes == 1

    by_category = {entry.category: entry for entry in diagnostics.categories}
    assert by_category["activity.page"].expired == 1


def test_cache_store_reset_telemetry_preserves_entries(tmp_path: Path) -> None:
    cache = DiskCacheStore(file_path=tmp_path / "cache.json")
    key = "pricing.native.v1.ethereum"

    cache.set(key, {"price": "1"})
    assert cache.get(key) == {"price": "1"}
    before = cache.diagnostics()
    assert before.entry_count == 1
    assert before.sets == 1
    assert before.gets == 1

    cache.reset_telemetry()
    after_reset = cache.diagnostics()
    assert after_reset.entry_count == 1
    assert after_reset.sets == 0
    assert after_reset.gets == 0
    assert after_reset.hits == 0
    assert after_reset.misses == 0
    assert after_reset.categories == []

    assert cache.get(key) == {"price": "1"}
    after_get = cache.diagnostics()
    assert after_get.gets == 1
    assert after_get.hits == 1
    assert {entry.category for entry in after_get.categories} == {"pricing.native"}


def test_cache_store_reports_loaded_entries_from_disk(tmp_path: Path) -> None:
    file_path = tmp_path / "cache.json"
    first = DiskCacheStore(file_path=file_path)
    first.set("wallet.overview.v1.ethereum.0x1", {"v": 1})
    first.set("activity.page.v1.ethereum.0x1", {"v": 2})

    second = DiskCacheStore(file_path=file_path)
    diagnostics = second.diagnostics()
    assert diagnostics.entry_count == 2
    assert diagnostics.loads_from_disk == 2


def test_cache_store_remove_by_prefix_removes_matching_entries(tmp_path: Path) -> None:
    cache = DiskCacheStore(file_path=tmp_path / "cache.json")
    cache.set("activity.page.v1.ethereum.0x1", {"v": 1})
    cache.set("activity.page.v1.ethereum.0x2", {"v": 2})
    cache.set("wallet.overview.v1.ethereum.0x1", {"v": 3})

    removed = cache.remove_by_prefix("activity.page")
    diagnostics = cache.diagnostics()

    assert removed == 2
    assert cache.get("wallet.overview.v1.ethereum.0x1") == {"v": 3}
    assert cache.get("activity.page.v1.ethereum.0x1") is None
    assert diagnostics.removes == 2
    by_category = {entry.category: entry for entry in diagnostics.categories}
    assert by_category["activity.page"].removes == 2


def test_cache_store_purge_expired_removes_only_expired_entries(tmp_path: Path) -> None:
    cache = DiskCacheStore(file_path=tmp_path / "cache.json")
    expired_key = "activity.page.v1.ethereum.expiring"
    live_key = "activity.page.v1.ethereum.live"
    cache.set(expired_key, {"v": "old"}, ttl_seconds=1)
    cache.set(live_key, {"v": "fresh"}, ttl_seconds=60)

    time.sleep(1.1)
    removed = cache.purge_expired()

    assert removed == 1
    assert cache.get(expired_key) is None
    assert cache.get(live_key) == {"v": "fresh"}
    diagnostics = cache.diagnostics()
    assert diagnostics.expired >= 1
    assert diagnostics.removes >= 1
