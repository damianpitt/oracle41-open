"""Test transaction inspection, action persistence, and schema migration.

The cases cover receipts, traces, actions, cached decoding, ABIs, proxies, reverts, and raw-data requirements.
They protect the full M5 transaction enrichment path.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from eth_abi.abi import encode as abi_encode

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    Chain,
    CompletenessState,
    DataProvenance,
    InternalCall,
    ProviderCapabilities,
    ProviderResponseError,
    ProxyKind,
    ProxyResolution,
    ProxyResolutionStatus,
    RawTransactionLog,
    TraceDialect,
    TraceStatus,
    TransactionInspection,
    TransactionTrace,
    ValidationError,
)
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
from oracle41_open.core.services.action_normalizer import WalletActionNormalizer
from oracle41_open.core.services.contract_abi_service import ContractABIService
from oracle41_open.core.services.transaction_inspection_service import (
    TransactionInspectionService,
)
from oracle41_open.storage.db import (
    ContractABIRepository,
    EventLedgerRepository,
    SQLiteDatabase,
    TransactionRepository,
)

_TX_HASH = "0x" + "ab" * 32
_ADDRESS = "0x1111111111111111111111111111111111111111"
_TO = "0x2222222222222222222222222222222222222222"
_IMPLEMENTATION = "0x3333333333333333333333333333333333333333"
_CUSTOM_ABI = """
[
  {"type":"function","name":"execute","inputs":[{"name":"amount","type":"uint256"}]},
  {"type":"error","name":"LimitExceeded","inputs":[{"name":"limit","type":"uint256"}]}
]
"""


def test_v2_database_migrates_to_latest_without_losing_ledger_rows(tmp_path: Path) -> None:
    database_path = tmp_path / "state.sqlite3"
    database = SQLiteDatabase(database_path)
    ledger = EventLedgerRepository(database)
    ledger.persist_page(
        _ADDRESS,
        Chain.ETHEREUM,
        "activity",
        ActivityPage([_activity_item()], None),
        DataProvenance("alchemy", datetime(2026, 8, 10, tzinfo=UTC)),
        CompletenessState.COMPLETE,
    )
    with database.connection() as conn:
        conn.execute("UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'")
        conn.execute("DROP TABLE transaction_enrichments")
        conn.execute("DROP TABLE transaction_actions")
        conn.execute("DROP TABLE transaction_trace_calls")
        conn.execute("DROP TABLE transaction_traces")
        conn.execute("DROP TABLE proxy_resolutions")
        conn.execute("DROP TABLE contract_abis")
        conn.execute("DROP TABLE decoded_event_logs")
        conn.execute("DROP TABLE decoded_transaction_calls")
        conn.execute("DROP TABLE abi_signature_sources")
        conn.execute("DROP TABLE ledger_raw_logs")
        conn.execute("DROP TABLE ledger_transaction_receipts")
        conn.execute("DROP TABLE ledger_transaction_details")

    SQLiteDatabase(database_path)

    with sqlite3.connect(database_path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        event_count = conn.execute("SELECT COUNT(*) FROM ledger_events").fetchone()
        receipt_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'ledger_transaction_receipts'"
        ).fetchone()
    assert version == ("9",)
    assert event_count == (1,)
    assert receipt_table == ("ledger_transaction_receipts",)


def test_transaction_repository_roundtrip_and_fee_derivation(tmp_path: Path) -> None:
    database, repository = _repository_with_transaction(tmp_path)
    inspection = _inspection()

    repository.save_inspection(inspection)
    restored = repository.get_inspection(Chain.ETHEREUM, _TX_HASH)

    assert restored == inspection
    with database.connection() as conn:
        fee = conn.execute(
            "SELECT raw_value, value_decimal, asset_symbol FROM ledger_fees"
        ).fetchone()
    assert fee is not None
    assert fee["raw_value"] == "42000000000000"
    assert Decimal(fee["value_decimal"]) == Decimal("0.000042")
    assert fee["asset_symbol"] == "ETH"


def test_transaction_decoding_repository_roundtrip(tmp_path: Path) -> None:
    _, repository = _repository_with_transaction(tmp_path)
    inspection = _inspection()
    repository.save_inspection(inspection)
    decoding = StandardABIDecoder().decode(inspection)

    repository.save_decoding(Chain.ETHEREUM, _TX_HASH, decoding)

    assert repository.get_decoding(Chain.ETHEREUM, _TX_HASH) == decoding


def test_transaction_trace_repository_roundtrip_and_replace(tmp_path: Path) -> None:
    _, repository = _repository_with_transaction(tmp_path)
    repository.save_inspection(_inspection())
    trace = _trace()

    repository.save_trace(trace)

    assert repository.get_trace(Chain.ETHEREUM, _TX_HASH) == trace
    unavailable = replace(
        trace,
        status=TraceStatus.UNAVAILABLE,
        calls=(),
        raw_json=None,
        dialect=None,
        error="temporary failure",
    )
    repository.save_trace(unavailable)
    assert repository.get_trace(Chain.ETHEREUM, _TX_HASH) == unavailable


def test_transaction_action_repository_roundtrip_and_replace(tmp_path: Path) -> None:
    _, repository = _repository_with_transaction(tmp_path)
    inspection = _inspection()
    repository.save_inspection(inspection)
    decoding = StandardABIDecoder().decode(inspection)
    actions = WalletActionNormalizer().normalize(inspection, decoding, _trace())

    repository.save_actions(Chain.ETHEREUM, _TX_HASH, actions)

    assert repository.get_actions(Chain.ETHEREUM, _TX_HASH) == actions
    replacement = actions[:1]
    repository.save_actions(Chain.ETHEREUM, _TX_HASH, replacement)
    assert repository.get_actions(Chain.ETHEREUM, _TX_HASH) == replacement


def test_transaction_decoding_requires_raw_inspection(tmp_path: Path) -> None:
    repository = TransactionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    decoding = StandardABIDecoder().decode(_inspection())

    with pytest.raises(ValidationError, match="Load the transaction"):
        repository.save_decoding(Chain.ETHEREUM, _TX_HASH, decoding)


def test_transaction_repository_requires_canonical_parent(tmp_path: Path) -> None:
    repository = TransactionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))

    with pytest.raises(ValidationError, match="Synchronize Activity"):
        repository.save_inspection(_inspection())


def test_transaction_repository_rolls_back_duplicate_logs(tmp_path: Path) -> None:
    database, repository = _repository_with_transaction(tmp_path)
    duplicate = RawTransactionLog(3, _TO, ("0x" + "01" * 32,), "0x", False)
    inspection = _inspection(logs=(duplicate, duplicate))

    with pytest.raises(sqlite3.IntegrityError):
        repository.save_inspection(inspection)

    assert repository.get_inspection(Chain.ETHEREUM, _TX_HASH) is None
    with database.connection() as conn:
        fee_count = conn.execute("SELECT COUNT(*) FROM ledger_fees").fetchone()[0]
    assert fee_count == 0


def test_transaction_inspection_service_reuses_persisted_result(tmp_path: Path) -> None:
    _, repository = _repository_with_transaction(tmp_path)
    provider = _FakeTransactionProvider(_inspection())
    service = TransactionInspectionService(provider, repository)

    first = service.inspect(_TX_HASH, Chain.ETHEREUM)
    repository.save_actions(
        Chain.ETHEREUM,
        _TX_HASH,
        tuple(replace(action, normalizer_version="0") for action in first.actions),
    )
    second = service.inspect(_TX_HASH, Chain.ETHEREUM)

    assert not first.is_cached
    assert second.is_cached
    assert second.inspection == first.inspection
    assert second.decoding == first.decoding
    assert repository.get_decoding(Chain.ETHEREUM, _TX_HASH) == first.decoding
    assert second.actions == first.actions
    assert repository.get_actions(Chain.ETHEREUM, _TX_HASH) == first.actions
    assert provider.calls == 1
    assert provider.trace_calls == 1
    assert second.action_set is not None
    assert second.action_set.completeness.value == "partial"
    assert second.action_set.trace_status is TraceStatus.UNSUPPORTED
    assert second.action_set.missing_evidence


def test_transaction_inspection_service_redecodes_stale_cached_result(tmp_path: Path) -> None:
    _, repository = _repository_with_transaction(tmp_path)
    inspection = _inspection()
    repository.save_inspection(inspection)
    current_decoding = StandardABIDecoder().decode(inspection)
    repository.save_decoding(
        Chain.ETHEREUM,
        _TX_HASH,
        replace(current_decoding, decoder_version="0"),
    )
    provider = _FakeTransactionProvider(inspection)

    result = TransactionInspectionService(provider, repository).inspect(
        _TX_HASH,
        Chain.ETHEREUM,
    )

    assert result.is_cached
    assert result.decoding == current_decoding
    assert provider.calls == 0
    assert provider.trace_calls == 1


def test_transaction_inspection_service_retries_unavailable_trace(tmp_path: Path) -> None:
    _, repository = _repository_with_transaction(tmp_path)
    provider = _RetryingTraceProvider(_inspection())
    service = TransactionInspectionService(provider, repository)

    first = service.inspect(_TX_HASH, Chain.ETHEREUM)
    second = service.inspect(_TX_HASH, Chain.ETHEREUM)

    assert first.trace is not None
    assert first.trace.status is TraceStatus.UNAVAILABLE
    assert second.trace == _trace()
    assert provider.trace_calls == 2
    assert second.action_set is not None
    assert second.action_set.completeness.value == "complete"
    assert second.action_set.missing_evidence == ()


def test_transaction_inspection_resolves_proxy_and_decodes_custom_revert(
    tmp_path: Path,
) -> None:
    database, repository = _repository_with_transaction(tmp_path)
    abi_repository = ContractABIRepository(database)
    abi_service = ContractABIService(abi_repository)
    abi_service.import_verified_abi(
        Chain.ETHEREUM,
        _IMPLEMENTATION,
        _CUSTOM_ABI,
        datetime(2026, 8, 12, tzinfo=UTC),
        source_name="Verified test source",
        reference="https://example.invalid/contracts/implementation",
        source_version="1",
        contract_name="Executor",
    )
    registry = abi_service.registry_for(Chain.ETHEREUM, _IMPLEMENTATION)
    assert registry is not None
    call_definition = next(iter(registry.functions_by_selector.values()))[0]
    error_definition = next(iter(registry.errors_by_selector.values()))[0]
    inspection = replace(
        _inspection(),
        status=False,
        input_data=call_definition.selector + abi_encode(("uint256",), (25,)).hex(),
    )
    revert_data = error_definition.selector + abi_encode(("uint256",), (10,)).hex()
    resolution = ProxyResolution(
        chain=Chain.ETHEREUM,
        proxy_address=_TO,
        status=ProxyResolutionStatus.RESOLVED,
        proxy_kind=ProxyKind.EIP_1967,
        implementation_address=_IMPLEMENTATION,
        block_number=inspection.block_number,
        source_provider="test-rpc",
        resolved_at=datetime(2026, 8, 12, tzinfo=UTC),
    )
    provider = _ContextTransactionProvider(inspection, resolution, revert_data)
    service = TransactionInspectionService(
        provider,
        repository,
        abi_registry_provider=abi_service,
        proxy_repository=abi_repository,
    )

    first = service.inspect(_TX_HASH, Chain.ETHEREUM)
    second = service.inspect(_TX_HASH, Chain.ETHEREUM)

    assert first.proxy_resolution == resolution
    assert first.decoding.call.canonical_signature == "execute(uint256)"
    assert first.decoding.call.arguments[0].value == "25"
    assert first.decoding.implementation_address == _IMPLEMENTATION
    assert first.decoding.revert is not None
    assert first.decoding.revert.canonical_signature == "LimitExceeded(uint256)"
    assert first.decoding.revert.arguments[0].value == "10"
    assert first.decoding.revert.raw_data == revert_data
    assert first.decoding.revert.provenance is not None
    assert first.decoding.revert.provenance.is_verified
    assert second.is_cached
    assert second.decoding == first.decoding
    assert provider.inspection_calls == 1
    assert provider.proxy_calls == 1
    assert provider.revert_calls == 1
    assert provider.trace_calls == 1


class _FakeTransactionProvider:
    def __init__(self, inspection: TransactionInspection) -> None:
        self.inspection = inspection
        self.calls = 0
        self.trace_calls = 0

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        _ = chain
        return ProviderCapabilities(True, True)

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        _ = tx_hash
        _ = chain
        self.calls += 1
        return self.inspection

    def get_transaction_trace(self, tx_hash: str, chain: Chain) -> TransactionTrace:
        self.trace_calls += 1
        return _unsupported_trace(tx_hash, chain)


class _ContextTransactionProvider:
    def __init__(
        self,
        inspection: TransactionInspection,
        resolution: ProxyResolution,
        revert_data: str,
    ) -> None:
        self.inspection = inspection
        self.resolution = resolution
        self.revert_data = revert_data
        self.inspection_calls = 0
        self.proxy_calls = 0
        self.revert_calls = 0
        self.trace_calls = 0

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        _ = chain
        return ProviderCapabilities(
            transaction_lookup=True,
            receipts=True,
            proxy_resolution=True,
            revert_replay=True,
        )

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        _ = tx_hash, chain
        self.inspection_calls += 1
        return self.inspection

    def resolve_proxy(
        self,
        contract_address: str,
        chain: Chain,
        block_number: int,
    ) -> ProxyResolution:
        _ = contract_address, chain, block_number
        self.proxy_calls += 1
        return self.resolution

    def get_revert_data(self, inspection: TransactionInspection) -> str | None:
        _ = inspection
        self.revert_calls += 1
        return self.revert_data

    def get_transaction_trace(self, tx_hash: str, chain: Chain) -> TransactionTrace:
        self.trace_calls += 1
        return _unsupported_trace(tx_hash, chain)


class _RetryingTraceProvider(_FakeTransactionProvider):
    def get_transaction_trace(self, tx_hash: str, chain: Chain) -> TransactionTrace:
        self.trace_calls += 1
        if self.trace_calls == 1:
            raise ProviderResponseError("temporary trace failure")
        return _trace()


def _repository_with_transaction(tmp_path: Path) -> tuple[SQLiteDatabase, TransactionRepository]:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")
    EventLedgerRepository(database).persist_page(
        _ADDRESS,
        Chain.ETHEREUM,
        "activity",
        ActivityPage([_activity_item()], None),
        DataProvenance("alchemy", datetime(2026, 8, 10, tzinfo=UTC)),
        CompletenessState.COMPLETE,
    )
    return database, TransactionRepository(database)


def _activity_item() -> ActivityItem:
    return ActivityItem(
        block_number=24_000_000,
        tx_hash=_TX_HASH,
        log_index="0x0",
        timestamp=datetime(2026, 8, 10, tzinfo=UTC),
        from_address=_ADDRESS,
        to_address=_TO,
        asset_symbol="ETH",
        contract_address=None,
        raw_value="1",
        value_decimal=Decimal("0.000000000000000001"),
        value_usd=None,
        is_verified=True,
        category=ActivityCategory.EXTERNAL,
        chain=Chain.ETHEREUM,
    )


def _inspection(
    logs: tuple[RawTransactionLog, ...] | None = None,
) -> TransactionInspection:
    return TransactionInspection(
        chain=Chain.ETHEREUM,
        tx_hash=_TX_HASH,
        block_number=24_000_000,
        block_hash="0x" + "cd" * 32,
        transaction_index=2,
        from_address=_ADDRESS,
        to_address=_TO,
        contract_address=None,
        nonce=7,
        value_wei=1,
        input_data="0xa9059cbb",
        gas_limit=21_000,
        gas_price=2_000_000_000,
        max_fee_per_gas=3_000_000_000,
        max_priority_fee_per_gas=1_000_000_000,
        status=True,
        gas_used=21_000,
        cumulative_gas_used=90_000,
        effective_gas_price=2_000_000_000,
        transaction_type=2,
        logs_bloom="0x" + "00" * 256,
        logs=logs
        if logs is not None
        else (RawTransactionLog(3, _TO, ("0x" + "01" * 32,), "0x", False),),
        source_provider="json-rpc",
        fetched_at=datetime(2026, 8, 10, 1, tzinfo=UTC),
    )


def _trace() -> TransactionTrace:
    return TransactionTrace(
        chain=Chain.ETHEREUM,
        tx_hash=_TX_HASH,
        status=TraceStatus.COMPLETE,
        calls=(
            InternalCall(
                trace_address=(),
                depth=0,
                call_type="CALL",
                from_address=_ADDRESS,
                to_address=_TO,
                created_contract=None,
                value_wei=1,
                gas_limit=21_000,
                gas_used=20_000,
                input_data="0x1234",
                output_data="0x",
            ),
        ),
        raw_json='{"type":"CALL"}',
        source_provider="test-rpc",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        dialect=TraceDialect.DEBUG_CALL_TRACER,
    )


def _unsupported_trace(tx_hash: str, chain: Chain) -> TransactionTrace:
    return TransactionTrace(
        chain=chain,
        tx_hash=tx_hash,
        status=TraceStatus.UNSUPPORTED,
        calls=(),
        raw_json=None,
        source_provider="test-rpc",
        fetched_at=datetime(2026, 8, 13, tzinfo=UTC),
        error="trace methods unsupported",
    )
