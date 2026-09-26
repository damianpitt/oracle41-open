"""Describe verified tokenized real-world assets and their source evidence.

Recognition is based on an exact chain and contract address match. Symbols and names are display
metadata only and never prove identity. These models are shared by live issuer catalogs, reviewed
offline registries, storage, and the Token Detail view.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from oracle41_open.core.models.chain import Chain


class RealWorldAssetCategory(str, Enum):
    """Group supported RWA products without making investment or legal claims."""

    STOCK_TOKEN = "stock_token"
    TOKENIZED_EQUITY = "tokenized_equity"
    TOKENIZED_ETF = "tokenized_etf"
    TOKENIZED_FUND = "tokenized_fund"


@dataclass(frozen=True)
class RealWorldAssetIdentity:
    """Record one issuer-verified asset deployment."""

    asset_id: str
    symbol: str
    name: str
    chain: Chain
    contract_address: str
    category: RealWorldAssetCategory
    issuer: str
    source_name: str
    source_reference: str
    underlying_symbol: str | None = None
    underlying_isin: str | None = None

