"""Describe provider-observed block identity and hash-pinned contract reads.

References bind a chain, number, hash, parent, and UTC timestamp to one source. They are node
assertions, not independent consensus proofs. Changed-reference errors stop collection rather
than allowing failover to hide a possible reorganization or inconsistent endpoint.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from oracle41_open.core.models.chain import Chain
from oracle41_open.core.models.errors import ProviderResponseError, ValidationError
from oracle41_open.core.models.historical_price import utc_time


class BlockReferenceChangedError(ProviderResponseError):
    """The provider changed canonical identity during one collection."""


@dataclass(frozen=True)
class BlockReference:
    chain: Chain
    number: int
    block_hash: str
    parent_hash: str
    timestamp: datetime
    source_provider: str
    fetched_at: datetime

    def __post_init__(self) -> None:
        if (
            isinstance(self.number, bool)
            or not isinstance(self.number, int)
            or not 0 <= self.number < 2**63
        ):
            raise ValidationError("Invalid block number in historical reference.")
        for field in ("block_hash", "parent_hash"):
            value = getattr(self, field).lower()
            if not re.fullmatch(r"0x[0-9a-f]{64}", value):
                raise ValidationError("Invalid hash in historical block reference.")
            object.__setattr__(self, field, value)
        if self.block_hash == "0x" + "00" * 32:
            raise ValidationError("A historical block hash cannot be zero.")
        object.__setattr__(self, "timestamp", utc_time(self.timestamp))
        object.__setattr__(self, "fetched_at", utc_time(self.fetched_at))
        if self.timestamp.timestamp() < 0 or not self.source_provider.strip():
            raise ValidationError("Invalid timestamp or source in historical block reference.")


@dataclass(frozen=True)
class HashPinnedContractRead:
    chain: Chain
    contract_address: str
    block_number: int
    block_hash: str
    data: str
    source_provider: str
    fetched_at: datetime


@dataclass(frozen=True)
class BlockReferenceCheck:
    check_id: str
    report_id: str
    checked_at: datetime
    document_json: str
