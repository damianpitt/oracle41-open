"""Define the EVM networks supported by Oracle41 Open.

Each chain provides display, native-asset, Alchemy, and Ankr identifiers used throughout the application.
Adding a chain here also requires provider and release validation work.
"""

from __future__ import annotations

from enum import Enum


class Chain(str, Enum):
    ETHEREUM = "ethereum"
    OPTIMISM = "optimism"
    POLYGON = "polygon"
    BASE = "base"
    ARBITRUM = "arbitrum"

    @property
    def display_name(self) -> str:
        mapping = {
            Chain.ETHEREUM: "Ethereum",
            Chain.OPTIMISM: "Optimism",
            Chain.POLYGON: "Polygon",
            Chain.BASE: "Base",
            Chain.ARBITRUM: "Arbitrum",
        }
        return mapping[self]

    @property
    def native_symbol(self) -> str:
        if self is Chain.POLYGON:
            return "MATIC"
        return "ETH"

    @property
    def native_pricing_symbol(self) -> str:
        return self.native_symbol

    @property
    def alchemy_network_path(self) -> str:
        mapping = {
            Chain.ETHEREUM: "eth-mainnet",
            Chain.OPTIMISM: "opt-mainnet",
            Chain.POLYGON: "polygon-mainnet",
            Chain.BASE: "base-mainnet",
            Chain.ARBITRUM: "arb-mainnet",
        }
        return mapping[self]

    @property
    def ankr_rpc_path(self) -> str:
        mapping = {
            Chain.ETHEREUM: "eth",
            Chain.OPTIMISM: "optimism",
            Chain.POLYGON: "polygon",
            Chain.BASE: "base",
            Chain.ARBITRUM: "arbitrum",
        }
        return mapping[self]

    @property
    def ankr_blockchain_code(self) -> str:
        return self.ankr_rpc_path
