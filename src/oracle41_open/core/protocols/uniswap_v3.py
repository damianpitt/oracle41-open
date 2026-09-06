"""Normalize Uniswap V3 concentrated-liquidity NFT snapshots.

Each position-manager NFT defines one pool, fee tier, tick range, and liquidity amount. This
module converts exact-block contract evidence into the two underlying token amounts and estimated
uncollected fees. It keeps range and pool details as position metadata and does not present the
calculation as a withdrawal quote.
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
    ProtocolEvidenceValue,
    ProtocolPosition,
    ProtocolPositionCompleteness,
    ProtocolPositionKind,
    ProtocolPositionProvenance,
    ProtocolRawEvidence,
    ProtocolRiskSnapshot,
    ProtocolRiskState,
)

_POSITION_EVIDENCE_KIND = "uniswap_v3_position"
_COLLECTION_EVIDENCE_KIND = "uniswap_v3_collection"
_COLLECTION_ISSUE_KIND = "uniswap_v3_collection_issue"
_PROTOCOL_ID = "uniswap-v3"
_PROTOCOL_NAME = "Uniswap V3"
_Q96 = 1 << 96
_Q128 = 1 << 128
_UINT256_MODULUS = 1 << 256
_MIN_TICK = -887_272
_MAX_TICK = 887_272
_TICK_RATIO_FACTORS = (
    (0x1, 0xFFFcb933BD6FAD37AA2D162D1A594001),
    (0x2, 0xFFF97272373D413259A46990580E213A),
    (0x4, 0xFFF2E50F5F656932EF12357CF3C7FDCC),
    (0x8, 0xFFE5CACA7E10E4E61C3624EAA0941CD0),
    (0x10, 0xFFCB9843D60F6159C9DB58835C926644),
    (0x20, 0xFF973B41FA98C081472E6896DFB254C0),
    (0x40, 0xFF2EA16466C96A3843EC78B326B52861),
    (0x80, 0xFE5DEE046A99A2A811C461F1969C3053),
    (0x100, 0xFCBE86C7900A88AEDCFFC83B479AA3A4),
    (0x200, 0xF987A7253AC413176F2B074CF7815E54),
    (0x400, 0xF3392B0822B70005940C7A398E4B70F3),
    (0x800, 0xE7159475A2C29B7443B29C7FA6E889D9),
    (0x1000, 0xD097F3BDFD2022B8845AD8F792AA5825),
    (0x2000, 0xA9F746462D870FDF8A65DC1F90E061E5),
    (0x4000, 0x70D869A156D2A1B890BB3DF62BAF32F7),
    (0x8000, 0x31BE135F97D08FD981231505542FCFA6),
    (0x10000, 0x9AA508B5B7A84E1C677DE54F3E99BC9),
    (0x20000, 0x5D6AF8DEDB81196699C329225EE604),
    (0x40000, 0x2216E584F5FA1EA926041BEDFE98),
    (0x80000, 0x48A170391F7DC42444E8FA2),
)


@dataclass(frozen=True)
class UniswapV3Deployment:
    """Describe one official factory and NFT position manager deployment."""

    chain: Chain
    factory: str
    position_manager: str

    @property
    def contracts(self) -> tuple[str, str]:
        return self.factory, self.position_manager


_SHARED_FACTORY = "0x1f98431c8ad98523631ae4a59f267346ea31f984"
_SHARED_POSITION_MANAGER = "0xc36442b4a4522e871399cd717abdd847ab11fe88"
_DEPLOYMENTS = {
    Chain.ETHEREUM: UniswapV3Deployment(
        Chain.ETHEREUM, _SHARED_FACTORY, _SHARED_POSITION_MANAGER
    ),
    Chain.OPTIMISM: UniswapV3Deployment(
        Chain.OPTIMISM, _SHARED_FACTORY, _SHARED_POSITION_MANAGER
    ),
    Chain.POLYGON: UniswapV3Deployment(
        Chain.POLYGON, _SHARED_FACTORY, _SHARED_POSITION_MANAGER
    ),
    Chain.ARBITRUM: UniswapV3Deployment(
        Chain.ARBITRUM, _SHARED_FACTORY, _SHARED_POSITION_MANAGER
    ),
    Chain.BASE: UniswapV3Deployment(
        Chain.BASE,
        "0x33128a8fc17869897dce68ed026d694621f6fdfd",
        "0x03a520b32c04bf3beef7beb72e919cf822ed34f1",
    ),
}


def uniswap_v3_deployment(chain: Chain) -> UniswapV3Deployment:
    """Return the configured Uniswap V3 deployment for one supported chain."""
    return _DEPLOYMENTS[chain]


class UniswapV3Adapter:
    """Build deterministic liquidity positions from recorded pool state."""

    def __init__(self) -> None:
        self._capabilities = ProtocolAdapterCapabilities(
            adapter_id="oracle41.uniswap-v3",
            adapter_version="1",
            protocol_id=_PROTOCOL_ID,
            protocol_name=_PROTOCOL_NAME,
            chains=frozenset(_DEPLOYMENTS),
            position_kinds=frozenset({ProtocolPositionKind.LIQUIDITY}),
            contracts=tuple(
                ProtocolContract(chain=deployment.chain, address=address)
                for deployment in _DEPLOYMENTS.values()
                for address in deployment.contracts
            ),
        )

    @property
    def capabilities(self) -> ProtocolAdapterCapabilities:
        return self._capabilities

    def supports(self, context: ProtocolAdapterContext) -> bool:
        deployment = _DEPLOYMENTS.get(context.chain)
        if deployment is None:
            return False
        contracts = {address.lower() for address in context.contract_addresses}
        return deployment.position_manager in contracts or deployment.factory in contracts

    def analyze(self, context: ProtocolAdapterContext) -> ProtocolAdapterResult:
        deployment = uniswap_v3_deployment(context.chain)
        evidence = tuple(
            item
            for item in context.raw_evidence
            if item.contract_address is not None
            and item.contract_address.lower() == deployment.position_manager
        )
        positions: list[ProtocolPosition] = []
        warnings = [_issue_warning(item) for item in evidence if item.kind == _COLLECTION_ISSUE_KIND]
        collection = next((item for item in evidence if item.kind == _COLLECTION_EVIDENCE_KIND), None)
        if collection is None:
            warnings.append("The Uniswap V3 owned-position list is missing.")

        failed_token_ids = {
            item.value("token_id")
            for item in evidence
            if item.kind == _COLLECTION_ISSUE_KIND and item.value("token_id") is not None
        }
        for item in evidence:
            if item.kind != _POSITION_EVIDENCE_KIND:
                continue
            position, item_warnings = self._position(
                context,
                item,
                item.value("token_id") in failed_token_ids,
            )
            if position is not None:
                positions.append(position)
            warnings.extend(item_warnings)

        status = ProtocolAdapterStatus.MATCHED if not warnings else ProtocolAdapterStatus.PARTIAL
        return ProtocolAdapterResult(
            schema_version=1,
            status=status,
            adapter_id=self.capabilities.adapter_id,
            adapter_version=self.capabilities.adapter_version,
            protocol_id=_PROTOCOL_ID,
            protocol_name=_PROTOCOL_NAME,
            positions=tuple(positions),
            source_actions=context.actions,
            source_balances=context.token_balances,
            source_events=context.decoded_events,
            raw_evidence=context.raw_evidence,
            warnings=tuple(warnings),
            risk_snapshot=(
                self._not_applicable_risk(context, collection)
                if collection is not None
                else None
            ),
        )

    def _position(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
        has_collection_issue: bool,
    ) -> tuple[ProtocolPosition | None, tuple[str, ...]]:
        try:
            token_id = _unsigned(evidence.value("token_id"), "token ID")
            token0 = _address(evidence.value("token0"), "token0")
            token1 = _address(evidence.value("token1"), "token1")
            pool = _address(evidence.value("pool"), "pool")
            fee = _unsigned(evidence.value("fee"), "fee tier")
            tick_lower = _integer(evidence.value("tick_lower"), "lower tick")
            tick_upper = _integer(evidence.value("tick_upper"), "upper tick")
            current_tick = _integer(evidence.value("current_tick"), "current tick")
            sqrt_price_x96 = _unsigned(evidence.value("sqrt_price_x96"), "square-root price")
            liquidity = _unsigned(evidence.value("liquidity"), "liquidity")
            fee0 = _uncollected_fee(evidence, 0, liquidity, current_tick, tick_lower, tick_upper)
            fee1 = _uncollected_fee(evidence, 1, liquidity, current_tick, tick_lower, tick_upper)
            owed0 = _unsigned(evidence.value("tokens_owed0"), "owed token0") + fee0
            owed1 = _unsigned(evidence.value("tokens_owed1"), "owed token1") + fee1
            if token0 == token1 or not _MIN_TICK <= tick_lower < tick_upper <= _MAX_TICK:
                raise ValueError("invalid token pair or tick range")
            if not _MIN_TICK <= current_tick <= _MAX_TICK or sqrt_price_x96 <= 0:
                raise ValueError("invalid current pool state")
            if fee > 1_000_000:
                raise ValueError("invalid fee tier")
        except ValueError:
            return None, ("A Uniswap V3 position record has missing or malformed required fields.",)

        amount0, amount1 = liquidity_amounts(
            liquidity,
            sqrt_price_x96,
            tick_lower,
            tick_upper,
        )
        symbol0 = _symbol(evidence.value("symbol0"), token0)
        symbol1 = _symbol(evidence.value("symbol1"), token1)
        decimals0 = _optional_decimals(evidence.value("decimals0"))
        decimals1 = _optional_decimals(evidence.value("decimals1"))
        warnings: list[str] = []
        if decimals0 is None or decimals1 is None:
            warnings.append("Uniswap V3 token decimals are incomplete, so some values cannot be priced.")
        if has_collection_issue:
            warnings.append("Some optional data for this Uniswap V3 position could not be loaded.")

        assets = [
            _asset(ProtocolAssetRole.UNDERLYING, token0, symbol0, token_id, amount0, decimals0),
            _asset(ProtocolAssetRole.UNDERLYING, token1, symbol1, token_id, amount1, decimals1),
        ]
        if owed0 > 0:
            assets.append(_asset(ProtocolAssetRole.REWARD, token0, symbol0, token_id, owed0, decimals0))
        if owed1 > 0:
            assets.append(_asset(ProtocolAssetRole.REWARD, token1, symbol1, token_id, owed1, decimals1))

        range_state = _range_state(current_tick, tick_lower, tick_upper)
        metadata = evidence_values(
            {
                "pool_address": pool,
                "fee_tier": str(fee),
                "tick_lower": str(tick_lower),
                "tick_upper": str(tick_upper),
                "current_tick": str(current_tick),
                "range_state": range_state,
                "liquidity": str(liquidity),
            }
        )
        completeness = (
            ProtocolPositionCompleteness.PARTIAL
            if warnings
            else ProtocolPositionCompleteness.COMPLETE
        )
        return ProtocolPosition(
            schema_version=1,
            position_id=(
                f"{context.chain.value}:{_PROTOCOL_ID}:{context.wallet_address.lower()}:{token_id}"
            ),
            wallet_address=context.wallet_address.lower(),
            chain=context.chain,
            block_number=context.block_number,
            protocol_id=_PROTOCOL_ID,
            protocol_name=_PROTOCOL_NAME,
            kind=ProtocolPositionKind.LIQUIDITY,
            label=f"Uniswap V3 {symbol0}/{symbol1} {fee / 10_000:g}% NFT #{token_id}",
            assets=tuple(assets),
            contract_addresses=(
                uniswap_v3_deployment(context.chain).position_manager,
                pool,
                token0,
                token1,
            ),
            completeness=completeness,
            warnings=tuple(warnings),
            provenance=self._provenance(context, evidence),
            metadata=metadata,
        ), tuple(warnings)

    def _not_applicable_risk(
        self,
        context: ProtocolAdapterContext,
        evidence: ProtocolRawEvidence,
    ) -> ProtocolRiskSnapshot:
        return ProtocolRiskSnapshot(
            wallet_address=context.wallet_address.lower(),
            chain=context.chain,
            block_number=context.block_number,
            protocol_id=_PROTOCOL_ID,
            total_collateral_base=None,
            total_debt_base=None,
            available_borrow_base=None,
            liquidation_threshold_bps=None,
            ltv_bps=None,
            health_factor_wad=None,
            base_currency_unit=None,
            state=ProtocolRiskState.NOT_APPLICABLE,
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


def liquidity_amounts(
    liquidity: int,
    sqrt_price_x96: int,
    tick_lower: int,
    tick_upper: int,
) -> tuple[int, int]:
    """Return floor-rounded token amounts represented by active or inactive liquidity."""
    if liquidity < 0 or sqrt_price_x96 <= 0 or not _MIN_TICK <= tick_lower < tick_upper <= _MAX_TICK:
        raise ValueError("Invalid Uniswap V3 liquidity state.")
    lower = sqrt_ratio_at_tick(tick_lower)
    upper = sqrt_ratio_at_tick(tick_upper)
    if sqrt_price_x96 <= lower:
        return _amount0(liquidity, lower, upper), 0
    if sqrt_price_x96 < upper:
        return (
            _amount0(liquidity, sqrt_price_x96, upper),
            _amount1(liquidity, lower, sqrt_price_x96),
        )
    return 0, _amount1(liquidity, lower, upper)


def sqrt_ratio_at_tick(tick: int) -> int:
    """Calculate the exact upward-rounded Q64.96 ratio used by Uniswap V3."""
    if not _MIN_TICK <= tick <= _MAX_TICK:
        raise ValueError("Uniswap V3 tick is outside the supported range.")
    absolute_tick = abs(tick)
    ratio = 1 << 128
    for mask, factor in _TICK_RATIO_FACTORS:
        if absolute_tick & mask:
            ratio = ratio * factor >> 128
    if tick > 0:
        ratio = (_UINT256_MODULUS - 1) // ratio
    remainder_mask = (1 << 32) - 1
    return (ratio >> 32) + (1 if ratio & remainder_mask else 0)


def _amount0(liquidity: int, lower: int, upper: int) -> int:
    return liquidity * (upper - lower) * _Q96 // (upper * lower)


def _amount1(liquidity: int, lower: int, upper: int) -> int:
    return liquidity * (upper - lower) // _Q96


def _uncollected_fee(
    evidence: ProtocolRawEvidence,
    token_index: int,
    liquidity: int,
    current_tick: int,
    tick_lower: int,
    tick_upper: int,
) -> int:
    global_growth = _unsigned(
        evidence.value(f"fee_growth_global{token_index}_x128"), "global fee growth"
    )
    lower_outside = _unsigned(
        evidence.value(f"fee_growth_outside{token_index}_lower_x128"), "lower fee growth"
    )
    upper_outside = _unsigned(
        evidence.value(f"fee_growth_outside{token_index}_upper_x128"), "upper fee growth"
    )
    last_inside = _unsigned(
        evidence.value(f"fee_growth_inside{token_index}_last_x128"), "last fee growth"
    )
    below = lower_outside if current_tick >= tick_lower else (global_growth - lower_outside) % _UINT256_MODULUS
    above = upper_outside if current_tick < tick_upper else (global_growth - upper_outside) % _UINT256_MODULUS
    inside = (global_growth - below - above) % _UINT256_MODULUS
    delta = (inside - last_inside) % _UINT256_MODULUS
    return liquidity * delta // _Q128


def _asset(
    role: ProtocolAssetRole,
    contract: str,
    symbol: str,
    token_id: int,
    amount: int,
    decimals: int | None,
) -> ProtocolAsset:
    return ProtocolAsset(
        role=role,
        standard="ERC-20",
        contract_address=contract,
        symbol=symbol,
        token_id=str(token_id),
        raw_amount=str(amount),
        decimals=decimals,
    )


def _range_state(current: int, lower: int, upper: int) -> str:
    if current < lower:
        return "below_range"
    if current >= upper:
        return "above_range"
    return "in_range"


def _address(value: str | None, field: str) -> str:
    normalized = (value or "").lower()
    if len(normalized) != 42 or not normalized.startswith("0x"):
        raise ValueError(f"Invalid {field}.")
    try:
        bytes.fromhex(normalized[2:])
    except ValueError as error:
        raise ValueError(f"Invalid {field}.") from error
    if normalized == "0x" + "00" * 20:
        raise ValueError(f"Invalid {field}.")
    return normalized


def _integer(value: str | None, field: str) -> int:
    try:
        return int(value or "")
    except ValueError as error:
        raise ValueError(f"Invalid {field}.") from error


def _unsigned(value: str | None, field: str) -> int:
    parsed = _integer(value, field)
    if parsed < 0:
        raise ValueError(f"Invalid {field}.")
    return parsed


def _optional_decimals(value: str | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if 0 <= parsed <= 255 else None


def _symbol(value: str | None, address: str) -> str:
    cleaned = (value or "").strip()
    return cleaned or f"{address[:8]}...{address[-4:]}"


def _issue_warning(evidence: ProtocolRawEvidence) -> str:
    stage = (evidence.value("stage") or "position data").replace("_", " ")
    token_id = evidence.value("token_id")
    suffix = f" for NFT #{token_id}" if token_id is not None else ""
    return f"Uniswap V3 {stage} could not be loaded{suffix}."


def evidence_values(values: dict[str, str]) -> tuple[ProtocolEvidenceValue, ...]:
    """Convert calculated metadata to stable immutable values."""
    return tuple(
        ProtocolEvidenceValue(name=name, value=value)
        for name, value in sorted(values.items())
    )
