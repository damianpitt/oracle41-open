"""Collect and reopen durable historical price evidence without touching current pricing.

Refresh is explicit and requires a configured source. Offline reads and point selection use only
SQLite. Every successful refresh gets a new fetch ID, preserving earlier source revisions for
reproducible reports. Future requests and future-point substitution are not permitted.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from oracle41_open.core.models import Chain, ProviderAuthError, ValidationError
from oracle41_open.core.models.historical_price import (
    HistoricalPriceBatch,
    HistoricalPricePoint,
    HistoricalPriceQuery,
    HistoricalPriceSelection,
    utc_time,
)
from oracle41_open.storage.db.historical_price_repository import HistoricalPriceRepository


class HistoricalPriceProvider(Protocol):
    source_provider: str
    source_reference: str

    def fetch(self, query: HistoricalPriceQuery) -> tuple[HistoricalPricePoint, ...]: ...


class HistoricalPriceService:
    def __init__(
        self,
        repository: HistoricalPriceRepository,
        provider: HistoricalPriceProvider | None = None,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._provider = provider
        self._now = now_func or (lambda: datetime.now(tz=UTC))

    @property
    def can_refresh(self) -> bool:
        return self._provider is not None

    def refresh(self, query: HistoricalPriceQuery) -> HistoricalPriceBatch:
        """Validate and append a whole response; never replace previously saved observations."""
        if query.end > utc_time(self._now()):
            raise ValidationError("Historical price requests cannot end in the future.")
        if self._provider is None:
            raise ProviderAuthError(
                "Enable Alchemy and configure its key in Settings to fetch price history."
            )
        points = self._provider.fetch(query)
        batch = HistoricalPriceBatch(
            str(uuid4()),
            query,
            self._provider.source_provider,
            self._provider.source_reference,
            self._now(),
            tuple(sorted(points, key=lambda point: point.timestamp)),
        )
        self._repository.save(batch)
        return batch

    def load_saved(self, query: HistoricalPriceQuery) -> HistoricalPriceBatch | None:
        """Read the newest saved response for the exact range, even without any credentials."""
        return self._repository.latest(query)

    def get_fetch(self, fetch_id: str) -> HistoricalPriceBatch | None:
        return self._repository.get(fetch_id)

    def select(
        self,
        chain: Chain,
        contract_address: str,
        at: datetime,
        *,
        max_age_seconds: int = 86_400,
        known_by: datetime | None = None,
    ) -> HistoricalPriceSelection:
        """Return a prior-or-exact point with an explicit maximum-age policy, never a live quote."""
        return self._repository.select(
            chain, contract_address, at, max_age_seconds=max_age_seconds, known_by=known_by
        )
