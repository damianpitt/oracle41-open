"""Build the complete application dependency container.

This module creates settings, storage, providers, failover chains, and core services in one place.
Views receive the finished container and do not construct providers directly.
"""

from __future__ import annotations

import os
from contextlib import suppress
from dataclasses import dataclass

from oracle41_open.core.models import Chain, ProviderError
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
from oracle41_open.core.services.activity_service import ActivityService
from oracle41_open.core.services.contract_abi_service import ContractABIService
from oracle41_open.core.services.label_resolution_service import LabelResolutionService
from oracle41_open.core.services.portfolio_service import PortfolioService
from oracle41_open.core.services.pricing_service import PricingService
from oracle41_open.core.services.protocol_portfolio_service import ProtocolPortfolioService
from oracle41_open.core.services.protocol_position_service import ProtocolPositionService
from oracle41_open.core.services.provider_credential_diagnostics_service import (
    ProviderCredentialDiagnosticsService,
)
from oracle41_open.core.services.provider_key_validation_service import ProviderKeyValidationService
from oracle41_open.core.services.snapshot_compare_service import SnapshotCompareService
from oracle41_open.core.services.token_detail_service import TokenDetailService
from oracle41_open.core.services.transaction_inspection_service import TransactionInspectionService
from oracle41_open.core.services.wallet_service import WalletService
from oracle41_open.core.services.watchlist_service import WatchlistService
from oracle41_open.providers.alchemy import AlchemyPricingProvider, AlchemyProvider
from oracle41_open.providers.ankr import AnkrProvider
from oracle41_open.providers.blockscout import BlockscoutABIProvider
from oracle41_open.providers.capabilities import WalletDataProviderId
from oracle41_open.providers.data_provider import DataProvider
from oracle41_open.providers.evm_rpc import EVMJSONRPCProvider, FailoverTransactionDataProvider
from oracle41_open.providers.failover import OrderedDataProviderPool, ProviderPoolEntry
from oracle41_open.providers.goldrush import GoldRushProvider
from oracle41_open.providers.moralis import MoralisProvider
from oracle41_open.providers.pricing_provider import PricingProvider
from oracle41_open.providers.stub import (
    StubDataProvider,
    StubPricingProvider,
    UnavailablePricingProvider,
)
from oracle41_open.storage.backup_restore import BackupRestoreService
from oracle41_open.storage.cache_store import DiskCacheStore
from oracle41_open.storage.db import (
    ContractABIRepository,
    EventLedgerRepository,
    ProtocolPositionRepository,
    SavedViewsRepository,
    SnapshotsRepository,
    SQLiteDatabase,
    TransactionEnrichmentRepository,
    TransactionRepository,
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
    protocol_position_repository: ProtocolPositionRepository
    transaction_repository: TransactionRepository
    transaction_enrichment_repository: TransactionEnrichmentRepository
    contract_abi_repository: ContractABIRepository
    watchlist_service: WatchlistService
    data_provider: DataProvider
    pricing_provider: PricingProvider
    wallet_service: WalletService
    activity_service: ActivityService
    token_detail_service: TokenDetailService
    transaction_inspection_service: TransactionInspectionService
    protocol_position_service: ProtocolPositionService
    contract_abi_service: ContractABIService
    label_resolution_service: LabelResolutionService
    provider_key_validation_service: ProviderKeyValidationService
    provider_credential_diagnostics_service: ProviderCredentialDiagnosticsService
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
    protocol_position_repository = ProtocolPositionRepository(sqlite_database)
    transaction_repository = TransactionRepository(sqlite_database)
    transaction_enrichment_repository = TransactionEnrichmentRepository(sqlite_database)
    contract_abi_repository = ContractABIRepository(sqlite_database)
    blockscout_provider = BlockscoutABIProvider()
    contract_abi_service = ContractABIService(
        contract_abi_repository,
        verified_abi_provider=blockscout_provider,
    )
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
    moralis_api_key = _load_provider_key(
        secret_store,
        key_name="moralis_api_key",
        environment_name="ORACLE41_MORALIS_API_KEY",
    )
    goldrush_api_key = _load_provider_key(
        secret_store,
        key_name="goldrush_api_key",
        environment_name="ORACLE41_GOLDRUSH_API_KEY",
    )

    enabled_provider_ids = settings.ordered_enabled_provider_ids()
    configured_providers: dict[WalletDataProviderId, DataProvider] = {}
    pricing_provider: PricingProvider
    if alchemy_api_key and WalletDataProviderId.ALCHEMY in enabled_provider_ids:
        with suppress(ProviderError):
            configured_providers[WalletDataProviderId.ALCHEMY] = AlchemyProvider(
                api_key=alchemy_api_key
            )
    if ankr_api_key and WalletDataProviderId.ANKR in enabled_provider_ids:
        with suppress(ProviderError):
            configured_providers[WalletDataProviderId.ANKR] = AnkrProvider(api_key=ankr_api_key)
    if moralis_api_key and WalletDataProviderId.MORALIS in enabled_provider_ids:
        with suppress(ProviderError):
            configured_providers[WalletDataProviderId.MORALIS] = MoralisProvider(
                api_key=moralis_api_key
            )
    if goldrush_api_key and WalletDataProviderId.GOLDRUSH in enabled_provider_ids:
        with suppress(ProviderError):
            configured_providers[WalletDataProviderId.GOLDRUSH] = GoldRushProvider(
                api_key=goldrush_api_key
            )

    data_provider: DataProvider
    pool_entries = [
        ProviderPoolEntry(
            provider_id=provider_id.value,
            provider=configured_providers[provider_id],
        )
        for provider_id in enabled_provider_ids
        if provider_id in configured_providers
    ]
    if pool_entries:
        # A one-provider pool still owns cursors and can accept more providers later.
        data_provider = OrderedDataProviderPool(pool_entries)
        uses_live_providers = True
    else:
        data_provider = StubDataProvider()

    if alchemy_api_key and WalletDataProviderId.ALCHEMY in enabled_provider_ids:
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
    transaction_providers: list[EVMJSONRPCProvider] = []
    custom_rpc_endpoints = _load_custom_rpc_endpoints(secret_store)
    if custom_rpc_endpoints:
        transaction_providers.append(
            EVMJSONRPCProvider(custom_rpc_endpoints, source_name="custom-json-rpc")
        )
    if alchemy_api_key and WalletDataProviderId.ALCHEMY in enabled_provider_ids:
        transaction_providers.append(
            EVMJSONRPCProvider(
                {
                    chain: (
                        f"https://{chain.alchemy_network_path}.g.alchemy.com/v2/"
                        f"{alchemy_api_key}"
                    )
                    for chain in Chain
                },
                source_name="alchemy",
            )
        )
    if ankr_api_key and WalletDataProviderId.ANKR in enabled_provider_ids:
        transaction_providers.append(
            EVMJSONRPCProvider(
                {
                    chain: f"https://rpc.ankr.com/{chain.ankr_rpc_path}/{ankr_api_key}"
                    for chain in Chain
                },
                source_name="ankr",
            )
        )
    transaction_data_provider = FailoverTransactionDataProvider(transaction_providers)
    transaction_inspection_service = TransactionInspectionService(
        provider=transaction_data_provider,
        repository=transaction_repository,
        decoder=StandardABIDecoder(),
        abi_registry_provider=contract_abi_service,
        proxy_repository=contract_abi_repository,
        enrichment_provider=blockscout_provider,
        enrichment_repository=transaction_enrichment_repository,
    )
    protocol_position_service = ProtocolPositionService(
        provider=transaction_data_provider,
        repository=protocol_position_repository,
    )
    label_resolution_service = LabelResolutionService(cache_store=cache_store)
    provider_key_validation_service = ProviderKeyValidationService()
    provider_credential_diagnostics_service = ProviderCredentialDiagnosticsService(
        settings_store=settings_store,
        secret_store=secret_store,
    )
    snapshot_compare_service = SnapshotCompareService()
    portfolio_service = PortfolioService(
        watchlist_reader=watchlist_service,
        wallet_loader=wallet_service,
        protocol_snapshot_reader=protocol_position_repository,
        protocol_valuator=ProtocolPortfolioService(pricing_service),
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
        protocol_position_repository=protocol_position_repository,
        transaction_repository=transaction_repository,
        transaction_enrichment_repository=transaction_enrichment_repository,
        contract_abi_repository=contract_abi_repository,
        watchlist_service=watchlist_service,
        data_provider=data_provider,
        pricing_provider=pricing_service,
        wallet_service=wallet_service,
        activity_service=activity_service,
        token_detail_service=token_detail_service,
        transaction_inspection_service=transaction_inspection_service,
        protocol_position_service=protocol_position_service,
        contract_abi_service=contract_abi_service,
        label_resolution_service=label_resolution_service,
        provider_key_validation_service=provider_key_validation_service,
        provider_credential_diagnostics_service=provider_credential_diagnostics_service,
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


def _load_custom_rpc_endpoints(secret_store: SecretStore) -> dict[Chain, str]:
    endpoints: dict[Chain, str] = {}
    for chain in Chain:
        environment_name = f"ORACLE41_RPC_{chain.value.upper()}_URL"
        endpoint = (
            secret_store.get_secret(f"rpc_url_{chain.value}")
            or os.environ.get(environment_name, "")
        ).strip()
        if endpoint:
            endpoints[chain] = endpoint
    return endpoints
