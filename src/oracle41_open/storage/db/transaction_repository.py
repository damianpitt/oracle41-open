"""Persist transaction inspections, traces, and decoded results.

Receipt details, raw logs, internal calls, fees, decoded values, and signature sources are stored without replacing canonical ledger rows.
Trace and decoded data require an existing durable raw inspection.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    DecodedArgument,
    DecodedCall,
    DecodedEvent,
    DecodedRevert,
    DecodeStatus,
    InternalCall,
    RawTransactionLog,
    SignatureProvenance,
    SignatureSourceKind,
    TraceDialect,
    TraceStatus,
    TransactionDecoding,
    TransactionInspection,
    TransactionTrace,
    ValidationError,
)
from oracle41_open.storage.db._helpers import parse_datetime, utc_now_iso
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

    def save_decoding(
        self,
        chain: Chain,
        tx_hash: str,
        decoding: TransactionDecoding,
    ) -> None:
        normalized_hash = tx_hash.lower()
        with self._database.connection() as conn:
            if not self._inspection_exists(conn, chain, normalized_hash):
                raise ValidationError(
                    "Transaction inspection is not present. Load the transaction first."
                )
            provenances = {
                provenance.source_id: provenance
                for provenance in (
                    decoding.call.provenance,
                    *(event.provenance for event in decoding.events),
                    decoding.revert.provenance if decoding.revert is not None else None,
                )
                if provenance is not None
            }
            for provenance in provenances.values():
                self._upsert_provenance(conn, provenance)
            decoded_at = utc_now_iso()
            self._upsert_decoded_call(
                conn,
                chain,
                normalized_hash,
                decoding,
                decoded_at,
            )
            conn.execute(
                "DELETE FROM decoded_event_logs WHERE chain = ? AND tx_hash = ?",
                (chain.value, normalized_hash),
            )
            for event in decoding.events:
                self._insert_decoded_event(
                    conn,
                    chain,
                    normalized_hash,
                    decoding.decoder_version,
                    event,
                    decoded_at,
                )

    def get_decoding(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionDecoding | None:
        normalized_hash = tx_hash.lower()
        with self._database.connection() as conn:
            call_row = conn.execute(
                """
                SELECT calls.*, sources.source_name, sources.source_kind,
                       sources.version AS source_version, sources.is_verified,
                       sources.reference,
                       revert_sources.source_name AS revert_source_name,
                       revert_sources.source_kind AS revert_source_kind,
                       revert_sources.version AS revert_source_version,
                       revert_sources.is_verified AS revert_is_verified,
                       revert_sources.reference AS revert_reference
                FROM decoded_transaction_calls AS calls
                LEFT JOIN abi_signature_sources AS sources
                  ON sources.source_id = calls.source_id
                LEFT JOIN abi_signature_sources AS revert_sources
                  ON revert_sources.source_id = calls.revert_source_id
                WHERE calls.chain = ? AND calls.tx_hash = ?
                LIMIT 1
                """,
                (chain.value, normalized_hash),
            ).fetchone()
            if call_row is None:
                return None
            event_rows = conn.execute(
                """
                SELECT events.*, sources.source_name, sources.source_kind,
                       sources.version AS source_version, sources.is_verified,
                       sources.reference
                FROM decoded_event_logs AS events
                LEFT JOIN abi_signature_sources AS sources
                  ON sources.source_id = events.source_id
                WHERE events.chain = ? AND events.tx_hash = ?
                ORDER BY events.log_index
                """,
                (chain.value, normalized_hash),
            ).fetchall()
        decoder_version = str(call_row["decoder_version"])
        return TransactionDecoding(
            decoder_version=decoder_version,
            call=_call_from_row(call_row),
            events=tuple(_event_from_row(row) for row in event_rows),
            contract_address=(
                str(call_row["contract_address"])
                if call_row["contract_address"] is not None
                else None
            ),
            implementation_address=(
                str(call_row["implementation_address"])
                if call_row["implementation_address"] is not None
                else None
            ),
            revert=_revert_from_row(call_row),
        )

    def save_trace(self, trace: TransactionTrace) -> None:
        normalized_hash = trace.tx_hash.lower()
        with self._database.connection() as conn:
            if not self._inspection_exists(conn, trace.chain, normalized_hash):
                raise ValidationError(
                    "Transaction inspection is not present. Load the transaction first."
                )
            conn.execute(
                """
                INSERT INTO transaction_traces(
                    chain, tx_hash, status, dialect, raw_json,
                    source_provider, fetched_at, error
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, tx_hash) DO UPDATE SET
                    status = excluded.status,
                    dialect = excluded.dialect,
                    raw_json = excluded.raw_json,
                    source_provider = excluded.source_provider,
                    fetched_at = excluded.fetched_at,
                    error = excluded.error
                """,
                (
                    trace.chain.value,
                    normalized_hash,
                    trace.status.value,
                    trace.dialect.value if trace.dialect is not None else None,
                    trace.raw_json,
                    trace.source_provider,
                    trace.fetched_at.astimezone(UTC).isoformat(),
                    trace.error,
                ),
            )
            conn.execute(
                "DELETE FROM transaction_trace_calls WHERE chain = ? AND tx_hash = ?",
                (trace.chain.value, normalized_hash),
            )
            for ordinal, call in enumerate(trace.calls):
                self._insert_trace_call(
                    conn,
                    trace.chain,
                    normalized_hash,
                    ordinal,
                    call,
                )

    def get_trace(self, chain: Chain, tx_hash: str) -> TransactionTrace | None:
        normalized_hash = tx_hash.lower()
        with self._database.connection() as conn:
            trace_row = conn.execute(
                """
                SELECT * FROM transaction_traces
                WHERE chain = ? AND tx_hash = ?
                LIMIT 1
                """,
                (chain.value, normalized_hash),
            ).fetchone()
            if trace_row is None:
                return None
            call_rows = conn.execute(
                """
                SELECT * FROM transaction_trace_calls
                WHERE chain = ? AND tx_hash = ?
                ORDER BY ordinal
                """,
                (chain.value, normalized_hash),
            ).fetchall()
        raw_dialect = trace_row["dialect"]
        return TransactionTrace(
            chain=chain,
            tx_hash=normalized_hash,
            status=TraceStatus(str(trace_row["status"])),
            calls=tuple(_trace_call_from_row(row) for row in call_rows),
            raw_json=(
                str(trace_row["raw_json"])
                if trace_row["raw_json"] is not None
                else None
            ),
            source_provider=str(trace_row["source_provider"]),
            fetched_at=parse_datetime(trace_row["fetched_at"]),
            dialect=TraceDialect(str(raw_dialect)) if raw_dialect is not None else None,
            error=str(trace_row["error"]) if trace_row["error"] is not None else None,
        )

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
    def _inspection_exists(
        conn: sqlite3.Connection,
        chain: Chain,
        tx_hash: str,
    ) -> bool:
        row = conn.execute(
            """
            SELECT 1 FROM ledger_transaction_details
            WHERE chain = ? AND tx_hash = ?
            LIMIT 1
            """,
            (chain.value, tx_hash),
        ).fetchone()
        return row is not None

    @staticmethod
    def _upsert_provenance(
        conn: sqlite3.Connection,
        provenance: SignatureProvenance,
    ) -> None:
        conn.execute(
            """
            INSERT INTO abi_signature_sources(
                source_id, source_name, source_kind, version, is_verified, reference
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET
                source_name = excluded.source_name,
                source_kind = excluded.source_kind,
                version = excluded.version,
                is_verified = excluded.is_verified,
                reference = excluded.reference
            """,
            (
                provenance.source_id,
                provenance.source_name,
                provenance.source_kind.value,
                provenance.version,
                int(provenance.is_verified),
                provenance.reference,
            ),
        )

    @staticmethod
    def _upsert_decoded_call(
        conn: sqlite3.Connection,
        chain: Chain,
        tx_hash: str,
        decoding: TransactionDecoding,
        decoded_at: str,
    ) -> None:
        call = decoding.call
        conn.execute(
            """
            INSERT INTO decoded_transaction_calls(
                chain, tx_hash, decoder_version, status, selector, name,
                canonical_signature, arguments_json, source_id, error, decoded_at,
                contract_address, implementation_address, revert_status, revert_data,
                revert_selector, revert_name, revert_signature, revert_arguments_json,
                revert_source_id, revert_error
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chain, tx_hash) DO UPDATE SET
                decoder_version = excluded.decoder_version,
                status = excluded.status,
                selector = excluded.selector,
                name = excluded.name,
                canonical_signature = excluded.canonical_signature,
                arguments_json = excluded.arguments_json,
                source_id = excluded.source_id,
                error = excluded.error,
                decoded_at = excluded.decoded_at,
                contract_address = excluded.contract_address,
                implementation_address = excluded.implementation_address,
                revert_status = excluded.revert_status,
                revert_data = excluded.revert_data,
                revert_selector = excluded.revert_selector,
                revert_name = excluded.revert_name,
                revert_signature = excluded.revert_signature,
                revert_arguments_json = excluded.revert_arguments_json,
                revert_source_id = excluded.revert_source_id,
                revert_error = excluded.revert_error
            """,
            (
                chain.value,
                tx_hash,
                decoding.decoder_version,
                call.status.value,
                call.selector,
                call.name,
                call.canonical_signature,
                _arguments_json(call.arguments),
                call.provenance.source_id if call.provenance is not None else None,
                call.error,
                decoded_at,
                decoding.contract_address,
                decoding.implementation_address,
                decoding.revert.status.value if decoding.revert is not None else None,
                decoding.revert.raw_data if decoding.revert is not None else None,
                decoding.revert.selector if decoding.revert is not None else None,
                decoding.revert.name if decoding.revert is not None else None,
                (
                    decoding.revert.canonical_signature
                    if decoding.revert is not None
                    else None
                ),
                (
                    _arguments_json(decoding.revert.arguments)
                    if decoding.revert is not None
                    else None
                ),
                (
                    decoding.revert.provenance.source_id
                    if decoding.revert is not None
                    and decoding.revert.provenance is not None
                    else None
                ),
                decoding.revert.error if decoding.revert is not None else None,
            ),
        )

    @staticmethod
    def _insert_decoded_event(
        conn: sqlite3.Connection,
        chain: Chain,
        tx_hash: str,
        decoder_version: str,
        event: DecodedEvent,
        decoded_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO decoded_event_logs(
                chain, tx_hash, log_index, decoder_version, status, topic0, name,
                canonical_signature, standard, arguments_json, source_id, error,
                decoded_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chain.value,
                tx_hash,
                event.log_index,
                decoder_version,
                event.status.value,
                event.topic0,
                event.name,
                event.canonical_signature,
                event.standard,
                _arguments_json(event.arguments),
                event.provenance.source_id if event.provenance is not None else None,
                event.error,
                decoded_at,
            ),
        )

    @staticmethod
    def _insert_trace_call(
        conn: sqlite3.Connection,
        chain: Chain,
        tx_hash: str,
        ordinal: int,
        call: InternalCall,
    ) -> None:
        conn.execute(
            """
            INSERT INTO transaction_trace_calls(
                chain, tx_hash, ordinal, trace_address_json, depth, call_type,
                from_address, to_address, created_contract, value_wei,
                gas_limit, gas_used, input_data, output_data, error, revert_reason
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                chain.value,
                tx_hash,
                ordinal,
                json_dumps(list(call.trace_address), pretty=False).decode("utf-8"),
                call.depth,
                call.call_type,
                call.from_address,
                call.to_address,
                call.created_contract,
                str(call.value_wei),
                call.gas_limit,
                call.gas_used,
                call.input_data,
                call.output_data,
                call.error,
                call.revert_reason,
            ),
        )

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


