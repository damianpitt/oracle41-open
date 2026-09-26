"""Test Stock Token audit persistence, schema migration, and refresh behavior.

The cases protect action upserts, first-seen evidence, material-change-only multiplier history, and
service round trips using deterministic provider data.
"""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from oracle41_open.core.models import (
    Chain,
    CorporateActionStatus,
    CorporateActionType,
    StockTokenCorporateAction,
    StockTokenMetadata,
    StockTokenStatus,
)
from oracle41_open.core.services.stock_token_audit_service import StockTokenAuditService
from oracle41_open.storage.db import SQLiteDatabase, StockTokenAuditRepository

_CONTRACT = "0x1111111111111111111111111111111111111111"
_FIRST = datetime(2026, 9, 25, 8, 0, tzinfo=UTC)
_SECOND = datetime(2026, 9, 26, 8, 0, tzinfo=UTC)


def test_schema_v12_adds_stock_token_audit_tables(tmp_path: Path) -> None:
    database = SQLiteDatabase(tmp_path / "state.sqlite3")

    with database.connection() as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert version is not None and version["value"] == "12"
    assert {
        "stock_token_corporate_actions",
        "stock_token_multiplier_observations",
    }.issubset(tables)


def test_multiplier_history_only_appends_material_changes(tmp_path: Path) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    metadata = _metadata()

    repository.record_multiplier(metadata, _FIRST, "robinhood-stock-token-api")
    repository.record_multiplier(metadata, _SECOND, "robinhood-stock-token-api")
    repository.record_multiplier(
        replace(metadata, current_multiplier=Decimal("2"), pending_multiplier=None),
        _SECOND,
        "robinhood-stock-token-api",
    )

    history = repository.list_multiplier_history(Chain.ROBINHOOD, _CONTRACT)
    assert len(history) == 2
    assert history[0].current_multiplier == Decimal("2")
    assert history[1].current_multiplier == Decimal("1")
    assert history[1].observed_at == _FIRST


def test_corporate_action_status_update_preserves_first_seen(tmp_path: Path) -> None:
    repository = StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    pending = _action(CorporateActionStatus.IN_PROGRESS)
    completed = replace(pending, status=CorporateActionStatus.COMPLETED)

    repository.save_corporate_actions((pending,), _FIRST)
    repository.save_corporate_actions((completed,), _SECOND)

    stored = repository.list_corporate_actions(Chain.ROBINHOOD, _CONTRACT)
    assert len(stored) == 1
    assert stored[0].action.status is CorporateActionStatus.COMPLETED
    assert stored[0].first_seen_at == _FIRST
    assert stored[0].last_seen_at == _SECOND


def test_service_refresh_persists_provider_actions_and_multiplier(tmp_path: Path) -> None:
    provider = _AuditProvider((_action(CorporateActionStatus.COMPLETED),))
    service = StockTokenAuditService(
        provider,
        StockTokenAuditRepository(SQLiteDatabase(tmp_path / "state.sqlite3")),
        now_func=lambda: _FIRST,
    )

    context = service.refresh(_metadata())

    assert len(context.corporate_actions) == 1
    assert len(context.multiplier_history) == 1
    assert provider.contracts == [_CONTRACT]


def test_existing_v11_database_migrates_forward(tmp_path: Path) -> None:
    path = tmp_path / "state.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            INSERT INTO schema_meta(key, value) VALUES('schema_version', '11');
            """
        )

    database = SQLiteDatabase(path)

    with database.connection() as conn:
        version = conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
    assert version is not None and version["value"] == "12"


class _AuditProvider:
    def __init__(self, actions: tuple[StockTokenCorporateAction, ...]) -> None:
        self.actions = actions
        self.contracts: list[str | None] = []

    def get_corporate_actions(
        self,
        contract_address: str | None = None,
    ) -> tuple[StockTokenCorporateAction, ...]:
        self.contracts.append(contract_address)
        return self.actions


def _metadata() -> StockTokenMetadata:
    return StockTokenMetadata(
        asset_id="0x" + "aa" * 32,
        symbol="TEST",
        name="Test Stock Token",
        contract_address=_CONTRACT,
        chain_id=4663,
        current_multiplier=Decimal("1"),
        pending_multiplier=Decimal("2"),
        pending_multiplier_effective_at=datetime(2026, 10, 1, tzinfo=UTC),
        logo_url=None,
        status=StockTokenStatus.ACTIVE,
        fractional_tradability="tradable",
        all_day_tradability="tradable",
        extended_hours_fractional_tradability=True,
    )


def _action(status: CorporateActionStatus) -> StockTokenCorporateAction:
    return StockTokenCorporateAction(
        action_id="0x" + "bb" * 32,
        action_type=CorporateActionType.FORWARD_SPLIT,
        status=status,
        process_date=date(2026, 9, 20),
        token_symbol="TEST",
        chain=Chain.ROBINHOOD,
        contract_address=_CONTRACT,
        detail_kind="forwardSplit",
        details=(("newRate", "2"), ("oldRate", "1")),
        source_provider="robinhood-stock-token-api",
    )

