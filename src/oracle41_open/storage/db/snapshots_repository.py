from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import Chain, WalletOverviewResult
from oracle41_open.storage.db._helpers import (
    expect_int,
    normalize_address_or_raise,
    normalize_label,
    parse_datetime,
    parse_decimal,
    parse_optional_decimal,
    utc_now,
    utc_now_iso,
)
from oracle41_open.storage.db.models import WalletSnapshot
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class SnapshotsRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def create_snapshot(
        self,
        address: str,
        chain: Chain,
        overview: WalletOverviewResult,
        label: str | None = None,
        captured_at: datetime | None = None,
    ) -> WalletSnapshot:
        normalized_address = normalize_address_or_raise(address)
        normalized_label = normalize_label(label)
        effective_captured_at = captured_at or utc_now()
        payload = _snapshot_payload(overview=overview)
        serialized_payload = json_dumps(payload, pretty=False).decode("utf-8")
        with self._database.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO snapshots(
                    address,
                    chain,
                    label,
                    captured_at,
                    native_balance,
                    native_price_usd,
                    total_usd,
                    token_count,
                    payload_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_address,
                    chain.value,
                    normalized_label,
                    effective_captured_at.isoformat(),
                    str(overview.native_balance),
                    str(overview.native_price_usd) if overview.native_price_usd is not None else None,
                    str(overview.total_usd) if overview.total_usd is not None else None,
                    len(overview.token_balances),
                    serialized_payload,
                ),
            )
            raw_snapshot_id = cursor.lastrowid
            if not isinstance(raw_snapshot_id, int):
                raise RuntimeError("Failed to persist snapshot.")
            row = conn.execute(
                """
                SELECT
                    id,
                    address,
                    chain,
                    label,
                    captured_at,
                    native_balance,
                    native_price_usd,
                    total_usd,
                    token_count,
                    payload_json
                FROM snapshots
                WHERE id = ?
                LIMIT 1
                """,
                (raw_snapshot_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Could not load snapshot after insert.")
        return _row_to_wallet_snapshot(row)

    def get_snapshot(self, snapshot_id: int) -> WalletSnapshot | None:
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT
                    id,
                    address,
                    chain,
                    label,
                    captured_at,
                    native_balance,
                    native_price_usd,
                    total_usd,
                    token_count,
                    payload_json
                FROM snapshots
                WHERE id = ?
                LIMIT 1
                """,
                (snapshot_id,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_wallet_snapshot(row)

    def list_snapshots(self, address: str, chain: Chain, limit: int = 20) -> list[WalletSnapshot]:
        normalized_address = normalize_address_or_raise(address)
        normalized_limit = max(1, min(200, limit))
        with self._database.connection() as conn:
            rows = conn.execute(
                """
                SELECT
                    id,
                    address,
                    chain,
                    label,
                    captured_at,
                    native_balance,
                    native_price_usd,
                    total_usd,
                    token_count,
                    payload_json
                FROM snapshots
                WHERE address = ? AND chain = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT ?
                """,
                (normalized_address, chain.value, normalized_limit),
            ).fetchall()
        return [_row_to_wallet_snapshot(row) for row in rows]

    def delete_snapshot(self, snapshot_id: int) -> bool:
        with self._database.connection() as conn:
            cursor = conn.execute("DELETE FROM snapshots WHERE id = ?", (snapshot_id,))
        return cursor.rowcount > 0

    def prune_snapshots(self, address: str, chain: Chain, keep_latest: int = 20) -> int:
        normalized_address = normalize_address_or_raise(address)
        keep_count = max(0, keep_latest)
        if keep_count == 0:
            with self._database.connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM snapshots WHERE address = ? AND chain = ?",
                    (normalized_address, chain.value),
                )
            return cursor.rowcount
        with self._database.connection() as conn:
            protected_rows = conn.execute(
                """
                SELECT id
                FROM snapshots
                WHERE address = ? AND chain = ?
                ORDER BY captured_at DESC, id DESC
                LIMIT ?
                """,
                (normalized_address, chain.value, keep_count),
            ).fetchall()
            protected_ids = [
                row["id"] for row in protected_rows if isinstance(row["id"], int)
            ]
            if not protected_ids:
                cursor = conn.execute(
                    "DELETE FROM snapshots WHERE address = ? AND chain = ?",
                    (normalized_address, chain.value),
                )
                return cursor.rowcount
            placeholders = ",".join(["?"] * len(protected_ids))
            cursor = conn.execute(
                f"""
                DELETE FROM snapshots
                WHERE address = ? AND chain = ? AND id NOT IN ({placeholders})
                """,
                [normalized_address, chain.value, *protected_ids],
            )
        return cursor.rowcount


def _snapshot_payload(overview: WalletOverviewResult) -> dict[str, Any]:
    return {
        "version": 1,
        "captured_at": utc_now_iso(),
        "overview_updated_at": overview.updated_at.isoformat(),
        "native_balance": str(overview.native_balance),
        "native_price_usd": (
            str(overview.native_price_usd)
            if overview.native_price_usd is not None
            else None
        ),
        "total_usd": str(overview.total_usd) if overview.total_usd is not None else None,
        "token_balance_page_count": overview.token_balance_page_count,
        "token_balances_truncated": overview.token_balances_truncated,
        "token_balances": [
            {
                "contract_address": balance.token.contract_address,
                "symbol": balance.token.symbol,
                "name": balance.token.name,
                "decimals": balance.token.decimals,
                "is_verified": balance.token.is_verified,
                "balance_decimal": str(balance.balance_decimal),
                "price_usd": str(balance.price_usd) if balance.price_usd is not None else None,
                "balance_usd": str(balance.balance_usd) if balance.balance_usd is not None else None,
            }
            for balance in overview.token_balances
        ],
    }


def _row_to_wallet_snapshot(row: sqlite3.Row) -> WalletSnapshot:
    raw_id = row["id"]
    raw_address = row["address"]
    raw_chain = row["chain"]
    raw_label = row["label"]
    raw_captured_at = row["captured_at"]
    raw_native_balance = row["native_balance"]
    raw_native_price = row["native_price_usd"]
    raw_total_usd = row["total_usd"]
    raw_token_count = row["token_count"]
    raw_payload = row["payload_json"]

    if not isinstance(raw_address, str):
        raise ValueError("Invalid snapshot address.")
    if not isinstance(raw_chain, str):
        raise ValueError("Invalid snapshot chain.")
    if raw_label is not None and not isinstance(raw_label, str):
        raise ValueError("Invalid snapshot label.")
    if not isinstance(raw_payload, str):
        raise ValueError("Invalid snapshot payload.")
    decoded_payload = json_loads(raw_payload)
    if not isinstance(decoded_payload, dict):
        raise ValueError("Snapshot payload must be a JSON object.")
    return WalletSnapshot(
        id=expect_int(raw_id, "snapshot id"),
        address=raw_address,
        chain=Chain(raw_chain),
        label=raw_label,
        captured_at=parse_datetime(raw_captured_at),
        native_balance=parse_decimal(raw_native_balance),
        native_price_usd=parse_optional_decimal(raw_native_price),
        total_usd=parse_optional_decimal(raw_total_usd),
        token_count=expect_int(raw_token_count, "snapshot token_count"),
        payload=decoded_payload,
    )
