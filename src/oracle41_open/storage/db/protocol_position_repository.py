"""Persist finished protocol snapshots and resumable collection checkpoints.

The repository stores one immutable result per wallet, chain, protocol, and block. Checkpoints are
updated after each collected reserve. Saving the final snapshot and deleting its checkpoint happen
in one SQLite transaction, so a failed write cannot remove the last safe resume point.
"""

from __future__ import annotations

from datetime import datetime
from typing import cast

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
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
    StoredProtocolSnapshot,
)
from oracle41_open.storage.db._helpers import normalize_address_or_raise, parse_datetime, utc_now
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class ProtocolPositionRepository:
    """Read and write versioned protocol state without provider-specific tables."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save_checkpoint(self, checkpoint: ProtocolCollectionCheckpoint) -> None:
        wallet = normalize_address_or_raise(checkpoint.wallet_address)
        if checkpoint.next_reserve_index > len(checkpoint.reserves):
            raise ValueError("Protocol checkpoint points beyond its reserve list.")
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO protocol_sync_checkpoints(
                    wallet_address, chain, protocol_id, block_number, reserves_json,
                    next_reserve_index, evidence_json, source_provider, observed_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_address, chain, protocol_id, block_number) DO UPDATE SET
                    reserves_json = excluded.reserves_json,
                    next_reserve_index = excluded.next_reserve_index,
                    evidence_json = excluded.evidence_json,
                    source_provider = excluded.source_provider,
                    observed_at = excluded.observed_at,
                    updated_at = excluded.updated_at
                """,
                (
                    wallet,
                    checkpoint.chain.value,
                    checkpoint.protocol_id,
                    checkpoint.block_number,
                    _json_text([[symbol, address] for symbol, address in checkpoint.reserves]),
                    checkpoint.next_reserve_index,
                    _json_text([_evidence_payload(item) for item in checkpoint.raw_evidence]),
                    checkpoint.source_provider,
                    checkpoint.observed_at.isoformat(),
                    checkpoint.updated_at.isoformat(),
                ),
            )

    def get_checkpoint(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
    ) -> ProtocolCollectionCheckpoint | None:
        wallet = normalize_address_or_raise(wallet_address)
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM protocol_sync_checkpoints
                WHERE wallet_address = ? AND chain = ? AND protocol_id = ? AND block_number = ?
                """,
                (wallet, chain.value, protocol_id, block_number),
            ).fetchone()
        if row is None:
            return None
        reserves_payload = _json_list(row["reserves_json"], "protocol checkpoint reserves")
        evidence_payload = _json_list(row["evidence_json"], "protocol checkpoint evidence")
        reserves = tuple(_reserve_from_payload(item) for item in reserves_payload)
        next_index = _integer(row["next_reserve_index"], "protocol checkpoint index")
        if next_index < 0 or next_index > len(reserves):
            raise ValueError("Stored protocol checkpoint index is invalid.")
        return ProtocolCollectionCheckpoint(
            wallet_address=wallet,
            chain=chain,
            protocol_id=protocol_id,
            block_number=block_number,
            reserves=reserves,
            next_reserve_index=next_index,
            raw_evidence=tuple(_evidence_from_payload(item) for item in evidence_payload),
            source_provider=_text(row["source_provider"], "protocol checkpoint provider"),
            observed_at=parse_datetime(row["observed_at"]),
            updated_at=parse_datetime(row["updated_at"]),
        )

    def delete_checkpoint(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
    ) -> None:
        wallet = normalize_address_or_raise(wallet_address)
        with self._database.connection() as conn:
            conn.execute(
                """
                DELETE FROM protocol_sync_checkpoints
                WHERE wallet_address = ? AND chain = ? AND protocol_id = ? AND block_number = ?
                """,
                (wallet, chain.value, protocol_id, block_number),
            )

    def save_snapshot(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
        result: ProtocolAdapterResult,
        source_provider: str,
        observed_at: datetime,
    ) -> StoredProtocolSnapshot:
        wallet = normalize_address_or_raise(wallet_address)
        _validate_result_context(wallet, chain, protocol_id, block_number, result)
        _validate_result_provenance(result, source_provider)
        saved_at = utc_now()
        payload = _result_payload(result)
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO protocol_snapshots(
                    wallet_address, chain, protocol_id, block_number, adapter_id,
                    adapter_version, status, source_provider, observed_at, saved_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_address, chain, protocol_id, block_number) DO UPDATE SET
                    adapter_id = excluded.adapter_id,
                    adapter_version = excluded.adapter_version,
                    status = excluded.status,
                    source_provider = excluded.source_provider,
                    observed_at = excluded.observed_at,
                    saved_at = excluded.saved_at,
                    payload_json = excluded.payload_json
                """,
                (
                    wallet,
                    chain.value,
                    protocol_id,
                    block_number,
                    result.adapter_id,
                    result.adapter_version,
                    result.status.value,
                    source_provider,
                    observed_at.isoformat(),
                    saved_at.isoformat(),
                    _json_text(payload),
                ),
            )
            conn.execute(
                """
                DELETE FROM protocol_sync_checkpoints
                WHERE wallet_address = ? AND chain = ? AND protocol_id = ? AND block_number = ?
                """,
                (wallet, chain.value, protocol_id, block_number),
            )
        return StoredProtocolSnapshot(
            wallet_address=wallet,
            chain=chain,
            protocol_id=protocol_id,
            block_number=block_number,
            result=result,
            source_provider=source_provider,
            observed_at=observed_at,
            saved_at=saved_at,
        )

    def get_snapshot(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
    ) -> StoredProtocolSnapshot | None:
        wallet = normalize_address_or_raise(wallet_address)
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT payload_json, source_provider, observed_at, saved_at
                FROM protocol_snapshots
                WHERE wallet_address = ? AND chain = ? AND protocol_id = ? AND block_number = ?
                """,
                (wallet, chain.value, protocol_id, block_number),
            ).fetchone()
        if row is None:
            return None
        payload = _json_mapping(row["payload_json"], "protocol snapshot")
        result = _result_from_payload(payload)
        _validate_result_context(wallet, chain, protocol_id, block_number, result)
        return StoredProtocolSnapshot(
            wallet_address=wallet,
            chain=chain,
            protocol_id=protocol_id,
            block_number=block_number,
            result=result,
            source_provider=_text(row["source_provider"], "protocol snapshot provider"),
            observed_at=parse_datetime(row["observed_at"]),
            saved_at=parse_datetime(row["saved_at"]),
        )

    def list_snapshots(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str | None = None,
    ) -> tuple[StoredProtocolSnapshot, ...]:
        wallet = normalize_address_or_raise(wallet_address)
        query = """
            SELECT protocol_id, block_number, payload_json, source_provider, observed_at, saved_at
            FROM protocol_snapshots WHERE wallet_address = ? AND chain = ?
        """
        parameters: tuple[object, ...] = (wallet, chain.value)
        if protocol_id is not None:
            query += " AND protocol_id = ?"
            parameters = (*parameters, protocol_id)
        query += " ORDER BY block_number DESC, protocol_id"
        with self._database.connection() as conn:
            rows = conn.execute(query, parameters).fetchall()
        return tuple(
            StoredProtocolSnapshot(
                wallet_address=wallet,
                chain=chain,
                protocol_id=_text(row["protocol_id"], "protocol ID"),
                block_number=_integer(row["block_number"], "protocol block number"),
                result=_result_from_payload(
                    _json_mapping(row["payload_json"], "protocol snapshot")
                ),
                source_provider=_text(row["source_provider"], "protocol snapshot provider"),
                observed_at=parse_datetime(row["observed_at"]),
                saved_at=parse_datetime(row["saved_at"]),
            )
            for row in rows
        )


def _validate_result_context(
    wallet: str,
    chain: Chain,
    protocol_id: str,
    block_number: int,
    result: ProtocolAdapterResult,
) -> None:
    if result.protocol_id != protocol_id:
        raise ValueError("Protocol snapshot result has a different protocol ID.")
    if result.source_actions or result.source_balances or result.source_events:
        raise ValueError("Protocol snapshot persistence requires protocol-owned raw evidence.")
    contexts = [
        (item.wallet_address, item.chain, item.block_number, item.protocol_id)
        for item in result.positions
    ]
    if result.risk_snapshot is not None:
        risk = result.risk_snapshot
        contexts.append((risk.wallet_address, risk.chain, risk.block_number, risk.protocol_id))
    if any(context != (wallet, chain, block_number, protocol_id) for context in contexts):
        raise ValueError("Protocol snapshot result does not match its storage key.")


def _validate_result_provenance(
    result: ProtocolAdapterResult,
    source_provider: str,
) -> None:
    if not source_provider:
        raise ValueError("Protocol snapshot source provider is required.")
    provenances = [position.provenance for position in result.positions]
    if result.risk_snapshot is not None:
        provenances.append(result.risk_snapshot.provenance)
    providers = {item.source_provider for item in provenances}
    if providers and providers != {source_provider}:
        raise ValueError("Protocol snapshot contains mixed provider provenance.")


def _result_payload(result: ProtocolAdapterResult) -> dict[str, object]:
    return {
        "schema_version": result.schema_version,
        "status": result.status.value,
        "adapter_id": result.adapter_id,
        "adapter_version": result.adapter_version,
        "protocol_id": result.protocol_id,
        "protocol_name": result.protocol_name,
        "positions": [_position_payload(item) for item in result.positions],
        "raw_evidence": [_evidence_payload(item) for item in result.raw_evidence],
        "warnings": list(result.warnings),
        "risk_snapshot": (
            _risk_payload(result.risk_snapshot) if result.risk_snapshot is not None else None
        ),
    }


def _result_from_payload(payload: dict[str, object]) -> ProtocolAdapterResult:
    return ProtocolAdapterResult(
        schema_version=_integer(payload.get("schema_version"), "protocol schema version"),
        status=ProtocolAdapterStatus(_text(payload.get("status"), "protocol status")),
        adapter_id=_text(payload.get("adapter_id"), "protocol adapter ID"),
        adapter_version=_text(payload.get("adapter_version"), "protocol adapter version"),
        protocol_id=_optional_text(payload.get("protocol_id")),
        protocol_name=_optional_text(payload.get("protocol_name")),
        positions=tuple(
            _position_from_payload(item)
            for item in _list_value(payload.get("positions"), "protocol positions")
        ),
        source_actions=(),
        source_balances=(),
        source_events=(),
        raw_evidence=tuple(
            _evidence_from_payload(item)
            for item in _list_value(payload.get("raw_evidence"), "protocol evidence")
        ),
        warnings=tuple(
            _text(item, "protocol warning")
            for item in _list_value(payload.get("warnings"), "protocol warnings")
        ),
        risk_snapshot=(
            None
            if payload.get("risk_snapshot") is None
            else _risk_from_payload(_mapping(payload.get("risk_snapshot"), "risk snapshot"))
        ),
    )


def _position_payload(position: ProtocolPosition) -> dict[str, object]:
    return {
        "schema_version": position.schema_version,
        "position_id": position.position_id,
        "wallet_address": position.wallet_address,
        "chain": position.chain.value,
        "block_number": position.block_number,
        "protocol_id": position.protocol_id,
        "protocol_name": position.protocol_name,
        "kind": position.kind.value,
        "label": position.label,
        "assets": [
            {
                "role": asset.role.value,
                "standard": asset.standard,
                "contract_address": asset.contract_address,
                "symbol": asset.symbol,
                "token_id": asset.token_id,
                "raw_amount": asset.raw_amount,
                "decimals": asset.decimals,
            }
            for asset in position.assets
        ],
        "contract_addresses": list(position.contract_addresses),
        "completeness": position.completeness.value,
        "warnings": list(position.warnings),
        "provenance": _provenance_payload(position.provenance),
    }


def _position_from_payload(raw: object) -> ProtocolPosition:
    payload = _mapping(raw, "protocol position")
    return ProtocolPosition(
        schema_version=_integer(payload.get("schema_version"), "position schema version"),
        position_id=_text(payload.get("position_id"), "position ID"),
        wallet_address=_text(payload.get("wallet_address"), "position wallet"),
        chain=Chain(_text(payload.get("chain"), "position chain")),
        block_number=_integer(payload.get("block_number"), "position block"),
        protocol_id=_text(payload.get("protocol_id"), "position protocol ID"),
        protocol_name=_text(payload.get("protocol_name"), "position protocol name"),
        kind=ProtocolPositionKind(_text(payload.get("kind"), "position kind")),
        label=_text(payload.get("label"), "position label"),
        assets=tuple(_asset_from_payload(item) for item in _list_value(payload.get("assets"), "position assets")),
        contract_addresses=tuple(
            _text(item, "position contract")
            for item in _list_value(payload.get("contract_addresses"), "position contracts")
        ),
        completeness=ProtocolPositionCompleteness(
            _text(payload.get("completeness"), "position completeness")
        ),
        warnings=tuple(
            _text(item, "position warning")
            for item in _list_value(payload.get("warnings"), "position warnings")
        ),
        provenance=_provenance_from_payload(
            _mapping(payload.get("provenance"), "position provenance")
        ),
    )


def _asset_from_payload(raw: object) -> ProtocolAsset:
    payload = _mapping(raw, "protocol asset")
    raw_decimals = payload.get("decimals")
    return ProtocolAsset(
        role=ProtocolAssetRole(_text(payload.get("role"), "asset role")),
        standard=_text(payload.get("standard"), "asset standard"),
        contract_address=_optional_text(payload.get("contract_address")),
        symbol=_optional_text(payload.get("symbol")),
        token_id=_optional_text(payload.get("token_id")),
        raw_amount=_text(payload.get("raw_amount"), "asset amount"),
        decimals=None if raw_decimals is None else _integer(raw_decimals, "asset decimals"),
    )


def _risk_payload(risk: ProtocolRiskSnapshot) -> dict[str, object]:
    return {
        "wallet_address": risk.wallet_address,
        "chain": risk.chain.value,
        "block_number": risk.block_number,
        "protocol_id": risk.protocol_id,
        "total_collateral_base": risk.total_collateral_base,
        "total_debt_base": risk.total_debt_base,
        "available_borrow_base": risk.available_borrow_base,
        "liquidation_threshold_bps": risk.liquidation_threshold_bps,
        "ltv_bps": risk.ltv_bps,
        "health_factor_wad": risk.health_factor_wad,
        "base_currency_unit": risk.base_currency_unit,
        "state": risk.state.value,
        "provenance": _provenance_payload(risk.provenance),
    }


def _risk_from_payload(payload: dict[str, object]) -> ProtocolRiskSnapshot:
    return ProtocolRiskSnapshot(
        wallet_address=_text(payload.get("wallet_address"), "risk wallet"),
        chain=Chain(_text(payload.get("chain"), "risk chain")),
        block_number=_integer(payload.get("block_number"), "risk block"),
        protocol_id=_text(payload.get("protocol_id"), "risk protocol ID"),
        total_collateral_base=_text(payload.get("total_collateral_base"), "total collateral"),
        total_debt_base=_text(payload.get("total_debt_base"), "total debt"),
        available_borrow_base=_text(payload.get("available_borrow_base"), "available borrow"),
        liquidation_threshold_bps=_integer(payload.get("liquidation_threshold_bps"), "liquidation threshold"),
        ltv_bps=_integer(payload.get("ltv_bps"), "loan-to-value"),
        health_factor_wad=_text(payload.get("health_factor_wad"), "health factor"),
        base_currency_unit=_text(payload.get("base_currency_unit"), "base currency unit"),
        state=ProtocolRiskState(_text(payload.get("state"), "risk state")),
        provenance=_provenance_from_payload(_mapping(payload.get("provenance"), "risk provenance")),
    )


def _provenance_payload(provenance: ProtocolPositionProvenance) -> dict[str, object]:
    return {
        "adapter_id": provenance.adapter_id,
        "adapter_version": provenance.adapter_version,
        "source_provider": provenance.source_provider,
        "source_reference": provenance.source_reference,
        "observed_at": provenance.observed_at.isoformat(),
    }


def _provenance_from_payload(payload: dict[str, object]) -> ProtocolPositionProvenance:
    return ProtocolPositionProvenance(
        adapter_id=_text(payload.get("adapter_id"), "provenance adapter ID"),
        adapter_version=_text(payload.get("adapter_version"), "provenance adapter version"),
        source_provider=_text(payload.get("source_provider"), "provenance provider"),
        source_reference=_text(payload.get("source_reference"), "provenance reference"),
        observed_at=parse_datetime(payload.get("observed_at")),
    )


def _evidence_payload(evidence: ProtocolRawEvidence) -> dict[str, object]:
    return {
        "kind": evidence.kind,
        "reference": evidence.reference,
        "contract_address": evidence.contract_address,
        "tx_hash": evidence.tx_hash,
        "signature": evidence.signature,
        "values": [{"name": item.name, "value": item.value} for item in evidence.values],
    }


def _evidence_from_payload(raw: object) -> ProtocolRawEvidence:
    payload = _mapping(raw, "protocol evidence")
    return ProtocolRawEvidence(
        kind=_text(payload.get("kind"), "evidence kind"),
        reference=_text(payload.get("reference"), "evidence reference"),
        contract_address=_optional_text(payload.get("contract_address")),
        tx_hash=_optional_text(payload.get("tx_hash")),
        signature=_optional_text(payload.get("signature")),
        values=tuple(
            ProtocolEvidenceValue(
                name=_text(_mapping(item, "evidence value").get("name"), "evidence name"),
                value=_text(_mapping(item, "evidence value").get("value"), "evidence value"),
            )
            for item in _list_value(payload.get("values"), "evidence values")
        ),
    )


def _reserve_from_payload(raw: object) -> tuple[str, str]:
    values = _list_value(raw, "protocol reserve")
    if len(values) != 2:
        raise ValueError("Stored protocol reserve is invalid.")
    return (_text(values[0], "reserve symbol"), _text(values[1], "reserve address"))


def _json_text(value: object) -> str:
    return json_dumps(value).decode("utf-8")


def _json_mapping(raw: object, label: str) -> dict[str, object]:
    return _mapping(json_loads(_text(raw, label)), label)


def _json_list(raw: object, label: str) -> list[object]:
    return _list_value(json_loads(_text(raw, label)), label)


def _mapping(raw: object, label: str) -> dict[str, object]:
    if not isinstance(raw, dict):
        raise ValueError(f"Stored {label} is invalid.")
    return cast(dict[str, object], raw)


def _list_value(raw: object, label: str) -> list[object]:
    if not isinstance(raw, list):
        raise ValueError(f"Stored {label} is invalid.")
    return cast(list[object], raw)


def _text(raw: object, label: str) -> str:
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"Stored {label} is invalid.")
    return raw


def _optional_text(raw: object) -> str | None:
    if raw is None:
        return None
    return _text(raw, "optional text")


def _integer(raw: object, label: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"Stored {label} is invalid.")
    return raw
