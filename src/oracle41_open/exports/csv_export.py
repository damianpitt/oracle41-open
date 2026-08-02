from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import ActivityItem, WatchlistEntry
from oracle41_open.core.services.portfolio_service import PortfolioLoadResult, PortfolioWalletResult
from oracle41_open.exports.templates import (
    ActivityExportTemplate,
    PortfolioExportTemplate,
    SnapshotExportTemplate,
)
from oracle41_open.storage.db.models import WalletSnapshot

_ACTIVITY_TEMPLATE_FIELDS: dict[ActivityExportTemplate, list[str]] = {
    ActivityExportTemplate.FULL: [
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
    ],
    ActivityExportTemplate.COMPACT: [
        "timestamp",
        "chain",
        "category",
        "asset_symbol",
        "value",
        "value_usd",
        "from",
        "to",
        "tx_hash",
    ],
    ActivityExportTemplate.AUDIT: [
        "timestamp",
        "chain",
        "category",
        "from",
        "to",
        "asset_symbol",
        "value",
        "value_usd",
        "contract_address",
        "is_verified",
        "tx_hash",
        "log_index",
        "block_number",
    ],
}

_SNAPSHOT_TEMPLATE_FIELDS: dict[SnapshotExportTemplate, list[str]] = {
    SnapshotExportTemplate.SUMMARY: [
        "id",
        "address",
        "chain",
        "label",
        "captured_at",
        "native_balance",
        "native_price_usd",
        "total_usd",
        "token_count",
    ],
    SnapshotExportTemplate.DETAILED: [
        "id",
        "address",
        "chain",
        "label",
        "captured_at",
        "native_balance",
        "native_price_usd",
        "total_usd",
        "token_count",
        "token_balance_page_count",
        "token_balances_truncated",
        "payload_json",
    ],
}

_PORTFOLIO_TEMPLATE_FIELDS: dict[PortfolioExportTemplate, list[str]] = {
    PortfolioExportTemplate.SUMMARY: [
        "selected_wallet_count",
        "loaded_wallet_count",
        "failed_wallet_count",
        "truncated_wallet_count",
        "wallets_missing_total_usd_count",
        "total_usd",
        "known_total_usd",
    ],
    PortfolioExportTemplate.CHAINS: [
        "chain",
        "wallet_count",
        "native_balance_total",
        "native_usd_total",
        "native_usd_missing_wallet_count",
    ],
    PortfolioExportTemplate.TOKENS: [
        "chain",
        "contract_address",
        "symbol",
        "name",
        "decimals",
        "wallet_count",
        "total_balance",
        "total_usd",
        "usd_missing_wallet_count",
    ],
    PortfolioExportTemplate.WALLETS: [
        "entry_id",
        "address",
        "chain",
        "label",
        "is_loaded",
        "error",
        "native_balance",
        "native_price_usd",
        "total_usd",
        "token_count",
        "token_balance_page_count",
        "token_balances_truncated",
        "updated_at",
    ],
}


def activity_csv_text(
    items: list[ActivityItem],
    template: ActivityExportTemplate | str = ActivityExportTemplate.FULL,
) -> str:
    resolved_template = _resolve_activity_template(template)
    field_names = _ACTIVITY_TEMPLATE_FIELDS[resolved_template]
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(field_names)
    for item in items:
        row = _activity_row(item)
        writer.writerow([row[field_name] for field_name in field_names])
    return output.getvalue()


def write_activity_csv(
    items: list[ActivityItem],
    output_path: Path,
    template: ActivityExportTemplate | str = ActivityExportTemplate.FULL,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(activity_csv_text(items, template=template), encoding="utf-8")
    return output_path


def watchlist_csv_text(entries: list[WatchlistEntry]) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "address", "chain", "label", "created_at"])
    for entry in entries:
        writer.writerow(
            [
                entry.id,
                entry.address,
                entry.chain.value,
                "" if entry.label is None else entry.label,
                entry.created_at.isoformat(),
            ]
        )
    return output.getvalue()


