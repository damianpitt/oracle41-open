"""Capture local ledger inputs and append historical holdings reports.

Events, receipt status, checkpoint context, and saved fees are read in one SQLite snapshot.
Events are read without joining query scopes, which avoids repeating transfers saved by multiple
views. Reports are separate from mutable ledger projections and never edit imported history.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from oracle41_open.core.models import Chain, ValidationError
from oracle41_open.core.models.historical_holdings import (
    HistoricalHoldingsReport,
    HistoricalHoldingsReportSummary,
    HistoricalHoldingsRequest,
)
from oracle41_open.core.models.historical_price import time_text, token_address
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase

_MAX_EVENTS = 20_000


class HistoricalHoldingsRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def capture(self, request: HistoricalHoldingsRequest) -> dict[str, Any]:
        """Include unknown block positions as explicit exclusions, not silently missing rows."""
        wallet, chain, token = request.wallet_address, request.chain.value, request.contract_address
        with self._database.connection() as conn:
            conn.execute("BEGIN")
            events = conn.execute(
                "SELECT e.*, r.status AS receipt_status, r.block_number AS receipt_block_number FROM ledger_events e "
                "LEFT JOIN ledger_transaction_receipts r ON r.chain=e.chain AND r.tx_hash=e.tx_hash "
                "WHERE e.wallet_address=? AND e.chain=? AND lower(e.contract_address)=? "
                "AND (e.block_number IS NULL OR (e.block_number>? AND e.block_number<=?)) "
                "ORDER BY e.block_number, e.tx_hash, e.log_index LIMIT ?",
                (
                    wallet,
                    chain,
                    token,
                    request.opening_block,
                    request.closing_block,
                    _MAX_EVENTS + 1,
                ),
            ).fetchall()
            if len(events) > _MAX_EVENTS:
                raise ValidationError(
                    "More than 20,000 token events match this report. Choose a narrower block range."
                )
            checkpoints = conn.execute(
                "SELECT * FROM sync_checkpoints WHERE wallet_address=? AND chain=? ORDER BY scope",
                (wallet, chain),
            ).fetchall()
            fees = conn.execute(
                "SELECT f.*, COALESCE(r.block_number,t.block_number) AS block_number, r.status AS receipt_status "
                "FROM ledger_fees f JOIN ledger_transactions t ON t.chain=f.chain AND t.tx_hash=f.tx_hash "
                "LEFT JOIN ledger_transaction_receipts r ON r.chain=f.chain AND r.tx_hash=f.tx_hash "
                "WHERE lower(f.payer_address)=? AND f.chain=? "
                "AND (COALESCE(r.block_number,t.block_number) IS NULL OR "
                "(COALESCE(r.block_number,t.block_number)>? AND COALESCE(r.block_number,t.block_number)<=?)) "
                "ORDER BY block_number, f.tx_hash LIMIT ?",
                (wallet, chain, request.opening_block, request.closing_block, _MAX_EVENTS + 1),
            ).fetchall()
            if len(fees) > _MAX_EVENTS:
                raise ValidationError(
                    "Too many saved fees in this block range. Choose a narrower range."
                )
        return {
            "events": [dict(row) for row in events],
            "checkpoints": [dict(row) for row in checkpoints],
            "fees": [dict(row) for row in fees],
            "coverage": "saved_rows_only_not_proven_complete",
        }

    def save(self, report: HistoricalHoldingsReport) -> None:
        """Append a frozen report, refusing ID reuse rather than replacing earlier evidence."""
        request = report.document["inputs"]["request"]
        with self._database.connection() as conn:
            conn.execute(
                "INSERT INTO historical_holdings_reports VALUES (?, ?, ?, ?, ?, ?)",
                (
                    report.report_id,
                    request["wallet_address"],
                    request["chain"],
                    request["contract_address"],
                    time_text(report.created_at),
                    report.document_json,
                ),
            )

    def get(self, report_id: str) -> HistoricalHoldingsReport | None:
        with self._database.connection() as conn:
            row = conn.execute(
                "SELECT * FROM historical_holdings_reports WHERE report_id=?", (report_id,)
            ).fetchone()
        return (
            None
            if row is None
            else HistoricalHoldingsReport(
                row["report_id"], datetime.fromisoformat(row["created_at"]), row["document_json"]
            )
        )

    def list_reports(
        self, wallet: str, chain: Chain, token: str
    ) -> tuple[HistoricalHoldingsReportSummary, ...]:
        with self._database.connection() as conn:
            rows = conn.execute(
                "SELECT report_id, created_at FROM historical_holdings_reports WHERE wallet_address=? AND chain=? "
                "AND contract_address=? ORDER BY created_at DESC, rowid DESC LIMIT 50",
                (token_address(wallet), chain.value, token_address(token)),
            ).fetchall()
        return tuple(
            HistoricalHoldingsReportSummary(
                row["report_id"], datetime.fromisoformat(row["created_at"])
            )
            for row in rows
        )
