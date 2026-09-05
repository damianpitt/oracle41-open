"""Normalize recorded Compound V3 Comet market snapshots.

Each Comet contract is an independent lending market with one base asset and several collateral
assets. This module converts exact-block contract evidence into supplied, collateral, and debt
positions. It preserves Compound's own collateralized and liquidatable checks instead of creating
an unsupported health factor or combining collateral assets with different units.
"""

from __future__ import annotations

from dataclasses import dataclass

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
    ProtocolRiskSnapshot,
    ProtocolRiskState,
)

_BASE_EVIDENCE_KIND = "compound_v3_base_position"
_COLLATERAL_EVIDENCE_KIND = "compound_v3_collateral_position"
_COLLECTION_ISSUE_KIND = "compound_v3_collection_issue"
_FACTOR_SCALE = 10**18


@dataclass(frozen=True)
class CompoundV3Market:
    """Describe one official Comet proxy and its base asset label."""

    chain: Chain
    market_id: str
    base_symbol: str
    comet: str

    @property
    def protocol_id(self) -> str:
        return f"compound-v3-{self.market_id}"

    @property
    def protocol_name(self) -> str:
        return f"Compound V3 {self.base_symbol}"

    @property
    def adapter_id(self) -> str:
        return f"oracle41.compound-v3.{self.chain.value}.{self.market_id}"


# Stable Comet proxy addresses from compound-finance/comet deployment roots, checked 2026-09-04.
# The proxy is the protocol's supported interaction address and remains stable across upgrades.
_MARKETS = (
    CompoundV3Market(Chain.ETHEREUM, "usdc", "USDC", "0xc3d688b66703497daa19211eedff47f25384cdc3"),
    CompoundV3Market(Chain.ETHEREUM, "usds", "USDS", "0x5d409e56d886231adaf00c8775665ad0f9897b56"),
    CompoundV3Market(Chain.ETHEREUM, "usdt", "USDT", "0x3afdc9bca9213a35503b077a6072f3d0d5ab0840"),
    CompoundV3Market(Chain.ETHEREUM, "wbtc", "WBTC", "0xe85dc543813b8c2cfeaac371517b925a166a9293"),
    CompoundV3Market(Chain.ETHEREUM, "weth", "WETH", "0xa17581a9e3356d9a858b789d68b4d866e593ae94"),
    CompoundV3Market(Chain.ETHEREUM, "wsteth", "wstETH", "0x3d0bb1ccab520a66e607822fc55bc921738fafe3"),
    CompoundV3Market(Chain.OPTIMISM, "usdc", "USDC", "0x2e44e174f7d53f0212823acc11c01a11d58c5bcb"),
    CompoundV3Market(Chain.OPTIMISM, "usdt", "USDT", "0x995e394b8b2437ac8ce61ee0bc610d617962b214"),
    CompoundV3Market(Chain.OPTIMISM, "weth", "WETH", "0xe36a30d249f7761327fd973001a32010b521b6fd"),
    CompoundV3Market(Chain.POLYGON, "usdc", "USDC", "0xf25212e676d1f7f89cd72ffee66158f541246445"),
    CompoundV3Market(Chain.POLYGON, "usdt", "USDT", "0xaeb318360f27748acb200ce616e389a6c9409a07"),
    CompoundV3Market(Chain.BASE, "aero", "AERO", "0x784efeb622244d2348d4f2522f8860b96fbece89"),
    CompoundV3Market(Chain.BASE, "usdbc", "USDbC", "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"),
    CompoundV3Market(Chain.BASE, "usdc", "USDC", "0xb125e6687d4313864e53df431d5425969c15eb2f"),
    CompoundV3Market(Chain.BASE, "usds", "USDS", "0x2c776041ccfe903071af44aa147368a9c8eea518"),
    CompoundV3Market(Chain.BASE, "weth", "WETH", "0x46e6b214b524310239732d51387075e0e70970bf"),
    CompoundV3Market(Chain.ARBITRUM, "usdc-e", "USDC.e", "0xa5edbdd9646f8dff606d7448e414884c7d905dca"),
    CompoundV3Market(Chain.ARBITRUM, "usdc", "USDC", "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"),
    CompoundV3Market(Chain.ARBITRUM, "usdt", "USDT", "0xd98be00b5d27fc98112bde293e487f8d4ca57d07"),
    CompoundV3Market(Chain.ARBITRUM, "weth", "WETH", "0x6f7d514bbd4aff3bcd1140b7344b32f063dee486"),
)


def compound_v3_markets(chain: Chain | None = None) -> tuple[CompoundV3Market, ...]:
    """Return official configured markets, optionally limited to one chain."""
    if chain is None:
        return _MARKETS
    return tuple(market for market in _MARKETS if market.chain is chain)


