"""Price stored protocol positions for safe portfolio aggregation.

The service values underlying assets with the existing pricing provider, treats debt as a
liability, and identifies protocol receipt tokens that represent already-counted positions. It also
builds immutable health and freshness reports from stored protocol evidence. Missing prices and
partial, stale, or future-dated snapshots remain explicit so callers cannot present an incomplete
net total as complete.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import floor
from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    ProtocolAdapterStatus,
    ProtocolAsset,
    ProtocolAssetRole,
    ProtocolPosition,
    ProtocolPositionCompleteness,
    ProtocolPositionKind,
    ProtocolRiskState,
    StoredProtocolSnapshot,
    WalletOverviewResult,
)


class ProtocolAssetPricingProvider(Protocol):
    def get_token_prices(
        self,
        chain: Chain,
        contract_addresses: list[str],
    ) -> dict[str, Decimal]:
        ...


class ProtocolObservationFreshness(str, Enum):
    """Classify the age of provider observation time, not chain-head distance."""

    FRESH = "fresh"
    STALE = "stale"
    FUTURE = "future"


@dataclass(frozen=True)
class ProtocolPortfolioInput:
    wallet_address: str
    chain: Chain
    overview: WalletOverviewResult
    snapshot: StoredProtocolSnapshot


@dataclass(frozen=True)
class ProtocolPositionValuation:
    wallet_address: str
    chain: Chain
    protocol_id: str
    protocol_name: str
    block_number: int
    position_id: str
    kind: ProtocolPositionKind
    label: str
    asset_role: ProtocolAssetRole
    asset_standard: str
    symbol: str | None
    contract_address: str | None
    token_id: str | None
    raw_amount: str
    decimals: int | None
    amount: Decimal | None
    price_usd: Decimal | None
    value_usd: Decimal | None
    net_value_usd: Decimal | None
    is_liability: bool
    completeness: ProtocolPositionCompleteness
    source_provider: str
    observed_at: datetime
    observation_age_seconds: int
    observation_freshness: ProtocolObservationFreshness


@dataclass(frozen=True)
class ProtocolRiskReport:
    """Expose one stored protocol snapshot's health and freshness evidence."""

    wallet_address: str
    chain: Chain
    protocol_id: str
    protocol_name: str
    block_number: int
    adapter_status: ProtocolAdapterStatus
    adapter_id: str
    adapter_version: str
    source_reference: str | None
    source_provider: str
    observed_at: datetime
    saved_at: datetime
    observation_age_seconds: int
    observation_freshness: ProtocolObservationFreshness
    stale_after_seconds: int
    warning_count: int
    warnings: tuple[str, ...]
    risk_state: ProtocolRiskState | None
    total_collateral_base: str | None
    total_debt_base: str | None
    available_borrow_base: str | None
    liquidation_threshold_bps: int | None
    ltv_bps: int | None
    health_factor_wad: str | None
    health_factor: Decimal | None
    base_currency_unit: str | None
    is_borrow_collateralized: bool | None = None
    is_liquidatable: bool | None = None


@dataclass(frozen=True)
class ProtocolAggregateValuation:
    chain: Chain
    protocol_id: str
    protocol_name: str
    position_count: int
    asset_usd_total: Decimal
    liability_usd_total: Decimal
    net_usd: Decimal
    unpriced_position_count: int


@dataclass(frozen=True)
class ProtocolWalletValuation:
    wallet_address: str
    chain: Chain
    snapshot_block_numbers: tuple[int, ...]
    adjusted_wallet_total_usd: Decimal | None
    excluded_receipt_token_addresses: tuple[str, ...]


@dataclass(frozen=True)
class ProtocolPortfolioValuation:
    snapshot_count: int
    partial_snapshot_count: int
    stale_snapshot_count: int
    future_observation_count: int
    missing_risk_snapshot_count: int
    unpriced_position_count: int
    excluded_receipt_token_count: int
    asset_usd_total: Decimal
    liability_usd_total: Decimal
    net_usd: Decimal
    positions: tuple[ProtocolPositionValuation, ...]
    aggregates: tuple[ProtocolAggregateValuation, ...]
    wallets: tuple[ProtocolWalletValuation, ...]
    risk_reports: tuple[ProtocolRiskReport, ...]


