"""Select protocol adapters through explicit capabilities.

The registry rejects duplicate adapter IDs and overlapping contract claims so selection remains deterministic.
When no adapter matches, the fallback returns all original evidence instead of inventing a position.
"""

from __future__ import annotations

from collections.abc import Iterable

from oracle41_open.core.models import Chain
from oracle41_open.core.models.protocol_position import (
    ProtocolAdapterCapabilities,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
    ProtocolAdapterStatus,
)
from oracle41_open.core.protocols.adapter import ProtocolAdapter


class UnknownProtocolAdapter:
    _capabilities = ProtocolAdapterCapabilities(
        adapter_id="oracle41.unknown-protocol",
        adapter_version="1",
        protocol_id="unknown",
        protocol_name="Unknown protocol",
        chains=frozenset(Chain),
        position_kinds=frozenset(),
        contracts=(),
    )

    @property
    def capabilities(self) -> ProtocolAdapterCapabilities:
        return self._capabilities

    def supports(self, context: ProtocolAdapterContext) -> bool:
        _ = context
        return True

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        return ProtocolAdapterResult(
            schema_version=1,
            status=ProtocolAdapterStatus.UNKNOWN_PROTOCOL,
            adapter_id=self.capabilities.adapter_id,
            adapter_version=self.capabilities.adapter_version,
            protocol_id=None,
            protocol_name=None,
            positions=(),
            source_actions=context.actions,
            source_balances=context.token_balances,
            source_events=context.decoded_events,
            raw_evidence=context.raw_evidence,
            warnings=(
                "No installed protocol adapter matched this evidence. Token balances and decoded events remain available.",
            ),
        )


class ProtocolAdapterRegistry:
    def __init__(
        self,
        adapters: Iterable[ProtocolAdapter] = (),
        fallback: ProtocolAdapter | None = None,
    ) -> None:
        self._adapters = tuple(adapters)
        self._fallback = fallback or UnknownProtocolAdapter()
        _validate_adapter_set(self._adapters)

    @property
    def capabilities(self) -> tuple[ProtocolAdapterCapabilities, ...]:
        return tuple(adapter.capabilities for adapter in self._adapters)

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        matches = tuple(candidate for candidate in self._adapters if candidate.supports(context))
        if len(matches) > 1:
            adapter_ids = ", ".join(item.capabilities.adapter_id for item in matches)
            raise ValueError(f"Multiple protocol adapters matched the same context: {adapter_ids}")
        adapter = matches[0] if matches else self._fallback
        result = adapter.analyze(context)
        _validate_adapter_result(adapter, context, result)
        return result


def production_protocol_registry() -> ProtocolAdapterRegistry:
    """Return the adapters that are ready for production evidence."""
    from oracle41_open.core.protocols.aave_v3 import AaveV3Adapter
    from oracle41_open.core.protocols.compound_v3 import (
        CompoundV3Adapter,
        compound_v3_markets,
    )

    return ProtocolAdapterRegistry(
        (
            AaveV3Adapter(),
            *(CompoundV3Adapter(market) for market in compound_v3_markets()),
        )
    )


def _validate_adapter_set(adapters: tuple[ProtocolAdapter, ...]) -> None:
    adapter_ids: set[str] = set()
    claimed_contracts: dict[tuple[Chain, str], str] = {}
    for adapter in adapters:
        capabilities = adapter.capabilities
        if not capabilities.adapter_id.strip() or not capabilities.adapter_version.strip():
            raise ValueError("Protocol adapter ID and version must not be empty.")
        if capabilities.adapter_id in adapter_ids:
            raise ValueError(f"Duplicate protocol adapter ID: {capabilities.adapter_id}")
        adapter_ids.add(capabilities.adapter_id)

        for contract in capabilities.contracts:
            if contract.chain not in capabilities.chains:
                raise ValueError(
                    f"Protocol adapter {capabilities.adapter_id} declares {contract.address} "
                    f"outside its supported chains."
                )
            if not _is_address(contract.address):
                raise ValueError(
                    f"Protocol adapter {capabilities.adapter_id} has an invalid contract address."
                )
            key = (contract.chain, contract.address.lower())
            owner = claimed_contracts.get(key)
            if owner is not None:
                raise ValueError(
                    f"Protocol contract {contract.address} on {contract.chain.value} "
                    f"is claimed by both {owner} and {capabilities.adapter_id}."
                )
            claimed_contracts[key] = capabilities.adapter_id


def _is_address(value: str) -> bool:
    if len(value) != 42 or not value.startswith("0x"):
        return False
    try:
        bytes.fromhex(value[2:])
    except ValueError:
        return False
    return True


def _validate_adapter_result(
    adapter: ProtocolAdapter,
    context: ProtocolAdapterContext,
    result: ProtocolAdapterResult,
) -> None:
    capabilities = adapter.capabilities
    if result.schema_version != 1:
        raise ValueError(f"Protocol adapter {capabilities.adapter_id} returned an unknown schema version.")
    if (
        result.adapter_id != capabilities.adapter_id
        or result.adapter_version != capabilities.adapter_version
    ):
        raise ValueError(
            f"Protocol adapter {capabilities.adapter_id} returned mismatched identity metadata."
        )
    if result.status is not ProtocolAdapterStatus.UNKNOWN_PROTOCOL and (
        result.protocol_id != capabilities.protocol_id
        or result.protocol_name != capabilities.protocol_name
    ):
        raise ValueError(
            f"Protocol adapter {capabilities.adapter_id} returned mismatched protocol metadata."
        )

    expected_wallet = context.wallet_address.lower()
    for position in result.positions:
        if (
            position.schema_version != 1
            or position.wallet_address.lower() != expected_wallet
            or position.chain is not context.chain
            or position.block_number != context.block_number
            or position.protocol_id != capabilities.protocol_id
            or position.protocol_name != capabilities.protocol_name
            or position.provenance.adapter_id != capabilities.adapter_id
            or position.provenance.adapter_version != capabilities.adapter_version
        ):
            raise ValueError(
                f"Protocol adapter {capabilities.adapter_id} returned a position outside its context."
            )

    risk = result.risk_snapshot
    if risk is not None and (
        risk.wallet_address.lower() != expected_wallet
        or risk.chain is not context.chain
        or risk.block_number != context.block_number
        or risk.protocol_id != capabilities.protocol_id
        or risk.provenance.adapter_id != capabilities.adapter_id
        or risk.provenance.adapter_version != capabilities.adapter_version
    ):
        raise ValueError(
            f"Protocol adapter {capabilities.adapter_id} returned risk data outside its context."
        )
