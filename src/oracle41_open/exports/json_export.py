"""Write structured JSON reports and export metadata.

The module serializes activity, portfolios, watchlists, and snapshots into versioned public formats.
Decimal values and timestamps are converted without losing their meaning.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import ActivityItem, WatchlistEntry
from oracle41_open.core.services.portfolio_service import PortfolioLoadResult, PortfolioWalletResult
from oracle41_open.exports.templates import (
    ACTIVITY_EXPORT_FORMAT,
    ACTIVITY_EXPORT_FORMAT_VERSION,
    ActivityExportContext,
    ActivityExportTemplate,
    PortfolioExportTemplate,
    SnapshotExportTemplate,
)
from oracle41_open.storage.db.models import WalletSnapshot

_ACTIVITY_TEMPLATE_FIELDS: dict[ActivityExportTemplate, list[str]] = {
    ActivityExportTemplate.FULL: [
        "id",
        "block_number",
        "tx_hash",
        "log_index",
        "timestamp",
        "from",
        "to",
        "asset_symbol",
        "contract_address",
        "raw_value",
        "value_decimal",
        "value_usd",
        "is_verified",
        "category",
        "chain",
    ],
    ActivityExportTemplate.COMPACT: [
        "id",
        "timestamp",
        "chain",
        "category",
        "asset_symbol",
        "value_decimal",
        "value_usd",
        "from",
        "to",
        "tx_hash",
    ],
    ActivityExportTemplate.AUDIT: [
        "id",
        "block_number",
        "timestamp",
        "chain",
        "category",
        "from",
        "to",
        "asset_symbol",
        "value_decimal",
        "value_usd",
        "contract_address",
        "is_verified",
        "tx_hash",
        "log_index",
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
        "payload",
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


def activity_json_bytes(
    items: list[ActivityItem],
    template: ActivityExportTemplate | str = ActivityExportTemplate.FULL,
    pretty: bool = True,
    context: ActivityExportContext | None = None,
) -> bytes:
    resolved_template = _resolve_activity_template(template)
    fields = _ACTIVITY_TEMPLATE_FIELDS[resolved_template]
    payload: dict[str, object] = {
        "format": ACTIVITY_EXPORT_FORMAT,
        "format_version": ACTIVITY_EXPORT_FORMAT_VERSION,
        "template": resolved_template.value,
        "fields": fields,
        "items": [_select_fields(_activity_item_to_dict(item), fields) for item in items],
    }
    if context is not None:
        payload["context"] = _activity_context_to_dict(context)
    return json_dumps(payload, pretty=pretty)


def write_activity_json(
    items: list[ActivityItem],
    output_path: Path,
    template: ActivityExportTemplate | str = ActivityExportTemplate.FULL,
    pretty: bool = True,
    context: ActivityExportContext | None = None,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(
        activity_json_bytes(items, template=template, pretty=pretty, context=context)
    )
    return output_path


def _activity_context_to_dict(context: ActivityExportContext) -> dict[str, object]:
    provenance = context.provenance
    return {
        "completeness": context.completeness.value,
        "source_provider": provenance.source_provider if provenance is not None else None,
        "fetched_at": provenance.fetched_at.isoformat() if provenance is not None else None,
        "request_cursor": provenance.request_cursor if provenance is not None else None,
        "query_from_block": provenance.query_from_block if provenance is not None else None,
        "query_to_block": provenance.query_to_block if provenance is not None else None,
        "ledger_updated_at": context.updated_at.isoformat(),
        "is_persisted": context.is_persisted,
    }


def watchlist_json_bytes(entries: list[WatchlistEntry], pretty: bool = True) -> bytes:
    payload = {
        "items": [
            {
                "id": entry.id,
                "address": entry.address,
                "chain": entry.chain.value,
                "label": entry.label,
                "created_at": entry.created_at.isoformat(),
            }
            for entry in entries
        ]
    }
    return json_dumps(payload, pretty=pretty)


def write_watchlist_json(entries: list[WatchlistEntry], output_path: Path, pretty: bool = True) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(watchlist_json_bytes(entries, pretty=pretty))
    return output_path


def snapshot_json_bytes(
    snapshots: list[WalletSnapshot],
    template: SnapshotExportTemplate | str = SnapshotExportTemplate.SUMMARY,
    pretty: bool = True,
) -> bytes:
    resolved_template = _resolve_snapshot_template(template)
    fields = _SNAPSHOT_TEMPLATE_FIELDS[resolved_template]
    payload = {
        "template": resolved_template.value,
        "fields": fields,
        "items": [_select_fields(_snapshot_to_dict(snapshot), fields) for snapshot in snapshots],
    }
    return json_dumps(payload, pretty=pretty)


def write_snapshot_json(
    snapshots: list[WalletSnapshot],
    output_path: Path,
    template: SnapshotExportTemplate | str = SnapshotExportTemplate.SUMMARY,
    pretty: bool = True,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(snapshot_json_bytes(snapshots, template=template, pretty=pretty))
    return output_path


def portfolio_json_bytes(
    result: PortfolioLoadResult,
    template: PortfolioExportTemplate | str = PortfolioExportTemplate.SUMMARY,
    pretty: bool = True,
) -> bytes:
    resolved_template = _resolve_portfolio_template(template)
    fields = _PORTFOLIO_TEMPLATE_FIELDS[resolved_template]
    payload = {
        "template": resolved_template.value,
        "fields": fields,
        "items": _portfolio_records(result, template=resolved_template),
    }
    return json_dumps(payload, pretty=pretty)


def write_portfolio_json(
    result: PortfolioLoadResult,
    output_path: Path,
    template: PortfolioExportTemplate | str = PortfolioExportTemplate.SUMMARY,
    pretty: bool = True,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(portfolio_json_bytes(result, template=template, pretty=pretty))
    return output_path


def _activity_item_to_dict(item: ActivityItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "block_number": item.block_number,
        "tx_hash": item.tx_hash,
        "log_index": item.log_index,
        "timestamp": item.timestamp.isoformat(),
        "from": item.from_address,
        "to": item.to_address,
        "asset_symbol": item.asset_symbol,
        "contract_address": item.contract_address,
        "raw_value": item.raw_value,
        "value_decimal": str(item.value_decimal),
        "value_usd": str(item.value_usd) if item.value_usd is not None else None,
        "is_verified": item.is_verified,
        "category": item.category.value,
        "chain": item.chain.value,
    }


def _snapshot_to_dict(snapshot: WalletSnapshot) -> dict[str, Any]:
    return {
        "id": snapshot.id,
        "address": snapshot.address,
        "chain": snapshot.chain.value,
        "label": snapshot.label,
        "captured_at": snapshot.captured_at.isoformat(),
        "native_balance": str(snapshot.native_balance),
        "native_price_usd": (
            str(snapshot.native_price_usd) if snapshot.native_price_usd is not None else None
        ),
        "total_usd": str(snapshot.total_usd) if snapshot.total_usd is not None else None,
        "token_count": snapshot.token_count,
        "payload": snapshot.payload,
    }


def _select_fields(record: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: record.get(field) for field in fields}


def _resolve_activity_template(template: ActivityExportTemplate | str) -> ActivityExportTemplate:
    if isinstance(template, ActivityExportTemplate):
        return template
    return ActivityExportTemplate(template)


def _resolve_snapshot_template(template: SnapshotExportTemplate | str) -> SnapshotExportTemplate:
    if isinstance(template, SnapshotExportTemplate):
        return template
    return SnapshotExportTemplate(template)


def _portfolio_records(
    result: PortfolioLoadResult,
    template: PortfolioExportTemplate,
) -> list[dict[str, Any]]:
    if template is PortfolioExportTemplate.SUMMARY:
        return [
            {
                "selected_wallet_count": result.selected_wallet_count,
                "loaded_wallet_count": result.loaded_wallet_count,
                "failed_wallet_count": result.failed_wallet_count,
                "truncated_wallet_count": result.truncated_wallet_count,
                "wallets_missing_total_usd_count": result.wallets_missing_total_usd_count,
                "total_usd": str(result.total_usd) if result.total_usd is not None else None,
                "known_total_usd": (
                    str(result.known_total_usd) if result.known_total_usd is not None else None
                ),
            }
        ]
    if template is PortfolioExportTemplate.CHAINS:
        return [
            {
                "chain": aggregate.chain.value,
                "wallet_count": aggregate.wallet_count,
                "native_balance_total": str(aggregate.native_balance_total),
                "native_usd_total": str(aggregate.native_usd_total),
                "native_usd_missing_wallet_count": aggregate.native_usd_missing_wallet_count,
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
                "decimals": aggregate.decimals,
                "wallet_count": aggregate.wallet_count,
                "total_balance": str(aggregate.total_balance),
                "total_usd": str(aggregate.total_usd),
                "usd_missing_wallet_count": aggregate.usd_missing_wallet_count,
            }
            for aggregate in result.token_aggregates
        ]
    return [_portfolio_wallet_record(wallet) for wallet in result.wallet_results]


def _portfolio_wallet_record(wallet: PortfolioWalletResult) -> dict[str, Any]:
    if wallet.overview is None:
        return {
            "entry_id": wallet.entry.id,
            "address": wallet.entry.address,
            "chain": wallet.entry.chain.value,
            "label": wallet.entry.label,
            "is_loaded": False,
            "error": wallet.error,
            "native_balance": None,
            "native_price_usd": None,
            "total_usd": None,
            "token_count": None,
            "token_balance_page_count": None,
            "token_balances_truncated": None,
            "updated_at": None,
        }
    return {
        "entry_id": wallet.entry.id,
        "address": wallet.entry.address,
        "chain": wallet.entry.chain.value,
        "label": wallet.entry.label,
        "is_loaded": True,
        "error": wallet.error,
        "native_balance": str(wallet.overview.native_balance),
        "native_price_usd": (
            str(wallet.overview.native_price_usd) if wallet.overview.native_price_usd is not None else None
        ),
        "total_usd": str(wallet.overview.total_usd) if wallet.overview.total_usd is not None else None,
        "token_count": len(wallet.overview.token_balances),
        "token_balance_page_count": wallet.overview.token_balance_page_count,
        "token_balances_truncated": wallet.overview.token_balances_truncated,
        "updated_at": wallet.overview.updated_at.isoformat(),
    }


def _resolve_portfolio_template(template: PortfolioExportTemplate | str) -> PortfolioExportTemplate:
    if isinstance(template, PortfolioExportTemplate):
        return template
    return PortfolioExportTemplate(template)
