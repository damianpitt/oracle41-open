from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from platformdirs import user_cache_dir

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads


@dataclass
class CacheItem:
    value: Any
    expires_at: datetime | None
    last_accessed_at: datetime


@dataclass(frozen=True)
class CacheCategoryStats:
    category: str
    gets: int
    hits: int
    misses: int
    expired: int
    sets: int
    removes: int
    evictions: int

    @property
    def hit_rate(self) -> float:
        if self.gets <= 0:
            return 0.0
        return self.hits / self.gets


@dataclass(frozen=True)
class CacheDiagnostics:
    cache_file: str
    entry_count: int
    estimated_size_bytes: int
    max_size_bytes: int
    utilization_ratio: float
    gets: int
    hits: int
    misses: int
    expired: int
    sets: int
    removes: int
    evictions: int
    loads_from_disk: int
    persistence_writes: int
    categories: list[CacheCategoryStats]

    @property
    def hit_rate(self) -> float:
        if self.gets <= 0:
            return 0.0
        return self.hits / self.gets


@dataclass
class _MutableCategoryStats:
    gets: int = 0
    hits: int = 0
    misses: int = 0
    expired: int = 0
    sets: int = 0
    removes: int = 0
    evictions: int = 0


@dataclass
class _CacheTelemetry:
    gets: int = 0
    hits: int = 0
    misses: int = 0
    expired: int = 0
    sets: int = 0
    removes: int = 0
    evictions: int = 0
    loads_from_disk: int = 0
    persistence_writes: int = 0
    categories: dict[str, _MutableCategoryStats] = field(default_factory=dict)


