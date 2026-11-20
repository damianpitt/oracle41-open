"""Store historical USD price evidence without overwriting earlier fetches.

A fetch and all its points commit together, including empty successful responses. Lookups select
only provider timestamps at or before the requested time. Fetch IDs preserve revisions so a report
can be reconstructed even after a provider later changes its historical values.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from oracle41_open.core.models import Chain, ValidationError
from oracle41_open.core.models.historical_price import (
    HistoricalPriceBatch,
    HistoricalPricePoint,
    HistoricalPriceQuery,
    HistoricalPriceSelection,
    time_text,
    token_address,
    utc_time,
)
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class HistoricalPriceRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, batch: HistoricalPriceBatch) -> None:
        """Append the complete evidence batch; a failed point insert rolls everything back."""
        query = batch.query
        with self._database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "INSERT INTO historical_price_fetches VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    batch.fetch_id,
                    query.chain.value,
                    query.contract_address,
                    query.interval,
                    time_text(query.start),
                    time_text(query.end),
                    batch.source_provider,
                    batch.source_reference,
                    time_text(batch.fetched_at),
                ),
            )
            conn.executemany(
                "INSERT INTO historical_price_points VALUES (?, ?, ?)",
                [
                    (batch.fetch_id, time_text(point.timestamp), str(point.value_usd))
                    for point in batch.points
                ],
            )

    def get(self, fetch_id: str) -> HistoricalPriceBatch | None:
        """Reopen the exact evidence used by a report, without a network request."""
        with self._database.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM historical_price_fetches WHERE fetch_id = ?", (fetch_id,)
            ).fetchone()
            return _batch(conn, row) if row is not None else None

    def latest(
        self, query: HistoricalPriceQuery, source_provider: str = "alchemy"
    ) -> HistoricalPriceBatch | None:
        """Read the newest saved response for this exact query, including an empty response."""
        with self._database.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM historical_price_fetches WHERE chain = ? AND contract_address = ? "
                "AND interval = ? AND start_at = ? AND end_at = ? AND source_provider = ? "
                "ORDER BY fetched_at DESC, rowid DESC LIMIT 1",
                (
                    query.chain.value,
                    query.contract_address,
                    query.interval,
                    time_text(query.start),
                    time_text(query.end),
                    source_provider,
                ),
            ).fetchone()
            return _batch(conn, row) if row is not None else None

    def select(
        self,
        chain: Chain,
        contract_address: str,
        at: datetime,
        *,
        max_age_seconds: int = 86_400,
        source_provider: str = "alchemy",
        known_by: datetime | None = None,
    ) -> HistoricalPriceSelection:
        """Never select a later observation, interpolate, or fall back to a current price."""
        if (
            not isinstance(max_age_seconds, int)
            or isinstance(max_age_seconds, bool)
            or max_age_seconds < 0
        ):
            raise ValidationError("Historical price age limit must be a nonnegative integer.")
        at = utc_time(at)
        params: list[object] = [
            chain.value,
            token_address(contract_address),
            source_provider,
            time_text(at),
        ]
        cutoff = ""
        if known_by is not None:
            cutoff = " AND f.fetched_at <= ?"
            params.append(time_text(known_by))
        # Timestamp comes first; a newer fetch only breaks ties for that same observation time.
        with self._database.connection() as conn:
            row = conn.execute(
                "SELECT p.*, f.source_provider FROM historical_price_points p "
                "JOIN historical_price_fetches f ON f.fetch_id = p.fetch_id "
                "WHERE f.chain = ? AND f.contract_address = ? AND f.source_provider = ? "
                "AND f.interval = '1d' AND p.price_at <= ?"
                + cutoff
                + " ORDER BY p.price_at DESC, f.fetched_at DESC, f.rowid DESC LIMIT 1",
                params,
            ).fetchone()
        if row is None:
            return HistoricalPriceSelection(at, max_age_seconds, "missing")
        point = HistoricalPricePoint(
            datetime.fromisoformat(row["price_at"]), Decimal(row["value_usd"])
        )
        age = (at - point.timestamp).total_seconds()
        status = "stale" if age > max_age_seconds else "exact" if age == 0 else "prior"
        return HistoricalPriceSelection(
            at, max_age_seconds, status, row["fetch_id"], row["source_provider"], point
        )


def _batch(conn: sqlite3.Connection, row: sqlite3.Row) -> HistoricalPriceBatch:
    points = conn.execute(
        "SELECT * FROM historical_price_points WHERE fetch_id = ? ORDER BY price_at",
        (row["fetch_id"],),
    ).fetchall()
    return HistoricalPriceBatch(
        fetch_id=row["fetch_id"],
        query=HistoricalPriceQuery(
            Chain(row["chain"]),
            row["contract_address"],
            datetime.fromisoformat(row["start_at"]),
            datetime.fromisoformat(row["end_at"]),
            row["interval"],
        ),
        source_provider=row["source_provider"],
        source_reference=row["source_reference"],
        fetched_at=datetime.fromisoformat(row["fetched_at"]),
        points=tuple(
            HistoricalPricePoint(
                datetime.fromisoformat(point["price_at"]), Decimal(point["value_usd"])
            )
            for point in points
        ),
    )
