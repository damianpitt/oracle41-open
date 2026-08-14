"""Create and migrate the local SQLite database.

Forward-only schema migrations run in order and update the schema version only after successful SQL execution.
Connections enable foreign keys and roll back failed repository operations.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from platformdirs import user_data_dir

_SCHEMA_VERSION = 7

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

_SCHEMA_V2_SQL = """
CREATE TABLE ledger_transactions (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    block_number INTEGER,
    timestamp TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(chain, tx_hash)
);
CREATE INDEX idx_ledger_transactions_chain_time
    ON ledger_transactions(chain, timestamp DESC);

CREATE TABLE ledger_events (
    wallet_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    event_id TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index TEXT NOT NULL,
    block_number INTEGER,
    timestamp TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    asset_symbol TEXT NOT NULL,
    contract_address TEXT,
    raw_value TEXT NOT NULL,
    value_decimal TEXT NOT NULL,
    value_usd TEXT,
    is_verified INTEGER,
    category TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(wallet_address, chain, event_id),
    FOREIGN KEY(chain, tx_hash) REFERENCES ledger_transactions(chain, tx_hash)
);
CREATE INDEX idx_ledger_events_wallet_chain_time
    ON ledger_events(wallet_address, chain, timestamp DESC);
CREATE INDEX idx_ledger_events_wallet_chain_block
    ON ledger_events(wallet_address, chain, block_number DESC);
CREATE INDEX idx_ledger_events_contract
    ON ledger_events(chain, contract_address, timestamp DESC);

CREATE TABLE ledger_assets (
    chain TEXT NOT NULL,
    asset_key TEXT NOT NULL,
    contract_address TEXT,
    symbol TEXT NOT NULL,
    category TEXT NOT NULL,
    is_verified INTEGER,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    PRIMARY KEY(chain, asset_key)
);

CREATE TABLE ledger_asset_movements (
    wallet_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    event_id TEXT NOT NULL,
    asset_key TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    value_decimal TEXT NOT NULL,
    PRIMARY KEY(wallet_address, chain, event_id),
    FOREIGN KEY(wallet_address, chain, event_id)
        REFERENCES ledger_events(wallet_address, chain, event_id) ON DELETE CASCADE,
    FOREIGN KEY(chain, asset_key) REFERENCES ledger_assets(chain, asset_key)
);
CREATE INDEX idx_ledger_asset_movements_wallet
    ON ledger_asset_movements(wallet_address, chain, asset_key);

CREATE TABLE ledger_approvals (
    wallet_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    event_id TEXT NOT NULL,
    asset_key TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    owner_address TEXT NOT NULL,
    spender_address TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    value_decimal TEXT NOT NULL,
    PRIMARY KEY(wallet_address, chain, event_id),
    FOREIGN KEY(wallet_address, chain, event_id)
        REFERENCES ledger_events(wallet_address, chain, event_id) ON DELETE CASCADE,
    FOREIGN KEY(chain, asset_key) REFERENCES ledger_assets(chain, asset_key)
);
CREATE INDEX idx_ledger_approvals_wallet
    ON ledger_approvals(wallet_address, chain, asset_key);

CREATE TABLE ledger_fees (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    payer_address TEXT NOT NULL,
    raw_value TEXT NOT NULL,
    value_decimal TEXT NOT NULL,
    asset_symbol TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(chain, tx_hash),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES ledger_transactions(chain, tx_hash) ON DELETE CASCADE
);

CREATE TABLE ledger_event_scopes (
    wallet_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    scope TEXT NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY(wallet_address, chain, scope, event_id),
    FOREIGN KEY(wallet_address, chain, event_id)
        REFERENCES ledger_events(wallet_address, chain, event_id) ON DELETE CASCADE
);
CREATE INDEX idx_ledger_event_scopes_lookup
    ON ledger_event_scopes(wallet_address, chain, scope);

CREATE TABLE sync_checkpoints (
    wallet_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    scope TEXT NOT NULL,
    next_cursor TEXT,
    completeness TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    request_cursor TEXT,
    query_from_block INTEGER,
    query_to_block INTEGER,
    fetched_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(wallet_address, chain, scope)
);

