"""Define supported EVM networks and their public connection metadata.

The registry is the single source for chain IDs, native assets, explorers, and provider network names.
Provider capability declarations remain separate so a known network is not automatically presented as supported by every data source.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class Chain(str, Enum):
    """Stable chain values stored in settings, SQLite records, and exports."""

    ETHEREUM = "ethereum"
    OPTIMISM = "optimism"
    POLYGON = "polygon"
    BASE = "base"
    ARBITRUM = "arbitrum"
    ROBINHOOD = "robinhood"

    @property
    def network(self) -> NetworkDescriptor:
        """Return the immutable registry entry for this chain."""

        return NETWORK_REGISTRY[self]

    @property
    def display_name(self) -> str:
        return self.network.display_name

    @property
    def native_symbol(self) -> str:
        return self.network.native_symbol

    @property
    def native_pricing_symbol(self) -> str:
        return self.network.native_pricing_symbol

    @property
    def alchemy_network_path(self) -> str:
        """Return the Alchemy Node API network name."""

        value = self.network.alchemy_network_path
        if value is None:
            raise ValueError(f"Alchemy is not configured for {self.display_name}.")
        return value

    @property
    def ankr_rpc_path(self) -> str:
        """Return the Ankr RPC path for a supported chain."""

        value = self.network.ankr_rpc_path
        if value is None:
            raise ValueError(f"Ankr is not configured for {self.display_name}.")
        return value

    @property
    def ankr_blockchain_code(self) -> str:
        """Return the Ankr Advanced API chain code."""

        value = self.network.ankr_blockchain_code
        if value is None:
            raise ValueError(f"Ankr is not configured for {self.display_name}.")
        return value


@dataclass(frozen=True)
class NetworkDescriptor:
    """Store public identifiers used to connect to one EVM mainnet."""

    chain: Chain
    chain_id: int
    display_name: str
    native_symbol: str
    native_pricing_symbol: str
    explorer_url: str
    public_rpc_url: str | None
    alchemy_network_path: str | None
    alchemy_pricing_network_path: str | None
    ankr_rpc_path: str | None
    ankr_blockchain_code: str | None
    moralis_chain_code: str | None
    goldrush_chain_name: str | None


def _network(
    chain: Chain,
    chain_id: int,
    display_name: str,
    native_symbol: str,
    explorer_url: str,
    alchemy_network_path: str,
    ankr_path: str,
    moralis_code: str,
    goldrush_name: str,
) -> NetworkDescriptor:
    """Build a descriptor for the five established provider-compatible chains."""

    return NetworkDescriptor(
        chain=chain,
        chain_id=chain_id,
        display_name=display_name,
        native_symbol=native_symbol,
        native_pricing_symbol=native_symbol,
        explorer_url=explorer_url,
        public_rpc_url=None,
        alchemy_network_path=alchemy_network_path,
        alchemy_pricing_network_path=alchemy_network_path,
        ankr_rpc_path=ankr_path,
        ankr_blockchain_code=ankr_path,
        moralis_chain_code=moralis_code,
        goldrush_chain_name=goldrush_name,
    )


NETWORK_REGISTRY: Mapping[Chain, NetworkDescriptor] = MappingProxyType({
    Chain.ETHEREUM: _network(
        Chain.ETHEREUM, 1, "Ethereum", "ETH", "https://eth.blockscout.com",
        "eth-mainnet", "eth", "eth", "eth-mainnet",
    ),
    Chain.OPTIMISM: _network(
        Chain.OPTIMISM, 10, "Optimism", "ETH", "https://optimism.blockscout.com",
        "opt-mainnet", "optimism", "optimism", "optimism-mainnet",
    ),
    Chain.POLYGON: _network(
        Chain.POLYGON, 137, "Polygon", "MATIC", "https://polygon.blockscout.com",
        "polygon-mainnet", "polygon", "polygon", "matic-mainnet",
    ),
    Chain.BASE: _network(
        Chain.BASE, 8453, "Base", "ETH", "https://base.blockscout.com",
        "base-mainnet", "base", "base", "base-mainnet",
    ),
    Chain.ARBITRUM: _network(
        Chain.ARBITRUM, 42161, "Arbitrum", "ETH", "https://arbitrum.blockscout.com",
        "arb-mainnet", "arbitrum", "arbitrum", "arbitrum-mainnet",
    ),
    Chain.ROBINHOOD: NetworkDescriptor(
        chain=Chain.ROBINHOOD,
        chain_id=4663,
        display_name="Robinhood Chain",
        native_symbol="ETH",
        native_pricing_symbol="ETH",
        explorer_url="https://robinhoodchain.blockscout.com",
        public_rpc_url="https://rpc.mainnet.chain.robinhood.com",
        alchemy_network_path="robinhood-mainnet",
        # Token-by-address price support has not been confirmed for this network.
        alchemy_pricing_network_path=None,
        ankr_rpc_path=None,
        ankr_blockchain_code=None,
        moralis_chain_code=None,
        goldrush_chain_name="robinhood-mainnet",
    ),
})


def network_descriptor(chain: Chain) -> NetworkDescriptor:
    """Return one registered network without exposing the registry mapping."""

    return NETWORK_REGISTRY[chain]


def network_descriptors() -> tuple[NetworkDescriptor, ...]:
    """Return all registered networks in the same stable order as ``Chain``."""

    return tuple(NETWORK_REGISTRY[chain] for chain in Chain)
