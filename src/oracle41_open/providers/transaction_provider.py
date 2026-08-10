from __future__ import annotations

from typing import Protocol

from oracle41_open.core.models import Chain, ProviderCapabilities, TransactionInspection


class TransactionDataProvider(Protocol):
    def capabilities(self, chain: Chain) -> ProviderCapabilities:
        ...

    def get_transaction_inspection(
        self,
        tx_hash: str,
        chain: Chain,
    ) -> TransactionInspection:
        ...
