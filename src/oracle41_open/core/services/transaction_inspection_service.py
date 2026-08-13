"""Enrich canonical transactions with receipts, traces, and decoded details.

The service reuses durable raw data, retries temporary trace failures, resolves proxy implementations, and loads matching ABIs.
Decoder fingerprints trigger safe re-decoding of calls, logs, and reverts when an ABI changes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    ProviderCapabilities,
    ProviderError,
    ProxyKind,
    ProxyResolution,
    ProxyResolutionStatus,
    TraceStatus,
    TransactionDecoding,
    TransactionInspection,
    TransactionTrace,
)
from oracle41_open.core.services.abi_decoder import SignatureRegistry, StandardABIDecoder
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

    def save_trace(self, trace: TransactionTrace) -> None:
        ...

    def get_trace(self, chain: Chain, tx_hash: str) -> TransactionTrace | None:
        ...


class TransactionDecoder(Protocol):
    version: str

    def version_for(
        self,
        registries_by_address: Mapping[str, SignatureRegistry] | None = None,
    ) -> str:
        ...

    def decode(
        self,
        inspection: TransactionInspection,
        registries_by_address: Mapping[str, SignatureRegistry] | None = None,
        revert_data: str | None = None,
        implementation_address: str | None = None,
    ) -> TransactionDecoding:
        ...


class ContractABIRegistryProvider(Protocol):
    def registry_for(
        self,
        chain: Chain,
        contract_address: str,
    ) -> SignatureRegistry | None:
        ...


class ProxyResolutionStore(Protocol):
    def save_proxy_resolution(self, resolution: ProxyResolution) -> None:
        ...

    def get_proxy_resolution(
        self,
        chain: Chain,
        proxy_address: str,
        block_number: int,
    ) -> ProxyResolution | None:
        ...


@dataclass(frozen=True)
class TransactionInspectionResult:
    inspection: TransactionInspection
    decoding: TransactionDecoding
    is_cached: bool
    proxy_resolution: ProxyResolution | None = None
    trace: TransactionTrace | None = None


class TransactionInspectionService:
    def __init__(
        self,
        provider: TransactionDataProvider,
        repository: TransactionInspectionStore,
        decoder: TransactionDecoder | None = None,
        abi_registry_provider: ContractABIRegistryProvider | None = None,
        proxy_repository: ProxyResolutionStore | None = None,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._decoder = decoder or StandardABIDecoder()
        self._abi_registry_provider = abi_registry_provider
        self._proxy_repository = proxy_repository

    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        return self._provider.capabilities(chain)

    def inspect(
        self,
        tx_hash: str,
        chain: Chain,
        force_refresh: bool = False,
    ) -> TransactionInspectionResult:
        inspection: TransactionInspection | None = None
        is_cached = False
        if not force_refresh:
            inspection = self._repository.get_inspection(chain, tx_hash)
            is_cached = inspection is not None
        if inspection is None:
            inspection = self._provider.get_transaction_inspection(tx_hash, chain)
            self._repository.save_inspection(inspection)

        registries, proxy_resolution = self._load_contract_context(inspection)
        trace = self._load_trace(inspection, force_refresh)
        expected_decoder_version = self._decoder.version_for(registries)
        stored_decoding = self._repository.get_decoding(chain, tx_hash) if is_cached else None
        if (
            stored_decoding is not None
            and stored_decoding.decoder_version == expected_decoder_version
        ):
            return TransactionInspectionResult(
                inspection=inspection,
                decoding=stored_decoding,
                is_cached=True,
                proxy_resolution=proxy_resolution,
                trace=trace,
            )

        revert_data = None
        if stored_decoding is not None and stored_decoding.revert is not None:
            revert_data = stored_decoding.revert.raw_data
        elif inspection.status is False:
            try:
                revert_data = self._provider.get_revert_data(inspection)
            except ProviderError:
                revert_data = None
        implementation_address = (
            proxy_resolution.implementation_address
            if proxy_resolution is not None
            and proxy_resolution.status is ProxyResolutionStatus.RESOLVED
            else None
        )
        decoding = self._decoder.decode(
            inspection,
            registries_by_address=registries,
            revert_data=revert_data,
            implementation_address=implementation_address,
        )
        self._repository.save_decoding(chain, tx_hash, decoding)
        return TransactionInspectionResult(
            inspection=inspection,
            decoding=decoding,
            is_cached=is_cached,
            proxy_resolution=proxy_resolution,
            trace=trace,
        )

    def _load_trace(
        self,
        inspection: TransactionInspection,
        force_refresh: bool,
    ) -> TransactionTrace:
        stored = None if force_refresh else self._repository.get_trace(
            inspection.chain, inspection.tx_hash
        )
        # Temporary failures are diagnostic records, not durable capability results.
        if stored is not None and stored.status is not TraceStatus.UNAVAILABLE:
            return stored
        try:
            trace = self._provider.get_transaction_trace(
                inspection.tx_hash,
                inspection.chain,
            )
        except ProviderError as error:
            trace = TransactionTrace(
                chain=inspection.chain,
                tx_hash=inspection.tx_hash,
                status=TraceStatus.UNAVAILABLE,
                calls=(),
                raw_json=None,
                source_provider="unavailable",
                fetched_at=datetime.now(tz=UTC),
                error=str(error),
            )
        self._repository.save_trace(trace)
        return trace

    def _load_contract_context(
        self,
        inspection: TransactionInspection,
    ) -> tuple[dict[str, SignatureRegistry], ProxyResolution | None]:
        target = inspection.to_address
        if target is None or self._abi_registry_provider is None:
            return {}, None
        resolution = self._load_proxy_resolution(inspection, target)
        addresses = {target, *(log.address for log in inspection.logs)}
        implementation = (
            resolution.implementation_address
            if resolution is not None
            and resolution.status is ProxyResolutionStatus.RESOLVED
            else None
        )
        if implementation is not None:
            addresses.add(implementation)
        registries = {
            address: registry
            for address in addresses
            if (registry := self._abi_registry_provider.registry_for(inspection.chain, address))
            is not None
        }
        if implementation is not None and implementation in registries:
            implementation_registry = registries[implementation]
            proxy_registry = registries.get(target)
            registries[target] = (
                SignatureRegistry.combine(implementation_registry, proxy_registry)
                if proxy_registry is not None
                else implementation_registry
            )
        return registries, resolution

    def _load_proxy_resolution(
        self,
        inspection: TransactionInspection,
        target: str,
    ) -> ProxyResolution | None:
        if self._proxy_repository is None:
            return None
        cached = self._proxy_repository.get_proxy_resolution(
            inspection.chain,
            target,
            inspection.block_number,
        )
        # Resolution failures are transient; only immutable block-state results are reusable.
        if cached is not None and cached.status is not ProxyResolutionStatus.UNAVAILABLE:
            return cached
        try:
            resolution = self._provider.resolve_proxy(
                target,
                inspection.chain,
                inspection.block_number,
            )
        except ProviderError as error:
            resolution = ProxyResolution(
                chain=inspection.chain,
                proxy_address=target,
                status=ProxyResolutionStatus.UNAVAILABLE,
                proxy_kind=ProxyKind.NONE,
                implementation_address=None,
                block_number=inspection.block_number,
                source_provider=inspection.source_provider,
                resolved_at=datetime.now(tz=UTC),
                error=str(error),
            )
        self._proxy_repository.save_proxy_resolution(resolution)
        return resolution