@dataclass
class _MutableAggregate:
    chain: Chain
    protocol_id: str
    protocol_name: str
    position_count: int = 0
    asset_usd_total: Decimal = Decimal("0")
    liability_usd_total: Decimal = Decimal("0")
    unpriced_position_count: int = 0


class ProtocolPortfolioService:
    """Convert stored raw protocol amounts into current USD valuations."""

    def __init__(
        self,
        pricing_provider: ProtocolAssetPricingProvider,
        stale_after_seconds: int = 3_600,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._pricing_provider = pricing_provider
        self._stale_after_seconds = max(0, stale_after_seconds)
        self._now = now_func or (lambda: datetime.now(tz=UTC))

    def value(
        self,
        inputs: tuple[ProtocolPortfolioInput, ...],
    ) -> ProtocolPortfolioValuation:
        prices = self._load_prices(inputs)
        now = _as_utc(self._now())
        risk_reports = tuple(self._risk_report(item.snapshot, now) for item in inputs)
        positions: list[ProtocolPositionValuation] = []
        wallets: list[ProtocolWalletValuation] = []
        aggregates: dict[tuple[Chain, str], _MutableAggregate] = {}
        partial_snapshot_count = 0
        excluded_receipt_token_count = 0

        wallet_groups: dict[
            tuple[str, Chain],
            tuple[WalletOverviewResult, set[str], set[int]],
        ] = {}
        for item in inputs:
            wallet_key = (item.wallet_address.lower(), item.chain)
            group = wallet_groups.get(wallet_key)
            if group is None:
                group = (item.overview, set(), set())
                wallet_groups[wallet_key] = group
            group[1].update(protocol_receipt_token_addresses(item.snapshot))
            group[2].add(item.snapshot.block_number)

        for (wallet_address, chain), (overview, excluded_addresses, blocks) in wallet_groups.items():
            excluded_receipt_token_count += sum(
                1
                for balance in overview.token_balances
                if balance.token.contract_address.lower() in excluded_addresses
            )
            wallets.append(
                ProtocolWalletValuation(
                    wallet_address=wallet_address,
                    chain=chain,
                    snapshot_block_numbers=tuple(sorted(blocks, reverse=True)),
                    adjusted_wallet_total_usd=adjusted_wallet_total(
                        overview,
                        excluded_addresses,
                    ),
                    excluded_receipt_token_addresses=tuple(sorted(excluded_addresses)),
                )
            )

        for item, risk_report in zip(inputs, risk_reports, strict=True):
            if (
                item.snapshot.result.status is not ProtocolAdapterStatus.MATCHED
                or any(
                    position.completeness is ProtocolPositionCompleteness.PARTIAL
                    for position in item.snapshot.result.positions
                )
            ):
                partial_snapshot_count += 1
            for position in item.snapshot.result.positions:
                aggregate_key = (position.chain, position.protocol_id)
                aggregate = aggregates.setdefault(
                    aggregate_key,
                    _MutableAggregate(
                        chain=position.chain,
                        protocol_id=position.protocol_id,
                        protocol_name=position.protocol_name,
                    ),
                )
                aggregate.position_count += 1
                position_is_unpriced = not position.assets
                for asset in position.assets:
                    valuation = _value_asset(
                        item.snapshot,
                        position,
                        asset,
                        prices,
                        risk_report.observation_age_seconds,
                        risk_report.observation_freshness,
                    )
                    positions.append(valuation)
                    if valuation.value_usd is None:
                        position_is_unpriced = True
                    elif valuation.is_liability:
                        aggregate.liability_usd_total += valuation.value_usd
                    else:
                        aggregate.asset_usd_total += valuation.value_usd
                if position_is_unpriced:
                    aggregate.unpriced_position_count += 1

        aggregate_values = tuple(
            ProtocolAggregateValuation(
                chain=item.chain,
                protocol_id=item.protocol_id,
                protocol_name=item.protocol_name,
                position_count=item.position_count,
                asset_usd_total=item.asset_usd_total,
                liability_usd_total=item.liability_usd_total,
                net_usd=item.asset_usd_total - item.liability_usd_total,
                unpriced_position_count=item.unpriced_position_count,
            )
            for item in sorted(
                aggregates.values(),
                key=lambda current: (current.chain.display_name, current.protocol_name),
            )
        )
        asset_total = sum(
            (item.asset_usd_total for item in aggregate_values),
            start=Decimal("0"),
        )
        liability_total = sum(
            (item.liability_usd_total for item in aggregate_values),
            start=Decimal("0"),
        )
        return ProtocolPortfolioValuation(
            snapshot_count=len(inputs),
            partial_snapshot_count=partial_snapshot_count,
            stale_snapshot_count=sum(
                report.observation_freshness is ProtocolObservationFreshness.STALE
                for report in risk_reports
            ),
            future_observation_count=sum(
                report.observation_freshness is ProtocolObservationFreshness.FUTURE
                for report in risk_reports
            ),
            missing_risk_snapshot_count=sum(
                report.risk_state is None for report in risk_reports
            ),
            unpriced_position_count=sum(
                item.unpriced_position_count for item in aggregate_values
            ),
            excluded_receipt_token_count=excluded_receipt_token_count,
            asset_usd_total=asset_total,
            liability_usd_total=liability_total,
            net_usd=asset_total - liability_total,
            positions=tuple(positions),
            aggregates=aggregate_values,
            wallets=tuple(wallets),
            risk_reports=risk_reports,
        )

    def _risk_report(
        self,
        snapshot: StoredProtocolSnapshot,
        now: datetime,
    ) -> ProtocolRiskReport:
        observed_at = _as_utc(snapshot.observed_at)
        exact_age_seconds = (now - observed_at).total_seconds()
        age_seconds = floor(exact_age_seconds)
        if exact_age_seconds < 0:
            freshness = ProtocolObservationFreshness.FUTURE
        elif exact_age_seconds > self._stale_after_seconds:
            freshness = ProtocolObservationFreshness.STALE
        else:
            freshness = ProtocolObservationFreshness.FRESH

        risk = snapshot.result.risk_snapshot
        provenance = risk.provenance if risk is not None else None
        health_factor = None
        if (
            risk is not None
            and risk.state is not ProtocolRiskState.NO_DEBT
            and risk.health_factor_wad is not None
        ):
            health_factor = _decimal_amount(risk.health_factor_wad, 18)
        return ProtocolRiskReport(
            wallet_address=snapshot.wallet_address,
            chain=snapshot.chain,
            protocol_id=snapshot.protocol_id,
            protocol_name=snapshot.result.protocol_name or snapshot.protocol_id,
            block_number=snapshot.block_number,
            adapter_status=snapshot.result.status,
            adapter_id=snapshot.result.adapter_id,
            adapter_version=snapshot.result.adapter_version,
            source_reference=(
                provenance.source_reference if provenance is not None else None
            ),
            source_provider=snapshot.source_provider,
            observed_at=observed_at,
            saved_at=_as_utc(snapshot.saved_at),
            observation_age_seconds=age_seconds,
            observation_freshness=freshness,
            stale_after_seconds=self._stale_after_seconds,
            warning_count=len(snapshot.result.warnings),
            warnings=snapshot.result.warnings,
            risk_state=risk.state if risk is not None else None,
            total_collateral_base=(
                risk.total_collateral_base if risk is not None else None
            ),
            total_debt_base=risk.total_debt_base if risk is not None else None,
            available_borrow_base=(
                risk.available_borrow_base if risk is not None else None
            ),
            liquidation_threshold_bps=(
                risk.liquidation_threshold_bps if risk is not None else None
            ),
            ltv_bps=risk.ltv_bps if risk is not None else None,
            health_factor_wad=risk.health_factor_wad if risk is not None else None,
            health_factor=health_factor,
            base_currency_unit=risk.base_currency_unit if risk is not None else None,
            is_borrow_collateralized=(
                risk.is_borrow_collateralized if risk is not None else None
            ),
            is_liquidatable=risk.is_liquidatable if risk is not None else None,
        )

    def _load_prices(
        self,
        inputs: tuple[ProtocolPortfolioInput, ...],
    ) -> dict[tuple[Chain, str], Decimal]:
        addresses_by_chain: dict[Chain, set[str]] = {}
        for item in inputs:
            for position in item.snapshot.result.positions:
                for asset in position.assets:
                    if asset.contract_address is not None:
                        addresses_by_chain.setdefault(position.chain, set()).add(
                            asset.contract_address.lower()
                        )

        prices: dict[tuple[Chain, str], Decimal] = {}
        for chain, addresses in addresses_by_chain.items():
            quotes = self._pricing_provider.get_token_prices(chain, sorted(addresses))
            for address, value in quotes.items():
                normalized = address.lower()
                if normalized in addresses and value.is_finite() and value >= 0:
                    prices[(chain, normalized)] = value
        return prices


def _value_asset(
    snapshot: StoredProtocolSnapshot,
    position: ProtocolPosition,
    asset: ProtocolAsset,
    prices: dict[tuple[Chain, str], Decimal],
    observation_age_seconds: int,
    observation_freshness: ProtocolObservationFreshness,
) -> ProtocolPositionValuation:
    amount = _decimal_amount(asset.raw_amount, asset.decimals)
    contract = asset.contract_address.lower() if asset.contract_address is not None else None
    price = prices.get((position.chain, contract)) if contract is not None else None
    value = amount * price if amount is not None and price is not None else None
    is_liability = (
        position.kind is ProtocolPositionKind.DEBT
        or asset.role is ProtocolAssetRole.BORROWED
    )
    return ProtocolPositionValuation(
        wallet_address=position.wallet_address,
        chain=position.chain,
        protocol_id=position.protocol_id,
        protocol_name=position.protocol_name,
        block_number=position.block_number,
        position_id=position.position_id,
        kind=position.kind,
        label=position.label,
        asset_role=asset.role,
        asset_standard=asset.standard,
        symbol=asset.symbol,
        contract_address=contract,
        token_id=asset.token_id,
        raw_amount=asset.raw_amount,
        decimals=asset.decimals,
        amount=amount,
        price_usd=price,
        value_usd=value,
        net_value_usd=(-value if is_liability else value) if value is not None else None,
        is_liability=is_liability,
        completeness=position.completeness,
        source_provider=snapshot.source_provider,
        observed_at=_as_utc(snapshot.observed_at),
        observation_age_seconds=observation_age_seconds,
        observation_freshness=observation_freshness,
    )


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _decimal_amount(raw_amount: str, decimals: int | None) -> Decimal | None:
    if decimals is None or decimals < 0:
        return None
    try:
        amount = Decimal(raw_amount)
    except InvalidOperation:
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount.scaleb(-decimals)


def protocol_receipt_token_addresses(snapshot: StoredProtocolSnapshot) -> set[str]:
    """Return receipt tokens that mirror positive positions in this snapshot."""
    addresses: set[str] = set()
    for evidence in snapshot.result.raw_evidence:
        if evidence.kind == "aave_v3_reserve_position":
            if _positive(evidence.value("current_a_token_balance")):
                _add_address(addresses, evidence.value("a_token_contract"))
            if _positive(evidence.value("current_stable_debt")):
                _add_address(addresses, evidence.value("stable_debt_token_contract"))
            if _positive(evidence.value("current_variable_debt")):
                _add_address(addresses, evidence.value("variable_debt_token_contract"))
        elif evidence.kind == "compound_v3_base_position" and _positive(
            evidence.value("supplied_base")
        ):
            _add_address(addresses, evidence.contract_address)
    return addresses


def _positive(raw: str | None) -> bool:
    if raw is None:
        return False
    try:
        return int(raw) > 0
    except ValueError:
        return False


def _add_address(addresses: set[str], raw: str | None) -> None:
    if raw is None:
        return
    normalized = raw.lower()
    if len(normalized) != 42 or not normalized.startswith("0x"):
        return
    try:
        int(normalized[2:], 16)
    except ValueError:
        return
    addresses.add(normalized)


def adjusted_wallet_total(
    overview: WalletOverviewResult,
    excluded_addresses: set[str],
) -> Decimal | None:
    if overview.total_usd is None:
        return None
    excluded_usd = sum(
        (
            balance.balance_usd
            for balance in overview.token_balances
            if balance.token.contract_address.lower() in excluded_addresses
            and balance.balance_usd is not None
        ),
        start=Decimal("0"),
    )
    return max(Decimal("0"), overview.total_usd - excluded_usd)
