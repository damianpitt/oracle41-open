"""Export one pinned historical-price fetch as versioned CSV or JSON.

Reports preserve the exact request, source, fetch ID, provider timestamps, and decimal price text.
An empty CSV includes a status row instead of inventing a zero-dollar observation. No credential,
private request URL, wallet balance, or accounting result belongs in this report.
"""

from __future__ import annotations

import csv
from io import StringIO

from oracle41_open._json import dumps
from oracle41_open.core.models.historical_price import HistoricalPriceBatch, time_text


def _context(batch: HistoricalPriceBatch) -> dict[str, object]:
    return {
        "format": "oracle41-historical-prices",
        "format_version": 1,
        "fetch_id": batch.fetch_id,
        "chain": batch.query.chain.value,
        "contract_address": batch.query.contract_address,
        "currency": "usd",
        "interval": batch.query.interval,
        "start": time_text(batch.query.start),
        "end": time_text(batch.query.end),
        "source_provider": batch.source_provider,
        "source_reference": batch.source_reference,
        "fetched_at": time_text(batch.fetched_at),
        "point_count": len(batch.points),
        "status": "observations" if batch.points else "empty",
        "scope": "one_saved_fetch_not_complete_market_history",
    }


def historical_price_json_bytes(batch: HistoricalPriceBatch) -> bytes:
    return dumps(
        {
            **_context(batch),
            "items": [
                {"price_at": time_text(point.timestamp), "value_usd": str(point.value_usd)}
                for point in batch.points
            ],
        },
        pretty=True,
    )


def historical_price_csv_text(batch: HistoricalPriceBatch) -> str:
    context = _context(batch)
    output = StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=[*context, "record_type", "price_at", "value_usd"])
    writer.writeheader()
    if not batch.points:
        writer.writerow({**context, "record_type": "fetch_status"})
    for point in batch.points:
        writer.writerow(
            {
                **context,
                "record_type": "price",
                "price_at": time_text(point.timestamp),
                "value_usd": str(point.value_usd),
            }
        )
    return output.getvalue()
