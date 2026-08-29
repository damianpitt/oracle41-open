"""Normalize recorded Aave V3 lending snapshots.

The adapter recognizes official Aave V3 Pool deployments on Oracle41's five current networks.
It turns reserve snapshots into supplied, collateral, and debt positions and keeps account health
values in the exact integer units returned by Aave. It does not fetch data, calculate prices, or
offer liquidation advice; a separate service must record the required contract calls at one block.
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

_RESERVE_EVIDENCE_KIND = "aave_v3_reserve_position"
_ACCOUNT_EVIDENCE_KIND = "aave_v3_account_data"
_COLLECTION_ISSUE_KIND = "aave_v3_collection_issue"
_WAD = 10**18


@dataclass(frozen=True)
class AaveV3Deployment:
    """List the official contracts needed to collect one Aave V3 snapshot."""

    pool_addresses_provider: str
    pool: str
    protocol_data_provider: str

    @property
    def contracts(self) -> tuple[str, ...]:
        return (self.pool_addresses_provider, self.pool, self.protocol_data_provider)


# Addresses were checked against the generated Aave DAO address book on 2026-08-28. Matching is
# chain-specific because Optimism, Polygon, and Arbitrum use the same deterministic Pool addresses.
_MARKETS = {
    Chain.ETHEREUM: AaveV3Deployment(
        pool_addresses_provider="0x2f39d218133afab8f2b819b1066c7e434ad94e9e",
        pool="0x87870bca3f3fd6335c3f4ce8392d69350b4fa4e2",
        protocol_data_provider="0x0a16f2fcc0d44fae41cc54e079281d84a363becd",
    ),
    Chain.OPTIMISM: AaveV3Deployment(
        pool_addresses_provider="0xa97684ead0e402dc232d5a977953df7ecbab3cdb",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        protocol_data_provider="0x243aa95cac2a25651eda86e80bee66114413c43b",
    ),
    Chain.POLYGON: AaveV3Deployment(
        pool_addresses_provider="0xa97684ead0e402dc232d5a977953df7ecbab3cdb",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        protocol_data_provider="0x243aa95cac2a25651eda86e80bee66114413c43b",
    ),
    Chain.BASE: AaveV3Deployment(
        pool_addresses_provider="0xe20fcbdbffc4dd138ce8b2e6fbb6cb49777ad64d",
        pool="0xa238dd80c259a72e81d7e4664a9801593f98d1c5",
        protocol_data_provider="0x0f43731eb8d45a581f4a36dd74f5f358bc90c73a",
    ),
    Chain.ARBITRUM: AaveV3Deployment(
        pool_addresses_provider="0xa97684ead0e402dc232d5a977953df7ecbab3cdb",
        pool="0x794a61358d6845594f94dc1db02a252b5b4814ad",
        protocol_data_provider="0x243aa95cac2a25651eda86e80bee66114413c43b",
    ),
}


def aave_v3_deployment(chain: Chain) -> AaveV3Deployment:
    """Return one immutable deployment or fail clearly for an unsupported chain."""
    try:
        return _MARKETS[chain]
    except KeyError as error:
        raise ValueError(f"Aave V3 is not configured for {chain.display_name}.") from error


class AaveV3Adapter:
    """Build deterministic Aave V3 positions from block-specific call results."""

    _capabilities = ProtocolAdapterCapabilities(
        adapter_id="oracle41.aave-v3",
        adapter_version="1",
        protocol_id="aave-v3",
        protocol_name="Aave V3",
        chains=frozenset(_MARKETS),
        position_kinds=frozenset(
            {
                ProtocolPositionKind.SUPPLIED,
                ProtocolPositionKind.COLLATERAL,
                ProtocolPositionKind.DEBT,
            }
        ),
        contracts=tuple(
            ProtocolContract(chain=chain, address=address)
            for chain, market in _MARKETS.items()
            for address in market.contracts
        ),
    )

    @property
    def capabilities(self) -> ProtocolAdapterCapabilities:
        return self._capabilities

    def supports(self, context: ProtocolAdapterContext) -> bool:
        market = _MARKETS.get(context.chain)
        if market is None:
            return False
        context_contracts = {address.lower() for address in context.contract_addresses}
        return bool(context_contracts.intersection(market.contracts))

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        market = _MARKETS[context.chain]
        evidence = tuple(
            item
            for item in context.raw_evidence
            if item.contract_address is not None
            and item.contract_address.lower() in market.contracts
        )
        reserve_evidence = tuple(item for item in evidence if item.kind == _RESERVE_EVIDENCE_KIND)
        account_evidence = tuple(item for item in evidence if item.kind == _ACCOUNT_EVIDENCE_KIND)
        collection_issues = tuple(item for item in evidence if item.kind == _COLLECTION_ISSUE_KIND)

        warnings = [_collection_issue_warning(item) for item in collection_issues]
        positions: list[ProtocolPosition] = []
        for item in reserve_evidence:
            item_positions, item_warnings = self._reserve_positions(context, market, item)
            positions.extend(item_positions)
            warnings.extend(item_warnings)

        if not reserve_evidence:
            warnings.append("Aave V3 reserve snapshots are missing.")

        risk_snapshot: ProtocolRiskSnapshot | None = None
        if len(account_evidence) == 1:
            risk_snapshot, risk_warnings = self._risk_snapshot(context, account_evidence[0])
            warnings.extend(risk_warnings)
        elif not account_evidence:
            warnings.append("Aave V3 account health data is missing.")
        else:
            warnings.append("Multiple Aave V3 account health snapshots were provided for one block.")

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

    def _reserve_positions(
        self,
        context: ProtocolAdapterContext,
        market: AaveV3Deployment,
        evidence: ProtocolRawEvidence,
    ) -> tuple[tuple[ProtocolPosition, ...], tuple[str, ...]]:
        reserve = _normalize_address(evidence.value("reserve_contract"))
        symbol = _clean_text(evidence.value("symbol"))
        decimals = _parse_decimals(evidence.value("decimals"))
        supplied = _parse_unsigned(evidence.value("current_a_token_balance"))
        stable_debt = _parse_unsigned(evidence.value("current_stable_debt"))
        variable_debt = _parse_unsigned(evidence.value("current_variable_debt"))
        collateral_enabled = _parse_bool(evidence.value("collateral_enabled"))

        if (
            reserve is None
            or symbol is None
            or decimals is None
            or supplied is None
            or stable_debt is None
            or variable_debt is None
        ):
            return (), ("An Aave V3 reserve snapshot has missing or malformed required fields.",)
        if supplied > 0 and collateral_enabled is None:
            return (), ("An Aave V3 supplied balance is missing its collateral-enabled state.",)

        positions: list[ProtocolPosition] = []
        if supplied > 0:
            kind = (
                ProtocolPositionKind.COLLATERAL
                if collateral_enabled
                else ProtocolPositionKind.SUPPLIED
            )
            role = (
                ProtocolAssetRole.COLLATERAL
                if collateral_enabled
                else ProtocolAssetRole.SUPPLIED
            )
            positions.append(
                self._position(context, market, evidence, kind, role, reserve, symbol, decimals, supplied)
            )

        total_debt = stable_debt + variable_debt
        if total_debt > 0:
            positions.append(
                self._position(
                    context,
                    market,
                    evidence,
                    ProtocolPositionKind.DEBT,
                    ProtocolAssetRole.BORROWED,
                    reserve,
                    symbol,
                    decimals,
                    total_debt,
                )
            )
        return tuple(positions), ()

    def _position(
        self,
        context: ProtocolAdapterContext,
        market: AaveV3Deployment,
        evidence: ProtocolRawEvidence,
        kind: ProtocolPositionKind,
        role: ProtocolAssetRole,
        reserve: str,
        symbol: str,
        decimals: int,
        amount: int,
    ) -> ProtocolPosition:
        position_id = ":".join(
            (context.chain.value, "aave-v3", context.wallet_address.lower(), kind.value, reserve)
        )
        token_contracts = tuple(
            address
            for name in (
                "a_token_contract",
                "stable_debt_token_contract",
                "variable_debt_token_contract",
            )
            if (address := _normalize_address(evidence.value(name))) is not None
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
            label=f"Aave V3 {kind.value} {symbol}",
            assets=(
                ProtocolAsset(
                    role=role,
                    standard="ERC-20",
                    contract_address=reserve,
                    symbol=symbol,
                    token_id=None,
                    raw_amount=str(amount),
                    decimals=decimals,
                ),
            ),
            contract_addresses=tuple(dict.fromkeys((market.pool, reserve, *token_contracts))),
            completeness=ProtocolPositionCompleteness.COMPLETE,
            warnings=(),
            provenance=self._provenance(context, evidence),
        )

    def _risk_snapshot(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
    ) -> tuple[ProtocolRiskSnapshot | None, tuple[str, ...]]:
        collateral = _parse_unsigned(evidence.value("total_collateral_base"))
        debt = _parse_unsigned(evidence.value("total_debt_base"))
        available = _parse_unsigned(evidence.value("available_borrows_base"))
        threshold = _parse_bps(evidence.value("liquidation_threshold_bps"))
        ltv = _parse_bps(evidence.value("ltv_bps"))
        health = _parse_unsigned(evidence.value("health_factor_wad"))
        base_unit = _parse_positive(evidence.value("base_currency_unit"))
        if (
            collateral is None
            or debt is None
            or available is None
            or threshold is None
            or ltv is None
            or health is None
            or base_unit is None
        ):
            return None, ("The Aave V3 account health snapshot is missing or malformed.",)

        if debt == 0:
            state = ProtocolRiskState.NO_DEBT
        elif health < _WAD:
            state = ProtocolRiskState.BELOW_LIQUIDATION_THRESHOLD
        else:
            state = ProtocolRiskState.ABOVE_OR_EQUAL_LIQUIDATION_THRESHOLD

        return (
            ProtocolRiskSnapshot(
                wallet_address=context.wallet_address.lower(),
                chain=context.chain,
                block_number=context.block_number,
                protocol_id=self.capabilities.protocol_id,
                total_collateral_base=str(collateral),
                total_debt_base=str(debt),
                available_borrow_base=str(available),
                liquidation_threshold_bps=threshold,
                ltv_bps=ltv,
                health_factor_wad=str(health),
                base_currency_unit=str(base_unit),
                state=state,
                provenance=self._provenance(context, evidence),
            ),
            (),
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


def _parse_positive(value: str | None) -> int | None:
    parsed = _parse_unsigned(value)
    return parsed if parsed is not None and parsed > 0 else None


def _parse_decimals(value: str | None) -> int | None:
    parsed = _parse_unsigned(value)
    return parsed if parsed is not None and parsed <= 255 else None


def _parse_bps(value: str | None) -> int | None:
    parsed = _parse_unsigned(value)
    return parsed if parsed is not None and parsed <= 10_000 else None


def _parse_bool(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {"true", "1"}:
        return True
    if normalized in {"false", "0"}:
        return False
    return None


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


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


def _collection_issue_warning(evidence: ProtocolRawEvidence) -> str:
    stage = evidence.value("stage") or "unknown call"
    reserve = evidence.value("reserve_contract")
    target = f" for reserve {reserve}" if reserve is not None else ""
    return f"Aave V3 snapshot collection could not complete {stage}{target}."