def _trace_call_from_row(row: sqlite3.Row) -> InternalCall:
    raw_trace_address = json_loads(str(row["trace_address_json"]))
    if not isinstance(raw_trace_address, list) or not all(
        isinstance(item, int) and item >= 0 for item in raw_trace_address
    ):
        raise ValueError("Stored trace address is invalid.")
    return InternalCall(
        trace_address=tuple(raw_trace_address),
        depth=int(row["depth"]),
        call_type=str(row["call_type"]),
        from_address=(
            str(row["from_address"]) if row["from_address"] is not None else None
        ),
        to_address=str(row["to_address"]) if row["to_address"] is not None else None,
        created_contract=(
            str(row["created_contract"])
            if row["created_contract"] is not None
            else None
        ),
        value_wei=int(str(row["value_wei"])),
        gas_limit=int(row["gas_limit"]) if row["gas_limit"] is not None else None,
        gas_used=int(row["gas_used"]) if row["gas_used"] is not None else None,
        input_data=str(row["input_data"]),
        output_data=str(row["output_data"]),
        error=str(row["error"]) if row["error"] is not None else None,
        revert_reason=(
            str(row["revert_reason"]) if row["revert_reason"] is not None else None
        ),
    )


def _optional_int_text(value: int | None) -> str | None:
    return str(value) if value is not None else None


