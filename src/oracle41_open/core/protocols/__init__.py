"""Expose the protocol-adapter extension boundary.

Adapters turn provider-neutral evidence into protocol positions without importing GUI or storage code.
The registry always provides an unknown-protocol fallback that preserves the original evidence.
"""

from oracle41_open.core.protocols.adapter import ProtocolAdapter
from oracle41_open.core.protocols.reference_lending import ReferenceLendingAdapter
from oracle41_open.core.protocols.registry import (
    ProtocolAdapterRegistry,
    UnknownProtocolAdapter,
)

__all__ = [
    "ProtocolAdapter",
    "ProtocolAdapterRegistry",
    "ReferenceLendingAdapter",
    "UnknownProtocolAdapter",
]
