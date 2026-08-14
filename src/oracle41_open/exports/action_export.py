"""Write normalized wallet actions to versioned CSV and JSON reports.

JSON keeps participants, assets, and evidence as structured lists.
CSV uses stable top-level columns and JSON text for the same nested values so no provenance is lost.
"""

from __future__ import annotations

import csv
from io import StringIO
from pathlib import Path

from oracle41_open._json import dumps as json_dumps
from oracle41_open.core.models import WalletAction

ACTION_EXPORT_FORMAT = "oracle41-wallet-actions"
ACTION_EXPORT_FORMAT_VERSION = 1

_ACTION_FIELDS = (
    "chain",
    "tx_hash",
    "action_index",
    "kind",
    "status",
    "summary",
    "protocol_hint",
    "confidence",
    "normalizer_version",
    "participants",
    "assets",
    "evidence",
)


def wallet_actions_json_bytes(
    actions: tuple[WalletAction, ...],
    pretty: bool = True,
) -> bytes:
    payload = {
        "format": ACTION_EXPORT_FORMAT,
        "format_version": ACTION_EXPORT_FORMAT_VERSION,
        "fields": list(_ACTION_FIELDS),
        "items": [_action_dict(action) for action in actions],
    }
    return json_dumps(payload, pretty=pretty)


def write_wallet_actions_json(
    actions: tuple[WalletAction, ...],
    output_path: Path,
    pretty: bool = True,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(wallet_actions_json_bytes(actions, pretty=pretty))
    return output_path


def wallet_actions_csv_text(actions: tuple[WalletAction, ...]) -> str:
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(_ACTION_FIELDS)
    for action in actions:
        item = _action_dict(action)
        writer.writerow(
            [
                json_dumps(item[field], pretty=False).decode("utf-8")
                if field in {"participants", "assets", "evidence"}
                else item[field]
                for field in _ACTION_FIELDS
            ]
        )
    return output.getvalue()


def write_wallet_actions_csv(
    actions: tuple[WalletAction, ...],
    output_path: Path,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(wallet_actions_csv_text(actions), encoding="utf-8")
    return output_path


def _action_dict(action: WalletAction) -> dict[str, object]:
    return {
        "chain": action.chain.value,
        "tx_hash": action.tx_hash,
        "action_index": action.action_index,
        "kind": action.kind.value,
        "status": action.status.value,
        "summary": action.summary,
        "protocol_hint": action.protocol_hint,
        "confidence": action.confidence.value,
        "normalizer_version": action.normalizer_version,
        "participants": [
            {"role": item.role, "address": item.address} for item in action.participants
        ],
        "assets": [
            {
                "direction": item.direction.value,
                "standard": item.standard,
                "contract_address": item.contract_address,
                "symbol": item.symbol,
                "token_id": item.token_id,
                "raw_amount": item.raw_amount,
            }
            for item in action.assets
        ],
        "evidence": [
            {
                "kind": item.kind.value,
                "reference": item.reference,
                "contract_address": item.contract_address,
                "signature": item.signature,
                "source_id": item.source_id,
            }
            for item in action.evidence
        ],
    }