class DiskCacheStore:
    def __init__(self, file_path: Path, max_size_mb: int = 150) -> None:
        self._file_path = file_path
        self._max_size_bytes = max(10, min(500, max_size_mb)) * 1024 * 1024
        self._lock = Lock()
        self._store: dict[str, CacheItem] = {}
        self._telemetry = _CacheTelemetry()
        self._load_from_disk()

    @staticmethod
    def default(max_size_mb: int = 150) -> DiskCacheStore:
        root = Path(user_cache_dir(appname="oracle41-open", appauthor=False))
        return DiskCacheStore(file_path=root / "cache.json", max_size_mb=max_size_mb)

    def get(self, key: str) -> Any | None:
        with self._lock:
            category_stats = self._category_stats_locked(key)
            self._telemetry.gets += 1
            category_stats.gets += 1
            item = self._store.get(key)
            if item is None:
                self._telemetry.misses += 1
                category_stats.misses += 1
                return None
            if item.expires_at is not None and item.expires_at <= datetime.now(UTC):
                del self._store[key]
                self._telemetry.expired += 1
                self._telemetry.misses += 1
                self._telemetry.removes += 1
                category_stats.expired += 1
                category_stats.misses += 1
                category_stats.removes += 1
                self._persist_locked()
                return None
            item.last_accessed_at = datetime.now(UTC)
            self._telemetry.hits += 1
            category_stats.hits += 1
            return item.value

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        with self._lock:
            category_stats = self._category_stats_locked(key)
            self._telemetry.sets += 1
            category_stats.sets += 1
            expires_at = None
            if ttl_seconds is not None and ttl_seconds > 0:
                expires_at = datetime.now(UTC) + timedelta(seconds=ttl_seconds)
            self._store[key] = CacheItem(
                value=value,
                expires_at=expires_at,
                last_accessed_at=datetime.now(UTC),
            )
            self._evict_if_needed_locked()
            self._persist_locked()

    def remove(self, key: str) -> None:
        with self._lock:
            if key in self._store:
                del self._store[key]
                self._record_remove_locked(key)
            self._persist_locked()

    def clear(self) -> None:
        with self._lock:
            for key in list(self._store.keys()):
                self._record_remove_locked(key)
            self._store.clear()
            self._persist_locked()

    def remove_by_prefix(self, prefix: str) -> int:
        normalized_prefix = prefix.strip()
        if not normalized_prefix:
            return 0
        with self._lock:
            removed = 0
            for key in list(self._store.keys()):
                if not key.startswith(normalized_prefix):
                    continue
                del self._store[key]
                self._record_remove_locked(key)
                removed += 1
            if removed > 0:
                self._persist_locked()
            return removed

    def purge_expired(self) -> int:
        with self._lock:
            now = datetime.now(UTC)
            removed = 0
            for key, item in list(self._store.items()):
                if item.expires_at is None or item.expires_at > now:
                    continue
                del self._store[key]
                self._telemetry.expired += 1
                self._category_stats_locked(key).expired += 1
                self._record_remove_locked(key)
                removed += 1
            if removed > 0:
                self._persist_locked()
            return removed

    def diagnostics(self) -> CacheDiagnostics:
        with self._lock:
            estimated_size = self._estimated_size_locked()
            utilization_ratio = (
                estimated_size / self._max_size_bytes if self._max_size_bytes > 0 else 0.0
            )
            categories = [
                CacheCategoryStats(
                    category=category,
                    gets=stats.gets,
                    hits=stats.hits,
                    misses=stats.misses,
                    expired=stats.expired,
                    sets=stats.sets,
                    removes=stats.removes,
                    evictions=stats.evictions,
                )
                for category, stats in sorted(self._telemetry.categories.items())
            ]
            return CacheDiagnostics(
                cache_file=str(self._file_path),
                entry_count=len(self._store),
                estimated_size_bytes=estimated_size,
                max_size_bytes=self._max_size_bytes,
                utilization_ratio=utilization_ratio,
                gets=self._telemetry.gets,
                hits=self._telemetry.hits,
                misses=self._telemetry.misses,
                expired=self._telemetry.expired,
                sets=self._telemetry.sets,
                removes=self._telemetry.removes,
                evictions=self._telemetry.evictions,
                loads_from_disk=self._telemetry.loads_from_disk,
                persistence_writes=self._telemetry.persistence_writes,
                categories=categories,
            )

    def reset_telemetry(self) -> None:
        with self._lock:
            self._telemetry = _CacheTelemetry()

    def _load_from_disk(self) -> None:
        if not self._file_path.exists():
            return
        try:
            payload = json_loads(self._file_path.read_bytes())
        except ValueError:
            return
        if not isinstance(payload, dict):
            return
        loaded_count = 0
        for key, raw in payload.items():
            if not isinstance(key, str) or not isinstance(raw, dict):
                continue
            expires_at = self._read_datetime(raw.get("expires_at"))
            last_accessed = self._read_datetime(raw.get("last_accessed_at"))
            if last_accessed is None:
                continue
            self._store[key] = CacheItem(
                value=raw.get("value"),
                expires_at=expires_at,
                last_accessed_at=last_accessed,
            )
            loaded_count += 1
        self._telemetry.loads_from_disk += loaded_count

    def _read_datetime(self, value: object) -> datetime | None:
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)

    def _serialize_store_locked(self) -> dict[str, dict[str, object]]:
        payload: dict[str, dict[str, object]] = {}
        for key, item in self._store.items():
            payload[key] = {
                "value": item.value,
                "expires_at": item.expires_at.isoformat() if item.expires_at else None,
                "last_accessed_at": item.last_accessed_at.isoformat(),
            }
        return payload

    def _persist_locked(self) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._serialize_store_locked()
        self._file_path.write_bytes(json_dumps(payload, pretty=True))
        self._telemetry.persistence_writes += 1

    def _evict_if_needed_locked(self) -> None:
        while self._estimated_size_locked() > self._max_size_bytes and self._store:
            oldest_key = min(
                self._store.items(),
                key=lambda item: item[1].last_accessed_at,
            )[0]
            del self._store[oldest_key]
            self._record_eviction_locked(oldest_key)

    def _estimated_size_locked(self) -> int:
        serialized = json_dumps(self._serialize_store_locked(), pretty=False)
        return len(serialized)

    def _category_stats_locked(self, key: str) -> _MutableCategoryStats:
        category = self._category_for_key(key)
        existing = self._telemetry.categories.get(category)
        if existing is not None:
            return existing
        created = _MutableCategoryStats()
        self._telemetry.categories[category] = created
        return created

    def _category_for_key(self, key: str) -> str:
        if not key:
            return "uncategorized"
        parts = [part for part in key.split(".") if part]
        if not parts:
            return "uncategorized"
        if len(parts) == 1:
            return parts[0]
        return f"{parts[0]}.{parts[1]}"

    def _record_remove_locked(self, key: str) -> None:
        self._telemetry.removes += 1
        self._category_stats_locked(key).removes += 1

    def _record_eviction_locked(self, key: str) -> None:
        self._telemetry.evictions += 1
        self._category_stats_locked(key).evictions += 1
