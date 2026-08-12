"""Store wallet notes and tags in SQLite.

The repository saves one note per wallet and chain and keeps tag updates consistent with the note record.
Text and tag normalization happens before data is returned to views.
"""

from __future__ import annotations

import sqlite3

from oracle41_open.core.models import Chain
from oracle41_open.storage.db._helpers import (
    normalize_address_or_raise,
    normalize_non_empty,
    normalize_tags,
    parse_datetime,
    utc_now_iso,
)
from oracle41_open.storage.db.models import WalletNote
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class WalletNotesRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def upsert_note(
        self,
        address: str,
        chain: Chain,
        note: str,
        tags: list[str] | None = None,
    ) -> WalletNote:
        normalized_address = normalize_address_or_raise(address)
        normalized_note = normalize_non_empty(note, field_name="Note")
        now_iso = utc_now_iso()
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT id, created_at
                FROM wallet_notes
                WHERE address = ? AND chain = ?
                LIMIT 1
                """,
                (normalized_address, chain.value),
            ).fetchone()
            note_id: int
            if row is None:
                cursor = conn.execute(
                    """
                    INSERT INTO wallet_notes(address, chain, note, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (normalized_address, chain.value, normalized_note, now_iso, now_iso),
                )
                raw_last_row_id = cursor.lastrowid
                if not isinstance(raw_last_row_id, int):
                    raise RuntimeError("Failed to persist wallet note.")
                note_id = raw_last_row_id
            else:
                raw_note_id = row["id"]
                if not isinstance(raw_note_id, int):
                    raise ValueError("Invalid wallet note id.")
                note_id = raw_note_id
                conn.execute(
                    """
                    UPDATE wallet_notes
                    SET note = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (normalized_note, now_iso, note_id),
                )
            if tags is not None:
                self._replace_tags(conn=conn, note_id=note_id, tags=tags)
            loaded = self._get_note_by_id(conn=conn, note_id=note_id)
        if loaded is None:
            raise RuntimeError("Could not load wallet note after upsert.")
        return loaded

    def get_note(self, address: str, chain: Chain) -> WalletNote | None:
        normalized_address = normalize_address_or_raise(address)
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT id
                FROM wallet_notes
                WHERE address = ? AND chain = ?
                LIMIT 1
                """,
                (normalized_address, chain.value),
            ).fetchone()
            if row is None:
                return None
            raw_note_id = row["id"]
            if not isinstance(raw_note_id, int):
                raise ValueError("Invalid wallet note id.")
            return self._get_note_by_id(conn=conn, note_id=raw_note_id)

    def list_notes(self, chain: Chain | None = None) -> list[WalletNote]:
        query = """
            SELECT id
            FROM wallet_notes
        """
        params: tuple[object, ...] = ()
        if chain is not None:
            query += " WHERE chain = ?"
            params = (chain.value,)
        query += " ORDER BY updated_at DESC, id DESC"

        notes: list[WalletNote] = []
        with self._database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
            for row in rows:
                raw_note_id = row["id"]
                if not isinstance(raw_note_id, int):
                    raise ValueError("Invalid wallet note id.")
                note = self._get_note_by_id(conn=conn, note_id=raw_note_id)
                if note is not None:
                    notes.append(note)
        return notes

    def delete_note(self, address: str, chain: Chain) -> bool:
        normalized_address = normalize_address_or_raise(address)
        with self._database.connection() as conn:
            cursor = conn.execute(
                """
                DELETE FROM wallet_notes
                WHERE address = ? AND chain = ?
                """,
                (normalized_address, chain.value),
            )
        return cursor.rowcount > 0

    def _replace_tags(self, conn: sqlite3.Connection, note_id: int, tags: list[str]) -> None:
        normalized = normalize_tags(tags)
        conn.execute("DELETE FROM wallet_note_tags WHERE note_id = ?", (note_id,))
        for tag in normalized:
            conn.execute(
                """
                INSERT INTO wallet_note_tags(note_id, tag)
                VALUES(?, ?)
                ON CONFLICT(note_id, tag) DO NOTHING
                """,
                (note_id, tag),
            )

    def _get_note_by_id(self, conn: sqlite3.Connection, note_id: int) -> WalletNote | None:
        row = conn.execute(
            """
            SELECT id, address, chain, note, created_at, updated_at
            FROM wallet_notes
            WHERE id = ?
            LIMIT 1
            """,
            (note_id,),
        ).fetchone()
        if row is None:
            return None

        raw_tags = conn.execute(
            """
            SELECT tag
            FROM wallet_note_tags
            WHERE note_id = ?
            ORDER BY tag COLLATE NOCASE ASC, id ASC
            """,
            (note_id,),
        ).fetchall()
        tags = [tag_row["tag"] for tag_row in raw_tags if isinstance(tag_row["tag"], str)]
        return _row_to_wallet_note(row=row, tags=tags)


def _row_to_wallet_note(row: sqlite3.Row, tags: list[str]) -> WalletNote:
    raw_id = row["id"]
    raw_address = row["address"]
    raw_chain = row["chain"]
    raw_note = row["note"]
    raw_created_at = row["created_at"]
    raw_updated_at = row["updated_at"]
    if not isinstance(raw_id, int):
        raise ValueError("Invalid wallet note id.")
    if not isinstance(raw_address, str):
        raise ValueError("Invalid wallet note address.")
    if not isinstance(raw_chain, str):
        raise ValueError("Invalid wallet note chain.")
    if not isinstance(raw_note, str):
        raise ValueError("Invalid wallet note body.")

    return WalletNote(
        id=raw_id,
        address=raw_address,
        chain=Chain(raw_chain),
        note=raw_note,
        tags=tags,
        created_at=parse_datetime(raw_created_at),
        updated_at=parse_datetime(raw_updated_at),
    )
