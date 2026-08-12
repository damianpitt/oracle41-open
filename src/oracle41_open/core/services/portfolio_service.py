"""Build a combined portfolio from saved wallets.

The service loads each watchlist entry, groups balances by token and chain, and records wallet-level failures.
One failed wallet does not hide successful portfolio results from other wallets.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from oracle41_open.core.models import Chain, TokenBalance, WalletOverviewResult, WatchlistEntry


class WatchlistReader(Protocol):
    def list_entries(self, chain: Chain | None = None) -> list[WatchlistEntry]:
        ...


class WalletOverviewLoader(Protocol):
    def load_wallet_overview(
        self,
        address: str,
        chain: Chain,
        hide_unverified: bool = True,
        hide_dust: bool = False,
        dust_threshold_usd: str | Decimal = "1",
        force_refresh: bool = False,
    ) -> WalletOverviewResult:
        ...


@dataclass(frozen=True)
class PortfolioWalletResult:
    entry: WatchlistEntry
    overview: WalletOverviewResult | None
    error: str | None

    @property
    def is_loaded(self) -> bool:
        return self.overview is not None and self.error is None

    @property
    def is_truncated(self) -> bool:
        if self.overview is None:
            return False
        return self.overview.token_balances_truncated


@dataclass(frozen=True)
class PortfolioChainAggregate:
    chain: Chain
    wallet_count: int
    native_balance_total: Decimal
    native_usd_total: Decimal
    native_usd_missing_wallet_count: int


@dataclass(frozen=True)
class PortfolioTokenAggregate:
    chain: Chain
    contract_address: str
    symbol: str
    name: str
    decimals: int
    wallet_count: int
    total_balance: Decimal
    total_usd: Decimal
    usd_missing_wallet_count: int


@dataclass(frozen=True)
class PortfolioLoadResult:
    selected_wallet_count: int
    loaded_wallet_count: int
    failed_wallet_count: int
    truncated_wallet_count: int
    wallets_missing_total_usd_count: int
    total_usd: Decimal | None
    known_total_usd: Decimal | None
    chain_aggregates: list[PortfolioChainAggregate]
    token_aggregates: list[PortfolioTokenAggregate]
    wallet_results: list[PortfolioWalletResult]


@dataclass
class _MutableTokenAggregate:
    chain: Chain
    contract_address: str
    symbol: str
    name: str
    decimals: int
    wallet_count: int = 0
    total_balance: Decimal = Decimal("0")
    total_usd: Decimal = Decimal("0")
    usd_missing_wallet_count: int = 0


class PortfolioService:
    def __init__(self, watchlist_reader: WatchlistReader, wallet_loader: WalletOverviewLoader) -> None:
        self._watchlist_reader = watchlist_reader
        self._wallet_loader = wallet_loader

    def load_portfolio(
        self,
        chain: Chain | None = None,
        selected_entry_ids: list[int] | None = None,
        hide_unverified: bool = True,
        hide_dust: bool = False,
        dust_threshold_usd: str | Decimal = "1",
        force_refresh: bool = False,
    ) -> PortfolioLoadResult:
        entries = self._watchlist_reader.list_entries(chain=chain)
        if selected_entry_ids is not None:
            selected_ids = {entry_id for entry_id in selected_entry_ids if isinstance(entry_id, int)}
            entries = [entry for entry in entries if entry.id in selected_ids]

        wallet_results: list[PortfolioWalletResult] = []
        loaded_wallet_count = 0
        failed_wallet_count = 0
        truncated_wallet_count = 0
        wallets_missing_total_usd_count = 0
        known_total_usd = Decimal("0")

        chain_wallet_count: dict[Chain, int] = {}
        chain_native_balance_total: dict[Chain, Decimal] = {}
        chain_native_usd_total: dict[Chain, Decimal] = {}
        chain_native_usd_missing: dict[Chain, int] = {}
        token_aggregates_by_key: dict[tuple[Chain, str], _MutableTokenAggregate] = {}

        for entry in entries:
            try:
                overview = self._wallet_loader.load_wallet_overview(
                    address=entry.address,
                    chain=entry.chain,
                    hide_unverified=hide_unverified,
                    hide_dust=hide_dust,
                    dust_threshold_usd=dust_threshold_usd,
                    force_refresh=force_refresh,
                )
            except Exception as error:
                failed_wallet_count += 1
                wallet_results.append(
                    PortfolioWalletResult(entry=entry, overview=None, error=str(error))
                )
                continue

            loaded_wallet_count += 1
            if overview.token_balances_truncated:
                truncated_wallet_count += 1
            wallet_results.append(PortfolioWalletResult(entry=entry, overview=overview, error=None))

            if overview.total_usd is None:
                wallets_missing_total_usd_count += 1
            else:
                known_total_usd += overview.total_usd

            chain_wallet_count[entry.chain] = chain_wallet_count.get(entry.chain, 0) + 1
            chain_native_balance_total[entry.chain] = (
                chain_native_balance_total.get(entry.chain, Decimal("0")) + overview.native_balance
            )
            if overview.native_price_usd is None:
                chain_native_usd_missing[entry.chain] = chain_native_usd_missing.get(entry.chain, 0) + 1
            else:
                chain_native_usd_total[entry.chain] = (
                    chain_native_usd_total.get(entry.chain, Decimal("0"))
                    + (overview.native_balance * overview.native_price_usd)
                )
            self._merge_token_balances(
                token_aggregates_by_key,
                chain=entry.chain,
                token_balances=overview.token_balances,
            )

        chain_aggregates: list[PortfolioChainAggregate] = []
        for chain_key in sorted(chain_wallet_count.keys(), key=lambda current: current.display_name):
            chain_aggregates.append(
                PortfolioChainAggregate(
                    chain=chain_key,
                    wallet_count=chain_wallet_count.get(chain_key, 0),
                    native_balance_total=chain_native_balance_total.get(chain_key, Decimal("0")),
                    native_usd_total=chain_native_usd_total.get(chain_key, Decimal("0")),
                    native_usd_missing_wallet_count=chain_native_usd_missing.get(chain_key, 0),
                )
            )

        token_aggregates = [
            PortfolioTokenAggregate(
                chain=aggregate.chain,
                contract_address=aggregate.contract_address,
                symbol=aggregate.symbol,
                name=aggregate.name,
                decimals=aggregate.decimals,
                wallet_count=aggregate.wallet_count,
                total_balance=aggregate.total_balance,
                total_usd=aggregate.total_usd,
                usd_missing_wallet_count=aggregate.usd_missing_wallet_count,
            )
            for aggregate in token_aggregates_by_key.values()
        ]
        token_aggregates.sort(
            key=lambda aggregate: (
                -aggregate.total_usd,
                -abs(aggregate.total_balance),
                aggregate.chain.display_name,
                aggregate.symbol,
            )
        )

        complete_total_usd: Decimal | None
        if loaded_wallet_count == 0 or failed_wallet_count > 0 or wallets_missing_total_usd_count > 0:
            complete_total_usd = None
        else:
            complete_total_usd = known_total_usd
        effective_known_total_usd = known_total_usd if loaded_wallet_count > 0 else None

        return PortfolioLoadResult(
            selected_wallet_count=len(entries),
            loaded_wallet_count=loaded_wallet_count,
            failed_wallet_count=failed_wallet_count,
            truncated_wallet_count=truncated_wallet_count,
            wallets_missing_total_usd_count=wallets_missing_total_usd_count,
            total_usd=complete_total_usd,
            known_total_usd=effective_known_total_usd,
            chain_aggregates=chain_aggregates,
            token_aggregates=token_aggregates,
            wallet_results=wallet_results,
        )

    def _merge_token_balances(
        self,
        token_aggregates_by_key: dict[tuple[Chain, str], _MutableTokenAggregate],
        chain: Chain,
        token_balances: list[TokenBalance],
    ) -> None:
        touched_keys: set[tuple[Chain, str]] = set()
        for balance in token_balances:
            key = (chain, balance.token.contract_address.lower())
            aggregate = token_aggregates_by_key.get(key)
            if aggregate is None:
                aggregate = _MutableTokenAggregate(
                    chain=chain,
                    contract_address=balance.token.contract_address.lower(),
                    symbol=balance.token.symbol,
                    name=balance.token.name,
                    decimals=balance.token.decimals,
                )
                token_aggregates_by_key[key] = aggregate

            aggregate.total_balance += balance.balance_decimal
            if balance.balance_usd is None:
                aggregate.usd_missing_wallet_count += 1
            else:
                aggregate.total_usd += balance.balance_usd
            touched_keys.add(key)

        for key in touched_keys:
            token_aggregates_by_key[key].wallet_count += 1
