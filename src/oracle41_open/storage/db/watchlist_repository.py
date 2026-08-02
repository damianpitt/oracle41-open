from __future__ import annotations

import sqlite3

from oracle41_open.core.models import Chain, WatchlistEntry
from oracle41_open.storage.db._helpers import (
    normalize_address_or_raise,
    normalize_label,
    parse_datetime,
    utc_now_iso,
)
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class WatchlistRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def upsert_entry(self, address: str, chain: Chain, label: str | None = None) -> WatchlistEntry:
        normalized_address = normalize_address_or_raise(address)
        normalized_label = normalize_label(label)
        now_iso = utc_now_iso()
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO watchlist_entries(address, chain, label, created_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(address, chain) DO UPDATE SET
                    label = excluded.label
                """,
                (normalized_address, chain.value, normalized_label, now_iso),
            )
            row = conn.execute(
                """
                SELECT id, address, chain, label, created_at
                FROM watchlist_entries
                WHERE address = ? AND chain = ?
                LIMIT 1
                """,
                (normalized_address, chain.value),
            ).fetchone()
        if row is None:
            raise RuntimeError("Could not load watchlist entry after upsert.")
        return _row_to_watchlist_entry(row)

    def get_entry(self, address: str, chain: Chain) -> WatchlistEntry | None:
        normalized_address = normalize_address_or_raise(address)
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT id, address, chain, label, created_at
                FROM watchlist_entries
                WHERE address = ? AND chain = ?
                LIMIT 1
                """,
                (normalized_address, chain.value),
            ).fetchone()
        if row is None:
            return None
        return _row_to_watchlist_entry(row)

    def list_entries(self, chain: Chain | None = None) -> list[WatchlistEntry]:
        query = """
            SELECT id, address, chain, label, created_at
            FROM watchlist_entries
        """
        params: tuple[object, ...] = ()
        if chain is not None:
            query += " WHERE chain = ?"
            params = (chain.value,)
        query += " ORDER BY created_at DESC, id DESC"
        with self._database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_watchlist_entry(row) for row in rows]

    def remove_entry(self, address: str, chain: Chain) -> bool:
        normalized_address = normalize_address_or_raise(address)
        with self._database.connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM watchlist_entries
                WHERE address = ? AND chain = ?
                """,
                (normalized_address, chain.value),
            )
        return cursor.rowcount > 0


def _row_to_watchlist_entry(row: sqlite3.Row) -> WatchlistEntry:
    raw_id = row["id"]
    raw_address = row["address"]
    raw_chain = row["chain"]
    raw_label = row["label"]
    raw_created_at = row["created_at"]

    if not isinstance(raw_id, int):
        raise ValueError("Invalid watchlist id.")
    if not isinstance(raw_address, str):
        raise ValueError("Invalid watchlist address.")
    if not isinstance(raw_chain, str):
        raise ValueError("Invalid watchlist chain.")
    if raw_label is not None and not isinstance(raw_label, str):
        raise ValueError("Invalid watchlist label.")
    chain = Chain(raw_chain)
    return WatchlistEntry(
        id=raw_id,
        address=raw_address,
        chain=chain,
        label=raw_label,
        created_at=parse_datetime(raw_created_at),
    )