def write_watchlist_csv(entries: list[WatchlistEntry], output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(watchlist_csv_text(entries), encoding="utf-8")
    return output_path


def snapshot_csv_text(
    snapshots: list[WalletSnapshot],
    template: SnapshotExportTemplate | str = SnapshotExportTemplate.SUMMARY,
) -> str:
    resolved_template = _resolve_snapshot_template(template)
    field_names = _SNAPSHOT_TEMPLATE_FIELDS[resolved_template]
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(field_names)
    for snapshot in snapshots:
        row = _snapshot_row(snapshot)
        writer.writerow([row[field_name] for field_name in field_names])
    return output.getvalue()


def write_snapshot_csv(
    snapshots: list[WalletSnapshot],
    output_path: Path,
    template: SnapshotExportTemplate | str = SnapshotExportTemplate.SUMMARY,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(snapshot_csv_text(snapshots, template=template), encoding="utf-8")
    return output_path


def portfolio_csv_text(
    result: PortfolioLoadResult,
    template: PortfolioExportTemplate | str = PortfolioExportTemplate.SUMMARY,
) -> str:
    resolved_template = _resolve_portfolio_template(template)
    field_names = _PORTFOLIO_TEMPLATE_FIELDS[resolved_template]
    rows = _portfolio_rows(result, template=resolved_template)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(field_names)
    for row in rows:
        writer.writerow([row.get(field_name, "") for field_name in field_names])
    return output.getvalue()


def write_portfolio_csv(
    result: PortfolioLoadResult,
    output_path: Path,
    template: PortfolioExportTemplate | str = PortfolioExportTemplate.SUMMARY,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(portfolio_csv_text(result, template=template), encoding="utf-8")
    return output_path


def _activity_row(item: ActivityItem) -> dict[str, str]:
    return {
        "timestamp": item.timestamp.isoformat(),
        "chain": item.chain.value,
        "category": item.category.value,
        "asset_symbol": item.asset_symbol,
        "value": str(item.value_decimal),
        "value_usd": "" if item.value_usd is None else str(item.value_usd),
        "from": item.from_address,
        "to": item.to_address,
        "tx_hash": item.tx_hash,
        "log_index": item.log_index,
        "contract_address": "" if item.contract_address is None else item.contract_address,
        "is_verified": "" if item.is_verified is None else ("true" if item.is_verified else "false"),
        "block_number": "" if item.block_number is None else str(item.block_number),
    }


def _snapshot_row(snapshot: WalletSnapshot) -> dict[str, str]:
    return {
        "id": str(snapshot.id),
        "address": snapshot.address,
        "chain": snapshot.chain.value,
        "label": "" if snapshot.label is None else snapshot.label,
        "captured_at": snapshot.captured_at.isoformat(),
        "native_balance": str(snapshot.native_balance),
        "native_price_usd": "" if snapshot.native_price_usd is None else str(snapshot.native_price_usd),
        "total_usd": "" if snapshot.total_usd is None else str(snapshot.total_usd),
        "token_count": str(snapshot.token_count),
        "token_balance_page_count": _payload_text(snapshot.payload.get("token_balance_page_count")),
        "token_balances_truncated": _payload_text(snapshot.payload.get("token_balances_truncated")),
        "payload_json": json_dumps(snapshot.payload, pretty=False).decode("utf-8"),
    }


def _payload_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _resolve_activity_template(template: ActivityExportTemplate | str) -> ActivityExportTemplate:
    if isinstance(template, ActivityExportTemplate):
        return template
    return ActivityExportTemplate(template)


def _resolve_snapshot_template(template: SnapshotExportTemplate | str) -> SnapshotExportTemplate:
    if isinstance(template, SnapshotExportTemplate):
        return template
    return SnapshotExportTemplate(template)


def _portfolio_rows(
    result: PortfolioLoadResult,
    template: PortfolioExportTemplate,
) -> list[dict[str, str]]:
    if template is PortfolioExportTemplate.SUMMARY:
        return [_portfolio_summary_row(result)]
    if template is PortfolioExportTemplate.CHAINS:
        return [
            {
                "chain": aggregate.chain.value,
                "wallet_count": str(aggregate.wallet_count),
                "native_balance_total": str(aggregate.native_balance_total),
                "native_usd_total": str(aggregate.native_usd_total),
                "native_usd_missing_wallet_count": str(aggregate.native_usd_missing_wallet_count),
            }
            for aggregate in result.chain_aggregates
        ]
    if template is PortfolioExportTemplate.TOKENS:
        return [
            {
                "chain": aggregate.chain.value,
                "contract_address": aggregate.contract_address,
                "symbol": aggregate.symbol,
                "name": aggregate.name,
                "decimals": str(aggregate.decimals),
                "wallet_count": str(aggregate.wallet_count),
                "total_balance": str(aggregate.total_balance),
                "total_usd": str(aggregate.total_usd),
                "usd_missing_wallet_count": str(aggregate.usd_missing_wallet_count),
            }
            for aggregate in result.token_aggregates
        ]
    return [_portfolio_wallet_row(wallet) for wallet in result.wallet_results]


def _portfolio_summary_row(result: PortfolioLoadResult) -> dict[str, str]:
    return {
        "selected_wallet_count": str(result.selected_wallet_count),
        "loaded_wallet_count": str(result.loaded_wallet_count),
        "failed_wallet_count": str(result.failed_wallet_count),
        "truncated_wallet_count": str(result.truncated_wallet_count),
        "wallets_missing_total_usd_count": str(result.wallets_missing_total_usd_count),
        "total_usd": "" if result.total_usd is None else str(result.total_usd),
        "known_total_usd": "" if result.known_total_usd is None else str(result.known_total_usd),
    }


def _portfolio_wallet_row(wallet: PortfolioWalletResult) -> dict[str, str]:
    if wallet.overview is None:
        return {
            "entry_id": str(wallet.entry.id),
            "address": wallet.entry.address,
            "chain": wallet.entry.chain.value,
            "label": "" if wallet.entry.label is None else wallet.entry.label,
            "is_loaded": "false",
            "error": "" if wallet.error is None else wallet.error,
            "native_balance": "",
            "native_price_usd": "",
            "total_usd": "",
            "token_count": "",
            "token_balance_page_count": "",
            "token_balances_truncated": "",
            "updated_at": "",
        }
    return {
        "entry_id": str(wallet.entry.id),
        "address": wallet.entry.address,
        "chain": wallet.entry.chain.value,
        "label": "" if wallet.entry.label is None else wallet.entry.label,
        "is_loaded": "true",
        "error": "" if wallet.error is None else wallet.error,
        "native_balance": str(wallet.overview.native_balance),
        "native_price_usd": (
            "" if wallet.overview.native_price_usd is None else str(wallet.overview.native_price_usd)
        ),
        "total_usd": "" if wallet.overview.total_usd is None else str(wallet.overview.total_usd),
        "token_count": str(len(wallet.overview.token_balances)),
        "token_balance_page_count": str(wallet.overview.token_balance_page_count),
        "token_balances_truncated": "true" if wallet.overview.token_balances_truncated else "false",
        "updated_at": wallet.overview.updated_at.isoformat(),
    }


def _resolve_portfolio_template(template: PortfolioExportTemplate | str) -> PortfolioExportTemplate:
    if isinstance(template, PortfolioExportTemplate):
        return template
    return PortfolioExportTemplate(template)
