"""Stage xStocks history pages and resume them safely after interruption.

Each page's records and next-page checkpoint share a SQLite transaction. Completed history stays
separate until both feeds finish. Final publication and checkpoint deletion are atomic, so failure
keeps the previous published history and the staged resume point. Run revisions reject stale writers.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from datetime import UTC, datetime
from uuid import uuid4

from oracle41_open._json import loads
from oracle41_open.core.models import Chain, RealWorldAssetCategory, RealWorldAssetIdentity
from oracle41_open.core.models.xstocks_audit import (
    XStocksAuditContext,
    XStocksHistoryPage,
    XStocksSyncProgress,
)
from oracle41_open.storage.db._helpers import normalize_address_or_raise, parse_datetime
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase
from oracle41_open.storage.db.xstocks_audit_repository import (
    _action_from_payload,
    _action_payload,
    _change_from_payload,
    _change_payload,
    _save_history,
    _text,
)


class XStocksSyncRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def begin(
        self,
        identity: RealWorldAssetIdentity,
        *,
        restart: bool = False,
    ) -> XStocksSyncProgress:
        """Reuse pending progress, or explicitly replace staging while keeping published history."""
        address = normalize_address_or_raise(identity.contract_address)
        if identity.source_name != "xstocks-public-api":
            raise ValueError("xStocks sync requires an issuer catalog identity.")
        with self._database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = _read_progress(conn, identity.chain, address)
            if previous is not None and not restart:
                old = previous.identity
                if (
                    old.asset_id != identity.asset_id
                    or old.symbol.casefold() != identity.symbol.casefold()
                ):
                    raise ValueError("Catalog identity changed; start a new audit sync.")
                return previous
            conn.execute(
                "DELETE FROM xstocks_sync_checkpoints WHERE chain = ? AND contract_address = ?",
                (identity.chain.value, address),
            )
            now = datetime.now(tz=UTC).isoformat()
            conn.execute(
                """INSERT INTO xstocks_sync_checkpoints(
                    chain, contract_address, run_id, identity_json, state, started_at, updated_at
                ) VALUES (?, ?, ?, ?, 'in_progress', ?, ?)""",
                (identity.chain.value, address, str(uuid4()), _text(asdict(identity)), now, now),
            )
            progress = _read_progress(conn, identity.chain, address)
            assert progress is not None
            return progress

    def progress(self, chain: Chain, contract_address: str) -> XStocksSyncProgress | None:
        address = normalize_address_or_raise(contract_address)
        with self._database.connection() as conn:
            conn.execute("BEGIN")
            return _read_progress(conn, chain, address)

    def stage_page(
        self,
        progress: XStocksSyncProgress,
        page: XStocksHistoryPage,
    ) -> XStocksSyncProgress:
        """Deduplicate page records and advance only the feed that supplied them."""
        if page.feed not in {"actions", "multipliers"}:
            raise ValueError("Invalid xStocks sync feed.")
        expected_page = (
            progress.actions_next_page if page.feed == "actions" else progress.multipliers_next_page
        )
        finished = progress.actions_done if page.feed == "actions" else progress.multipliers_done
        if finished or page.page != expected_page or page.next_page not in {None, page.page + 1}:
            raise ValueError("xStocks history page does not match its checkpoint.")
        if page.feed == "actions":
            if page.multiplier_history:
                raise ValueError("Multiplier records cannot advance an action checkpoint.")
            records = []
            for action in page.corporate_actions:
                if action.symbol.casefold() != progress.identity.symbol.casefold():
                    raise ValueError("Corporate action belongs to another xStocks asset.")
                records.append(
                    (f"{action.event_id}:{action.version}", _text(_action_payload(action)))
                )
        else:
            if page.corporate_actions:
                raise ValueError("Action records cannot advance a multiplier checkpoint.")
            records = [
                (change.change_id, _text(_change_payload(change)))
                for change in page.multiplier_history
            ]
        if page.next_page is not None and not records:
            raise ValueError("xStocks history continuation has no records.")
        with self._database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _check_writer(conn, progress)
            for record_id, payload in records:
                previous = conn.execute(
                    "SELECT payload_json FROM xstocks_sync_records "
                    "WHERE run_id = ? AND feed = ? AND record_id = ?",
                    (progress.run_id, page.feed, record_id),
                ).fetchone()
                if previous is not None and previous["payload_json"] != payload:
                    raise ValueError("Conflicting staged xStocks issuer record.")
                conn.execute(
                    "INSERT OR IGNORE INTO xstocks_sync_records VALUES (?, ?, ?, ?)",
                    (progress.run_id, page.feed, record_id, payload),
                )
            # Column names come only from the two validated feed values above.
            prefix = "actions" if page.feed == "actions" else "multipliers"
            conn.execute(
                f"""UPDATE xstocks_sync_checkpoints SET
                    {prefix}_next_page = ?, {prefix}_done = ?, {prefix}_pages = {prefix}_pages + 1,
                    revision = revision + 1, state = 'in_progress', updated_at = ? WHERE run_id = ?""",
                (
                    page.next_page if page.next_page is not None else page.page,
                    int(page.next_page is None),
                    datetime.now(tz=UTC).isoformat(),
                    progress.run_id,
                ),
            )
            result = _read_progress(
                conn, progress.identity.chain, progress.identity.contract_address
            )
            assert result is not None
            return result

    def set_state(self, progress: XStocksSyncProgress, state: str) -> XStocksSyncProgress:
        """Record pause or failure without changing the next uncommitted page."""
        if state not in {"paused", "failed"}:
            raise ValueError("Invalid xStocks sync state.")
        with self._database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _check_writer(conn, progress)
            conn.execute(
                "UPDATE xstocks_sync_checkpoints SET state = ?, revision = revision + 1, "
                "updated_at = ? WHERE run_id = ?",
                (state, datetime.now(tz=UTC).isoformat(), progress.run_id),
            )
            result = _read_progress(
                conn, progress.identity.chain, progress.identity.contract_address
            )
            assert result is not None
            return result

    def publish(self, progress: XStocksSyncProgress) -> None:
        """Publish both completed feeds and remove their staging in one transaction."""
        if not progress.actions_done or not progress.multipliers_done:
            raise ValueError("Both xStocks history feeds must finish before publication.")
        with self._database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _check_writer(conn, progress)
            rows = conn.execute(
                "SELECT feed, payload_json FROM xstocks_sync_records WHERE run_id = ?",
                (progress.run_id,),
            ).fetchall()
            actions = tuple(
                _action_from_payload(loads(row["payload_json"]))
                for row in rows
                if row["feed"] == "actions"
            )
            changes = tuple(
                _change_from_payload(loads(row["payload_json"]))
                for row in rows
                if row["feed"] == "multipliers"
            )
            _save_history(
                conn,
                XStocksAuditContext(
                    progress.identity,
                    actions,
                    changes,
                    datetime.now(tz=UTC),
                ),
            )
            conn.execute(
                "DELETE FROM xstocks_sync_checkpoints WHERE run_id = ?", (progress.run_id,)
            )


def _check_writer(conn: sqlite3.Connection, progress: XStocksSyncProgress) -> None:
    row = conn.execute(
        "SELECT revision FROM xstocks_sync_checkpoints WHERE run_id = ?",
        (progress.run_id,),
    ).fetchone()
    if row is None or row["revision"] != progress.revision:
        raise ValueError("Audit sync changed in another task; reload its progress.")


def _read_progress(
    conn: sqlite3.Connection,
    chain: Chain,
    address: str,
) -> XStocksSyncProgress | None:
    row = conn.execute(
        "SELECT * FROM xstocks_sync_checkpoints WHERE chain = ? AND contract_address = ?",
        (chain.value, address.lower()),
    ).fetchone()
    if row is None:
        return None
    identity = loads(row["identity_json"])
    identity["chain"] = Chain(identity["chain"])
    identity["category"] = RealWorldAssetCategory(identity["category"])
    counts = {
        item["feed"]: item["total"]
        for item in conn.execute(
            "SELECT feed, COUNT(*) AS total FROM xstocks_sync_records WHERE run_id = ? GROUP BY feed",
            (row["run_id"],),
        ).fetchall()
    }
    return XStocksSyncProgress(
        identity=RealWorldAssetIdentity(**identity),
        run_id=row["run_id"],
        revision=row["revision"],
        state=row["state"],
        actions_next_page=row["actions_next_page"],
        multipliers_next_page=row["multipliers_next_page"],
        actions_done=bool(row["actions_done"]),
        multipliers_done=bool(row["multipliers_done"]),
        actions_pages=row["actions_pages"],
        multipliers_pages=row["multipliers_pages"],
        actions_staged=counts.get("actions", 0),
        multipliers_staged=counts.get("multipliers", 0),
        started_at=parse_datetime(row["started_at"]),
        updated_at=parse_datetime(row["updated_at"]),
    )
