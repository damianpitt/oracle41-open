"""Test conservative real-world asset recognition.

Reviewed Centrifuge deployments work offline, live issuer catalogs are optional, and unknown exact
contracts remain unclassified even when their symbols could resemble a known product.
"""

from __future__ import annotations

from oracle41_open.core.models import Chain, RealWorldAssetCategory
from oracle41_open.core.services.rwa_recognition_service import RWARecognitionService

_JTRSY = "0x8c213ee79581ff4984583c6a801e5263418c4b86"
_UNKNOWN = "0x1111111111111111111111111111111111111111"


def test_reviewed_centrifuge_deployment_is_available_offline() -> None:
    service = RWARecognitionService(
        _EmptyStockCatalog(),
        _EmptyExternalProvider(),
        allow_live_catalogs=False,
    )

    identity = service.recognize(Chain.BASE, _JTRSY)

    assert identity is not None
    assert identity.symbol == "JTRSY"
    assert identity.category is RealWorldAssetCategory.TOKENIZED_FUND
    assert service.recognize(Chain.OPTIMISM, _JTRSY) is None


def test_unknown_contract_stays_unclassified_when_live_catalogs_are_disabled() -> None:
    external = _EmptyExternalProvider()
    service = RWARecognitionService(
        _EmptyStockCatalog(),
        external,
        allow_live_catalogs=False,
    )

    assert service.recognize(Chain.ARBITRUM, _UNKNOWN) is None
    assert external.calls == 0


class _EmptyStockCatalog:
    def get_stock_token_metadata(self, contract_address: str) -> None:
        _ = contract_address
        return None


class _EmptyExternalProvider:
    def __init__(self) -> None:
        self.calls = 0

    def get_asset_identity(self, chain: Chain, contract_address: str) -> None:
        _ = chain, contract_address
        self.calls += 1
        return None

