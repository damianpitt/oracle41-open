"""Write saved RWA corporate actions and multiplier observations as audit reports.

Reports describe all locally saved history for one chain and contract. Decimal values stay as text
to preserve precision. CSV stores action details as JSON in one cell; JSON preserves named fields.
Both formats identify their scope so local observations cannot imply complete issuer history.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from io import StringIO
from pathlib import Path

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import Chain, RealWorldAssetIdentity, StockTokenAuditContext
from oracle41_open.core.services.address_validator import AddressValidator

RWA_AUDIT_FORMAT = "oracle41-rwa-audit"
RWA_AUDIT_VERSION = 1
_FIELDS = (
    "record_type",
    "issuer_id",
    "symbol",
    "status",
    "process_date",
    "detail_kind",
    "details",
    "current_multiplier",
    "pending_multiplier",
    "pending_effective_at",
    "observed_at",
    "first_seen_at",
    "last_seen_at",
    "source_provider",
)


@dataclass(frozen=True)
class RWAAuditReport:
    """Bind an export to one deployment and the saved history displayed by the browser."""

    chain: Chain
    contract_address: str
    history: StockTokenAuditContext
    exported_at: datetime
    identity: RealWorldAssetIdentity | None = None


def _rows(report: RWAAuditReport) -> list[dict[str, object]]:
    address = AddressValidator.normalized(report.contract_address)
    if AddressValidator.validation_error(address) is not None:
        raise ValueError("Audit export requires a valid contract address.")
    if report.identity is not None and (
        report.identity.chain is not report.chain
        or report.identity.contract_address.lower() != address
    ):
        raise ValueError("Audit identity does not match the export deployment.")
    rows: list[dict[str, object]] = []
    for stored in report.history.corporate_actions:
        action = stored.action
        if action.chain is not report.chain or action.contract_address.lower() != address:
            raise ValueError("Corporate action does not match the export deployment.")
        rows.append(
            {
                "record_type": "corporate_action",
                "issuer_id": action.action_id,
                "symbol": action.token_symbol,
                "status": action.status.value,
                "action_type": action.action_type.value,
                "process_date": action.process_date.isoformat() if action.process_date else None,
                "detail_kind": action.detail_kind,
                "details": dict(action.details),
                "first_seen_at": stored.first_seen_at.isoformat(),
                "last_seen_at": stored.last_seen_at.isoformat(),
                "source_provider": action.source_provider,
            }
        )
    for observation in report.history.multiplier_history:
        if observation.chain is not report.chain or observation.contract_address.lower() != address:
            raise ValueError("Multiplier observation does not match the export deployment.")
        rows.append(
            {
                "record_type": "multiplier_observation",
                "issuer_id": observation.asset_id,
                "symbol": observation.token_symbol,
                "status": observation.status.value,
                "current_multiplier": str(observation.current_multiplier),
                "pending_multiplier": (
                    str(observation.pending_multiplier)
                    if observation.pending_multiplier is not None
                    else None
                ),
                "pending_effective_at": (
                    observation.pending_multiplier_effective_at.isoformat()
                    if observation.pending_multiplier_effective_at
                    else None
                ),
                "observed_at": observation.observed_at.isoformat(),
                "source_provider": observation.source_provider,
            }
        )
    return rows


def _context(report: RWAAuditReport) -> dict[str, object]:
    identity = report.identity
    return {
        "format": RWA_AUDIT_FORMAT,
        "format_version": RWA_AUDIT_VERSION,
        "history_scope": "all_locally_saved_records",
        "chain": report.chain.value,
        "contract_address": AddressValidator.normalized(report.contract_address),
        "exported_at": report.exported_at.isoformat(),
        "issuer": identity.issuer if identity else None,
        "identity_source": identity.source_name if identity else None,
        "source_reference": identity.source_reference if identity else None,
        "audit_source_reference": "https://docs.robinhood.com/chain/stock-token-apis/"
        if report.chain is Chain.ROBINHOOD
        else None,
    }


def rwa_audit_json_bytes(report: RWAAuditReport) -> bytes:
    """Serialize deployment context and every saved audit record."""
    return json_dumps({**_context(report), "items": _rows(report)}, pretty=True)


def rwa_audit_csv_text(report: RWAAuditReport) -> str:
    """Write one row per action or multiplier observation with shared context."""
    context = _context(report)
    fields = (*context, *_FIELDS, "action_type")
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for row in _rows(report):
        details = row.get("details")
        if details is not None:
            row["details"] = json_dumps(details, pretty=False).decode("utf-8")
        writer.writerow({**context, **row})
    return output.getvalue()


def write_rwa_audit(report: RWAAuditReport, output_path: Path, *, as_json: bool) -> Path:
    """Validate and serialize before opening the destination file."""
    data = rwa_audit_json_bytes(report) if as_json else rwa_audit_csv_text(report).encode("utf-8")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(data)
    return output_path
