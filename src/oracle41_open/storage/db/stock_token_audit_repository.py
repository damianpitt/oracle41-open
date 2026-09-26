"""Persist Stock Token corporate actions and material multiplier changes.

Corporate actions use their issuer ID and deployment as a stable key. Repeated reads update the
last-seen time and lifecycle state. Multiplier rows are appended only when the current, pending, or
asset-status state changes, which keeps a compact but complete local audit trail.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    CorporateActionStatus,
    CorporateActionType,
    StockTokenCorporateAction,
    StockTokenMetadata,
    StockTokenMultiplierObservation,
    StockTokenStatus,
    StoredStockTokenCorporateAction,
)
from oracle41_open.storage.db._helpers import (
    normalize_address_or_raise,
    parse_datetime,
    parse_decimal,
    parse_optional_decimal,
)
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class StockTokenAuditRepository:
    """Read and write the local Stock Token audit ledger."""

    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save_corporate_actions(
        self,
        actions: tuple[StockTokenCorporateAction, ...],
        observed_at: datetime,
    ) -> None:
        with self._database.connection() as conn:
            for action in actions:
                address = normalize_address_or_raise(action.contract_address)
                conn.execute(
                    """
                    INSERT INTO stock_token_corporate_actions(
                        action_id, chain, contract_address, token_symbol, action_type,
                        status, process_date, detail_kind, details_json, source_provider,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(action_id, chain, contract_address) DO UPDATE SET
                        token_symbol = excluded.token_symbol,
                        action_type = excluded.action_type,
                        status = excluded.status,
                        process_date = excluded.process_date,
                        detail_kind = excluded.detail_kind,
                        details_json = excluded.details_json,
                        source_provider = excluded.source_provider,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        action.action_id,
                        action.chain.value,
                        address,
                        action.token_symbol,
                        action.action_type.value,
                        action.status.value,
                        action.process_date.isoformat() if action.process_date else None,
                        action.detail_kind,
                        json_dumps(dict(action.details), pretty=False).decode("utf-8"),
                        action.source_provider,
                        observed_at.isoformat(),
                        observed_at.isoformat(),
                    ),
                )

    def record_multiplier(
        self,
        metadata: StockTokenMetadata,
        observed_at: datetime,
        source_provider: str,
    ) -> StockTokenMultiplierObservation:
        address = normalize_address_or_raise(metadata.contract_address)
        candidate = StockTokenMultiplierObservation(
            asset_id=metadata.asset_id,
            token_symbol=metadata.symbol,
            chain=Chain.ROBINHOOD,
            contract_address=address,
            status=metadata.status,
            current_multiplier=metadata.current_multiplier,
            pending_multiplier=metadata.pending_multiplier,
            pending_multiplier_effective_at=metadata.pending_multiplier_effective_at,
            source_provider=source_provider,
            observed_at=observed_at,
        )
        latest = self._latest_multiplier(Chain.ROBINHOOD, address)
        if latest is not None and _same_multiplier_state(latest, candidate):
            return latest
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO stock_token_multiplier_observations(
                    asset_id, chain, contract_address, token_symbol, status,
                    current_multiplier, pending_multiplier, pending_multiplier_effective_at,
                    source_provider, observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    candidate.asset_id,
                    candidate.chain.value,
                    candidate.contract_address,
                    candidate.token_symbol,
                    candidate.status.value,
                    str(candidate.current_multiplier),
                    str(candidate.pending_multiplier) if candidate.pending_multiplier else None,
                    (
                        candidate.pending_multiplier_effective_at.isoformat()
                        if candidate.pending_multiplier_effective_at
                        else None
                    ),
                    candidate.source_provider,
                    candidate.observed_at.isoformat(),
                ),
            )
        return candidate

    def list_corporate_actions(
        self,
        chain: Chain,
        contract_address: str,
        limit: int = 20,
    ) -> tuple[StoredStockTokenCorporateAction, ...]:
        address = normalize_address_or_raise(contract_address)
        with self._database.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM stock_token_corporate_actions
                WHERE chain = ? AND contract_address = ?
                ORDER BY process_date DESC, action_id DESC
                LIMIT ?
                """,
                (chain.value, address, max(1, limit)),
            ).fetchall()
        return tuple(_action_from_row(row) for row in rows)

    def list_multiplier_history(
        self,
        chain: Chain,
        contract_address: str,
        limit: int = 20,
    ) -> tuple[StockTokenMultiplierObservation, ...]:
        address = normalize_address_or_raise(contract_address)
        with self._database.connection() as conn:
            rows = conn.execute(
                """
                SELECT * FROM stock_token_multiplier_observations
                WHERE chain = ? AND contract_address = ?
                ORDER BY observed_at DESC, id DESC
                LIMIT ?
                """,
                (chain.value, address, max(1, limit)),
            ).fetchall()
        return tuple(_multiplier_from_row(row) for row in rows)

    def _latest_multiplier(
        self,
        chain: Chain,
        contract_address: str,
    ) -> StockTokenMultiplierObservation | None:
        history = self.list_multiplier_history(chain, contract_address, limit=1)
        return history[0] if history else None


def _same_multiplier_state(
    left: StockTokenMultiplierObservation,
    right: StockTokenMultiplierObservation,
) -> bool:
    return (
        left.asset_id == right.asset_id
        and left.token_symbol == right.token_symbol
        and left.status is right.status
        and left.current_multiplier == right.current_multiplier
        and left.pending_multiplier == right.pending_multiplier
        and left.pending_multiplier_effective_at == right.pending_multiplier_effective_at
        and left.source_provider == right.source_provider
    )


def _action_from_row(row: sqlite3.Row) -> StoredStockTokenCorporateAction:
    values = dict(row)
    raw_details = json_loads(str(values["details_json"]))
    if not isinstance(raw_details, dict):
        raise ValueError("Stored corporate-action details are invalid.")
    raw_date = values["process_date"]
    return StoredStockTokenCorporateAction(
        action=StockTokenCorporateAction(
            action_id=str(values["action_id"]),
            action_type=CorporateActionType(str(values["action_type"])),
            status=CorporateActionStatus(str(values["status"])),
            process_date=date.fromisoformat(str(raw_date)) if raw_date else None,
            token_symbol=str(values["token_symbol"]),
            chain=Chain(str(values["chain"])),
            contract_address=str(values["contract_address"]),
            detail_kind=str(values["detail_kind"]),
            details=tuple(sorted((str(key), str(value)) for key, value in raw_details.items())),
            source_provider=str(values["source_provider"]),
        ),
        first_seen_at=parse_datetime(values["first_seen_at"]),
        last_seen_at=parse_datetime(values["last_seen_at"]),
    )


def _multiplier_from_row(row: sqlite3.Row) -> StockTokenMultiplierObservation:
    values = dict(row)
    raw_pending_at = values["pending_multiplier_effective_at"]
    return StockTokenMultiplierObservation(
        asset_id=str(values["asset_id"]),
        token_symbol=str(values["token_symbol"]),
        chain=Chain(str(values["chain"])),
        contract_address=str(values["contract_address"]),
        status=StockTokenStatus(str(values["status"])),
        current_multiplier=parse_decimal(values["current_multiplier"]),
        pending_multiplier=parse_optional_decimal(values["pending_multiplier"]),
        pending_multiplier_effective_at=(
            parse_datetime(raw_pending_at) if raw_pending_at else None
        ),
        source_provider=str(values["source_provider"]),
        observed_at=parse_datetime(values["observed_at"]),
    )
