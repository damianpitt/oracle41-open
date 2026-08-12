"""Describe normalized wallet activity.

The models represent transfers, approvals, external calls, and paginated activity results across providers.
Provider-specific response fields must be converted before reaching this layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum

from oracle41_open.core.models.chain import Chain


class ActivityCategory(str, Enum):
    EXTERNAL = "external"
    ERC20 = "erc20"
    ERC721 = "erc721"
    ERC1155 = "erc1155"
    INTERNAL_TRANSFER = "internalTransfer"
    APPROVAL = "approval"


@dataclass(frozen=True)
class ActivityItem:
    block_number: int | None
    tx_hash: str
    log_index: str
    timestamp: datetime
    from_address: str
    to_address: str
    asset_symbol: str
    contract_address: str | None
    raw_value: str
    value_decimal: Decimal
    value_usd: Decimal | None
    is_verified: bool | None
    category: ActivityCategory
    chain: Chain

    @property
    def id(self) -> str:
        return f"{self.tx_hash}-{self.log_index}"

    def with_value_usd(self, value_usd: Decimal | None) -> ActivityItem:
        return ActivityItem(
            block_number=self.block_number,
            tx_hash=self.tx_hash,
            log_index=self.log_index,
            timestamp=self.timestamp,
            from_address=self.from_address,
            to_address=self.to_address,
            asset_symbol=self.asset_symbol,
            contract_address=self.contract_address,
            raw_value=self.raw_value,
            value_decimal=self.value_decimal,
            value_usd=value_usd,
            is_verified=self.is_verified,
            category=self.category,
            chain=self.chain,
        )


@dataclass(frozen=True)
class ActivityPage:
    items: list[ActivityItem]
    next_cursor: str | None
    source_provider: str | None = None
    query_from_block: int | None = None
    query_to_block: int | None = None
