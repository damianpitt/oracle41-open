"""Describe wallet-data providers without creating network clients.

The catalog gives Settings and tests one source for provider names, availability, supported chains, wallet features, and validation destinations.
Available capabilities reflect code that exists in this release; planned providers do not claim support before their adapters pass conformance tests.
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
class WalletDataProviderDescriptor:
    """Hold public, non-secret facts about one provider adapter."""

    provider_id: WalletDataProviderId
    display_name: str
    availability: ProviderAvailability
    supported_chains: tuple[Chain, ...]
    features: tuple[WalletDataFeature, ...]
    validation_destination: str | None

    def supports(self, feature: WalletDataFeature, chain: Chain | None = None) -> bool:
        """Check a feature and, when provided, its chain coverage."""

        if self.availability is not ProviderAvailability.AVAILABLE:
            return False
        if feature not in self.features:
            return False
        return chain is None or chain in self.supported_chains


_CURRENT_CHAINS = tuple(Chain)
_COMMON_FEATURES = (
    WalletDataFeature.NATIVE_BALANCE,
    WalletDataFeature.TOKEN_BALANCES,
    WalletDataFeature.WALLET_ACTIVITY,
    WalletDataFeature.TOKEN_HISTORY,
    WalletDataFeature.NFT_TRANSFERS,
    WalletDataFeature.PAGINATION,
)
_INDEXED_HISTORY_FEATURES = (*_COMMON_FEATURES, WalletDataFeature.APPROVAL_HISTORY)
_MORALIS_FEATURES = (*_COMMON_FEATURES, WalletDataFeature.ACTIVE_APPROVALS)

PROVIDER_DESCRIPTORS = (
    WalletDataProviderDescriptor(
        provider_id=WalletDataProviderId.ALCHEMY,
        display_name="Alchemy",
        availability=ProviderAvailability.AVAILABLE,
        supported_chains=_CURRENT_CHAINS,
        features=_INDEXED_HISTORY_FEATURES,
        validation_destination="api.g.alchemy.com",
    ),
    WalletDataProviderDescriptor(
        provider_id=WalletDataProviderId.ANKR,
        display_name="Ankr",
        availability=ProviderAvailability.AVAILABLE,
        supported_chains=_CURRENT_CHAINS,
        features=_INDEXED_HISTORY_FEATURES,
        validation_destination="rpc.ankr.com",
    ),
    WalletDataProviderDescriptor(
        provider_id=WalletDataProviderId.MORALIS,
        display_name="Moralis",
        availability=ProviderAvailability.AVAILABLE,
        supported_chains=_CURRENT_CHAINS,
        features=_MORALIS_FEATURES,
        validation_destination="deep-index.moralis.io",
    ),
    WalletDataProviderDescriptor(
        provider_id=WalletDataProviderId.GOLDRUSH,
        display_name="GoldRush",
        availability=ProviderAvailability.AVAILABLE,
        supported_chains=_CURRENT_CHAINS,
        features=_INDEXED_HISTORY_FEATURES,
        validation_destination="api.covalenthq.com",
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
