"""Test durable protocol snapshots, checkpoints, and schema migration.

The cases protect exact round trips, ordered snapshot history, atomic finalization, and the resume
state that survives application restarts. Provider calls are covered by the service test module.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from oracle41_open.core.models import (
    Chain,
    ProtocolAdapterResult,
    ProtocolAdapterStatus,
    ProtocolAsset,
    ProtocolAssetRole,
    ProtocolCollectionCheckpoint,
    ProtocolEvidenceValue,
    ProtocolPosition,
    ProtocolPositionCompleteness,
    ProtocolPositionKind,
    ProtocolPositionProvenance,
    ProtocolRawEvidence,
    ProtocolRiskSnapshot,
    ProtocolRiskState,
)
from oracle41_open.storage.db import ProtocolPositionRepository, SQLiteDatabase

_WALLET = "0x1111111111111111111111111111111111111111"
_RESERVE = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_BLOCK = 24_000_000
_OBSERVED_AT = datetime(2026, 8, 31, 8, 0, tzinfo=UTC)


def test_database_migrates_v9_to_protocol_schema(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '9');
            """
        )

    SQLiteDatabase(path)

    with sqlite3.connect(path) as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert version == ("10",)
    assert {"protocol_snapshots", "protocol_sync_checkpoints"}.issubset(tables)