def _optional_db_int(value: object) -> int | None:
    return int(str(value)) if value is not None else None


def _arguments_json(arguments: tuple[DecodedArgument, ...]) -> str:
    payload = [
        {
            "name": argument.name,
            "abi_type": argument.abi_type,
            "value": argument.value,
            "indexed": argument.indexed,
        }
        for argument in arguments
    ]
    return json_dumps(payload, pretty=False).decode("utf-8")


def _arguments_from_row(row: sqlite3.Row) -> tuple[DecodedArgument, ...]:
    return _arguments_from_json(str(row["arguments_json"]))


def _arguments_from_json(raw_json: str) -> tuple[DecodedArgument, ...]:
    raw_arguments = json_loads(raw_json)
    if not isinstance(raw_arguments, list):
        raise ValueError("Stored decoded arguments are invalid.")
    arguments: list[DecodedArgument] = []
    for raw in raw_arguments:
        if not isinstance(raw, dict):
            raise ValueError("Stored decoded argument is invalid.")
        arguments.append(
            DecodedArgument(
                name=str(raw["name"]),
                abi_type=str(raw["abi_type"]),
                value=str(raw["value"]),
                indexed=bool(raw["indexed"]),
            )
        )
    return tuple(arguments)


def _provenance_from_row(row: sqlite3.Row) -> SignatureProvenance | None:
    source_id = row["source_id"]
    if source_id is None:
        return None
    return SignatureProvenance(
        source_id=str(source_id),
        source_name=str(row["source_name"]),
        source_kind=SignatureSourceKind(str(row["source_kind"])),
        version=str(row["source_version"]),
        is_verified=bool(row["is_verified"]),
        reference=str(row["reference"]) if row["reference"] is not None else None,
    )


