from __future__ import annotations

from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    ProviderCapabilities,
    ProxyResolution,
    TransactionInspection,
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