def test_checkpoint_round_trip_survives_repository_restart(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")
    repository = ProtocolPositionRepository(database)
    checkpoint = _checkpoint()

    repository.save_checkpoint(checkpoint)
    restarted = ProtocolPositionRepository(SQLiteDatabase(database.file_path))

    assert restarted.get_checkpoint(_WALLET.upper(), Chain.ETHEREUM, "aave-v3", _BLOCK) == checkpoint


def test_finished_snapshot_round_trip_removes_checkpoint(tmp_path: Path) -> None:
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    repository.save_checkpoint(_checkpoint())
    result = _result()

    saved = repository.save_snapshot(
        _WALLET,
        Chain.ETHEREUM,
        "aave-v3",
        _BLOCK,
        result,
        "alchemy",
        _OBSERVED_AT,
    )
    restored = repository.get_snapshot(_WALLET, Chain.ETHEREUM, "aave-v3", _BLOCK)

    assert restored == saved
    assert restored is not None
    assert restored.result == result
    assert restored.source_provider == "alchemy"
    assert repository.get_checkpoint(_WALLET, Chain.ETHEREUM, "aave-v3", _BLOCK) is None


def test_snapshot_finalization_rolls_back_when_checkpoint_delete_fails(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")
    repository = ProtocolPositionRepository(database)
    repository.save_checkpoint(_checkpoint())
    with database.connection() as conn:
        conn.execute(
            """
            CREATE TRIGGER prevent_protocol_checkpoint_delete
            BEFORE DELETE ON protocol_sync_checkpoints
            BEGIN
                SELECT RAISE(ABORT, 'checkpoint retained');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError, match="checkpoint retained"):
        repository.save_snapshot(
            _WALLET,
            Chain.ETHEREUM,
            "aave-v3",
            _BLOCK,
            _result(),
            "alchemy",
            _OBSERVED_AT,
        )

    assert repository.get_snapshot(_WALLET, Chain.ETHEREUM, "aave-v3", _BLOCK) is None
    assert repository.get_checkpoint(_WALLET, Chain.ETHEREUM, "aave-v3", _BLOCK) is not None


def test_snapshot_list_is_newest_first_and_can_filter_protocol(tmp_path: Path) -> None:
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    for block in (_BLOCK, _BLOCK + 1):
        result = _result(block)
        repository.save_snapshot(
            _WALLET,
            Chain.ETHEREUM,
            "aave-v3",
            block,
            result,
            "alchemy",
            _OBSERVED_AT,
        )

    snapshots = repository.list_snapshots(_WALLET, Chain.ETHEREUM, "aave-v3")

    assert [item.block_number for item in snapshots] == [_BLOCK + 1, _BLOCK]


def _checkpoint() -> ProtocolCollectionCheckpoint:
    evidence = ProtocolRawEvidence(
        kind="aave_v3_reserve_position",
        reference=f"eth_call:getUserReserveData:block:{_BLOCK}",
        contract_address="0x0a16f2fcc0d44fae41cc54e079281d84a363becd",
        tx_hash=None,
        signature="getUserReserveData(address,address)",
        values=(ProtocolEvidenceValue("reserve_contract", _RESERVE),),
    )
    return ProtocolCollectionCheckpoint(
        wallet_address=_WALLET,
        chain=Chain.ETHEREUM,
        protocol_id="aave-v3",
        block_number=_BLOCK,
        reserves=(("USDC", _RESERVE), ("WETH", "0xc02aa39b223fe8d0a0e5c4f27ead9083c756cc2")),
        next_reserve_index=1,
        raw_evidence=(evidence,),
        source_provider="alchemy",
        observed_at=_OBSERVED_AT,
        updated_at=_OBSERVED_AT,
    )


def _result(block_number: int = _BLOCK) -> ProtocolAdapterResult:
    provenance = ProtocolPositionProvenance(
        adapter_id="oracle41.aave-v3",
        adapter_version="1",
        source_provider="alchemy",
        source_reference=f"eth_call:getUserReserveData:block:{block_number}",
        observed_at=_OBSERVED_AT,
    )
    evidence = ProtocolRawEvidence(
        kind="aave_v3_reserve_position",
        reference=f"eth_call:getUserReserveData:block:{block_number}",
        contract_address="0x0a16f2fcc0d44fae41cc54e079281d84a363becd",
        tx_hash=None,
        signature="getUserReserveData(address,address)",
        values=(
            ProtocolEvidenceValue("reserve_contract", _RESERVE),
            ProtocolEvidenceValue("current_a_token_balance", "125000000"),
        ),
    )
    position = ProtocolPosition(
        schema_version=1,
        position_id=f"aave-v3:{block_number}:usdc",
        wallet_address=_WALLET,
        chain=Chain.ETHEREUM,
        block_number=block_number,
        protocol_id="aave-v3",
        protocol_name="Aave V3",
        kind=ProtocolPositionKind.COLLATERAL,
        label="USDC collateral",
        assets=(
            ProtocolAsset(
                role=ProtocolAssetRole.COLLATERAL,
                standard="ERC-20",
                contract_address=_RESERVE,
                symbol="USDC",
                token_id=None,
                raw_amount="125000000",
                decimals=6,
            ),
        ),
        contract_addresses=(_RESERVE,),
        completeness=ProtocolPositionCompleteness.COMPLETE,
        warnings=(),
        provenance=provenance,
    )
    risk = ProtocolRiskSnapshot(
        wallet_address=_WALLET,
        chain=Chain.ETHEREUM,
        block_number=block_number,
        protocol_id="aave-v3",
        total_collateral_base="12500000000",
        total_debt_base="2500000000",
        available_borrow_base="6500000000",
        liquidation_threshold_bps=8250,
        ltv_bps=8000,
        health_factor_wad="4125000000000000000",
        base_currency_unit="100000000",
        state=ProtocolRiskState.ABOVE_OR_EQUAL_LIQUIDATION_THRESHOLD,
        provenance=provenance,
    )
    return ProtocolAdapterResult(
        schema_version=1,
        status=ProtocolAdapterStatus.MATCHED,
        adapter_id="oracle41.aave-v3",
        adapter_version="1",
        protocol_id="aave-v3",
        protocol_name="Aave V3",
        positions=(position,),
        source_actions=(),
        source_balances=(),
        source_events=(),
        raw_evidence=(evidence,),
        warnings=(),
        risk_snapshot=risk,
    )
