from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from oracle41_open.core.models import Chain, ProviderCapabilities, TransactionInspection
from oracle41_open.providers.transaction_provider import TransactionDataProvider


class TransactionInspectionStore(Protocol):
    def save_inspection(self, inspection: TransactionInspection) -> None:
        ...

    def get_inspection(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionInspection | None:
        ...


@dataclass(frozen=True)
class TransactionInspectionResult:
    inspection: TransactionInspection
    is_cached: bool


class TransactionInspectionService:
    def __init__(
        self,
        provider: TransactionDataProvider,
        repository: TransactionInspectionStore,
    ) -> None:
        self._provider = provider
        self._repository = repository

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        return self._provider.capabilities(chain)

    def inspect(
        self,
        tx_hash: str,
        chain: Chain,
        force_refresh: bool = False,
    ) -> TransactionInspectionResult:
        if not force_refresh:
            cached = self._repository.get_inspection(chain, tx_hash)
            if cached is not None:
                return TransactionInspectionResult(inspection=cached, is_cached=True)

        inspection = self._provider.get_transaction_inspection(tx_hash, chain)
        self._repository.save_inspection(inspection)
        return TransactionInspectionResult(inspection=inspection, is_cached=False)
