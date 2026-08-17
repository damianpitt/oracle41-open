"""Persist optional transaction explorer enrichment.

The repository stores explorer context separately from receipts, local decoding, traces, and actions.
Each record keeps its status and provenance so unavailable data cannot be mistaken for canonical evidence.
"""

from __future__ import annotations

from oracle41_open._json import dumps as json_dumps
from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    Chain,
    EnrichmentStatus,
    ExplorerAddressContext,
    ExplorerDecodedParameter,
    TransactionEnrichment,
    ValidationError,
)
from oracle41_open.storage.db._helpers import parse_datetime
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class TransactionEnrichmentRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save_enrichment(self, enrichment: TransactionEnrichment) -> None:
        context = {
            "method_name": enrichment.method_name,
            "transaction_types": list(enrichment.transaction_types),
            "decoded_method_call": enrichment.decoded_method_call,
            "decoded_method_id": enrichment.decoded_method_id,
            "decoded_parameters": [
                {
                    "name": item.name,
                    "type_name": item.type_name,
                    "value": item.value,
                    "indexed": item.indexed,
                }
                for item in enrichment.decoded_parameters
            ],
            "target_context": _address_context_to_json(enrichment.target_context),
            "created_contract_context": _address_context_to_json(
                enrichment.created_contract_context
            ),
        }
        with self._database.connection() as conn:
            receipt = conn.execute(
                """
                SELECT 1 FROM ledger_transaction_receipts
                WHERE chain = ? AND tx_hash = ?
                """,
                (enrichment.chain.value, enrichment.tx_hash),
            ).fetchone()
            if receipt is None:
                raise ValidationError(
                    "Transaction receipt is required before explorer enrichment can be stored."
                )
            conn.execute(
                """
                INSERT INTO transaction_enrichments(
                    chain, tx_hash, status, context_json, source_name, source_version,
                    source_reference, fetched_at, error
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, tx_hash) DO UPDATE SET
                    status = excluded.status,
                    context_json = excluded.context_json,
                    source_name = excluded.source_name,
                    source_version = excluded.source_version,
                    source_reference = excluded.source_reference,
                    fetched_at = excluded.fetched_at,
                    error = excluded.error
                """,
                (
                    enrichment.chain.value,
                    enrichment.tx_hash,
                    enrichment.status.value,
                    json_dumps(context, pretty=False).decode("utf-8"),
                    enrichment.source_name,
                    enrichment.source_version,
                    enrichment.source_reference,
                    enrichment.fetched_at.isoformat(),
                    enrichment.error,
                ),
            )

    def get_enrichment(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionEnrichment | None:
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM transaction_enrichments
                WHERE chain = ? AND tx_hash = ?
                """,
                (chain.value, tx_hash.lower()),
            ).fetchone()
        if row is None:
            return None
        try:
            context = json_loads(str(row["context_json"]))
            if not isinstance(context, dict):
                raise ValueError("context is not an object")
            raw_parameters = context.get("decoded_parameters")
            parameters = (
                tuple(
                    _decoded_parameter_from_json(item)
                    for item in raw_parameters
                    if isinstance(item, dict)
                )
                if isinstance(raw_parameters, list)
                else ()
            )
            transaction_types = context.get("transaction_types")
            return TransactionEnrichment(
                chain=Chain(str(row["chain"])),
                tx_hash=str(row["tx_hash"]),
                status=EnrichmentStatus(str(row["status"])),
                method_name=_optional_text(context.get("method_name")),
                transaction_types=tuple(
                    str(item) for item in transaction_types
                ) if isinstance(transaction_types, list) else (),
                decoded_method_call=_optional_text(context.get("decoded_method_call")),
                decoded_method_id=_optional_text(context.get("decoded_method_id")),
                decoded_parameters=parameters,
                target_context=_address_context_from_json(context.get("target_context")),
                created_contract_context=_address_context_from_json(
                    context.get("created_contract_context")
                ),
                source_name=str(row["source_name"]),
                source_version=str(row["source_version"]),
                source_reference=_optional_text(row["source_reference"]),
                fetched_at=parse_datetime(str(row["fetched_at"])),
                error=_optional_text(row["error"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValidationError("Stored transaction enrichment is invalid.") from error


def _address_context_to_json(context: ExplorerAddressContext | None) -> object:
    if context is None:
        return None
    return {
        "address": context.address,
        "name": context.name,
        "implementation_name": context.implementation_name,
        "ens_name": context.ens_name,
        "is_contract": context.is_contract,
        "is_verified": context.is_verified,
        "creator_address": context.creator_address,
        "creation_tx_hash": context.creation_tx_hash,
        "source_reference": context.source_reference,
    }


def _address_context_from_json(raw: object) -> ExplorerAddressContext | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("address context is not an object")
    address = raw.get("address")
    reference = raw.get("source_reference")
    if not isinstance(address, str) or not isinstance(reference, str):
        raise ValueError("address context is incomplete")
    return ExplorerAddressContext(
        address=address,
        name=_optional_text(raw.get("name")),
        implementation_name=_optional_text(raw.get("implementation_name")),
        ens_name=_optional_text(raw.get("ens_name")),
        is_contract=_optional_bool(raw.get("is_contract")),
        is_verified=_optional_bool(raw.get("is_verified")),
        creator_address=_optional_text(raw.get("creator_address")),
        creation_tx_hash=_optional_text(raw.get("creation_tx_hash")),
        source_reference=reference,
    )


def _decoded_parameter_from_json(raw: dict[object, object]) -> ExplorerDecodedParameter:
    name = raw.get("name")
    type_name = raw.get("type_name")
    value = raw.get("value")
    if not isinstance(name, str) or not isinstance(type_name, str) or not isinstance(value, str):
        raise ValueError("decoded parameter is incomplete")
    return ExplorerDecodedParameter(
        name=name,
        type_name=type_name,
        value=value,
        indexed=_optional_bool(raw.get("indexed")),
    )


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None