def compound_v3_market(chain: Chain, market_id: str) -> CompoundV3Market:
    """Return one configured market or fail with a clear message."""
    normalized = market_id.strip().lower()
    for market in compound_v3_markets(chain):
        if market.market_id == normalized:
            return market
    raise ValueError(
        f"Compound V3 market {market_id!r} is not configured for {chain.display_name}."
    )


class CompoundV3Adapter:
    """Build deterministic positions for one configured Comet market."""

    def __init__(self, market: CompoundV3Market) -> None:
        self.market = market
        self._capabilities = ProtocolAdapterCapabilities(
            adapter_id=market.adapter_id,
            adapter_version="1",
            protocol_id=market.protocol_id,
            protocol_name=market.protocol_name,
            chains=frozenset({market.chain}),
            position_kinds=frozenset(
                {
                    ProtocolPositionKind.SUPPLIED,
                    ProtocolPositionKind.COLLATERAL,
                    ProtocolPositionKind.DEBT,
                }
            ),
            contracts=(ProtocolContract(chain=market.chain, address=market.comet),),
        )

    @property
    def capabilities(self) -> ProtocolAdapterCapabilities:
        return self._capabilities

    def supports(self, context: ProtocolAdapterContext) -> bool:
        return context.chain is self.market.chain and self.market.comet in {
            address.lower() for address in context.contract_addresses
        }

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        evidence = tuple(
            item
            for item in context.raw_evidence
            if item.contract_address is not None
            and item.contract_address.lower() == self.market.comet
        )
        base_items = tuple(item for item in evidence if item.kind == _BASE_EVIDENCE_KIND)
        collateral_items = tuple(
            item for item in evidence if item.kind == _COLLATERAL_EVIDENCE_KIND
        )
        issues = tuple(item for item in evidence if item.kind == _COLLECTION_ISSUE_KIND)
        warnings = [_collection_issue_warning(item) for item in issues]
        positions: list[ProtocolPosition] = []
        risk_snapshot: ProtocolRiskSnapshot | None = None

        if len(base_items) == 1:
            base_positions, risk_snapshot, base_warnings = self._base_positions(
                context,
                base_items[0],
            )
            positions.extend(base_positions)
            warnings.extend(base_warnings)
        elif not base_items:
            warnings.append("Compound V3 base position evidence is missing.")
        else:
            warnings.append("Multiple Compound V3 base position records were provided.")

        for item in collateral_items:
            position, item_warnings = self._collateral_position(context, item)
            if position is not None:
                positions.append(position)
            warnings.extend(item_warnings)

        status = ProtocolAdapterStatus.MATCHED if not warnings else ProtocolAdapterStatus.PARTIAL
        return ProtocolAdapterResult(
            schema_version=1,
            status=status,
            adapter_id=self.capabilities.adapter_id,
            adapter_version=self.capabilities.adapter_version,
            protocol_id=self.capabilities.protocol_id,
            protocol_name=self.capabilities.protocol_name,
            positions=tuple(positions),
            source_actions=context.actions,
            source_balances=context.token_balances,
            source_events=context.decoded_events,
            raw_evidence=context.raw_evidence,
            warnings=tuple(warnings),
            risk_snapshot=risk_snapshot,
        )

    def _base_positions(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
    ) -> tuple[tuple[ProtocolPosition, ...], ProtocolRiskSnapshot | None, tuple[str, ...]]:
        base = _normalize_address(evidence.value("base_contract"))
        symbol = _clean_text(evidence.value("base_symbol"))
        scale = _parse_scale(evidence.value("base_scale"))
        supplied = _parse_unsigned(evidence.value("supplied_base"))
        borrowed = _parse_unsigned(evidence.value("borrowed_base"))
        collateralized = _parse_bool(evidence.value("is_borrow_collateralized"))
        liquidatable = _parse_bool(evidence.value("is_liquidatable"))
        if (
            base is None
            or symbol is None
            or scale is None
            or supplied is None
            or borrowed is None
            or collateralized is None
            or liquidatable is None
        ):
            return (), None, (
                "The Compound V3 base position evidence is missing or malformed.",
            )
        if supplied > 0 and borrowed > 0:
            return (), None, (
                "Compound V3 base supply and debt cannot both be positive for one account.",
            )

        decimals = _scale_decimals(scale)
        positions = []
        if supplied > 0:
            positions.append(
                self._position(
                    context,
                    evidence,
                    ProtocolPositionKind.SUPPLIED,
                    ProtocolAssetRole.SUPPLIED,
                    base,
                    symbol,
                    supplied,
                    decimals,
                )
            )
        if borrowed > 0:
            positions.append(
                self._position(
                    context,
                    evidence,
                    ProtocolPositionKind.DEBT,
                    ProtocolAssetRole.BORROWED,
                    base,
                    symbol,
                    borrowed,
                    decimals,
                )
            )

        if borrowed == 0:
            state = ProtocolRiskState.NO_DEBT
        elif liquidatable:
            state = ProtocolRiskState.BELOW_LIQUIDATION_THRESHOLD
        elif not collateralized:
            state = ProtocolRiskState.BELOW_BORROW_COLLATERAL_REQUIREMENT
        else:
            state = ProtocolRiskState.ABOVE_OR_EQUAL_LIQUIDATION_THRESHOLD
        risk = ProtocolRiskSnapshot(
            wallet_address=context.wallet_address.lower(),
            chain=context.chain,
            block_number=context.block_number,
            protocol_id=self.capabilities.protocol_id,
            total_collateral_base=None,
            total_debt_base=str(borrowed),
            available_borrow_base=None,
            liquidation_threshold_bps=None,
            ltv_bps=None,
            health_factor_wad=None,
            base_currency_unit=str(scale),
            state=state,
            provenance=self._provenance(context, evidence),
            is_borrow_collateralized=collateralized,
            is_liquidatable=liquidatable,
        )
        return tuple(positions), risk, ()

    def _collateral_position(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
    ) -> tuple[ProtocolPosition | None, tuple[str, ...]]:
        asset = _normalize_address(evidence.value("asset_contract"))
        symbol = _clean_text(evidence.value("symbol"))
        scale = _parse_scale(evidence.value("scale"))
        balance = _parse_unsigned(evidence.value("balance"))
        if asset is None or scale is None or balance is None:
            return None, (
                "A Compound V3 collateral record has missing or malformed required fields.",
            )
        if balance == 0:
            return None, ()

        factors = (
            _parse_factor(evidence.value("borrow_collateral_factor_wad")),
            _parse_factor(evidence.value("liquidate_collateral_factor_wad")),
            _parse_factor(evidence.value("liquidation_factor_wad")),
        )
        warnings: tuple[str, ...] = ()
        completeness = ProtocolPositionCompleteness.COMPLETE
        if any(value is None for value in factors):
            warnings = ("Compound V3 collateral factors are missing or malformed.",)
            completeness = ProtocolPositionCompleteness.PARTIAL

        display_symbol = symbol or f"{asset[:8]}...{asset[-4:]}"
        return (
            self._position(
                context,
                evidence,
                ProtocolPositionKind.COLLATERAL,
                ProtocolAssetRole.COLLATERAL,
                asset,
                display_symbol,
                balance,
                _scale_decimals(scale),
                completeness=completeness,
                warnings=warnings,
            ),
            warnings,
        )

    def _position(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
        kind: ProtocolPositionKind,
        role: ProtocolAssetRole,
        asset: str,
        symbol: str,
        amount: int,
        decimals: int,
        completeness: ProtocolPositionCompleteness = ProtocolPositionCompleteness.COMPLETE,
        warnings: tuple[str, ...] = (),
    ) -> ProtocolPosition:
        position_id = ":".join(
            (
                context.chain.value,
                self.capabilities.protocol_id,
                context.wallet_address.lower(),
                kind.value,
                asset,
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
            label=f"{self.capabilities.protocol_name} {kind.value} {symbol}",
            assets=(
                ProtocolAsset(
                    role=role,
                    standard="ERC-20",
                    contract_address=asset,
                    symbol=symbol,
                    token_id=None,
                    raw_amount=str(amount),
                    decimals=decimals,
                ),
            ),
            contract_addresses=(self.market.comet, asset),
            completeness=completeness,
            warnings=warnings,
            provenance=self._provenance(context, evidence),
        )

    def _provenance(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
    ) -> ProtocolPositionProvenance:
        return ProtocolPositionProvenance(
            adapter_id=self.capabilities.adapter_id,
            adapter_version=self.capabilities.adapter_version,
            source_provider=context.source_provider,
            source_reference=evidence.reference,
            observed_at=context.observed_at,
        )


def _parse_unsigned(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _parse_scale(value: str | None) -> int | None:
    parsed = _parse_unsigned(value)
    if parsed is None or parsed < 1 or parsed > 10**18:
        return None
    return parsed if 10 ** _scale_decimals(parsed) == parsed else None


def _scale_decimals(scale: int) -> int:
    return len(str(scale)) - 1


def _parse_factor(value: str | None) -> int | None:
    parsed = _parse_unsigned(value)
    return parsed if parsed is not None and parsed <= _FACTOR_SCALE else None


def _parse_bool(value: str | None) -> bool | None:
    if value == "true":
        return True
    if value == "false":
        return False
    return None


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


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned if cleaned else None


def _collection_issue_warning(evidence: ProtocolRawEvidence) -> str:
    stage = evidence.value("stage") or "unknown stage"
    error_type = evidence.value("error_type") or "provider error"
    return f"Compound V3 collection could not load {stage} ({error_type})."
