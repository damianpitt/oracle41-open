"""Describe optional transaction context supplied by a block explorer.

Enrichment records add readable names, creation details, and explorer-decoded method context.
They keep source links and verification state, and never replace JSON-RPC receipts or local decoding.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from oracle41_open.core.models.chain import Chain


class EnrichmentStatus(str, Enum):
    AVAILABLE = "available"
    NOT_FOUND = "not_found"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class ExplorerCapabilities:
    transaction_context: bool
    contract_context: bool


@dataclass(frozen=True)
class ExplorerAddressContext:
    address: str
    name: str | None
    implementation_name: str | None
    ens_name: str | None
    is_contract: bool | None
    is_verified: bool | None
    creator_address: str | None
    creation_tx_hash: str | None
    source_reference: str


@dataclass(frozen=True)
class ExplorerDecodedParameter:
    name: str
    type_name: str
    value: str
    indexed: bool | None = None


@dataclass(frozen=True)
class TransactionEnrichment:
    chain: Chain
    tx_hash: str
    status: EnrichmentStatus
    method_name: str | None
    transaction_types: tuple[str, ...]
    decoded_method_call: str | None
    decoded_method_id: str | None
    decoded_parameters: tuple[ExplorerDecodedParameter, ...]
    target_context: ExplorerAddressContext | None
    created_contract_context: ExplorerAddressContext | None
    source_name: str
    source_version: str
    source_reference: str | None
    fetched_at: datetime
    error: str | None = None
