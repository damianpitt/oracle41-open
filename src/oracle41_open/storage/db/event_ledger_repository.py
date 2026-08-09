from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from decimal import Decimal

from oracle41_open.core.models import (
    ActivityCategory,
    ActivityItem,
    ActivityPage,
    ApprovalRecord,
    AssetMovement,
    AssetRecord,
    Chain,
    CompletenessState,
    DataProvenance,
    FeeRecord,
    LedgerCheckpoint,
    ValidationError,
)
from oracle41_open.storage.db._helpers import (
    normalize_address_or_raise,
    normalize_non_empty,
    parse_datetime,
)
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class EventLedgerRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def persist_page(
        self,
        address: str,
        chain: Chain,
        scope: str,
        page: ActivityPage,
        provenance: DataProvenance,
        completeness: CompletenessState,
    ) -> LedgerCheckpoint:
        normalized_address = normalize_address_or_raise(address)
        normalized_scope = normalize_non_empty(scope, "Ledger scope")
        self._validate_page_chain(page, chain)

        updated_at = datetime.now(tz=UTC)
        fetched_at = provenance.fetched_at.astimezone(UTC)
        with self._database.connection() as conn:
            for item in page.items:
                self._upsert_transaction(
                    conn=conn,
                    item=item,
                    source_provider=provenance.source_provider,
                    observed_at=fetched_at,
                )
                self._upsert_event(
                    conn=conn,
                    wallet_address=normalized_address,
                    item=item,
                    source_provider=provenance.source_provider,
                    observed_at=fetched_at,
                )
                self._upsert_asset_projection(
                    conn=conn,
                    wallet_address=normalized_address,
                    item=item,
                    observed_at=fetched_at,
                )
                conn.execute(
                    """
                    INSERT INTO ledger_event_scopes(wallet_address, chain, scope, event_id)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(wallet_address, chain, scope, event_id) DO NOTHING
                    """,
                    (normalized_address, chain.value, normalized_scope, item.id),
                )

            conn.execute(
                """
                INSERT INTO sync_checkpoints(
                    wallet_address, chain, scope, next_cursor, completeness,
                    source_provider, request_cursor, query_from_block,
                    query_to_block, fetched_at, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_address, chain, scope) DO UPDATE SET
                    next_cursor = excluded.next_cursor,
                    completeness = excluded.completeness,
                    source_provider = excluded.source_provider,
                    request_cursor = excluded.request_cursor,
                    query_from_block = excluded.query_from_block,
                    query_to_block = excluded.query_to_block,
                    fetched_at = excluded.fetched_at,
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_address,
                    chain.value,
                    normalized_scope,
                    page.next_cursor,
                    completeness.value,
                    provenance.source_provider,
                    provenance.request_cursor,
                    provenance.query_from_block,
                    provenance.query_to_block,
                    fetched_at.isoformat(),
                    updated_at.isoformat(),
                ),
            )
            conn.execute(
                """
                INSERT INTO ingestion_runs(
                    wallet_address, chain, scope, source_provider, request_cursor,
                    started_at, finished_at, item_count, completeness
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_address,
                    chain.value,
                    normalized_scope,
                    provenance.source_provider,
                    provenance.request_cursor,
                    fetched_at.isoformat(),
                    updated_at.isoformat(),
                    len(page.items),
                    completeness.value,
                ),
            )

        return LedgerCheckpoint(
            wallet_address=normalized_address,
            chain=chain,
            scope=normalized_scope,
            next_cursor=page.next_cursor,
            completeness=completeness,
            provenance=provenance,
            updated_at=updated_at,
        )

    def upsert_fee(self, fee: FeeRecord, provenance: DataProvenance) -> None:
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO ledger_fees(
                    chain, tx_hash, payer_address, raw_value, value_decimal,
                    asset_symbol, source_provider, observed_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, tx_hash) DO UPDATE SET
                    payer_address = excluded.payer_address,
                    raw_value = excluded.raw_value,
                    value_decimal = excluded.value_decimal,
                    asset_symbol = excluded.asset_symbol,
                    source_provider = excluded.source_provider,
                    observed_at = excluded.observed_at
                """,
                (
                    fee.chain.value,
                    fee.tx_hash,
                    fee.payer_address,
                    fee.raw_value,
                    str(fee.value_decimal),
                    fee.asset_symbol,
                    provenance.source_provider,
                    provenance.fetched_at.astimezone(UTC).isoformat(),
                ),
            )

    def list_approvals(self, address: str, chain: Chain, scope: str) -> list[ApprovalRecord]:
        normalized_address = normalize_address_or_raise(address)
        normalized_scope = normalize_non_empty(scope, "Ledger scope")
        with self._database.connection() as conn:
            rows = conn.execute(
                """
                SELECT approvals.*, assets.contract_address, assets.symbol,
                       assets.category, assets.is_verified
                FROM ledger_approvals AS approvals
                JOIN ledger_event_scopes AS scopes
                  ON scopes.wallet_address = approvals.wallet_address
                 AND scopes.chain = approvals.chain
                 AND scopes.event_id = approvals.event_id
                JOIN ledger_assets AS assets
                  ON assets.chain = approvals.chain
                 AND assets.asset_key = approvals.asset_key
                JOIN ledger_events AS events
                  ON events.wallet_address = approvals.wallet_address
                 AND events.chain = approvals.chain
                 AND events.event_id = approvals.event_id
                WHERE approvals.wallet_address = ?
                  AND approvals.chain = ?
                  AND scopes.scope = ?
                ORDER BY events.timestamp DESC, events.log_index DESC
                """,
                (normalized_address, chain.value, normalized_scope),
            ).fetchall()
        return [self._approval_from_row(row) for row in rows]

    def list_asset_movements(
        self,
        address: str,
        chain: Chain,
        scope: str,
    ) -> list[AssetMovement]:
        normalized_address = normalize_address_or_raise(address)
        normalized_scope = normalize_non_empty(scope, "Ledger scope")
        with self._database.connection() as conn:
            rows = conn.execute(
                """
                SELECT movements.*, assets.contract_address, assets.symbol,
                       assets.category, assets.is_verified
                FROM ledger_asset_movements AS movements
                JOIN ledger_event_scopes AS scopes
                  ON scopes.wallet_address = movements.wallet_address
                 AND scopes.chain = movements.chain
                 AND scopes.event_id = movements.event_id
                JOIN ledger_assets AS assets
                  ON assets.chain = movements.chain
                 AND assets.asset_key = movements.asset_key
                JOIN ledger_events AS events
                  ON events.wallet_address = movements.wallet_address
                 AND events.chain = movements.chain
                 AND events.event_id = movements.event_id
                WHERE movements.wallet_address = ?
                  AND movements.chain = ?
                  AND scopes.scope = ?
                ORDER BY events.timestamp DESC, events.log_index DESC
                """,
                (normalized_address, chain.value, normalized_scope),
            ).fetchall()
        return [self._movement_from_row(row) for row in rows]

    def load_page(self, address: str, chain: Chain, scope: str) -> ActivityPage:
        normalized_address = normalize_address_or_raise(address)
        normalized_scope = normalize_non_empty(scope, "Ledger scope")
        with self._database.connection() as conn:
            rows = conn.execute(
                """
                SELECT events.*
                FROM ledger_event_scopes AS scopes
                JOIN ledger_events AS events
                  ON events.wallet_address = scopes.wallet_address
                 AND events.chain = scopes.chain
                 AND events.event_id = scopes.event_id
                WHERE scopes.wallet_address = ? AND scopes.chain = ? AND scopes.scope = ?
                ORDER BY events.timestamp DESC, events.block_number DESC, events.log_index DESC
                """,
                (normalized_address, chain.value, normalized_scope),
            ).fetchall()
            checkpoint = self._get_checkpoint(conn, normalized_address, chain, normalized_scope)

        return ActivityPage(
            items=[self._activity_from_row(row) for row in rows],
            next_cursor=checkpoint.next_cursor if checkpoint is not None else None,
            source_provider=(
                checkpoint.provenance.source_provider if checkpoint is not None else None
            ),
            query_from_block=(
                checkpoint.provenance.query_from_block if checkpoint is not None else None
            ),
            query_to_block=(
                checkpoint.provenance.query_to_block if checkpoint is not None else None
            ),
        )

    def get_checkpoint(
        self,
        address: str,
        chain: Chain,
        scope: str,
    ) -> LedgerCheckpoint | None:
        normalized_address = normalize_address_or_raise(address)
        normalized_scope = normalize_non_empty(scope, "Ledger scope")
        with self._database.connection() as conn:
            return self._get_checkpoint(conn, normalized_address, chain, normalized_scope)

    def event_count(self, address: str, chain: Chain, scope: str) -> int:
        normalized_address = normalize_address_or_raise(address)
        normalized_scope = normalize_non_empty(scope, "Ledger scope")
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM ledger_event_scopes
                WHERE wallet_address = ? AND chain = ? AND scope = ?
                """,
                (normalized_address, chain.value, normalized_scope),
            ).fetchone()
        return int(row["count"]) if row is not None else 0

    @staticmethod
    def _validate_page_chain(page: ActivityPage, chain: Chain) -> None:
        if any(item.chain is not chain for item in page.items):
            raise ValidationError("Activity page contains events from a different chain.")

    @staticmethod
    def _upsert_transaction(
        conn: sqlite3.Connection,
        item: ActivityItem,
        source_provider: str,
        observed_at: datetime,
    ) -> None:
        conn.execute(
            """
            INSERT INTO ledger_transactions(
                chain, tx_hash, block_number, timestamp, from_address, to_address,
                source_provider, first_seen_at, last_seen_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain, tx_hash) DO UPDATE SET
                block_number = COALESCE(excluded.block_number, ledger_transactions.block_number),
                timestamp = excluded.timestamp,
                from_address = excluded.from_address,
                to_address = excluded.to_address,
                source_provider = excluded.source_provider,
                last_seen_at = excluded.last_seen_at
            """,
            (
                item.chain.value,
                item.tx_hash,
                item.block_number,
                item.timestamp.astimezone(UTC).isoformat(),
                item.from_address,
                item.to_address,
                source_provider,
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )

    @staticmethod
    def _upsert_event(
        conn: sqlite3.Connection,
        wallet_address: str,
        item: ActivityItem,
        source_provider: str,
        observed_at: datetime,
    ) -> None:
        conn.execute(
            """
            INSERT INTO ledger_events(
                wallet_address, chain, event_id, tx_hash, log_index, block_number,
                timestamp, from_address, to_address, asset_symbol, contract_address,
                raw_value, value_decimal, value_usd, is_verified, category,
                source_provider, observed_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_address, chain, event_id) DO UPDATE SET
                block_number = COALESCE(excluded.block_number, ledger_events.block_number),
                timestamp = excluded.timestamp,
                from_address = excluded.from_address,
                to_address = excluded.to_address,
                asset_symbol = excluded.asset_symbol,
                contract_address = COALESCE(excluded.contract_address, ledger_events.contract_address),
                raw_value = excluded.raw_value,
                value_decimal = excluded.value_decimal,
                value_usd = COALESCE(excluded.value_usd, ledger_events.value_usd),
                is_verified = COALESCE(excluded.is_verified, ledger_events.is_verified),
                category = excluded.category,
                source_provider = excluded.source_provider,
                observed_at = excluded.observed_at
            """,
            (
                wallet_address,
                item.chain.value,
                item.id,
                item.tx_hash,
                item.log_index,
                item.block_number,
                item.timestamp.astimezone(UTC).isoformat(),
                item.from_address,
                item.to_address,
                item.asset_symbol,
                item.contract_address,
                item.raw_value,
                str(item.value_decimal),
                str(item.value_usd) if item.value_usd is not None else None,
                int(item.is_verified) if item.is_verified is not None else None,
                item.category.value,
                source_provider,
                observed_at.isoformat(),
            ),
        )

    @classmethod
    def _upsert_asset_projection(
        cls,
        conn: sqlite3.Connection,
        wallet_address: str,
        item: ActivityItem,
        observed_at: datetime,
    ) -> None:
        asset_key = cls._asset_key(item)
        conn.execute(
            """
            INSERT INTO ledger_assets(
                chain, asset_key, contract_address, symbol, category,
                is_verified, first_seen_at, last_seen_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain, asset_key) DO UPDATE SET
                contract_address = COALESCE(
                    excluded.contract_address,
                    ledger_assets.contract_address
                ),
                symbol = CASE
                    WHEN excluded.symbol = 'UNKNOWN' THEN ledger_assets.symbol
                    ELSE excluded.symbol
                END,
                category = excluded.category,
                is_verified = COALESCE(excluded.is_verified, ledger_assets.is_verified),
                last_seen_at = excluded.last_seen_at
            """,
            (
                item.chain.value,
                asset_key,
                item.contract_address,
                item.asset_symbol,
                item.category.value,
                int(item.is_verified) if item.is_verified is not None else None,
                observed_at.isoformat(),
                observed_at.isoformat(),
            ),
        )
        if item.category is ActivityCategory.APPROVAL:
            conn.execute(
                """
                INSERT INTO ledger_approvals(
                    wallet_address, chain, event_id, asset_key, tx_hash,
                    owner_address, spender_address, raw_value, value_decimal
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wallet_address, chain, event_id) DO UPDATE SET
                    asset_key = excluded.asset_key,
                    owner_address = excluded.owner_address,
                    spender_address = excluded.spender_address,
                    raw_value = excluded.raw_value,
                    value_decimal = excluded.value_decimal
                """,
                (
                    wallet_address,
                    item.chain.value,
                    item.id,
                    asset_key,
                    item.tx_hash,
                    item.from_address,
                    item.to_address,
                    item.raw_value,
                    str(item.value_decimal),
                ),
            )
            return
        conn.execute(
            """
            INSERT INTO ledger_asset_movements(
                wallet_address, chain, event_id, asset_key, tx_hash,
                from_address, to_address, raw_value, value_decimal
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(wallet_address, chain, event_id) DO UPDATE SET
                asset_key = excluded.asset_key,
                from_address = excluded.from_address,
                to_address = excluded.to_address,
                raw_value = excluded.raw_value,
                value_decimal = excluded.value_decimal
            """,
            (
                wallet_address,
                item.chain.value,
                item.id,
                asset_key,
                item.tx_hash,
                item.from_address,
                item.to_address,
                item.raw_value,
                str(item.value_decimal),
            ),
        )

    @staticmethod
    def _asset_key(item: ActivityItem) -> str:
        if item.contract_address is not None:
            return item.contract_address.lower()
        return f"native:{item.chain.value}"

    @staticmethod
    def _asset_from_row(row: sqlite3.Row) -> AssetRecord:
        raw_verified = row["is_verified"]
        return AssetRecord(
            chain=Chain(str(row["chain"])),
            contract_address=row["contract_address"],
            symbol=str(row["symbol"]),
            category=str(row["category"]),
            is_verified=bool(raw_verified) if raw_verified is not None else None,
        )

    @classmethod
    def _approval_from_row(cls, row: sqlite3.Row) -> ApprovalRecord:
        return ApprovalRecord(
            wallet_address=str(row["wallet_address"]),
            chain=Chain(str(row["chain"])),
            event_id=str(row["event_id"]),
            tx_hash=str(row["tx_hash"]),
            owner_address=str(row["owner_address"]),
            spender_address=str(row["spender_address"]),
            asset=cls._asset_from_row(row),
            raw_value=str(row["raw_value"]),
            value_decimal=Decimal(str(row["value_decimal"])),
        )

    @classmethod
    def _movement_from_row(cls, row: sqlite3.Row) -> AssetMovement:
        return AssetMovement(
            wallet_address=str(row["wallet_address"]),
            chain=Chain(str(row["chain"])),
            event_id=str(row["event_id"]),
            tx_hash=str(row["tx_hash"]),
            from_address=str(row["from_address"]),
            to_address=str(row["to_address"]),
            asset=cls._asset_from_row(row),
            raw_value=str(row["raw_value"]),
            value_decimal=Decimal(str(row["value_decimal"])),
        )

    @staticmethod
    def _activity_from_row(row: sqlite3.Row) -> ActivityItem:
        raw_verified = row["is_verified"]
        return ActivityItem(
            block_number=row["block_number"],
            tx_hash=str(row["tx_hash"]),
            log_index=str(row["log_index"]),
            timestamp=parse_datetime(row["timestamp"]),
            from_address=str(row["from_address"]),
            to_address=str(row["to_address"]),
            asset_symbol=str(row["asset_symbol"]),
            contract_address=row["contract_address"],
            raw_value=str(row["raw_value"]),
            value_decimal=Decimal(str(row["value_decimal"])),
            value_usd=(
                Decimal(str(row["value_usd"])) if row["value_usd"] is not None else None
            ),
            is_verified=bool(raw_verified) if raw_verified is not None else None,
            category=ActivityCategory(str(row["category"])),
            chain=Chain(str(row["chain"])),
        )

    @staticmethod
    def _get_checkpoint(
        conn: sqlite3.Connection,
        address: str,
        chain: Chain,
        scope: str,
    ) -> LedgerCheckpoint | None:
        row = conn.execute(
            """
            SELECT * FROM sync_checkpoints
            WHERE wallet_address = ? AND chain = ? AND scope = ?
            LIMIT 1
            """,
            (address, chain.value, scope),
        ).fetchone()
        if row is None:
            return None
        return LedgerCheckpoint(
            wallet_address=str(row["wallet_address"]),
            chain=Chain(str(row["chain"])),
            scope=str(row["scope"]),
            next_cursor=row["next_cursor"],
            completeness=CompletenessState(str(row["completeness"])),
            provenance=DataProvenance(
                source_provider=str(row["source_provider"]),
                fetched_at=parse_datetime(row["fetched_at"]),
                request_cursor=row["request_cursor"],
                query_from_block=row["query_from_block"],
                query_to_block=row["query_to_block"],
            ),
            updated_at=parse_datetime(row["updated_at"]),
        )
