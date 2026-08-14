"""Test CSV and JSON export formats.

The cases cover actions, activity, portfolios, watchlists, snapshots, templates, metadata, and stable versions.
They protect public file compatibility and value formatting.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import (
    ActionAsset,
    ActionAssetDirection,
    ActionConfidence,
    ActionEvidence,
    ActionEvidenceKind,
    ActionParticipant,
    ActivityCategory,
    ActivityItem,
    Chain,
    CompletenessState,
    DataProvenance,
    Token,
    TokenBalance,
    WalletAction,
    WalletActionKind,
    WalletActionStatus,
    WalletOverviewResult,
    WatchlistEntry,
)
from oracle41_open.core.services.portfolio_service import (
    PortfolioChainAggregate,
    PortfolioLoadResult,
    PortfolioTokenAggregate,
    PortfolioWalletResult,
)
from oracle41_open.exports import (
    ActivityExportContext,
    ActivityExportTemplate,
    PortfolioExportTemplate,
    SnapshotExportTemplate,
    activity_csv_text,
    activity_json_bytes,
    portfolio_csv_text,
    portfolio_json_bytes,
    snapshot_csv_text,
    snapshot_json_bytes,
    wallet_actions_csv_text,
    wallet_actions_json_bytes,
    watchlist_csv_text,
    watchlist_json_bytes,
    write_activity_csv,
    write_activity_json,
    write_portfolio_csv,
    write_portfolio_json,
    write_snapshot_csv,
    write_snapshot_json,
    write_wallet_actions_csv,
    write_wallet_actions_json,
    write_watchlist_csv,
    write_watchlist_json,
)
from oracle41_open.storage.db.models import WalletSnapshot


def test_activity_csv_text_contains_expected_columns_and_rows() -> None:
    text = activity_csv_text(_sample_items())
    rows = list(csv.reader(text.splitlines()))
    assert rows[0] == [
        "timestamp",
        "chain",
        "category",
        "asset_symbol",
        "value",
        "value_usd",
        "from",
        "to",
        "tx_hash",
        "log_index",
        "contract_address",
        "is_verified",
    ]
    assert rows[1][2] == "erc20"
    assert rows[2][2] == "external"
    assert len(rows) == 3


def test_wallet_action_exports_preserve_nested_evidence_and_version() -> None:
    actions = (_sample_action(),)

    rows = list(csv.reader(wallet_actions_csv_text(actions).splitlines()))
    evidence_index = rows[0].index("evidence")
    assert rows[1][3] == "transfer"
    assert "log:3" in rows[1][evidence_index]

    payload = json_loads(wallet_actions_json_bytes(actions, pretty=False))
    assert payload["format"] == "oracle41-wallet-actions"
    assert payload["format_version"] == 1
    assert payload["items"][0]["assets"][0]["raw_amount"] == "25"
    assert payload["items"][0]["evidence"][0]["reference"] == "log:3"


def test_write_wallet_action_exports_create_files(tmp_path: Path) -> None:
    actions = (_sample_action(),)

    csv_path = write_wallet_actions_csv(actions, tmp_path / "actions.csv")
    json_path = write_wallet_actions_json(actions, tmp_path / "actions.json")

    assert csv_path.exists()
    assert json_path.exists()
    assert "normalizer_version" in csv_path.read_text(encoding="utf-8")


def test_activity_csv_text_compact_template_uses_subset_columns() -> None:
    text = activity_csv_text(_sample_items(), template=ActivityExportTemplate.COMPACT)
    rows = list(csv.reader(text.splitlines()))
    assert rows[0] == [
        "timestamp",
        "chain",
        "category",
        "asset_symbol",
        "value",
        "value_usd",
        "from",
        "to",
        "tx_hash",
    ]


def test_activity_json_bytes_serializes_items() -> None:
    payload = json_loads(activity_json_bytes(_sample_items(), pretty=False))
    assert isinstance(payload, dict)
    items = payload.get("items")
    assert isinstance(items, list)
    assert items[0]["category"] == "erc20"
    assert items[0]["value_decimal"] == "25"


def test_activity_json_bytes_audit_template_exposes_expected_keys() -> None:
    payload = json_loads(
        activity_json_bytes(_sample_items(), template=ActivityExportTemplate.AUDIT, pretty=False)
    )
    assert payload["template"] == "audit"
    items = payload.get("items")
    assert isinstance(items, list)
    assert "block_number" in items[0]
    assert "raw_value" not in items[0]


def test_activity_exports_include_optional_completeness_and_provenance() -> None:
    fetched_at = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)
    context = ActivityExportContext(
        completeness=CompletenessState.PARTIAL,
        updated_at=datetime(2026, 8, 7, 10, 1, tzinfo=UTC),
        provenance=DataProvenance(
            source_provider="alchemy",
            fetched_at=fetched_at,
            request_cursor="page-2",
            query_from_block=20_000_000,
        ),
        is_persisted=True,
    )

    csv_rows = list(csv.reader(activity_csv_text(_sample_items(), context=context).splitlines()))
    completeness_index = csv_rows[0].index("completeness")
    source_index = csv_rows[0].index("source_provider")
    assert csv_rows[1][completeness_index] == "partial"
    assert csv_rows[1][source_index] == "alchemy"

    payload = json_loads(activity_json_bytes(_sample_items(), pretty=False, context=context))
    assert payload["format"] == "oracle41-activity"
    assert payload["format_version"] == 2
    assert payload["context"]["completeness"] == "partial"
    assert payload["context"]["source_provider"] == "alchemy"
    assert payload["context"]["is_persisted"] is True


def test_watchlist_exports_include_expected_fields() -> None:
    entries = _sample_watchlist_entries()
    csv_text = watchlist_csv_text(entries)
    csv_rows = list(csv.reader(csv_text.splitlines()))
    assert csv_rows[0] == ["id", "address", "chain", "label", "created_at"]
    assert csv_rows[1][1] == entries[0].address

    payload = json_loads(watchlist_json_bytes(entries, pretty=False))
    assert isinstance(payload, dict)
    items = payload.get("items")
    assert isinstance(items, list)
    assert items[0]["address"] == entries[0].address


def test_snapshot_exports_support_summary_and_detailed_templates() -> None:
    snapshots = _sample_snapshots()
    summary_rows = list(csv.reader(snapshot_csv_text(snapshots).splitlines()))
    assert "payload_json" not in summary_rows[0]
    detailed_rows = list(
        csv.reader(
            snapshot_csv_text(
                snapshots,
                template=SnapshotExportTemplate.DETAILED,
            ).splitlines()
        )
    )
    assert "payload_json" in detailed_rows[0]

    summary_payload = json_loads(snapshot_json_bytes(snapshots, pretty=False))
    assert summary_payload["template"] == "summary"
    detailed_payload = json_loads(
        snapshot_json_bytes(snapshots, template=SnapshotExportTemplate.DETAILED, pretty=False)
    )
    assert detailed_payload["template"] == "detailed"
    assert "payload" in detailed_payload["items"][0]


def test_portfolio_exports_support_all_templates() -> None:
    result = _sample_portfolio_result()
    summary_rows = list(csv.reader(portfolio_csv_text(result).splitlines()))
    assert summary_rows[0] == [
        "selected_wallet_count",
        "loaded_wallet_count",
        "failed_wallet_count",
        "truncated_wallet_count",
        "wallets_missing_total_usd_count",
        "total_usd",
        "known_total_usd",
    ]

    chain_rows = list(
        csv.reader(
            portfolio_csv_text(result, template=PortfolioExportTemplate.CHAINS).splitlines()
        )
    )
    assert chain_rows[0] == [
        "chain",
        "wallet_count",
        "native_balance_total",
        "native_usd_total",
        "native_usd_missing_wallet_count",
    ]

    token_payload = json_loads(
        portfolio_json_bytes(result, template=PortfolioExportTemplate.TOKENS, pretty=False)
    )
    assert token_payload["template"] == "tokens"

    wallet_payload = json_loads(
        portfolio_json_bytes(result, template=PortfolioExportTemplate.WALLETS, pretty=False)
    )
    assert wallet_payload["template"] == "wallets"
    items = wallet_payload.get("items")
    assert isinstance(items, list)
    assert items[0]["is_loaded"] is True
    assert items[1]["is_loaded"] is False


def test_write_activity_exports_create_files(tmp_path: Path) -> None:
    csv_path = write_activity_csv(
        _sample_items(),
        tmp_path / "activity.csv",
        template=ActivityExportTemplate.COMPACT,
    )
    json_path = write_activity_json(
        _sample_items(),
        tmp_path / "activity.json",
        template=ActivityExportTemplate.AUDIT,
    )
    assert csv_path.exists()
    assert json_path.exists()
    assert "tx_hash" in csv_path.read_text(encoding="utf-8")
    payload = json_loads(json_path.read_bytes())
    assert isinstance(payload, dict)
    assert payload["template"] == "audit"


def test_write_watchlist_and_snapshot_exports_create_files(tmp_path: Path) -> None:
    watchlist_csv = write_watchlist_csv(_sample_watchlist_entries(), tmp_path / "watchlist.csv")
    watchlist_json = write_watchlist_json(_sample_watchlist_entries(), tmp_path / "watchlist.json")
    snapshot_csv = write_snapshot_csv(
        _sample_snapshots(),
        tmp_path / "snapshots.csv",
        template=SnapshotExportTemplate.DETAILED,
    )
    snapshot_json = write_snapshot_json(
        _sample_snapshots(),
        tmp_path / "snapshots.json",
        template=SnapshotExportTemplate.SUMMARY,
    )

    assert watchlist_csv.exists()
    assert watchlist_json.exists()
    assert snapshot_csv.exists()
    assert snapshot_json.exists()
    assert "address" in watchlist_csv.read_text(encoding="utf-8")
    snapshot_payload = json_loads(snapshot_json.read_bytes())
    assert snapshot_payload["template"] == "summary"


def test_write_portfolio_exports_create_files(tmp_path: Path) -> None:
    result = _sample_portfolio_result()
    portfolio_csv = write_portfolio_csv(
        result,
        tmp_path / "portfolio.csv",
        template=PortfolioExportTemplate.WALLETS,
    )
    portfolio_json = write_portfolio_json(
        result,
        tmp_path / "portfolio.json",
        template=PortfolioExportTemplate.CHAINS,
    )

    assert portfolio_csv.exists()
    assert portfolio_json.exists()
    assert "entry_id" in portfolio_csv.read_text(encoding="utf-8")
    payload = json_loads(portfolio_json.read_bytes())
    assert payload["template"] == "chains"


def _sample_items() -> list[ActivityItem]:
    return [
        ActivityItem(
            block_number=19_000_000,
            tx_hash="0xaaa",
            log_index="0x0",
            timestamp=datetime(2026, 1, 20, 12, 0, tzinfo=UTC),
            from_address="0x1111111111111111111111111111111111111111",
            to_address="0x2222222222222222222222222222222222222222",
            asset_symbol="USDC",
            contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
            raw_value="25000000",
            value_decimal=Decimal("25"),
            value_usd=Decimal("25"),
            is_verified=True,
            category=ActivityCategory.ERC20,
            chain=Chain.ETHEREUM,
        ),
        ActivityItem(
            block_number=19_000_001,
            tx_hash="0xbbb",
            log_index="0x1",
            timestamp=datetime(2026, 1, 20, 11, 30, tzinfo=UTC),
            from_address="0x3333333333333333333333333333333333333333",
            to_address="0x4444444444444444444444444444444444444444",
            asset_symbol="ETH",
            contract_address=None,
            raw_value="10000000000000000",
            value_decimal=Decimal("0.01"),
            value_usd=Decimal("32"),
            is_verified=True,
            category=ActivityCategory.EXTERNAL,
            chain=Chain.ETHEREUM,
        ),
    ]


def _sample_action() -> WalletAction:
    sender = "0x1111111111111111111111111111111111111111"
    recipient = "0x2222222222222222222222222222222222222222"
    contract = "0x3333333333333333333333333333333333333333"
    return WalletAction(
        chain=Chain.ETHEREUM,
        tx_hash="0xaaa",
        action_index=0,
        kind=WalletActionKind.TRANSFER,
        status=WalletActionStatus.SUCCESS,
        summary="Transfer ERC-20",
        participants=(
            ActionParticipant("sender", sender),
            ActionParticipant("recipient", recipient),
        ),
        assets=(
            ActionAsset(
                ActionAssetDirection.OUT,
                "ERC-20",
                contract,
                None,
                None,
                "25",
            ),
        ),
        protocol_hint=None,
        confidence=ActionConfidence.HIGH,
        evidence=(
            ActionEvidence(
                ActionEvidenceKind.EVENT,
                "log:3",
                contract_address=contract,
                signature="Transfer(address,address,uint256)",
                source_id="bundled:erc20",
            ),
        ),
        normalizer_version="1",
    )


def _sample_watchlist_entries() -> list[WatchlistEntry]:
    return [
        WatchlistEntry(
            id=1,
            address="0x1111111111111111111111111111111111111111",
            chain=Chain.ETHEREUM,
            label="Main Wallet",
            created_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
        ),
        WatchlistEntry(
            id=2,
            address="0x2222222222222222222222222222222222222222",
            chain=Chain.BASE,
            label=None,
            created_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
        ),
    ]


def _sample_snapshots() -> list[WalletSnapshot]:
    return [
        WalletSnapshot(
            id=10,
            address="0x3333333333333333333333333333333333333333",
            chain=Chain.ETHEREUM,
            label="before",
            captured_at=datetime(2026, 3, 1, 12, 0, tzinfo=UTC),
            native_balance=Decimal("1.5"),
            native_price_usd=Decimal("2500"),
            total_usd=Decimal("3802"),
            token_count=2,
            payload={
                "version": 1,
                "token_balance_page_count": 1,
                "token_balances_truncated": False,
            },
        )
    ]


def _sample_portfolio_result() -> PortfolioLoadResult:
    entry_ok = WatchlistEntry(
        id=1,
        address="0x1111111111111111111111111111111111111111",
        chain=Chain.ETHEREUM,
        label="Main",
        created_at=datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
    )
    entry_fail = WatchlistEntry(
        id=2,
        address="0x2222222222222222222222222222222222222222",
        chain=Chain.ETHEREUM,
        label="Failing",
        created_at=datetime(2026, 3, 1, 11, 0, tzinfo=UTC),
    )
    overview = WalletOverviewResult(
        native_balance=Decimal("1"),
        native_price_usd=Decimal("2500"),
        token_balances=[
            TokenBalance(
                token=Token(
                    contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                    symbol="USDC",
                    name="USD Coin",
                    decimals=6,
                    is_verified=True,
                ),
                balance_decimal=Decimal("100"),
                price_usd=Decimal("1"),
            )
        ],
        total_usd=Decimal("2600"),
        updated_at=datetime(2026, 3, 2, 10, 0, tzinfo=UTC),
        token_balance_page_count=1,
        token_balances_truncated=False,
    )
    return PortfolioLoadResult(
        selected_wallet_count=2,
        loaded_wallet_count=1,
        failed_wallet_count=1,
        truncated_wallet_count=0,
        wallets_missing_total_usd_count=0,
        total_usd=Decimal("2600"),
        known_total_usd=Decimal("2600"),
        chain_aggregates=[
            PortfolioChainAggregate(
                chain=Chain.ETHEREUM,
                wallet_count=1,
                native_balance_total=Decimal("1"),
                native_usd_total=Decimal("2500"),
                native_usd_missing_wallet_count=0,
            )
        ],
        token_aggregates=[
            PortfolioTokenAggregate(
                chain=Chain.ETHEREUM,
                contract_address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                symbol="USDC",
                name="USD Coin",
                decimals=6,
                wallet_count=1,
                total_balance=Decimal("100"),
                total_usd=Decimal("100"),
                usd_missing_wallet_count=0,
            )
        ],
        wallet_results=[
            PortfolioWalletResult(entry=entry_ok, overview=overview, error=None),
            PortfolioWalletResult(entry=entry_fail, overview=None, error="provider timeout"),
        ],
    )
