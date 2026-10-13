"""Coordinate verified xStocks history collection and durable local browsing.

The provider must finish its bounded pagination before storage is updated. Saved issuer history
remains available after network failures, and revisions are retained for later inspection and export.
"""

from __future__ import annotations

from typing import Protocol

from oracle41_open.core.models import Chain
from oracle41_open.core.models.xstocks_audit import XStocksAuditContext
from oracle41_open.storage.db.xstocks_audit_repository import XStocksAuditRepository


class XStocksHistoryProvider(Protocol):
    def get_audit_history(
        self, chain: Chain, contract_address: str
    ) -> XStocksAuditContext | None: ...


class XStocksAuditService:
    def __init__(
        self, provider: XStocksHistoryProvider, repository: XStocksAuditRepository
    ) -> None:
        self._provider = provider
        self._repository = repository

    def refresh(self, chain: Chain, contract_address: str) -> XStocksAuditContext | None:
        """Save a fully validated issuer response and return the retained local history."""
        history = self._provider.get_audit_history(chain, contract_address)
        if history is None:
            return None
        if (
            history.identity.chain is not chain
            or history.identity.contract_address.lower() != contract_address.lower()
        ):
            raise ValueError("xStocks audit response belongs to another deployment.")
        self._repository.save(history)
        return self.load(chain, contract_address)

    def load(self, chain: Chain, contract_address: str) -> XStocksAuditContext | None:
        return self._repository.load(chain, contract_address)
