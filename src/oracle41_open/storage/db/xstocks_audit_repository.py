"""Save verified xStocks identity, action revisions, and issuer multiplier history.

A refresh is committed only after all requested pages have passed validation. Repeated issuer IDs
are deduplicated, conflicting immutable records roll back the whole refresh, and earlier revisions
remain available after a corrected or cancelled revision arrives. Saved identity supports offline use.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal

from oracle41_open._json import dumps, loads
from oracle41_open.core.models import Chain, RealWorldAssetCategory, RealWorldAssetIdentity
from oracle41_open.core.models.xstocks_audit import (
    XStocksAuditContext,
    XStocksCorporateAction,
    XStocksMultiplierChange,
)
from oracle41_open.storage.db._helpers import normalize_address_or_raise, parse_datetime
from oracle41_open.storage.db.sqlite_database import SQLiteDatabase


class XStocksAuditRepository:
    def __init__(self, database: SQLiteDatabase) -> None:
        self._database = database

    def save(self, history: XStocksAuditContext) -> None:
        """Commit validated issuer evidence and the successful fetch time atomically."""
        identity = history.identity
        chain, address = identity.chain.value, normalize_address_or_raise(identity.contract_address)
        if identity.source_name != "xstocks-public-api":
            raise ValueError("xStocks audit requires an issuer catalog identity.")
        with self._database.connection() as conn:
            conn.execute("BEGIN IMMEDIATE")
            previous = conn.execute(
                "SELECT identity_json, fetched_at FROM xstocks_audit_deployments "
                "WHERE chain = ? AND contract_address = ?",
                (chain, address),
            ).fetchone()
            if previous is not None:
                if loads(previous["identity_json"])["asset_id"] != identity.asset_id:
                    raise ValueError("xStocks issuer identity changed for this deployment.")
                if parse_datetime(previous["fetched_at"]) > history.fetched_at:
                    return
            conn.execute(
                """INSERT INTO xstocks_audit_deployments VALUES (?, ?, ?, ?)
                ON CONFLICT(chain, contract_address) DO UPDATE SET
                identity_json = excluded.identity_json, fetched_at = excluded.fetched_at""",
                (chain, address, _text(asdict(identity)), history.fetched_at.isoformat()),
            )
            for action in history.corporate_actions:
                if action.symbol.casefold() != identity.symbol.casefold():
                    raise ValueError("Corporate action belongs to another xStocks asset.")
                payload = _text(_action_payload(action))
                previous = conn.execute(
                    "SELECT payload_json FROM xstocks_corporate_actions "
                    "WHERE chain = ? AND contract_address = ? AND event_id = ? AND version = ?",
                    (chain, address, action.event_id, action.version),
                ).fetchone()
                if previous is not None and previous["payload_json"] != payload:
                    raise ValueError("Conflicting saved xStocks action revision.")
                conn.execute(
                    "INSERT OR IGNORE INTO xstocks_corporate_actions VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        chain,
                        address,
                        action.event_id,
                        action.version,
                        action.created_at.isoformat(),
                        payload,
                    ),
                )
            for change in history.multiplier_history:
                payload = _text(_change_payload(change))
                previous = conn.execute(
                    "SELECT payload_json FROM xstocks_multiplier_history "
                    "WHERE chain = ? AND contract_address = ? AND change_id = ?",
                    (chain, address, change.change_id),
                ).fetchone()
                if previous is not None and previous["payload_json"] != payload:
                    raise ValueError("Conflicting saved xStocks multiplier change.")
                conn.execute(
                    "INSERT OR IGNORE INTO xstocks_multiplier_history VALUES (?, ?, ?, ?, ?)",
                    (chain, address, change.change_id, change.effective_at.isoformat(), payload),
                )

    def load(self, chain: Chain, contract_address: str) -> XStocksAuditContext | None:
        """Read one consistent local snapshot without contacting the issuer."""
        address = normalize_address_or_raise(contract_address)
        with self._database.connection() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM xstocks_audit_deployments WHERE chain = ? AND contract_address = ?",
                (chain.value, address),
            ).fetchone()
            if row is None:
                return None
            raw_identity = loads(row["identity_json"])
            raw_identity["chain"] = Chain(raw_identity["chain"])
            raw_identity["category"] = RealWorldAssetCategory(raw_identity["category"])
            actions = conn.execute(
                "SELECT payload_json FROM xstocks_corporate_actions "
                "WHERE chain = ? AND contract_address = ? ORDER BY created_at DESC, version DESC",
                (chain.value, address),
            ).fetchall()
            changes = conn.execute(
                "SELECT payload_json FROM xstocks_multiplier_history "
                "WHERE chain = ? AND contract_address = ? ORDER BY effective_at DESC, change_id",
                (chain.value, address),
            ).fetchall()
        return XStocksAuditContext(
            identity=RealWorldAssetIdentity(**raw_identity),
            corporate_actions=tuple(
                _action_from_payload(loads(item["payload_json"])) for item in actions
            ),
            multiplier_history=tuple(
                _change_from_payload(loads(item["payload_json"])) for item in changes
            ),
            fetched_at=parse_datetime(row["fetched_at"]),
        )


def _text(payload: object) -> str:
    return dumps(payload).decode("utf-8")


def _action_payload(action: XStocksCorporateAction) -> dict[str, object]:
    return {
        "event_id": action.event_id,
        "version": action.version,
        "symbol": action.symbol,
        "action_type": action.action_type,
        "status": action.status,
        "created_at": action.created_at.isoformat(),
        "effective_at": action.effective_at.isoformat() if action.effective_at else None,
        "details": dict(action.details),
    }


def _action_from_payload(raw: dict[str, object]) -> XStocksCorporateAction:
    details = raw["details"]
    if not isinstance(details, dict):
        raise ValueError("Invalid saved xStocks action details.")
    return XStocksCorporateAction(
        event_id=str(raw["event_id"]),
        version=int(str(raw["version"])),
        symbol=str(raw["symbol"]),
        action_type=str(raw["action_type"]),
        status=str(raw["status"]),
        created_at=parse_datetime(raw["created_at"]),
        effective_at=parse_datetime(raw["effective_at"]) if raw["effective_at"] else None,
        details=tuple(sorted((key, value) for key, value in details.items())),
    )


def _change_payload(change: XStocksMultiplierChange) -> dict[str, object]:
    return {
        "change_id": change.change_id,
        "reason": change.reason,
        "multiplier": str(change.multiplier),
        "previous_multiplier": str(change.previous_multiplier),
        "effective_at": change.effective_at.isoformat(),
    }


def _change_from_payload(raw: dict[str, object]) -> XStocksMultiplierChange:
    return XStocksMultiplierChange(
        change_id=str(raw["change_id"]),
        reason=str(raw["reason"]),
        multiplier=Decimal(str(raw["multiplier"])),
        previous_multiplier=Decimal(str(raw["previous_multiplier"])),
        effective_at=parse_datetime(raw["effective_at"]),
    )
