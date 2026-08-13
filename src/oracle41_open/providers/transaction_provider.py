"""Define the transaction inspection provider contract.

The protocol covers receipts, internal traces, proxy resolution, revert replay, and chain-specific capabilities.
Transaction services remain independent of JSON-RPC dialects and provider vendors.
"""

from __future__ import annotations

from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    ProviderCapabilities,
    ProxyResolution,
    TransactionInspection,
    TransactionTrace,
)


class TransactionDataProvider(Protocol):
    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        ...

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        ...

    def resolve_proxy(
        self,
        contract_address: str,
        chain: Chain,
        block_number: int,
    ) -> ProxyResolution:
        ...

    def get_revert_data(self, inspection: TransactionInspection) -> str | None:
        ...

    def get_transaction_trace(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionTrace:
        ...
