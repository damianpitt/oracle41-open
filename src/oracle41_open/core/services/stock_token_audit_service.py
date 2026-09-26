"""Refresh and read the durable Robinhood Stock Token audit trail.

The service keeps provider access separate from SQLite persistence. A refresh records the current
multiplier only when its material state changed, updates corporate-action lifecycle data, and then
returns the newest locally stored audit records for the selected contract.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    StockTokenAuditContext,
    StockTokenCorporateAction,
    StockTokenMetadata,
)
from oracle41_open.storage.db.stock_token_audit_repository import StockTokenAuditRepository


class StockTokenAuditProvider(Protocol):
    def get_corporate_actions(
        self,
        contract_address: str | None = None,
    ) -> tuple[StockTokenCorporateAction, ...]:
        ...


class StockTokenAuditService:
    """Coordinate issuer history with the local audit repository."""

    def __init__(
        self,
        provider: StockTokenAuditProvider,
        repository: StockTokenAuditRepository,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._now = now_func or (lambda: datetime.now(tz=UTC))

    def refresh(
        self,
        metadata: StockTokenMetadata,
        limit: int = 20,
    ) -> StockTokenAuditContext:
        """Persist the latest official state and return local history."""

        observed_at = self._now().astimezone(UTC)
        self._repository.record_multiplier(
            metadata,
            observed_at,
            source_provider="robinhood-stock-token-api",
        )
        actions = self._provider.get_corporate_actions(metadata.contract_address)
        self._repository.save_corporate_actions(actions, observed_at)
        return self.load(metadata.contract_address, limit=limit)

    def load(self, contract_address: str, limit: int = 20) -> StockTokenAuditContext:
        """Read stored audit history without contacting the provider."""

        return StockTokenAuditContext(
            corporate_actions=self._repository.list_corporate_actions(
                Chain.ROBINHOOD,
                contract_address,
                limit=limit,
            ),
            multiplier_history=self._repository.list_multiplier_history(
                Chain.ROBINHOOD,
                contract_address,
                limit=limit,
            ),
        )

