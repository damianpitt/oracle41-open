"""Expose the protocol-adapter extension boundary.

Adapters turn provider-neutral evidence into protocol positions without importing GUI or storage code.
The registry always provides an unknown-protocol fallback that preserves the original evidence.
"""

from oracle41_open.core.protocols.aave_v3 import (
    AaveV3Adapter,
    AaveV3Deployment,
    aave_v3_deployment,
)
from oracle41_open.core.protocols.adapter import ProtocolAdapter
from oracle41_open.core.protocols.compound_v3 import (
    CompoundV3Adapter,
    CompoundV3Market,
    compound_v3_market,
    compound_v3_markets,
)
from oracle41_open.core.protocols.reference_lending import ReferenceLendingAdapter
from oracle41_open.core.protocols.registry import (
    ProtocolAdapterRegistry,
    UnknownProtocolAdapter,
    production_protocol_registry,
)
from oracle41_open.core.protocols.uniswap_v3 import (
    UniswapV3Adapter,
    UniswapV3Deployment,
    liquidity_amounts,
    sqrt_ratio_at_tick,
    uniswap_v3_deployment,
)

__all__ = [
    "AaveV3Adapter",
    "AaveV3Deployment",
    "CompoundV3Adapter",
    "CompoundV3Market",
    "ProtocolAdapter",
    "ProtocolAdapterRegistry",
    "ReferenceLendingAdapter",
    "UnknownProtocolAdapter",
    "UniswapV3Adapter",
    "UniswapV3Deployment",
    "aave_v3_deployment",
    "compound_v3_market",
    "compound_v3_markets",
    "production_protocol_registry",
    "liquidity_amounts",
    "sqrt_ratio_at_tick",
    "uniswap_v3_deployment",
]
