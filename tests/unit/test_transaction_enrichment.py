"""Test optional explorer context persistence and service isolation.

The cases verify enrichment round trips, caching, failure retries, and unchanged normalized actions.
Explorer fixtures are local and no test contacts a public service.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    CompletenessState,
    DataProvenance,
    EnrichmentStatus,
    ExplorerAddressContext,
    ExplorerCapabilities,
    ExplorerDecodedParameter,
    ProviderCapabilities,
    ProviderResponseError,
    ProxyResolution,
    RawTransactionLog,
    TraceStatus,
    TransactionEnrichment,
    TransactionInspection,
    TransactionTrace,
)
from oracle41_open.core.services.transaction_inspection_service import (
    TransactionInspectionService,
)
from oracle41_open.storage.db import (
    EventLedgerRepository,
    SQLiteDatabase,
    TransactionEnrichmentRepository,
    TransactionRepository,
)

_TX_HASH = "0x" + "ab" * 32
_WALLET = "0x" + "11" * 20
_TARGET = "0x" + "22" * 20


def test_transaction_enrichment_repository_roundtrip(tmp_path: Path) -> None:
    database, transaction_repository = _database_with_transaction(tmp_path)
    transaction_repository.save_inspection(_inspection())
    repository = TransactionEnrichmentRepository(database)
    enrichment = _enrichment()

    repository.save_enrichment(enrichment)

    assert repository.get_enrichment(Chain.ETHEREUM, _TX_HASH) == enrichment
    with database.connection() as conn:
        schema_version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert schema_version is not None
    assert schema_version["value"] == "9"


def test_inspection_caches_enrichment_without_changing_actions(tmp_path: Path) -> None:
    database, transaction_repository = _database_with_transaction(tmp_path)
    enrichment_repository = TransactionEnrichmentRepository(database)
    provider = _TransactionProvider(_inspection())
    explorer = _EnrichmentProvider(_enrichment())
    service = TransactionInspectionService(
        provider,
        transaction_repository,
        enrichment_provider=explorer,
        enrichment_repository=enrichment_repository,
    )

    first = service.inspect(_TX_HASH, Chain.ETHEREUM)
    second = service.inspect(_TX_HASH, Chain.ETHEREUM)

    assert first.enrichment == _enrichment()
    assert second.enrichment == first.enrichment
    assert second.actions == first.actions
    assert explorer.calls == 1


def test_inspection_retries_unavailable_enrichment(tmp_path: Path) -> None:
    database, transaction_repository = _database_with_transaction(tmp_path)
    explorer = _RetryingEnrichmentProvider(_enrichment())
    service = TransactionInspectionService(
        _TransactionProvider(_inspection()),
        transaction_repository,
        enrichment_provider=explorer,
        enrichment_repository=TransactionEnrichmentRepository(database),
    )

    first = service.inspect(_TX_HASH, Chain.ETHEREUM)
    second = service.inspect(_TX_HASH, Chain.ETHEREUM)

    assert first.enrichment is not None
    assert first.enrichment.status is EnrichmentStatus.UNAVAILABLE
    assert second.enrichment == _enrichment()
    assert explorer.calls == 2


class _TransactionProvider:
    def __init__(self, inspection: TransactionInspection) -> None:
        self.inspection = inspection

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        _ = chain
        return ProviderCapabilities(transaction_lookup=True, receipts=True, traces=False)

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        _ = tx_hash, chain
        return self.inspection

    def get_transaction_trace(self, tx_hash: str, chain: Chain) -> TransactionTrace:
        return TransactionTrace(
            chain=chain,
            tx_hash=tx_hash,
            status=TraceStatus.UNSUPPORTED,
            calls=(),
            raw_json=None,
            source_provider="test-rpc",
            fetched_at=datetime(2026, 8, 17, tzinfo=UTC),
        )

    def resolve_proxy(
        self,
        contract_address: str,
        chain: Chain,
        block_number: int,
    ) -> ProxyResolution:
        _ = contract_address, chain, block_number
        raise ProviderResponseError("proxy resolution is not used by this test")

    def get_revert_data(self, inspection: TransactionInspection) -> str | None:
        _ = inspection
        return None


class _EnrichmentProvider:
    def __init__(self, enrichment: TransactionEnrichment) -> None:
        self.enrichment = enrichment
        self.calls = 0

    def capabilities(self, chain: Chain) -> ExplorerCapabilities:
        _ = chain
        return ExplorerCapabilities(transaction_context=True, contract_context=True)

    def fetch_transaction_enrichment(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionEnrichment:
        _ = chain, tx_hash
        self.calls += 1
        return self.enrichment


class _RetryingEnrichmentProvider(_EnrichmentProvider):
    def fetch_transaction_enrichment(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionEnrichment:
        self.calls += 1
        if self.calls == 1:
            raise ProviderResponseError("temporary explorer failure")
        return replace(self.enrichment, chain=chain, tx_hash=tx_hash)


def _database_with_transaction(
    tmp_path: Path,
) -> tuple[SQLiteDatabase, TransactionRepository]:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")
    EventLedgerRepository(database).persist_page(
        _WALLET,
        Chain.ETHEREUM,
        "activity",
        ActivityPage([_activity_item()], None),
        DataProvenance("alchemy", datetime(2026, 8, 17, tzinfo=UTC)),
        CompletenessState.COMPLETE,
    )
    return database, TransactionRepository(database)


def _activity_item() -> ActivityItem:
    return ActivityItem(
        block_number=24_000_000,
        tx_hash=_TX_HASH,
        log_index="0x0",
        timestamp=datetime(2026, 8, 17, tzinfo=UTC),
        from_address=_WALLET,
        to_address=_TARGET,
        asset_symbol="ETH",
        contract_address=None,
        raw_value="1",
        value_decimal=Decimal("0.000000000000000001"),
        value_usd=None,
        is_verified=True,
        category=ActivityCategory.EXTERNAL,
        chain=Chain.ETHEREUM,
    )


def _inspection() -> TransactionInspection:
    return TransactionInspection(
        chain=Chain.ETHEREUM,
        tx_hash=_TX_HASH,
        block_number=24_000_000,
        block_hash="0x" + "cd" * 32,
        transaction_index=2,
        from_address=_WALLET,
        to_address=_TARGET,
        contract_address=None,
        nonce=7,
        value_wei=1,
        input_data="0x12345678",
        gas_limit=21_000,
        gas_price=2_000_000_000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        status=True,
        gas_used=21_000,
        cumulative_gas_used=21_000,
        effective_gas_price=2_000_000_000,
        transaction_type=2,
        logs_bloom="0x" + "00" * 256,
        logs=(RawTransactionLog(0, _TARGET, (), "0x", False),),
        source_provider="alchemy",
        fetched_at=datetime(2026, 8, 17, tzinfo=UTC),
    )


def _enrichment() -> TransactionEnrichment:
    return TransactionEnrichment(
        chain=Chain.ETHEREUM,
        tx_hash=_TX_HASH,
        status=EnrichmentStatus.AVAILABLE,
        method_name="execute",
        transaction_types=("contract_call",),
        decoded_method_call="execute(uint256 amount)",
        decoded_method_id="0x12345678",
        decoded_parameters=(ExplorerDecodedParameter("amount", "uint256", "42"),),
        target_context=ExplorerAddressContext(
            address=_TARGET,
            name="Vault",
            implementation_name="VaultV2",
            ens_name=None,
            is_contract=True,
            is_verified=True,
            creator_address=_WALLET,
            creation_tx_hash="0x" + "44" * 32,
            source_reference=f"https://example.invalid/address/{_TARGET}",
        ),
        created_contract_context=None,
        source_name="Blockscout Ethereum",
        source_version="api-v2",
        source_reference=f"https://example.invalid/tx/{_TX_HASH}",
        fetched_at=datetime(2026, 8, 17, tzinfo=UTC),
    )
