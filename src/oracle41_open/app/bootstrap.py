from __future__ import annotations

import os
from dataclasses import dataclass

from oracle41_open.core.models import ProviderError
from oracle41_open.core.services.activity_service import ActivityService
from oracle41_open.core.services.label_resolution_service import LabelResolutionService
from oracle41_open.core.services.portfolio_service import PortfolioService
from oracle41_open.core.services.pricing_service import PricingService
from oracle41_open.core.services.provider_key_validation_service import ProviderKeyValidationService
from oracle41_open.core.services.snapshot_compare_service import SnapshotCompareService
from oracle41_open.core.services.token_detail_service import TokenDetailService
from oracle41_open.core.services.wallet_service import WalletService
from oracle41_open.core.services.watchlist_service import WatchlistService
from oracle41_open.providers.alchemy import AlchemyPricingProvider, AlchemyProvider
from oracle41_open.providers.ankr import AnkrProvider
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.failover import FailoverDataProvider
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.providers.stub import (
    StubDataProvider,
    StubPricingProvider,
    UnavailablePricingProvider,
)
from oracle41_open.storage.backup_restore import BackupRestoreService
from oracle41_open.storage.cache_store import DiskCacheStore
from oracle41_open.storage.db import (
    EventLedgerRepository,
    SavedViewsRepository,
    SnapshotsRepository,
    SQLiteDatabase,
    WalletNotesRepository,
    WatchlistRepository,
)
from oracle41_open.storage.secrets import SecretStore
from oracle41_open.storage.settings import SettingsStore


@dataclass
class AppContainer:
    settings_store: SettingsStore
    secret_store: SecretStore
    backup_restore_service: BackupRestoreService
    cache_store: DiskCacheStore
    sqlite_database: SQLiteDatabase
    watchlist_repository: WatchlistRepository
    wallet_notes_repository: WalletNotesRepository
    saved_views_repository: SavedViewsRepository
    snapshots_repository: SnapshotsRepository
    event_ledger_repository: EventLedgerRepository
    watchlist_service: WatchlistService
    data_provider: DataProvider
    pricing_provider: PricingProvider
    wallet_service: WalletService
    activity_service: ActivityService
    token_detail_service: TokenDetailService
    label_resolution_service: LabelResolutionService
    provider_key_validation_service: ProviderKeyValidationService
    snapshot_compare_service: SnapshotCompareService
    portfolio_service: PortfolioService
    uses_live_providers: bool


def build_container() -> AppContainer:
    settings_store = SettingsStore.default()
    settings = settings_store.load()
    secret_store = SecretStore(service_name="oracle41-open")
    sqlite_database = SQLiteDatabase.default()
    backup_restore_service = BackupRestoreService(
        settings_store=settings_store,
        sqlite_database=sqlite_database,
    )
    cache_store = DiskCacheStore.default(max_size_mb=settings.cache_max_size_mb)
    watchlist_repository = WatchlistRepository(sqlite_database)
    wallet_notes_repository = WalletNotesRepository(sqlite_database)
    saved_views_repository = SavedViewsRepository(sqlite_database)
    snapshots_repository = SnapshotsRepository(sqlite_database)
    event_ledger_repository = EventLedgerRepository(sqlite_database)
    watchlist_service = WatchlistService(repository=watchlist_repository)

    uses_live_providers = False
    alchemy_api_key = _load_provider_key(
        secret_store,
        key_name="alchemy_api_key",
        environment_name="ORACLE41_ALCHEMY_API_KEY",
    )
    ankr_api_key = _load_provider_key(
        secret_store,
        key_name="ankr_api_key",
        environment_name="ORACLE41_ANKR_API_KEY",
    )

    alchemy_provider: DataProvider | None = None
    ankr_provider: DataProvider | None = None
    pricing_provider: PricingProvider
    if alchemy_api_key:
        try:
            alchemy_provider = AlchemyProvider(api_key=alchemy_api_key)
        except ProviderError:
            alchemy_provider = None
    if ankr_api_key:
        try:
            ankr_provider = AnkrProvider(api_key=ankr_api_key)
        except ProviderError:
            ankr_provider = None

    data_provider: DataProvider
    if alchemy_provider is not None and ankr_provider is not None:
        data_provider = FailoverDataProvider(
            primary=alchemy_provider,
            fallback=ankr_provider,
        )
        uses_live_providers = True
    elif alchemy_provider is not None:
        data_provider = alchemy_provider
        uses_live_providers = True
    elif ankr_provider is not None:
        data_provider = ankr_provider
        uses_live_providers = True
    else:
        data_provider = StubDataProvider()

    if alchemy_api_key:
        try:
            pricing_provider = AlchemyPricingProvider(api_key=alchemy_api_key)
        except ProviderError:
            pricing_provider = UnavailablePricingProvider()
    elif uses_live_providers:
        pricing_provider = UnavailablePricingProvider()
    else:
        pricing_provider = StubPricingProvider()

    pricing_service = PricingService(
        pricing_provider=pricing_provider,
        cache_store=cache_store,
        max_stale_age_seconds=settings.pricing_max_stale_age_seconds,
    )

    wallet_service = WalletService(
        data_provider=data_provider,
        pricing_provider=pricing_service,
        cache_store=cache_store,
        cache_ttl_seconds=settings.wallet_overview_cache_ttl_seconds,
        max_token_balance_pages=settings.wallet_overview_max_token_pages,
    )
    activity_service = ActivityService(
        data_provider=data_provider,
        pricing_provider=pricing_service,
        cache_store=cache_store,
        cache_ttl_seconds=settings.activity_cache_ttl_seconds,
        event_ledger=event_ledger_repository,
    )
    token_detail_service = TokenDetailService(
        data_provider=data_provider,
        pricing_provider=pricing_service,
        cache_store=cache_store,
        cache_ttl_seconds=settings.token_detail_cache_ttl_seconds,
        event_ledger=event_ledger_repository,
    )
    label_resolution_service = LabelResolutionService(cache_store=cache_store)
    provider_key_validation_service = ProviderKeyValidationService()
    snapshot_compare_service = SnapshotCompareService()
    portfolio_service = PortfolioService(
        watchlist_reader=watchlist_service,
        wallet_loader=wallet_service,
    )

    return AppContainer(
        settings_store=settings_store,
        secret_store=secret_store,
        backup_restore_service=backup_restore_service,
        cache_store=cache_store,
        sqlite_database=sqlite_database,
        watchlist_repository=watchlist_repository,
        wallet_notes_repository=wallet_notes_repository,
        saved_views_repository=saved_views_repository,
        snapshots_repository=snapshots_repository,
        event_ledger_repository=event_ledger_repository,
        watchlist_service=watchlist_service,
        data_provider=data_provider,
        pricing_provider=pricing_service,
        wallet_service=wallet_service,
        activity_service=activity_service,
        token_detail_service=token_detail_service,
        label_resolution_service=label_resolution_service,
        provider_key_validation_service=provider_key_validation_service,
        snapshot_compare_service=snapshot_compare_service,
        portfolio_service=portfolio_service,
        uses_live_providers=uses_live_providers,
    )


def _load_provider_key(
    secret_store: SecretStore,
    key_name: str,
    environment_name: str,
) -> str:
    stored_value = (secret_store.get_secret(key_name) or "").strip()
    if stored_value:
        return stored_value
    return os.environ.get(environment_name, "").strip()