CREATE TABLE ingestion_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    wallet_address TEXT NOT NULL,
    chain TEXT NOT NULL,
    scope TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    request_cursor TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    item_count INTEGER NOT NULL,
    completeness TEXT NOT NULL
);
CREATE INDEX idx_ingestion_runs_wallet_chain_time
    ON ingestion_runs(wallet_address, chain, finished_at DESC);
"""

_SCHEMA_V3_SQL = """
CREATE TABLE ledger_transaction_details (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    nonce INTEGER NOT NULL,
    value_wei TEXT NOT NULL,
    input_data TEXT NOT NULL,
    gas_limit INTEGER NOT NULL,
    gas_price TEXT,
    max_fee_per_gas TEXT,
    max_priority_fee_per_gas TEXT,
    source_provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(chain, tx_hash),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES ledger_transactions(chain, tx_hash) ON DELETE CASCADE
);

CREATE TABLE ledger_transaction_receipts (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    block_hash TEXT NOT NULL,
    transaction_index INTEGER NOT NULL,
    from_address TEXT NOT NULL,
    to_address TEXT,
    contract_address TEXT,
    status INTEGER,
    gas_used INTEGER NOT NULL,
    cumulative_gas_used INTEGER NOT NULL,
    effective_gas_price TEXT NOT NULL,
    transaction_type INTEGER,
    logs_bloom TEXT NOT NULL,
    source_provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    PRIMARY KEY(chain, tx_hash),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES ledger_transactions(chain, tx_hash) ON DELETE CASCADE
);
CREATE INDEX idx_ledger_receipts_chain_block
    ON ledger_transaction_receipts(chain, block_number DESC);

CREATE TABLE ledger_raw_logs (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    address TEXT NOT NULL,
    topics_json TEXT NOT NULL,
    data TEXT NOT NULL,
    removed INTEGER NOT NULL,
    PRIMARY KEY(chain, tx_hash, log_index),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES ledger_transaction_receipts(chain, tx_hash) ON DELETE CASCADE
);
CREATE INDEX idx_ledger_raw_logs_address
    ON ledger_raw_logs(chain, address);
"""

_SCHEMA_V4_SQL = """
CREATE TABLE abi_signature_sources (
    source_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    version TEXT NOT NULL,
    is_verified INTEGER NOT NULL,
    reference TEXT
);

CREATE TABLE decoded_transaction_calls (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    decoder_version TEXT NOT NULL,
    status TEXT NOT NULL,
    selector TEXT,
    name TEXT,
    canonical_signature TEXT,
    arguments_json TEXT NOT NULL,
    source_id TEXT,
    error TEXT,
    decoded_at TEXT NOT NULL,
    PRIMARY KEY(chain, tx_hash),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES ledger_transaction_details(chain, tx_hash) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES abi_signature_sources(source_id)
);
CREATE INDEX idx_decoded_transaction_calls_signature
    ON decoded_transaction_calls(canonical_signature);

CREATE TABLE decoded_event_logs (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    decoder_version TEXT NOT NULL,
    status TEXT NOT NULL,
    topic0 TEXT,
    name TEXT,
    canonical_signature TEXT,
    standard TEXT,
    arguments_json TEXT NOT NULL,
    source_id TEXT,
    error TEXT,
    decoded_at TEXT NOT NULL,
    PRIMARY KEY(chain, tx_hash, log_index),
    FOREIGN KEY(chain, tx_hash, log_index)
        REFERENCES ledger_raw_logs(chain, tx_hash, log_index) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES abi_signature_sources(source_id)
);
CREATE INDEX idx_decoded_event_logs_signature
    ON decoded_event_logs(canonical_signature);
