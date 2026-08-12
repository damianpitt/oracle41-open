"""Store reusable filter views in SQLite.

Each named view keeps its chain, filter JSON, and creation and update times.
The repository validates stored JSON before returning it to the interface.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import Chain
from oracle41_open.storage.db._helpers import normalize_non_empty, parse_datetime, utc_now_iso
from oracle41_open.storage.db.models import SavedView
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class SavedViewsRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def upsert_view(self, name: str, chain: Chain, filters: dict[str, Any]) -> SavedView:
        normalized_name = normalize_non_empty(name, field_name="View name")
        serialized_filters = json_dumps(filters, pretty=False).decode("utf-8")
        now_iso = utc_now_iso()
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO saved_views(name, chain, filters_json, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    chain = excluded.chain,
                    filters_json = excluded.filters_json,
                    updated_at = excluded.updated_at
                """,
                (normalized_name, chain.value, serialized_filters, now_iso, now_iso),
            )
            row = conn.execute(
                """
                SELECT id, name, chain, filters_json, created_at, updated_at
                FROM saved_views
                WHERE name = ?
                LIMIT 1
                """,
                (normalized_name,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Could not load saved view after upsert.")
        return _row_to_saved_view(row)

    def get_view(self, name: str) -> SavedView | None:
        normalized_name = normalize_non_empty(name, field_name="View name")
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT id, name, chain, filters_json, created_at, updated_at
                FROM saved_views
                WHERE name = ?
                LIMIT 1
                """,
                (normalized_name,),
            ).fetchone()
        if row is None:
            return None
        return _row_to_saved_view(row)

    def list_views(self, chain: Chain | None = None) -> list[SavedView]:
        query = """
            SELECT id, name, chain, filters_json, created_at, updated_at
            FROM saved_views
        """
        params: tuple[object, ...] = ()
        if chain is not None:
            query += " WHERE chain = ?"
            params = (chain.value,)
        query += " ORDER BY updated_at DESC, id DESC"
        with self._database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [_row_to_saved_view(row) for row in rows]

    def delete_view(self, name: str) -> bool:
        normalized_name = normalize_non_empty(name, field_name="View name")
        with self._database.connection() as conn:
            cursor = conn.execute("DELETE FROM saved_views WHERE name = ?", (normalized_name,))
        return cursor.rowcount > 0


def _row_to_saved_view(row: sqlite3.Row) -> SavedView:
    raw_id = row["id"]
    raw_name = row["name"]
    raw_chain = row["chain"]
    raw_filters = row["filters_json"]
    raw_created_at = row["created_at"]
    raw_updated_at = row["updated_at"]
    if not isinstance(raw_id, int):
        raise ValueError("Invalid saved view id.")
    if not isinstance(raw_name, str):
        raise ValueError("Invalid saved view name.")
    if not isinstance(raw_chain, str):
        raise ValueError("Invalid saved view chain.")
    if not isinstance(raw_filters, str):
        raise ValueError("Invalid saved view filters payload.")

    decoded_filters = json_loads(raw_filters)
    if not isinstance(decoded_filters, dict):
        raise ValueError("Saved view filters payload must be a JSON object.")

    return SavedView(
        id=raw_id,
        name=raw_name,
        chain=Chain(raw_chain),
        filters=decoded_filters,
        created_at=parse_datetime(raw_created_at),
        updated_at=parse_datetime(raw_updated_at),
    )
