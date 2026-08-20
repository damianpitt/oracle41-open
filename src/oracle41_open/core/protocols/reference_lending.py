"""Provide a small reference lending adapter for conformance tests.

The adapter reads an illustrative position snapshot and emits supplied, debt, and reward positions.
Its contract is not a production deployment; contributors can use this implementation as a minimal adapter example.
"""

from __future__ import annotations

from oracle41_open.core.models import Chain
from oracle41_open.core.models.protocol_position import (
    ProtocolAdapterCapabilities,
    ProtocolAdapterContext,
    ProtocolAdapterResult,
    ProtocolAdapterStatus,
    ProtocolAsset,
    ProtocolAssetRole,
    ProtocolContract,
    ProtocolPosition,
    ProtocolPositionCompleteness,
    ProtocolPositionKind,
    ProtocolPositionProvenance,
    ProtocolRawEvidence,
)

_REFERENCE_CONTRACT = "0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_POSITION_EVIDENCE_KIND = "reference_lending_position"


class ReferenceLendingAdapter:
    _capabilities = ProtocolAdapterCapabilities(
        adapter_id="oracle41.reference-lending",
        adapter_version="1",
        protocol_id="reference-lending",
        protocol_name="Reference Lending",
        chains=frozenset({Chain.ETHEREUM}),
        position_kinds=frozenset(
            {
                ProtocolPositionKind.SUPPLIED,
                ProtocolPositionKind.DEBT,
                ProtocolPositionKind.REWARD,
            }
        ),
        contracts=(
            ProtocolContract(chain=Chain.ETHEREUM, address=_REFERENCE_CONTRACT),
        ),
    )

    @property
    def capabilities(self) -> ProtocolAdapterCapabilities:
        return self._capabilities

    def supports(self, context: ProtocolAdapterContext) -> bool:
        if context.chain not in self.capabilities.chains:
            return False
        supported = {
            contract.address.lower()
            for contract in self.capabilities.contracts
            if contract.chain is context.chain
        }
        return bool(supported.intersection(address.lower() for address in context.contract_addresses))

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        evidence = next(
            (
                item
                for item in context.raw_evidence
                if item.kind == _POSITION_EVIDENCE_KIND
                and item.contract_address is not None
                and item.contract_address.lower() == _REFERENCE_CONTRACT
            ),
            None,
        )
        if evidence is None:
            return self._result(
                context,
                positions=(),
                status=ProtocolAdapterStatus.PARTIAL,
                warnings=("The reference contract matched, but its position snapshot is missing.",),
            )

        positions, warnings = self._positions_from_evidence(context, evidence)
        status = ProtocolAdapterStatus.MATCHED if not warnings else ProtocolAdapterStatus.PARTIAL
        return self._result(context, positions, status, warnings)

    def _positions_from_evidence(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
    ) -> tuple[tuple[ProtocolPosition, ...], tuple[str, ...]]:
        positions: list[ProtocolPosition] = []
        warnings: list[str] = []
        asset_contract = evidence.value("asset_contract")
        asset_symbol = evidence.value("asset_symbol")
        reward_contract = evidence.value("reward_contract")
        reward_symbol = evidence.value("reward_symbol")

        for kind, role, amount_name, contract, symbol, decimals_name in (
            (
                ProtocolPositionKind.SUPPLIED,
                ProtocolAssetRole.SUPPLIED,
                "supplied_raw",
                asset_contract,
                asset_symbol,
                "asset_decimals",
            ),
            (
                ProtocolPositionKind.DEBT,
                ProtocolAssetRole.BORROWED,
                "debt_raw",
                asset_contract,
                asset_symbol,
                "asset_decimals",
            ),
            (
                ProtocolPositionKind.REWARD,
                ProtocolAssetRole.REWARD,
                "reward_raw",
                reward_contract,
                reward_symbol,
                "reward_decimals",
            ),
        ):
            raw_amount = evidence.value(amount_name)
            decimals = _parse_decimals(evidence.value(decimals_name))
            normalized_contract = _normalize_address(contract)
            if raw_amount is None or normalized_contract is None or symbol is None or decimals is None:
                warnings.append(f"The {kind.value} position is missing required snapshot fields.")
                continue
            parsed_amount = _parse_non_negative_integer(raw_amount)
            if parsed_amount is None:
                warnings.append(f"The {kind.value} amount is not a non-negative integer.")
                continue
            if parsed_amount == 0:
                continue
            positions.append(
                self._position(
                    context=context,
                    evidence=evidence,
                    kind=kind,
                    asset=ProtocolAsset(
                        role=role,
                        standard="ERC-20",
                        contract_address=normalized_contract,
                        symbol=symbol,
                        token_id=None,
                        raw_amount=str(parsed_amount),
                        decimals=decimals,
                    ),
                )
            )
        return tuple(positions), tuple(warnings)

    def _position(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
        kind: ProtocolPositionKind,
        asset: ProtocolAsset,
    ) -> ProtocolPosition:
        asset_address = asset.contract_address or "native"
        position_id = ":".join(
            (
                context.chain.value,
                self.capabilities.protocol_id,
                context.wallet_address.lower(),
                kind.value,
                asset_address,
            )
        )
        contracts = tuple(
            dict.fromkeys(
                (
                    _REFERENCE_CONTRACT,
                    *(address for address in (asset.contract_address,) if address is not None),
                )
            )
        )
        return ProtocolPosition(
            schema_version=1,
            position_id=position_id,
            wallet_address=context.wallet_address.lower(),
            chain=context.chain,
            block_number=context.block_number,
            protocol_id=self.capabilities.protocol_id,
            protocol_name=self.capabilities.protocol_name,
            kind=kind,
            label=f"{self.capabilities.protocol_name} {kind.value}",
            assets=(asset,),
            contract_addresses=contracts,
            completeness=ProtocolPositionCompleteness.COMPLETE,
            warnings=(),
            provenance=ProtocolPositionProvenance(
                adapter_id=self.capabilities.adapter_id,
                adapter_version=self.capabilities.adapter_version,
                source_provider=context.source_provider,
                source_reference=evidence.reference,
                observed_at=context.observed_at,
            ),
        )

    def _result(
        self,
        context: ProtocolAdapterContext,
        positions: tuple[ProtocolPosition, ...],
        status: ProtocolAdapterStatus,
        warnings: tuple[str, ...],
    ) -> ProtocolAdapterResult:
        return ProtocolAdapterResult(
            schema_version=1,
            status=status,
            adapter_id=self.capabilities.adapter_id,
            adapter_version=self.capabilities.adapter_version,
            protocol_id=self.capabilities.protocol_id,
            protocol_name=self.capabilities.protocol_name,
            positions=positions,
            source_actions=context.actions,
            source_balances=context.token_balances,
            source_events=context.decoded_events,
            raw_evidence=context.raw_evidence,
            warnings=warnings,
        )


def _parse_non_negative_integer(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_decimals(value: str | None) -> int | None:
    parsed = _parse_non_negative_integer(value)
    return parsed if parsed is not None and parsed <= 255 else None


def _normalize_address(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.lower()
    if len(normalized) != 42 or not normalized.startswith("0x"):
        return None
    try:
        bytes.fromhex(normalized[2:])
    except ValueError:
        return None
    return normalized