"""

_SCHEMA_V5_SQL = """
CREATE TABLE contract_abis (
    chain TEXT NOT NULL,
    contract_address TEXT NOT NULL,
    contract_name TEXT,
    abi_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source_id TEXT NOT NULL,
    imported_at TEXT NOT NULL,
    PRIMARY KEY(chain, contract_address),
    FOREIGN KEY(source_id) REFERENCES abi_signature_sources(source_id)
);
CREATE INDEX idx_contract_abis_content_hash ON contract_abis(content_hash);

CREATE TABLE proxy_resolutions (
    chain TEXT NOT NULL,
    proxy_address TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    status TEXT NOT NULL,
    proxy_kind TEXT NOT NULL,
    implementation_address TEXT,
    source_provider TEXT NOT NULL,
    resolved_at TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY(chain, proxy_address, block_number)
);

ALTER TABLE decoded_transaction_calls ADD COLUMN contract_address TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN implementation_address TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_status TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_data TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_selector TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_name TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_signature TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_arguments_json TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_source_id TEXT;
ALTER TABLE decoded_transaction_calls ADD COLUMN revert_error TEXT;
"""

_SCHEMA_V6_SQL = """
CREATE TABLE transaction_traces (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    dialect TEXT,
    raw_json TEXT,
    source_provider TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    error TEXT,
    PRIMARY KEY(chain, tx_hash),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES ledger_transaction_receipts(chain, tx_hash) ON DELETE CASCADE
);

CREATE TABLE transaction_trace_calls (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    ordinal INTEGER NOT NULL,
    trace_address_json TEXT NOT NULL,
    depth INTEGER NOT NULL,
    call_type TEXT NOT NULL,
    from_address TEXT,
    to_address TEXT,
    created_contract TEXT,
    value_wei TEXT NOT NULL,
    gas_limit INTEGER,
    gas_used INTEGER,
    input_data TEXT NOT NULL,
    output_data TEXT NOT NULL,
    error TEXT,
    revert_reason TEXT,
    PRIMARY KEY(chain, tx_hash, ordinal),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES transaction_traces(chain, tx_hash) ON DELETE CASCADE
);
CREATE INDEX idx_transaction_trace_calls_target
    ON transaction_trace_calls(chain, to_address);
"""

_SCHEMA_V7_SQL = """
CREATE TABLE transaction_actions (
    chain TEXT NOT NULL,
    tx_hash TEXT NOT NULL,
    action_index INTEGER NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    participants_json TEXT NOT NULL,
    assets_json TEXT NOT NULL,
    protocol_hint TEXT,
    confidence TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    normalizer_version TEXT NOT NULL,
    normalized_at TEXT NOT NULL,
    PRIMARY KEY(chain, tx_hash, action_index),
    FOREIGN KEY(chain, tx_hash)
        REFERENCES ledger_transaction_receipts(chain, tx_hash) ON DELETE CASCADE
);
CREATE INDEX idx_transaction_actions_kind
    ON transaction_actions(chain, kind, status);
"""

_MIGRATIONS = {
    1: _SCHEMA_V1_SQL,
    2: _SCHEMA_V2_SQL,
    3: _SCHEMA_V3_SQL,
    4: _SCHEMA_V4_SQL,
    5: _SCHEMA_V5_SQL,
    6: _SCHEMA_V6_SQL,
    7: _SCHEMA_V7_SQL,
}


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            current_version = self._read_schema_version(conn)
            if current_version > _SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema version {current_version} is newer than supported version "
                    f"{_SCHEMA_VERSION}."
                )
            for target_version in range(current_version + 1, _SCHEMA_VERSION + 1):
                self._apply_migration(conn, target_version)

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

    def _apply_migration(self, conn: sqlite3.Connection, target_version: int) -> None:
        migration = _MIGRATIONS.get(target_version)
        if migration is None:
            raise RuntimeError(f"Missing database migration for schema version {target_version}.")
        conn.executescript(
            f"""
            BEGIN IMMEDIATE;
            {migration}
            INSERT INTO schema_meta(key, value)
            VALUES('schema_version', '{target_version}')
            ON CONFLICT(key) DO UPDATE SET value = excluded.value;
            COMMIT;
            """
        )
