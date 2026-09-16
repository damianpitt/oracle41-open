"""Describe raw transaction, receipt, and internal execution data.

The models preserve calldata, logs, gas fields, trace completeness, provider source, and provider capabilities.
ABI decoding is stored separately so raw blockchain and trace data are never replaced.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from oracle41_open.core.models.chain import Chain, TransactionFeeModel


@dataclass(frozen=True)
class ProviderCapabilities:
    transaction_lookup: bool
    receipts: bool
    traces: bool | None = None
    archive_queries: bool | None = None
    proxy_resolution: bool | None = None
    revert_replay: bool | None = None


@dataclass(frozen=True)
class ContractReadResult:
    """Preserve one block-specific contract call and its provider provenance."""

    chain: Chain
    contract_address: str
    block_number: int
    data: str
    source_provider: str
    fetched_at: datetime


@dataclass(frozen=True)
class RawTransactionLog:
    log_index: int
    address: str
    topics: tuple[str, ...]
    data: str
    removed: bool


class TraceStatus(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


class TraceDialect(str, Enum):
    DEBUG_CALL_TRACER = "debug_call_tracer"
    PARITY_TRACE = "parity_trace"


class FeeBreakdownCompleteness(str, Enum):
    COMPLETE = "complete"
    TOTAL_ONLY = "total_only"


@dataclass(frozen=True)
class TransactionFeeBreakdown:
    """Separate a paid transaction fee when the receipt exposes enough evidence."""

    fee_model: TransactionFeeModel
    completeness: FeeBreakdownCompleteness
    total_fee_wei: int
    execution_fee_wei: int | None
    l1_data_fee_wei: int | None
    total_gas_used: int
    execution_gas_used: int | None
    l1_data_gas_used: int | None
    note: str | None = None


@dataclass(frozen=True)
class InternalCall:
    trace_address: tuple[int, ...]
    depth: int
    call_type: str
    from_address: str | None
    to_address: str | None
    created_contract: str | None
    value_wei: int
    gas_limit: int | None
    gas_used: int | None
    input_data: str
    output_data: str
    error: str | None = None
    revert_reason: str | None = None


@dataclass(frozen=True)
class TransactionTrace:
    chain: Chain
    tx_hash: str
    status: TraceStatus
    calls: tuple[InternalCall, ...]
    raw_json: str | None
    source_provider: str
    fetched_at: datetime
    dialect: TraceDialect | None = None
    error: str | None = None


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
    l1_gas_used: int | None = None

    @property
    def fee_wei(self) -> int:
        return self.gas_used * self.effective_gas_price

    @property
    def fee_native(self) -> Decimal:
        return Decimal(self.fee_wei) / Decimal(10**18)

    @property
    def fee_breakdown(self) -> TransactionFeeBreakdown:
        """Return a chain-aware fee split without estimating missing receipt data."""

        fee_model = self.chain.network.fee_model
        if fee_model is not TransactionFeeModel.ARBITRUM_NITRO:
            return TransactionFeeBreakdown(
                fee_model=fee_model,
                completeness=FeeBreakdownCompleteness.COMPLETE,
                total_fee_wei=self.fee_wei,
                execution_fee_wei=self.fee_wei,
                l1_data_fee_wei=0,
                total_gas_used=self.gas_used,
                execution_gas_used=self.gas_used,
                l1_data_gas_used=0,
            )
        if self.l1_gas_used is None:
            return TransactionFeeBreakdown(
                fee_model=fee_model,
                completeness=FeeBreakdownCompleteness.TOTAL_ONLY,
                total_fee_wei=self.fee_wei,
                execution_fee_wei=None,
                l1_data_fee_wei=None,
                total_gas_used=self.gas_used,
                execution_gas_used=None,
                l1_data_gas_used=None,
                note="The receipt does not expose gasUsedForL1, so the bundled fee cannot be separated.",
            )
        execution_gas_used = self.gas_used - self.l1_gas_used
        return TransactionFeeBreakdown(
            fee_model=fee_model,
            completeness=FeeBreakdownCompleteness.COMPLETE,
            total_fee_wei=self.fee_wei,
            execution_fee_wei=execution_gas_used * self.effective_gas_price,
            l1_data_fee_wei=self.l1_gas_used * self.effective_gas_price,
            total_gas_used=self.gas_used,
            execution_gas_used=execution_gas_used,
            l1_data_gas_used=self.l1_gas_used,
        )
