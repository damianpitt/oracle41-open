"""Describe historical price requests and immutable source observations.

Each price belongs to an exact chain and token contract, a provider timestamp, and a saved fetch.
Daily observations are not transaction execution prices. These models reject ambiguous time zones,
non-finite amounts, and oversized requests before data reaches a provider or SQLite.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from oracle41_open.core.models.chain import Chain
from oracle41_open.core.models.errors import ValidationError


def utc_time(value: datetime) -> datetime:
    """Require an explicit time zone so local machine settings cannot change a report."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("Historical prices require timestamps with an explicit time zone.")
    return value.astimezone(UTC)


def time_text(value: datetime) -> str:
    """Use one sortable UTC representation in database keys and reports."""
    return utc_time(value).isoformat(timespec="microseconds")


def token_address(value: str) -> str:
    normalized = value.strip().lower()
    if not re.fullmatch(r"0x[0-9a-f]{40}", normalized) or normalized == "0x" + "0" * 40:
        raise ValidationError("Enter a valid nonzero token contract address.")
    return normalized


@dataclass(frozen=True)
class HistoricalPriceQuery:
    """Bound one daily USD request to at most 30 days for an exact token deployment."""

    chain: Chain
    contract_address: str
    start: datetime
    end: datetime
    interval: str = "1d"

    def __post_init__(self) -> None:
        object.__setattr__(self, "contract_address", token_address(self.contract_address))
        object.__setattr__(self, "start", utc_time(self.start))
        object.__setattr__(self, "end", utc_time(self.end))
        if self.interval != "1d":
            raise ValidationError("This release collects daily historical prices only.")
        if not timedelta(0) < self.end - self.start <= timedelta(days=30):
            raise ValidationError(
                "Choose a historical price range greater than zero and at most 30 days."
            )


@dataclass(frozen=True)
class HistoricalPricePoint:
    timestamp: datetime
    value_usd: Decimal

    def __post_init__(self) -> None:
        object.__setattr__(self, "timestamp", utc_time(self.timestamp))
        if (
            not isinstance(self.value_usd, Decimal)
            or not self.value_usd.is_finite()
            or self.value_usd < 0
        ):
            raise ValidationError("Historical USD prices must be finite, nonnegative decimals.")


@dataclass(frozen=True)
class HistoricalPriceBatch:
    """Pin a request and its exact observations to a durable, exportable fetch ID."""

    fetch_id: str
    query: HistoricalPriceQuery
    source_provider: str
    source_reference: str
    fetched_at: datetime
    points: tuple[HistoricalPricePoint, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "fetched_at", utc_time(self.fetched_at))
        if not self.fetch_id or not re.fullmatch(r"[a-z0-9_-]{1,64}", self.source_provider):
            raise ValidationError("Historical price evidence requires a fetch ID and source name.")
        if len(self.points) > 32:
            raise ValidationError("Historical price response exceeded the bounded daily range.")
        seen: set[datetime] = set()
        for point in self.points:
            if point.timestamp in seen or not self.query.start <= point.timestamp <= self.query.end:
                raise ValidationError(
                    "Historical price timestamps are duplicated or outside the request."
                )
            if point.timestamp > self.fetched_at:
                raise ValidationError(
                    "Historical price evidence cannot contain future observations."
                )
            seen.add(point.timestamp)


@dataclass(frozen=True)
class HistoricalPriceSelection:
    """Explain an exact, prior, stale, or missing point without inventing a substitute price."""

    requested_at: datetime
    max_age_seconds: int
    status: str
    fetch_id: str | None = None
    source_provider: str | None = None
    point: HistoricalPricePoint | None = None

    @property
    def usable_value_usd(self) -> Decimal | None:
        return (
            self.point.value_usd
            if self.point is not None and self.status in {"exact", "prior"}
            else None
        )
