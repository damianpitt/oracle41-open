from __future__ import annotations

import sqlite3
from datetime import UTC

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    RawTransactionLog,
    TransactionInspection,
    ValidationError,
)
from oracle41_open.storage.db._helpers import parse_datetime
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class TransactionRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save_inspection(self, inspection: TransactionInspection) -> None:
        with self._database.connection() as conn:
            if not self._transaction_exists(conn, inspection.chain, inspection.tx_hash):
                raise ValidationError(
                    "Transaction is not present in the event ledger. Synchronize Activity first."
                )
            self._upsert_details(conn, inspection)
            self._upsert_receipt(conn, inspection)
            conn.execute(
                "DELETE FROM ledger_raw_logs WHERE chain = ? AND tx_hash = ?",
                (inspection.chain.value, inspection.tx_hash),
            )
            for log in inspection.logs:
                conn.execute(
                    """
                    INSERT INTO ledger_raw_logs(
                        chain, tx_hash, log_index, address, topics_json, data, removed
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        inspection.chain.value,
                        inspection.tx_hash,
                        log.log_index,
                        log.address,
                        json_dumps(list(log.topics), pretty=False).decode("utf-8"),
                        log.data,
                        int(log.removed),
                    ),
                )
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
                    inspection.chain.value,
                    inspection.tx_hash,
                    inspection.from_address,
                    str(inspection.fee_wei),
                    str(inspection.fee_native),
                    inspection.chain.native_symbol,
                    inspection.source_provider,
                    inspection.fetched_at.astimezone(UTC).isoformat(),
                ),
            )

    def get_inspection(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionInspection | None:
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT details.nonce, details.value_wei, details.input_data,
                       details.gas_limit, details.gas_price, details.max_fee_per_gas,
                       details.max_priority_fee_per_gas, receipts.*
                FROM ledger_transaction_details AS details
                JOIN ledger_transaction_receipts AS receipts
                  ON receipts.chain = details.chain AND receipts.tx_hash = details.tx_hash
                WHERE details.chain = ? AND details.tx_hash = ?
                LIMIT 1
                """,
                (chain.value, tx_hash.lower()),
            ).fetchone()
            if row is None:
                return None
            log_rows = conn.execute(
                """
                SELECT log_index, address, topics_json, data, removed
                FROM ledger_raw_logs
                WHERE chain = ? AND tx_hash = ?
                ORDER BY log_index
                """,
                (chain.value, tx_hash.lower()),
            ).fetchall()
        return self._inspection_from_rows(chain, row, log_rows)

    @staticmethod
    def _transaction_exists(
        conn: sqlite3.Connection,
        chain: Chain,
        tx_hash: str,
    ) -> bool:
        row = conn.execute(
            "SELECT 1 FROM ledger_transactions WHERE chain = ? AND tx_hash = ? LIMIT 1",
            (chain.value, tx_hash),
        ).fetchone()
        return row is not None

    @staticmethod
    def _upsert_details(conn: sqlite3.Connection, inspection: TransactionInspection) -> None:
        conn.execute(
            """
            INSERT INTO ledger_transaction_details(
                chain, tx_hash, nonce, value_wei, input_data, gas_limit, gas_price,
                max_fee_per_gas, max_priority_fee_per_gas, source_provider, fetched_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain, tx_hash) DO UPDATE SET
                nonce = excluded.nonce,
                value_wei = excluded.value_wei,
                input_data = excluded.input_data,
                gas_limit = excluded.gas_limit,
                gas_price = excluded.gas_price,
                max_fee_per_gas = excluded.max_fee_per_gas,
                max_priority_fee_per_gas = excluded.max_priority_fee_per_gas,
                source_provider = excluded.source_provider,
                fetched_at = excluded.fetched_at
            """,
            (
                inspection.chain.value,
                inspection.tx_hash,
                inspection.nonce,
                str(inspection.value_wei),
                inspection.input_data,
                inspection.gas_limit,
                _optional_int_text(inspection.gas_price),
                _optional_int_text(inspection.max_fee_per_gas),
                _optional_int_text(inspection.max_priority_fee_per_gas),
                inspection.source_provider,
                inspection.fetched_at.astimezone(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _upsert_receipt(conn: sqlite3.Connection, inspection: TransactionInspection) -> None:
        conn.execute(
            """
            INSERT INTO ledger_transaction_receipts(
                chain, tx_hash, block_number, block_hash, transaction_index,
                from_address, to_address, contract_address, status, gas_used,
                cumulative_gas_used, effective_gas_price, transaction_type,
                logs_bloom, source_provider, fetched_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain, tx_hash) DO UPDATE SET
                block_number = excluded.block_number,
                block_hash = excluded.block_hash,
                transaction_index = excluded.transaction_index,
                from_address = excluded.from_address,
                to_address = excluded.to_address,
                contract_address = excluded.contract_address,
                status = excluded.status,
                gas_used = excluded.gas_used,
                cumulative_gas_used = excluded.cumulative_gas_used,
                effective_gas_price = excluded.effective_gas_price,
                transaction_type = excluded.transaction_type,
                logs_bloom = excluded.logs_bloom,
                source_provider = excluded.source_provider,
                fetched_at = excluded.fetched_at
            """,
            (
                inspection.chain.value,
                inspection.tx_hash,
                inspection.block_number,
                inspection.block_hash,
                inspection.transaction_index,
                inspection.from_address,
                inspection.to_address,
                inspection.contract_address,
                None if inspection.status is None else int(inspection.status),
                inspection.gas_used,
                inspection.cumulative_gas_used,
                str(inspection.effective_gas_price),
                inspection.transaction_type,
                inspection.logs_bloom,
                inspection.source_provider,
                inspection.fetched_at.astimezone(UTC).isoformat(),
            ),
        )

    @staticmethod
    def _inspection_from_rows(
        chain: Chain,
        row: sqlite3.Row,
        log_rows: list[sqlite3.Row],
    ) -> TransactionInspection:
        raw_status = row["status"]
        return TransactionInspection(
            chain=chain,
            tx_hash=str(row["tx_hash"]),
            block_number=int(row["block_number"]),
            block_hash=str(row["block_hash"]),
            transaction_index=int(row["transaction_index"]),
            from_address=str(row["from_address"]),
            to_address=row["to_address"],
            contract_address=row["contract_address"],
            nonce=int(row["nonce"]),
            value_wei=int(str(row["value_wei"])),
            input_data=str(row["input_data"]),
            gas_limit=int(row["gas_limit"]),
            gas_price=_optional_db_int(row["gas_price"]),
            max_fee_per_gas=_optional_db_int(row["max_fee_per_gas"]),
            max_priority_fee_per_gas=_optional_db_int(row["max_priority_fee_per_gas"]),
            status=None if raw_status is None else bool(raw_status),
            gas_used=int(row["gas_used"]),
            cumulative_gas_used=int(row["cumulative_gas_used"]),
            effective_gas_price=int(str(row["effective_gas_price"])),
            transaction_type=(
                int(row["transaction_type"]) if row["transaction_type"] is not None else None
            ),
            logs_bloom=str(row["logs_bloom"]),
            logs=tuple(_log_from_row(log_row) for log_row in log_rows),
            source_provider=str(row["source_provider"]),
            fetched_at=parse_datetime(row["fetched_at"]),
        )


def _log_from_row(row: sqlite3.Row) -> RawTransactionLog:
    raw_topics = json_loads(str(row["topics_json"]))
    if not isinstance(raw_topics, list) or not all(
        isinstance(topic, str) for topic in raw_topics
    ):
        raise ValueError("Stored transaction log topics are invalid.")
    return RawTransactionLog(
        log_index=int(row["log_index"]),
        address=str(row["address"]),
        topics=tuple(raw_topics),
        data=str(row["data"]),
        removed=bool(row["removed"]),
    )


def _optional_int_text(value: int | None) -> str | None:
    return str(value) if value is not None else None


def _optional_db_int(value: object) -> int | None:
    return int(str(value)) if value is not None else None
