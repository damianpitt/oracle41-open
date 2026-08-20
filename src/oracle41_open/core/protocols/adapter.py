"""Define the protocol-adapter interface.

An adapter declares its chains, contracts, position kinds, and version before analyzing evidence.
Implementations return immutable domain results and must not call GUI or storage modules.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from oracle41_open.core.models.protocol_position import (
    ProtocolAdapterCapabilities,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
)


@runtime_checkable
class ProtocolAdapter(Protocol):
    @property
    def capabilities(self) -> ProtocolAdapterCapabilities:
        ...

    def supports(self, context: ProtocolAdapterContext) -> bool:
        ...

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        ...
