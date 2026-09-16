"""Test the public network registry and its provider identifiers.

These checks keep every stored chain value mapped exactly once and prevent duplicate EVM chain IDs.
Robinhood coverage stays explicit: its Alchemy, GoldRush, explorer, and public RPC names are known while unsupported provider names remain empty.
"""

from oracle41_open.core.models import (
    Chain,
    TransactionFeeModel,
    network_descriptor,
    network_descriptors,
)


def test_network_registry_covers_each_chain_once() -> None:
    descriptors = network_descriptors()

    assert tuple(item.chain for item in descriptors) == tuple(Chain)
    assert len({item.chain_id for item in descriptors}) == len(descriptors)
    assert all(item.chain_id > 0 for item in descriptors)
    assert all(item.explorer_url.startswith("https://") for item in descriptors)


def test_robinhood_network_identifiers_are_explicit() -> None:
    descriptor = network_descriptor(Chain.ROBINHOOD)

    assert descriptor.chain_id == 4663
    assert descriptor.display_name == "Robinhood Chain"
    assert descriptor.native_symbol == "ETH"
    assert descriptor.fee_model is TransactionFeeModel.ARBITRUM_NITRO
    assert descriptor.settlement_chain is Chain.ETHEREUM
    assert descriptor.alchemy_network_path == "robinhood-mainnet"
    assert descriptor.alchemy_pricing_network_path is None
    assert descriptor.goldrush_chain_name == "robinhood-mainnet"
    assert descriptor.ankr_rpc_path is None
    assert descriptor.moralis_chain_code is None
    assert descriptor.public_rpc_url == "https://rpc.mainnet.chain.robinhood.com"
    assert descriptor.explorer_url == "https://robinhoodchain.blockscout.com"


def test_existing_chain_properties_remain_compatible() -> None:
    assert Chain.ETHEREUM.alchemy_network_path == "eth-mainnet"
    assert Chain.ARBITRUM.ankr_rpc_path == "arbitrum"
    assert Chain.POLYGON.native_symbol == "MATIC"
    assert Chain.ROBINHOOD.alchemy_network_path == "robinhood-mainnet"
    assert Chain.ARBITRUM.network.fee_model is TransactionFeeModel.ARBITRUM_NITRO
