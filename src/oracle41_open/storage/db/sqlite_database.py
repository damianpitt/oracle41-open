from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

_SCHEMA_VERSION = 1

_SCHEMA_V1_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS watchlist_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    chain TEXT NOT NULL,
    label TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(address, chain)
);
CREATE INDEX IF NOT EXISTS idx_watchlist_entries_chain ON watchlist_entries(chain);

CREATE TABLE IF NOT EXISTS wallet_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    chain TEXT NOT NULL,
    note TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(address, chain)
);
CREATE INDEX IF NOT EXISTS idx_wallet_notes_chain ON wallet_notes(chain);

CREATE TABLE IF NOT EXISTS wallet_note_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id INTEGER NOT NULL,
    tag TEXT NOT NULL,
    FOREIGN KEY(note_id) REFERENCES wallet_notes(id) ON DELETE CASCADE,
    UNIQUE(note_id, tag)
);
CREATE INDEX IF NOT EXISTS idx_wallet_note_tags_note_id ON wallet_note_tags(note_id);

CREATE TABLE IF NOT EXISTS saved_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    chain TEXT NOT NULL,
    filters_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_saved_views_chain ON saved_views(chain);

CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    address TEXT NOT NULL,
    chain TEXT NOT NULL,
    label TEXT,
    captured_at TEXT NOT NULL,
    native_balance TEXT NOT NULL,
    native_price_usd TEXT,
    total_usd TEXT,
    token_count INTEGER NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_address_chain_time ON snapshots(address, chain, captured_at DESC);
"""


@dataclass
class SQLiteDatabase:
    file_path: Path

    def __post_init__(self) -> None:
        self.initialize()

    @staticmethod
    def default() -> SQLiteDatabase:
        root = Path(user_data_dir(appname="oracle41-open", appauthor=False))
        return SQLiteDatabase(file_path=root / "state.sqlite3")

    def initialize(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as conn:
            conn.executescript(_SCHEMA_V1_SQL)
            current_version = self._read_schema_version(conn)
            if current_version < _SCHEMA_VERSION:
                self._write_schema_version(conn, _SCHEMA_VERSION)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.file_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _read_schema_version(self, conn: sqlite3.Connection) -> int:
        row = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version' LIMIT 1"
        ).fetchone()
        if row is None:
            return 0
        raw = row["value"]
        if not isinstance(raw, str):
            return 0
        try:
            parsed = int(raw)
        except ValueError:
            return 0
        if parsed < 0:
            return 0
        return parsed

    def _write_schema_version(self, conn: sqlite3.Connection, version: int) -> None:
        conn.execute(
            """
            INSERT INTO schema_meta(key, value)
            VALUES('schema_version', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (str(version),),
        )
