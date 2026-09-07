"""Describe validated wallet-data capabilities without creating clients.

Capabilities are recorded per chain instead of being inherited from the global chain list.
This prevents a newly registered network from being sent to a provider before its adapter has passed recorded-fixture validation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from oracle41_open.core.models import Chain


class WalletDataProviderId(str, Enum):
    """Stable IDs used by settings, routing, and provider-owned cursors."""

    ALCHEMY = "alchemy"
    ANKR = "ankr"
    MORALIS = "moralis"
    GOLDRUSH = "goldrush"


class ProviderAvailability(str, Enum):
    """Separate usable adapters from providers that are still planned."""

    AVAILABLE = "available"
    PLANNED = "planned"


class WalletDataFeature(str, Enum):
    """Wallet-data operations exposed through the provider contract."""

    NATIVE_BALANCE = "native_balance"
    TOKEN_BALANCES = "token_balances"
    WALLET_ACTIVITY = "wallet_activity"
    TOKEN_HISTORY = "token_history"
    APPROVAL_HISTORY = "approval_history"
    ACTIVE_APPROVALS = "active_approvals"
    NFT_TRANSFERS = "nft_transfers"
    PAGINATION = "pagination"

    @property
    def display_name(self) -> str:
        labels = {
            WalletDataFeature.NATIVE_BALANCE: "Native balance",
            WalletDataFeature.TOKEN_BALANCES: "Token balances",
            WalletDataFeature.WALLET_ACTIVITY: "Wallet activity",
            WalletDataFeature.TOKEN_HISTORY: "Token history",
            WalletDataFeature.APPROVAL_HISTORY: "Approvals",
            WalletDataFeature.ACTIVE_APPROVALS: "Active ERC-20 approvals",
            WalletDataFeature.NFT_TRANSFERS: "ERC-721 / ERC-1155",
            WalletDataFeature.PAGINATION: "Pagination",
        }
        return labels[self]


@dataclass(frozen=True)
class ProviderChainCapabilities:
    """Record the fixture-validated wallet features for one provider and chain."""

    chain: Chain
    features: frozenset[WalletDataFeature]


@dataclass(frozen=True)
class WalletDataProviderDescriptor:
    """Hold public, non-secret facts about one provider adapter."""

    provider_id: WalletDataProviderId
    display_name: str
    availability: ProviderAvailability
    chain_capabilities: tuple[ProviderChainCapabilities, ...]
    validation_destination: str | None

    @property
    def supported_chains(self) -> tuple[Chain, ...]:
        """Return chains with at least one validated wallet-data feature."""

        return tuple(item.chain for item in self.chain_capabilities if item.features)

    @property
    def features(self) -> tuple[WalletDataFeature, ...]:
        """Return the union of features used by the Settings summary."""

        supported = {feature for item in self.chain_capabilities for feature in item.features}
        return tuple(feature for feature in WalletDataFeature if feature in supported)

    def features_for(self, chain: Chain) -> frozenset[WalletDataFeature]:
        """Return only the features validated on the requested chain."""

        for item in self.chain_capabilities:
            if item.chain is chain:
                return item.features
        return frozenset()

    def supports(self, feature: WalletDataFeature, chain: Chain | None = None) -> bool:
        """Check a feature and, when provided, its chain coverage."""

        if self.availability is not ProviderAvailability.AVAILABLE:
            return False
        if chain is not None:
            return feature in self.features_for(chain)
        return feature in self.features


_ESTABLISHED_CHAINS = (
    Chain.ETHEREUM,
    Chain.OPTIMISM,
    Chain.POLYGON,
    Chain.BASE,
    Chain.ARBITRUM,
)
_COMMON_FEATURES = frozenset(
    {
        WalletDataFeature.NATIVE_BALANCE,
        WalletDataFeature.TOKEN_BALANCES,
        WalletDataFeature.WALLET_ACTIVITY,
        WalletDataFeature.TOKEN_HISTORY,
        WalletDataFeature.NFT_TRANSFERS,
        WalletDataFeature.PAGINATION,
    }
)
_INDEXED_HISTORY_FEATURES = _COMMON_FEATURES | {WalletDataFeature.APPROVAL_HISTORY}
_MORALIS_FEATURES = _COMMON_FEATURES | {WalletDataFeature.ACTIVE_APPROVALS}


def _same_capabilities(
    chains: tuple[Chain, ...],
    features: frozenset[WalletDataFeature],
) -> tuple[ProviderChainCapabilities, ...]:
    """Build explicit entries without coupling coverage to every registered chain."""

    return tuple(ProviderChainCapabilities(chain, features) for chain in chains)


PROVIDER_DESCRIPTORS = (
    WalletDataProviderDescriptor(
        WalletDataProviderId.ALCHEMY,
        "Alchemy",
        ProviderAvailability.AVAILABLE,
        _same_capabilities(_ESTABLISHED_CHAINS, _INDEXED_HISTORY_FEATURES),
        "api.g.alchemy.com",
    ),
    WalletDataProviderDescriptor(
        WalletDataProviderId.ANKR,
        "Ankr",
        ProviderAvailability.AVAILABLE,
        _same_capabilities(_ESTABLISHED_CHAINS, _INDEXED_HISTORY_FEATURES),
        "rpc.ankr.com",
    ),
    WalletDataProviderDescriptor(
        WalletDataProviderId.MORALIS,
        "Moralis",
        ProviderAvailability.AVAILABLE,
        _same_capabilities(_ESTABLISHED_CHAINS, _MORALIS_FEATURES),
        "deep-index.moralis.io",
    ),
    WalletDataProviderDescriptor(
        WalletDataProviderId.GOLDRUSH,
        "GoldRush",
        ProviderAvailability.AVAILABLE,
        _same_capabilities(_ESTABLISHED_CHAINS, _INDEXED_HISTORY_FEATURES),
        "api.covalenthq.com",
    ),
)

_DESCRIPTOR_BY_ID = {
    descriptor.provider_id: descriptor for descriptor in PROVIDER_DESCRIPTORS
}


def provider_descriptor(
    provider_id: WalletDataProviderId,
) -> WalletDataProviderDescriptor:
    """Return the catalog entry for a stable provider ID."""

    return _DESCRIPTOR_BY_ID[provider_id]


def available_provider_descriptors() -> tuple[WalletDataProviderDescriptor, ...]:
    """Return adapters that are usable in the current release."""

    return tuple(
        descriptor
        for descriptor in PROVIDER_DESCRIPTORS
        if descriptor.availability is ProviderAvailability.AVAILABLE
    )
