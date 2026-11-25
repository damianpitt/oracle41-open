"""Export pinned historical holdings inputs without implying a complete portfolio.

JSON contains the full frozen report. CSV separates summary, anchors, price, raw ledger inputs,
applied movements, exclusions, fees, and checkpoint context. Nested evidence stays in JSON cells,
and raw integer or decimal values never pass through binary floating point.
"""

from __future__ import annotations

import csv
from io import StringIO

from oracle41_open._json import dumps
from oracle41_open.core.models.block_reference import BlockReferenceCheck
from oracle41_open.core.models.historical_holdings import HistoricalHoldingsReport


def historical_holdings_json_bytes(report: HistoricalHoldingsReport) -> bytes:
    return dumps(report.document, pretty=True)


def block_reference_check_json_bytes(check: BlockReferenceCheck) -> bytes:
    """Export a later observation separately, without changing the original report."""
    return check.document_json.encode("utf-8")


def historical_holdings_csv_text(report: HistoricalHoldingsReport) -> str:
    document = report.document
    inputs, result = document["inputs"], document["result"]
    request = inputs["request"]
    context = {
        "format": document["format"],
        "format_version": document["format_version"],
        "report_id": report.report_id,
        "created_at": document["created_at"],
        "wallet_address": request["wallet_address"],
        "chain": request["chain"],
        "contract_address": request["contract_address"],
        "opening_block": request["opening_block"],
        "closing_block": request["closing_block"],
        "requested_price_at": request["price_at"],
    }
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=[*context, "record_type", "details_json"])
    writer.writeheader()
    summary = {
        key: value
        for key, value in result.items()
        if key not in {"applied_movements", "excluded_events", "saved_native_fees"}
    }
    records = [
        ("summary", summary),
        ("opening_balance", inputs["opening"]),
        ("closing_balance", inputs["closing"]),
        ("price_input", inputs["price"]),
        ("request", request),
    ]
    for kind in ("timing", "finality"):
        if kind in inputs:
            records.append((kind, inputs[kind]))
    for kind, items in (
        ("ledger_event", inputs["ledger"]["events"]),
        ("checkpoint_context", inputs["ledger"]["checkpoints"]),
        ("saved_fee_input", inputs["ledger"]["fees"]),
        ("applied_movement", result["applied_movements"]),
        ("excluded_event", result["excluded_events"]),
        ("known_native_fee", result["saved_native_fees"]),
    ):
        records.extend((kind, item) for item in items)
    for kind, details in records:
        writer.writerow(
            {**context, "record_type": kind, "details_json": dumps(details).decode("utf-8")}
        )
    return output.getvalue()
