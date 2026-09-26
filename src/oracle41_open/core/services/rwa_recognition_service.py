"""Recognize verified tokenized real-world assets by chain and contract address.

Robinhood Stock Tokens and xStocks come from public issuer catalogs. A small offline Centrifuge
registry covers deployments published in the protocol's official documentation. The service does
not infer identity from token names or symbols because those fields are easy to copy.
"""

from __future__ import annotations

from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    RealWorldAssetCategory,
    RealWorldAssetIdentity,
    StockTokenMetadata,
)
from oracle41_open.core.services.address_validator import AddressValidator

_CENTRIFUGE_REFERENCE = "https://docs.centrifuge.io/developer/protocol/deployments/"


class StockTokenCatalog(Protocol):
    def get_stock_token_metadata(self, contract_address: str) -> StockTokenMetadata | None:
        ...


class ExternalRWAProvider(Protocol):
    def get_asset_identity(
        self,
        chain: Chain,
        contract_address: str,
    ) -> RealWorldAssetIdentity | None:
        ...


class RWARecognitionService:
    """Combine issuer catalogs and reviewed offline identities behind one lookup."""

    def __init__(
        self,
        stock_token_catalog: StockTokenCatalog,
        external_provider: ExternalRWAProvider,
        *,
        allow_live_catalogs: bool,
    ) -> None:
        self._stock_token_catalog = stock_token_catalog
        self._external_provider = external_provider
        self._allow_live_catalogs = allow_live_catalogs

    def recognize(
        self,
        chain: Chain,
        contract_address: str,
    ) -> RealWorldAssetIdentity | None:
        """Return verified identity or ``None`` for an unknown exact deployment."""

        address = AddressValidator.normalized(contract_address)
        if AddressValidator.validation_error(address) is not None:
            return None

        reviewed = _CENTRIFUGE_ASSETS.get((chain, address))
        if reviewed is not None:
            return reviewed
        if not self._allow_live_catalogs:
            return None
        if chain is Chain.ROBINHOOD:
            metadata = self._stock_token_catalog.get_stock_token_metadata(address)
            if metadata is not None:
                return _stock_token_identity(metadata)
        return self._external_provider.get_asset_identity(chain, address)


def _stock_token_identity(metadata: StockTokenMetadata) -> RealWorldAssetIdentity:
    return RealWorldAssetIdentity(
        asset_id=metadata.asset_id,
        symbol=metadata.symbol,
        name=metadata.name,
        chain=Chain.ROBINHOOD,
        contract_address=metadata.contract_address,
        category=RealWorldAssetCategory.STOCK_TOKEN,
        issuer="Robinhood Assets (Jersey) Limited",
        source_name="robinhood-stock-token-api",
        source_reference="https://docs.robinhood.com/chain/stock-token-apis/",
        underlying_symbol=metadata.symbol,
    )


def _centrifuge_identity(
    chain: Chain,
    address: str,
    symbol: str,
) -> RealWorldAssetIdentity:
    return RealWorldAssetIdentity(
        asset_id=f"centrifuge:{symbol.lower()}",
        symbol=symbol,
        name=f"Centrifuge {symbol} share token",
        chain=chain,
        contract_address=address,
        category=RealWorldAssetCategory.TOKENIZED_FUND,
        issuer="Centrifuge protocol registry",
        source_name="centrifuge-deployment-registry",
        source_reference=_CENTRIFUGE_REFERENCE,
    )


_CENTRIFUGE_DEPLOYMENTS = (
    ("JTRSY", "0x8c213ee79581ff4984583c6a801e5263418c4b86", (Chain.ETHEREUM, Chain.BASE, Chain.ARBITRUM)),
    ("JAAA", "0x5a0f93d040de44e78f251b03c43be9cf317dcf64", (Chain.ETHEREUM, Chain.BASE)),
    ("JAAA", "0x58f93d6b1ef2f44ec379cb975657c132cbed3b6b", (Chain.ARBITRUM,)),
    ("ACRDX", "0x9477724bb54ad5417de8baff29e59df3fb4da74f", (Chain.ETHEREUM, Chain.BASE)),
    ("ACRDX", "0x2fabf1c784b8583d63c00c5c9c0377d8cf1a3245", (Chain.OPTIMISM,)),
    ("SPXA", "0x09b61343097c1f9b159a3ae7151298efd10f0db2", (Chain.BASE,)),
    ("deJTRSY", "0xa6233014b9b7aaa74f38fa1977ffc7a89642dc72", (Chain.ETHEREUM, Chain.BASE, Chain.ARBITRUM)),
    ("deJAAA", "0xaaa0008c8cf3a7dca931adaf04336a5d808c82cc", (Chain.ETHEREUM, Chain.BASE, Chain.ARBITRUM)),
    ("deCRDX", "0x9e2679eabff131b8b1b48ff7566140794e0eedc4", (Chain.ETHEREUM, Chain.OPTIMISM)),
    ("deSPXA", "0x9c5c365e764829876243d0b289733b9d2b729685", (Chain.BASE,)),
)

_CENTRIFUGE_ASSETS = {
    (chain, address): _centrifuge_identity(chain, address, symbol)
    for symbol, address, chains in _CENTRIFUGE_DEPLOYMENTS
    for chain in chains
}

