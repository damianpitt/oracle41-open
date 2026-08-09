from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    CompletenessState,
    DataProvenance,
    LedgerCheckpoint,
    ValidationError,
)
from oracle41_open.core.services.address_validator import AddressValidator
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.pricing_provider import PricingProvider


class CacheStore(Protocol):
    def get(self, key: str) -> Any | None:
        ...

    def set(self, key: str, value: Any, ttl_seconds: int | None = None) -> None:
        ...


class ActivityLedger(Protocol):
    def persist_page(
        self,
        address: str,
        chain: Chain,
        scope: str,
        page: ActivityPage,
        provenance: DataProvenance,
        completeness: CompletenessState,
    ) -> LedgerCheckpoint:
        ...

    def load_page(self, address: str, chain: Chain, scope: str) -> ActivityPage:
        ...

    def get_checkpoint(
        self,
        address: str,
        chain: Chain,
        scope: str,
    ) -> LedgerCheckpoint | None:
        ...


@dataclass(frozen=True)
class ActivityPageResult:
    page: ActivityPage
    updated_at: datetime
    is_cached: bool
    completeness: CompletenessState = CompletenessState.COMPLETE
    provenance: DataProvenance | None = None
    is_persisted: bool = False


class ActivityService:
    def __init__(
        self,
        data_provider: DataProvider,
        pricing_provider: PricingProvider,
        cache_store: CacheStore | None = None,
        cache_ttl_seconds: int = 120,
        event_ledger: ActivityLedger | None = None,
        ledger_stale_after_seconds: int = 86_400,
    ) -> None:
        self._data_provider = data_provider
        self._pricing_provider = pricing_provider
        self._cache_store = cache_store
        self._cache_ttl_seconds = max(0, cache_ttl_seconds)
        self._event_ledger = event_ledger
        self._ledger_stale_after_seconds = max(0, ledger_stale_after_seconds)

    def load_activity(
        self,
        address: str,
        chain: Chain,
        cursor: str | None = None,
        from_block: int | None = None,
        min_value_usd: Decimal | None = None,
        categories: set[ActivityCategory] | None = None,
        force_refresh: bool = False,
    ) -> ActivityPageResult:
        normalized_address = AddressValidator.normalized(address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError(
                AddressValidator.validation_error(normalized_address) or "Invalid wallet address."
            )
        normalized_from_block = self._normalize_from_block(from_block)
        ledger_scope = self._ledger_scope(normalized_from_block)

        cache_key = self._cache_key(normalized_address, chain, cursor, normalized_from_block)
        if not force_refresh:
            cached = self._load_cached_page(cache_key)
            if cached is not None:
                filtered = self._filter_page(cached.page, min_value_usd=min_value_usd, categories=categories)
                return ActivityPageResult(
                    page=filtered,
                    updated_at=cached.updated_at,
                    is_cached=True,
                    completeness=cached.completeness,
                    provenance=cached.provenance,
                    is_persisted=cached.is_persisted,
                )
            if cursor is None:
                persisted = self._load_persisted_page(
                    normalized_address,
                    chain,
                    ledger_scope,
                )
                if persisted is not None:
                    filtered = self._filter_page(
                        persisted.page,
                        min_value_usd=min_value_usd,
                        categories=categories,
                    )
                    return ActivityPageResult(
                        page=filtered,
                        updated_at=persisted.updated_at,
                        is_cached=True,
                        completeness=persisted.completeness,
                        provenance=persisted.provenance,
                        is_persisted=True,
                    )

        page = self._data_provider.get_activity(
            address=normalized_address,
            chain=chain,
            cursor=cursor,
            from_block=normalized_from_block,
        )
        enriched = self._enrich_prices(page, chain)
        fetched_at = datetime.now(tz=UTC)
        completeness = self._completeness_for(enriched)
        provenance = DataProvenance(
            source_provider=enriched.source_provider or self._provider_name(),
            fetched_at=fetched_at,
            request_cursor=cursor,
            query_from_block=(
                enriched.query_from_block
                if enriched.query_from_block is not None
                else normalized_from_block
            ),
            query_to_block=enriched.query_to_block,
        )
        is_persisted = False
        if self._event_ledger is not None:
            checkpoint = self._event_ledger.persist_page(
                address=normalized_address,
                chain=chain,
                scope=ledger_scope,
                page=enriched,
                provenance=provenance,
                completeness=completeness,
            )
            enriched = self._event_ledger.load_page(normalized_address, chain, ledger_scope)
            fetched_at = checkpoint.updated_at
            is_persisted = True
        else:
            baseline_key = self._baseline_cache_key(
                normalized_address,
                chain,
                normalized_from_block,
            )
            if cursor is None:
                self._save_cached_baseline_page(baseline_key, enriched)
            else:
                baseline = self._load_cached_baseline_page(baseline_key)
                if baseline is not None:
                    merged_items = self._dedupe_and_sort(baseline.items + enriched.items)
                    enriched = ActivityPage(
                        items=merged_items,
                        next_cursor=enriched.next_cursor,
                        source_provider=enriched.source_provider,
                        query_from_block=enriched.query_from_block,
                        query_to_block=enriched.query_to_block,
                    )

        filtered = self._filter_page(enriched, min_value_usd=min_value_usd, categories=categories)
        result = ActivityPageResult(
            page=filtered,
            updated_at=fetched_at,
            is_cached=False,
            completeness=completeness,
            provenance=provenance,
            is_persisted=is_persisted,
        )

        self._save_cached_page(
            cache_key=cache_key,
            page=enriched,
            updated_at=result.updated_at,
        )
        return result

    def _load_persisted_page(
        self,
        address: str,
        chain: Chain,
        scope: str,
    ) -> ActivityPageResult | None:
        if self._event_ledger is None:
            return None
        checkpoint = self._event_ledger.get_checkpoint(address, chain, scope)
        if checkpoint is None:
            return None
        completeness = self._persisted_completeness(checkpoint)
        return ActivityPageResult(
            page=self._event_ledger.load_page(address, chain, scope),
            updated_at=checkpoint.updated_at,
            is_cached=True,
            completeness=completeness,
            provenance=checkpoint.provenance,
            is_persisted=True,
        )

    def clear_cached_activity(
        self,
        address: str,
        chain: Chain,
        from_block: int | None = None,
        cursor: str | None = None,
    ) -> bool:
        normalized_address = AddressValidator.normalized(address)
        if not AddressValidator.is_valid(normalized_address):
            raise ValidationError(
                AddressValidator.validation_error(normalized_address) or "Invalid wallet address."
            )
        normalized_from_block = self._normalize_from_block(from_block)
        keys = {
            self._cache_key(normalized_address, chain, None, normalized_from_block),
            self._baseline_cache_key(normalized_address, chain, normalized_from_block),
        }
        if cursor is not None and cursor.strip():
            keys.add(self._cache_key(normalized_address, chain, cursor, normalized_from_block))
        return all(self._clear_cache_entry(key) for key in keys)

    def _enrich_prices(self, page: ActivityPage, chain: Chain) -> ActivityPage:
        token_contracts = sorted(
            {
                item.contract_address.lower()
                for item in page.items
                if item.contract_address is not None and item.category is ActivityCategory.ERC20
            }
        )
        token_prices = self._pricing_provider.get_token_prices(chain=chain, contract_addresses=token_contracts)
        native_price = self._pricing_provider.get_native_price(chain)

        enriched_items: list[ActivityItem] = []
        for item in page.items:
            if item.value_usd is not None:
                enriched_items.append(item)
                continue

            quote: Decimal | None = None
            if item.category is ActivityCategory.ERC20 and item.contract_address is not None:
                quote = token_prices.get(item.contract_address.lower())
            elif item.category in {ActivityCategory.EXTERNAL, ActivityCategory.INTERNAL_TRANSFER}:
                quote = native_price

            if quote is None:
                enriched_items.append(item)
                continue

            enriched_items.append(item.with_value_usd(item.value_decimal * quote))

        deduped = self._dedupe_and_sort(enriched_items)
        return ActivityPage(
            items=deduped,
            next_cursor=page.next_cursor,
            source_provider=page.source_provider,
            query_from_block=page.query_from_block,
            query_to_block=page.query_to_block,
        )

    def _dedupe_and_sort(self, items: list[ActivityItem]) -> list[ActivityItem]:
        by_id: dict[str, ActivityItem] = {}
        for item in items:
            existing = by_id.get(item.id)
            if existing is None or item.timestamp > existing.timestamp:
                by_id[item.id] = item
        return sorted(by_id.values(), key=lambda item: item.timestamp, reverse=True)

    def _filter_page(
        self,
        page: ActivityPage,
        min_value_usd: Decimal | None,
        categories: set[ActivityCategory] | None,
    ) -> ActivityPage:
        filtered: list[ActivityItem] = []
        for item in page.items:
            if categories is not None and item.category not in categories:
                continue
            if min_value_usd is not None:
                if item.value_usd is None or item.value_usd < min_value_usd:
                    continue
            filtered.append(item)
        return ActivityPage(
            items=filtered,
            next_cursor=page.next_cursor,
            source_provider=page.source_provider,
            query_from_block=page.query_from_block,
            query_to_block=page.query_to_block,
        )

    def _cache_key(
        self,
        address: str,
        chain: Chain,
        cursor: str | None,
        from_block: int | None,
    ) -> str:
        suffix = cursor if cursor is not None and cursor else "first"
        from_block_key = str(from_block) if from_block is not None else "latest"
        return f"activity.page.v1.{chain.value}.{address}.fb{from_block_key}.{suffix}"

    def _baseline_cache_key(self, address: str, chain: Chain, from_block: int | None) -> str:
        from_block_key = str(from_block) if from_block is not None else "latest"
        return f"activity.baseline.v1.{chain.value}.{address}.fb{from_block_key}"

    def _load_cached_page(self, cache_key: str) -> ActivityPageResult | None:
        if self._cache_store is None:
            return None
        payload = self._cache_store.get(cache_key)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != 1:
            return None

        updated_at = _parse_datetime(payload.get("updated_at"))
        raw_page = payload.get("page")
        if updated_at is None or not isinstance(raw_page, dict):
            return None
        page = _decode_activity_page(raw_page)
        if page is None:
            return None
        return ActivityPageResult(page=page, updated_at=updated_at, is_cached=True)

    def _load_cached_baseline_page(self, cache_key: str) -> ActivityPage | None:
        if self._cache_store is None:
            return None
        payload = self._cache_store.get(cache_key)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != 1:
            return None
        raw_page = payload.get("page")
        if not isinstance(raw_page, dict):
            return None
        return _decode_activity_page(raw_page)

    def _save_cached_page(self, cache_key: str, page: ActivityPage, updated_at: datetime) -> None:
        if self._cache_store is None:
            return
        payload = {
            "version": 1,
            "updated_at": updated_at.isoformat(),
            "page": _encode_activity_page(page),
        }
        self._cache_store.set(cache_key, payload, ttl_seconds=self._cache_ttl_seconds)

    def _save_cached_baseline_page(self, cache_key: str, page: ActivityPage) -> None:
        if self._cache_store is None:
            return
        payload = {
            "version": 1,
            "page": _encode_activity_page(page),
        }
        self._cache_store.set(cache_key, payload, ttl_seconds=self._cache_ttl_seconds)

    def _clear_cache_entry(self, cache_key: str) -> bool:
        if self._cache_store is None:
            return False
        remove = getattr(self._cache_store, "remove", None)
        if callable(remove):
            remove(cache_key)
            return True
        self._cache_store.set(cache_key, None, ttl_seconds=1)
        return True

    def _normalize_from_block(self, from_block: int | None) -> int | None:
        if from_block is None:
            return None
        if isinstance(from_block, bool) or not isinstance(from_block, int) or from_block <= 0:
            raise ValidationError("From block must be a positive integer.")
        return from_block

    @staticmethod
    def _ledger_scope(from_block: int | None) -> str:
        block_key = str(from_block) if from_block is not None else "latest"
        return f"activity:from={block_key}"

    @staticmethod
    def _completeness_for(page: ActivityPage) -> CompletenessState:
        if page.next_cursor:
            return CompletenessState.PARTIAL
        return CompletenessState.COMPLETE

    def _provider_name(self) -> str:
        name = type(self._data_provider).__name__
        for suffix in ("DataProvider", "Provider"):
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        return name.lower() or "unknown"

    def _persisted_completeness(self, checkpoint: LedgerCheckpoint) -> CompletenessState:
        if checkpoint.completeness is not CompletenessState.COMPLETE:
            return checkpoint.completeness
        age_seconds = (datetime.now(tz=UTC) - checkpoint.updated_at).total_seconds()
        if age_seconds > self._ledger_stale_after_seconds:
            return CompletenessState.STALE
        return checkpoint.completeness


def _encode_activity_page(page: ActivityPage) -> dict[str, object]:
    return {
        "items": [_encode_activity_item(item) for item in page.items],
        "next_cursor": page.next_cursor,
        "source_provider": page.source_provider,
        "query_from_block": page.query_from_block,
        "query_to_block": page.query_to_block,
    }


def _encode_activity_item(item: ActivityItem) -> dict[str, object]:
    return {
        "block_number": item.block_number,
        "tx_hash": item.tx_hash,
        "log_index": item.log_index,
        "timestamp": item.timestamp.isoformat(),
        "from_address": item.from_address,
        "to_address": item.to_address,
        "asset_symbol": item.asset_symbol,
        "contract_address": item.contract_address,
        "raw_value": item.raw_value,
        "value_decimal": str(item.value_decimal),
        "value_usd": str(item.value_usd) if item.value_usd is not None else None,
        "is_verified": item.is_verified,
        "category": item.category.value,
        "chain": item.chain.value,
    }


def _decode_activity_page(raw: dict[str, Any]) -> ActivityPage | None:
    raw_items = raw.get("items")
    raw_next_cursor = raw.get("next_cursor")
    raw_source_provider = raw.get("source_provider")
    raw_query_from_block = raw.get("query_from_block")
    raw_query_to_block = raw.get("query_to_block")
    if not isinstance(raw_items, list):
        return None
    if raw_next_cursor is not None and not isinstance(raw_next_cursor, str):
        return None
    if raw_source_provider is not None and not isinstance(raw_source_provider, str):
        return None
    if raw_query_from_block is not None and not isinstance(raw_query_from_block, int):
        return None
    if raw_query_to_block is not None and not isinstance(raw_query_to_block, int):
        return None

    items: list[ActivityItem] = []
    for raw_item in raw_items:
        parsed = _decode_activity_item(raw_item)
        if parsed is None:
            return None
        items.append(parsed)
    return ActivityPage(
        items=items,
        next_cursor=raw_next_cursor,
        source_provider=raw_source_provider,
        query_from_block=raw_query_from_block,
        query_to_block=raw_query_to_block,
    )


def _decode_activity_item(raw: Any) -> ActivityItem | None:
    if not isinstance(raw, dict):
        return None
    tx_hash = raw.get("tx_hash")
    log_index = raw.get("log_index")
    timestamp_raw = raw.get("timestamp")
    from_address = raw.get("from_address")
    to_address = raw.get("to_address")
    asset_symbol = raw.get("asset_symbol")
    contract_address = raw.get("contract_address")
    raw_value = raw.get("raw_value")
    value_decimal_raw = raw.get("value_decimal")
    value_usd_raw = raw.get("value_usd")
    is_verified = raw.get("is_verified")
    category_raw = raw.get("category")
    chain_raw = raw.get("chain")
    block_number = raw.get("block_number")

    if not isinstance(tx_hash, str):
        return None
    if not isinstance(log_index, str):
        return None
    timestamp = _parse_datetime(timestamp_raw)
    if timestamp is None:
        return None
    if not isinstance(from_address, str):
        return None
    if not isinstance(to_address, str):
        return None
    if not isinstance(asset_symbol, str):
        return None
    if contract_address is not None and not isinstance(contract_address, str):
        return None
    if not isinstance(raw_value, str):
        return None
    value_decimal = _parse_decimal(value_decimal_raw)
    if value_decimal is None:
        return None
    value_usd = _parse_optional_decimal(value_usd_raw)
    if value_usd_raw is not None and value_usd is None:
        return None
    if is_verified is not None and not isinstance(is_verified, bool):
        return None
    if not isinstance(category_raw, str):
        return None
    if not isinstance(chain_raw, str):
        return None
    if block_number is not None and not isinstance(block_number, int):
        return None

    try:
        category = ActivityCategory(category_raw)
        chain = Chain(chain_raw)
    except ValueError:
        return None

    return ActivityItem(
        block_number=block_number,
        tx_hash=tx_hash,
        log_index=log_index,
        timestamp=timestamp,
        from_address=from_address,
        to_address=to_address,
        asset_symbol=asset_symbol,
        contract_address=contract_address,
        raw_value=raw_value,
        value_decimal=value_decimal,
        value_usd=value_usd,
        is_verified=is_verified,
        category=category,
        chain=chain,
    )


def _parse_decimal(raw: Any) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _parse_optional_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    return _parse_decimal(raw)


def _parse_datetime(raw: Any) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
