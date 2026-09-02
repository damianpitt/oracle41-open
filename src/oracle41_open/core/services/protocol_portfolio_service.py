"""Price stored protocol positions for safe portfolio aggregation.

The service values underlying assets with the existing pricing provider, treats debt as a
liability, and identifies Aave receipt tokens that represent already-counted positions. Missing
prices and partial snapshots remain explicit so callers cannot present an incomplete net total as
complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from oracle41_open.core.models import (
    Chain,
    ProtocolAdapterStatus,
    ProtocolAsset,
    ProtocolAssetRole,
    ProtocolPosition,
    ProtocolPositionCompleteness,
    ProtocolPositionKind,
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
    unpriced_position_count: int
    excluded_receipt_token_count: int
    asset_usd_total: Decimal
    liability_usd_total: Decimal
    net_usd: Decimal
    positions: tuple[ProtocolPositionValuation, ...]
    aggregates: tuple[ProtocolAggregateValuation, ...]
    wallets: tuple[ProtocolWalletValuation, ...]


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

    def __init__(self, pricing_provider: ProtocolAssetPricingProvider) -> None:
        self._pricing_provider = pricing_provider

    def value(
        self,
        inputs: tuple[ProtocolPortfolioInput, ...],
    ) -> ProtocolPortfolioValuation:
        prices = self._load_prices(inputs)
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

        for item in inputs:
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
                    valuation = _value_asset(item.snapshot, position, asset, prices)
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
        observed_at=snapshot.observed_at,
    )


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
        if evidence.kind != "aave_v3_reserve_position":
            continue
        if _positive(evidence.value("current_a_token_balance")):
            _add_address(addresses, evidence.value("a_token_contract"))
        if _positive(evidence.value("current_stable_debt")):
            _add_address(addresses, evidence.value("stable_debt_token_contract"))
        if _positive(evidence.value("current_variable_debt")):
            _add_address(addresses, evidence.value("variable_debt_token_contract"))
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