def _call_from_row(row: sqlite3.Row) -> DecodedCall:
    return DecodedCall(
        status=DecodeStatus(str(row["status"])),
        selector=str(row["selector"]) if row["selector"] is not None else None,
        name=str(row["name"]) if row["name"] is not None else None,
        canonical_signature=(
            str(row["canonical_signature"])
            if row["canonical_signature"] is not None
            else None
        ),
        arguments=_arguments_from_row(row),
        provenance=_provenance_from_row(row),
        error=str(row["error"]) if row["error"] is not None else None,
    )


def _event_from_row(row: sqlite3.Row) -> DecodedEvent:
    return DecodedEvent(
        status=DecodeStatus(str(row["status"])),
        log_index=int(row["log_index"]),
        topic0=str(row["topic0"]) if row["topic0"] is not None else None,
        name=str(row["name"]) if row["name"] is not None else None,
        canonical_signature=(
            str(row["canonical_signature"])
            if row["canonical_signature"] is not None
            else None
        ),
        standard=str(row["standard"]) if row["standard"] is not None else None,
        arguments=_arguments_from_row(row),
        provenance=_provenance_from_row(row),
        error=str(row["error"]) if row["error"] is not None else None,
    )


def _revert_from_row(row: sqlite3.Row) -> DecodedRevert | None:
    if row["revert_status"] is None or row["revert_data"] is None:
        return None
    source_id = row["revert_source_id"]
    provenance = None
    if source_id is not None:
        provenance = SignatureProvenance(
            source_id=str(source_id),
            source_name=str(row["revert_source_name"]),
            source_kind=SignatureSourceKind(str(row["revert_source_kind"])),
            version=str(row["revert_source_version"]),
            is_verified=bool(row["revert_is_verified"]),
            reference=(
                str(row["revert_reference"])
                if row["revert_reference"] is not None
                else None
            ),
        )
    raw_arguments = row["revert_arguments_json"]
    arguments: tuple[DecodedArgument, ...] = ()
    if raw_arguments is not None:
        arguments = _arguments_from_json(str(raw_arguments))
    return DecodedRevert(
        status=DecodeStatus(str(row["revert_status"])),
        raw_data=str(row["revert_data"]),
        selector=(str(row["revert_selector"]) if row["revert_selector"] is not None else None),
        name=str(row["revert_name"]) if row["revert_name"] is not None else None,
        canonical_signature=(
            str(row["revert_signature"])
            if row["revert_signature"] is not None
            else None
        ),
        arguments=arguments,
        provenance=provenance,
        error=str(row["revert_error"]) if row["revert_error"] is not None else None,
    )
