"""Collect xStocks issuer history in durable, bounded, resumable runs.

Validated pages and continuation positions are staged together. A run can pause between pages or
resume after restart. Only two finished feeds are published as normal audit history. The previous
published history remains readable while a new run is pending, and progress is available offline.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from typing import Protocol

from oracle41_open.core.models import Chain, ProviderError, RealWorldAssetIdentity
from oracle41_open.core.models.xstocks_audit import (
    XStocksAuditContext,
    XStocksHistoryPage,
    XStocksSyncProgress,
)
from oracle41_open.storage.db.xstocks_audit_repository import XStocksAuditRepository
from oracle41_open.storage.db.xstocks_sync_repository import XStocksSyncRepository


class XStocksHistoryProvider(Protocol):
    def get_asset_identity(
        self, chain: Chain, contract_address: str
    ) -> RealWorldAssetIdentity | None: ...

    def get_history_page(
        self,
        identity: RealWorldAssetIdentity,
        feed: str,
        page: int,
    ) -> XStocksHistoryPage: ...


class XStocksAuditService:
    def __init__(
        self,
        provider: XStocksHistoryProvider,
        repository: XStocksAuditRepository,
        *,
        page_budget: int = 20,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._sync_repository = XStocksSyncRepository(repository.database)
        self._page_budget = max(1, page_budget)

    def refresh(
        self,
        chain: Chain,
        contract_address: str,
        *,
        restart: bool = False,
        cancel_requested: Callable[[], bool] | None = None,
        on_progress: Callable[[XStocksSyncProgress], None] | None = None,
    ) -> XStocksAuditContext | None:
        """Resume staged progress, processing at most the configured page budget per call."""
        identity = self._provider.get_asset_identity(chain, contract_address)
        if identity is None:
            return None
        if (
            identity.chain is not chain
            or identity.contract_address.lower() != contract_address.lower()
        ):
            raise ValueError("xStocks catalog identity belongs to another deployment.")
        progress = self._sync_repository.begin(identity, restart=restart)
        notify = on_progress or (lambda _: None)
        cancelled = cancel_requested or (lambda: False)
        notify(progress)
        try:
            for _ in range(self._page_budget):
                if cancelled() or progress.actions_done and progress.multipliers_done:
                    break
                feed = "actions" if not progress.actions_done else "multipliers"
                number = (
                    progress.actions_next_page
                    if feed == "actions"
                    else progress.multipliers_next_page
                )
                page = self._provider.get_history_page(identity, feed, number)
                if page.feed != feed or page.page != number:
                    raise ValueError("xStocks response belongs to another feed or page.")
                progress = self._sync_repository.stage_page(progress, page)
                notify(progress)
            if not cancelled() and progress.actions_done and progress.multipliers_done:
                self._sync_repository.publish(progress)
            else:
                progress = self._sync_repository.set_state(progress, "paused")
                notify(progress)
        except (ProviderError, ValueError):
            # A stale writer must not overwrite progress already advanced by another task.
            with suppress(ValueError):
                notify(self._sync_repository.set_state(progress, "failed"))
            raise
        return self.load(chain, contract_address)

    def load(self, chain: Chain, contract_address: str) -> XStocksAuditContext | None:
        """Load published history and pending progress without contacting the issuer."""
        progress = self._sync_repository.progress(chain, contract_address)
        history = self._repository.load(chain, contract_address)
        if history is not None:
            return replace(history, sync_progress=progress)
        if progress is not None:
            return XStocksAuditContext(progress.identity, (), (), None, progress)
        return None
