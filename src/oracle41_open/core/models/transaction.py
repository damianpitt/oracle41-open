from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from oracle41_open.core.models.chain import Chain


@dataclass(frozen=True)
class ProviderCapabilities:
    transaction_lookup: bool
    receipts: bool
    traces: bool | None = None
    archive_queries: bool | None = None


@dataclass(frozen=True)
class RawTransactionLog:
    log_index: int
    address: str
    topics: tuple[str, ...]
    data: str
    removed: bool


@dataclass(frozen=True)
class TransactionInspection:
    chain: Chain
    tx_hash: str
    block_number: int
    block_hash: str
    transaction_index: int
    from_address: str
    to_address: str | None
    contract_address: str | None
    nonce: int
    value_wei: int
    input_data: str
    gas_limit: int
    gas_price: int | None
    max_fee_per_gas: int | None
    max_priority_fee_per_gas: int | None
    status: bool | None
    gas_used: int
    cumulative_gas_used: int
    effective_gas_price: int
    transaction_type: int | None
    logs_bloom: str
    logs: tuple[RawTransactionLog, ...]
    source_provider: str
    fetched_at: datetime

    @property
    def fee_wei(self) -> int:
        return self.gas_used * self.effective_gas_price

    @property
    def fee_native(self) -> Decimal:
        return Decimal(self.fee_wei) / Decimal(10**18)
