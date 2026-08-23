"""Test the public wallet-data provider catalog.

The cases keep stable IDs unique, prevent planned adapters from claiming support, and verify the public fixture schema.
No provider client or network request is created here.
"""

from pathlib import Path

from oracle41_open._json import loads as json_loads
from oracle41_open.core.models import Chain
from oracle41_open.providers.capabilities import (
    PROVIDER_DESCRIPTORS,
    ProviderAvailability,
    WalletDataFeature,
    WalletDataProviderId,
    available_provider_descriptors,
    provider_descriptor,
)


def test_provider_catalog_contains_each_stable_id_once() -> None:
    provider_ids = [descriptor.provider_id for descriptor in PROVIDER_DESCRIPTORS]

    assert set(provider_ids) == set(WalletDataProviderId)
    assert len(provider_ids) == len(set(provider_ids))


def test_available_providers_report_current_chain_and_feature_coverage() -> None:
    available = available_provider_descriptors()

    assert [descriptor.provider_id for descriptor in available] == [
        WalletDataProviderId.ALCHEMY,
        WalletDataProviderId.ANKR,
    ]
    for descriptor in available:
        assert descriptor.supported_chains == tuple(Chain)
        assert descriptor.supports(
            WalletDataFeature.NATIVE_BALANCE,
            Chain.ETHEREUM,
        )
        assert descriptor.supports(
            WalletDataFeature.NFT_TRANSFERS,
            Chain.BASE,
        )
        assert descriptor.validation_destination


def test_planned_providers_do_not_claim_runtime_support() -> None:
    for provider_id in (
        WalletDataProviderId.MORALIS,
        WalletDataProviderId.GOLDRUSH,
    ):
        descriptor = provider_descriptor(provider_id)
        assert descriptor.availability is ProviderAvailability.PLANNED
        assert descriptor.supported_chains == ()
        assert descriptor.features == ()
        assert descriptor.validation_destination is None
        assert not descriptor.supports(WalletDataFeature.WALLET_ACTIVITY)


def test_data_provider_conformance_schema_is_published_as_version_one() -> None:
    schema_path = (
        Path(__file__).resolve().parents[2]
        / "docs"
        / "schemas"
        / "data-provider-conformance-v1.schema.json"
    )
    schema = json_loads(schema_path.read_bytes())

    assert isinstance(schema, dict)
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    assert schema["properties"]["format"]["const"] == (
        "oracle41-data-provider-conformance"
    )
    assert schema["properties"]["version"]["const"] == 1
