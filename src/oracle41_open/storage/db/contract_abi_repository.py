"""Store contract ABIs and historical proxy resolutions.

ABI records keep source provenance, while proxy results are keyed by chain, address, and block number.
Replacing an ABI updates the active record without deleting raw transaction data.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC

from oracle41_open.core.models import (
    Chain,
    ContractABIRecord,
    ProxyKind,
    ProxyResolution,
    ProxyResolutionStatus,
    SignatureProvenance,
    SignatureSourceKind,
)
from oracle41_open.storage.db._helpers import parse_datetime
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class ContractABIRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def upsert_contract_abi(self, record: ContractABIRecord) -> None:
        with self._database.connection() as conn:
            _upsert_provenance(conn, record.provenance)
            conn.execute(
                """
                INSERT INTO contract_abis(
                    chain, contract_address, contract_name, abi_json, content_hash,
                    source_id, imported_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, contract_address) DO UPDATE SET
                    contract_name = excluded.contract_name,
                    abi_json = excluded.abi_json,
                    content_hash = excluded.content_hash,
                    source_id = excluded.source_id,
                    imported_at = excluded.imported_at
                """,
                (
                    record.chain.value,
                    record.contract_address.lower(),
                    record.contract_name,
                    record.abi_json,
                    record.content_hash,
                    record.provenance.source_id,
                    record.imported_at.astimezone(UTC).isoformat(),
                ),
            )

    def get_contract_abi(
        self,
        chain: Chain,
        contract_address: str,
    ) -> ContractABIRecord | None:
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT abis.*, sources.source_name, sources.source_kind,
                       sources.version AS source_version, sources.is_verified,
                       sources.reference
                FROM contract_abis AS abis
                JOIN abi_signature_sources AS sources
                  ON sources.source_id = abis.source_id
                WHERE abis.chain = ? AND abis.contract_address = ?
                LIMIT 1
                """,
                (chain.value, contract_address.lower()),
            ).fetchone()
        return _abi_from_row(chain, row) if row is not None else None

    def list_contract_abis(self, chain: Chain | None = None) -> tuple[ContractABIRecord, ...]:
        query = """
            SELECT abis.*, sources.source_name, sources.source_kind,
                   sources.version AS source_version, sources.is_verified,
                   sources.reference
            FROM contract_abis AS abis
            JOIN abi_signature_sources AS sources ON sources.source_id = abis.source_id
        """
        params: tuple[str, ...] = ()
        if chain is not None:
            query += " WHERE abis.chain = ?"
            params = (chain.value,)
        query += " ORDER BY abis.chain, abis.contract_address"
        with self._database.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return tuple(_abi_from_row(Chain(str(row["chain"])), row) for row in rows)

    def delete_contract_abi(self, chain: Chain, contract_address: str) -> bool:
        with self._database.connection() as conn:
            cursor = conn.execute(
                "DELETE FROM contract_abis WHERE chain = ? AND contract_address = ?",
                (chain.value, contract_address.lower()),
            )
        return cursor.rowcount > 0

    def save_proxy_resolution(self, resolution: ProxyResolution) -> None:
        with self._database.connection() as conn:
            conn.execute(
                """
                INSERT INTO proxy_resolutions(
                    chain, proxy_address, block_number, status, proxy_kind,
                    implementation_address, source_provider, resolved_at, error,
                    beacon_address
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chain, proxy_address, block_number) DO UPDATE SET
                    status = excluded.status,
                    proxy_kind = excluded.proxy_kind,
                    implementation_address = excluded.implementation_address,
                    source_provider = excluded.source_provider,
                    resolved_at = excluded.resolved_at,
                    error = excluded.error,
                    beacon_address = excluded.beacon_address
                """,
                (
                    resolution.chain.value,
                    resolution.proxy_address.lower(),
                    resolution.block_number,
                    resolution.status.value,
                    resolution.proxy_kind.value,
                    resolution.implementation_address,
                    resolution.source_provider,
                    resolution.resolved_at.astimezone(UTC).isoformat(),
                    resolution.error,
                    resolution.beacon_address,
                ),
            )

    def get_proxy_resolution(
        self,
        chain: Chain,
        proxy_address: str,
        block_number: int,
    ) -> ProxyResolution | None:
        with self._database.connection() as conn:
            row = conn.execute(
                """
                SELECT * FROM proxy_resolutions
                WHERE chain = ? AND proxy_address = ? AND block_number = ?
                LIMIT 1
                """,
                (chain.value, proxy_address.lower(), block_number),
            ).fetchone()
        if row is None:
            return None
        return ProxyResolution(
            chain=chain,
            proxy_address=str(row["proxy_address"]),
            status=ProxyResolutionStatus(str(row["status"])),
            proxy_kind=ProxyKind(str(row["proxy_kind"])),
            implementation_address=(
                str(row["implementation_address"])
                if row["implementation_address"] is not None
                else None
            ),
            block_number=int(row["block_number"]),
            source_provider=str(row["source_provider"]),
            resolved_at=parse_datetime(row["resolved_at"]),
            error=str(row["error"]) if row["error"] is not None else None,
            beacon_address=(
                str(row["beacon_address"])
                if row["beacon_address"] is not None
                else None
            ),
        )


def _upsert_provenance(conn: sqlite3.Connection, provenance: SignatureProvenance) -> None:
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


def _abi_from_row(chain: Chain, row: sqlite3.Row) -> ContractABIRecord:
    provenance = SignatureProvenance(
        source_id=str(row["source_id"]),
        source_name=str(row["source_name"]),
        source_kind=SignatureSourceKind(str(row["source_kind"])),
        version=str(row["source_version"]),
        is_verified=bool(row["is_verified"]),
        reference=str(row["reference"]) if row["reference"] is not None else None,
    )
    return ContractABIRecord(
        chain=chain,
        contract_address=str(row["contract_address"]),
        contract_name=(
            str(row["contract_name"]) if row["contract_name"] is not None else None
        ),
        abi_json=str(row["abi_json"]),
        content_hash=str(row["content_hash"]),
        provenance=provenance,
        imported_at=parse_datetime(row["imported_at"]),
    )
