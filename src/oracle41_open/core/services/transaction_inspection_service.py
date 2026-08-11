from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    ProviderCapabilities,
    TransactionDecoding,
    TransactionInspection,
)
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
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

    def save_decoding(
        self,
        chain: Chain,
        tx_hash: str,
        decoding: TransactionDecoding,
    ) -> None:
        ...

    def get_decoding(
        self,
        chain: Chain,
        tx_hash: str,
    ) -> TransactionDecoding | None:
        ...


class TransactionDecoder(Protocol):
    version: str

    def decode(self, inspection: TransactionInspection) -> TransactionDecoding:
        ...


@dataclass(frozen=True)
class TransactionInspectionResult:
    inspection: TransactionInspection
    decoding: TransactionDecoding
    is_cached: bool


class TransactionInspectionService:
    def __init__(
        self,
        provider: TransactionDataProvider,
        repository: TransactionInspectionStore,
        decoder: TransactionDecoder | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._decoder = decoder or StandardABIDecoder()

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
                decoding = self._repository.get_decoding(chain, tx_hash)
                if decoding is None or decoding.decoder_version != self._decoder.version:
                    decoding = self._decoder.decode(cached)
                    self._repository.save_decoding(chain, tx_hash, decoding)
                return TransactionInspectionResult(
                    inspection=cached,
                    decoding=decoding,
                    is_cached=True,
                )

        inspection = self._provider.get_transaction_inspection(tx_hash, chain)
        self._repository.save_inspection(inspection)
        decoding = self._decoder.decode(inspection)
        self._repository.save_decoding(chain, tx_hash, decoding)
        return TransactionInspectionResult(
            inspection=inspection,
            decoding=decoding,
            is_cached=False,
        )
