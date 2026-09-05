"""Share exact-block contract-read helpers between protocol collectors.

Protocol collectors use this module to encode read-only EVM calls, decode strict ABI responses,
and keep every successful read in one snapshot on the same chain, block, and provider. The storage
protocol describes only the checkpoint and finished-snapshot operations needed during collection.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from eth_abi.abi import decode as abi_decode
from eth_abi.abi import encode as abi_encode
from eth_abi.exceptions import DecodingError
from eth_utils.crypto import keccak

from oracle41_open.core.models import (
    Chain,
    ContractReadResult,
    ProtocolAdapterResult,
    ProtocolCollectionCheckpoint,
    ProtocolEvidenceValue,
    ProviderResponseError,
    StoredProtocolSnapshot,
)


class ContractStateReader(Protocol):
    """Read one contract at an explicit chain block."""

    def read_contract(
        self,
        contract_address: str,
        call_data: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        ...


class ProtocolSnapshotRepository(Protocol):
    """Describe durable methods shared by protocol collectors."""

    def get_snapshot(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
    ) -> StoredProtocolSnapshot | None:
        ...

    def get_checkpoint(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
    ) -> ProtocolCollectionCheckpoint | None:
        ...

    def save_checkpoint(self, checkpoint: ProtocolCollectionCheckpoint) -> None:
        ...

    def save_snapshot(
        self,
        wallet_address: str,
        chain: Chain,
        protocol_id: str,
        block_number: int,
        result: ProtocolAdapterResult,
        source_provider: str,
        observed_at: datetime,
    ) -> StoredProtocolSnapshot:
        ...


class SnapshotReadTracker:
    """Require all successful calls in one snapshot to use the same provider."""

    def __init__(
        self,
        provider: ContractStateReader,
        chain: Chain,
        block_number: int,
        source_provider: str | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        self._provider = provider
        self._chain = chain
        self.block_number = block_number
        self._source_provider = source_provider
        self._observed_at = observed_at

    @property
    def source_provider(self) -> str:
        if self._source_provider is None:
            raise ProviderResponseError("Protocol snapshot did not complete any contract reads.")
        return self._source_provider

    @property
    def observed_at(self) -> datetime:
        if self._observed_at is None:
            raise ProviderResponseError("Protocol snapshot has no observation time.")
        return self._observed_at

    def read(self, contract: str, data: str) -> ContractReadResult:
        result = self._provider.read_contract(
            contract,
            data,
            self._chain,
            self.block_number,
        )
        if (
            result.chain is not self._chain
            or result.contract_address.lower() != contract.lower()
            or result.block_number != self.block_number
        ):
            raise ProviderResponseError(
                "Contract provider returned a result outside the snapshot context."
            )
        if self._source_provider is not None and result.source_provider != self._source_provider:
            raise ProviderResponseError(
                "Protocol snapshot reads changed provider; retry to keep provenance consistent."
            )
        self._source_provider = result.source_provider
        if self._observed_at is None or result.fetched_at > self._observed_at:
            self._observed_at = result.fetched_at
        return result


def call_data(
    signature: str,
    input_types: tuple[str, ...] = (),
    values: tuple[object, ...] = (),
) -> str:
    """Encode a function selector and its ABI arguments for ``eth_call``."""
    selector = keccak(text=signature)[:4]
    arguments = abi_encode(input_types, values) if input_types else b""
    return "0x" + (selector + arguments).hex()


def decode_call_result(
    data: str,
    output_types: tuple[str, ...],
    operation: str,
) -> tuple[object, ...]:
    """Decode one strict ABI result and map malformed data to a provider error."""
    try:
        raw = bytes.fromhex(data.removeprefix("0x"))
        return tuple(abi_decode(output_types, raw, strict=True))
    except (DecodingError, ValueError) as error:
        raise ProviderResponseError(f"{operation} returned malformed ABI data.") from error


def evidence_values(values: dict[str, str]) -> tuple[ProtocolEvidenceValue, ...]:
    """Convert a mapping to stable, immutable evidence values."""
    return tuple(
        ProtocolEvidenceValue(name=name, value=value)
        for name, value in sorted(values.items())
    )
