"""Test protocol pricing, liabilities, and receipt-token double-count protection.

The cases use immutable stored snapshots and local prices. They prove that supplied assets add to
portfolio value, debt subtracts from it, receipt tokens are excluded, and missing evidence keeps
the final total explicitly incomplete.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from oracle41_open.core.models import (
    Chain,
    ProtocolAdapterResult,
    ProtocolAdapterStatus,
    ProtocolAsset,
    ProtocolAssetRole,
    ProtocolEvidenceValue,
    ProtocolPosition,
    ProtocolPositionCompleteness,
    ProtocolPositionKind,
    ProtocolPositionProvenance,
    ProtocolRawEvidence,
    ProtocolRiskSnapshot,
    ProtocolRiskState,
    StoredProtocolSnapshot,
    Token,
    TokenBalance,
    ValidationError,
    WalletOverviewResult,
    WatchlistEntry,
)
from oracle41_open.core.services.portfolio_service import PortfolioService
from oracle41_open.core.services.protocol_portfolio_service import (
    ProtocolObservationFreshness,
    ProtocolPortfolioInput,
    ProtocolPortfolioService,
    protocol_receipt_token_addresses,
)

_WALLET = "0x1111111111111111111111111111111111111111"
_UNDERLYING = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_A_TOKEN = "0x98c23e9d8f34fefeaaba7d1bfee3b5b2720c4a39"
_VARIABLE_DEBT_TOKEN = "0x72e95bcba2c5d0fe2291e84a3081dbe01fabd900"
_COMET = "0xc3d688b66703497daa19211eedff47f25384cdc3"
_DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
_OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def _valuator(
    pricing: _PricingProvider,
    *,
    now: datetime = _OBSERVED_AT,
    stale_after_seconds: int = 3_600,
) -> ProtocolPortfolioService:
    return ProtocolPortfolioService(
        pricing,
        stale_after_seconds=stale_after_seconds,
        now_func=lambda: now,
    )


def test_protocol_valuation_adds_assets_subtracts_debt_and_excludes_receipts() -> None:
    pricing = _PricingProvider({_UNDERLYING: Decimal("1")})
    overview = _overview()

    result = _valuator(pricing).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, overview, _snapshot()),)
    )

    assert result.snapshot_count == 1
    assert result.asset_usd_total == Decimal("100")
    assert result.liability_usd_total == Decimal("20")
    assert result.net_usd == Decimal("80")
    assert result.unpriced_position_count == 0
    assert result.excluded_receipt_token_count == 1
    assert result.stale_snapshot_count == 0
    assert result.future_observation_count == 0
    assert result.missing_risk_snapshot_count == 0
    assert result.wallets[0].adjusted_wallet_total_usd == Decimal("50")
    assert result.wallets[0].excluded_receipt_token_addresses == (
        _VARIABLE_DEBT_TOKEN,
        _A_TOKEN,
    )
    assert [item.net_value_usd for item in result.positions] == [
        Decimal("100"),
        Decimal("-20"),
    ]
    assert pricing.calls == [(Chain.ETHEREUM, [_UNDERLYING])]
    assert result.risk_reports[0].health_factor == Decimal("4.125")
    assert result.risk_reports[0].risk_state is ProtocolRiskState.ABOVE_OR_EQUAL_LIQUIDATION_THRESHOLD
    assert result.risk_reports[0].observation_freshness is ProtocolObservationFreshness.FRESH
    assert result.risk_reports[0].adapter_id == "oracle41.aave-v3"
    assert result.risk_reports[0].adapter_version == "1"
    assert result.risk_reports[0].source_reference == "eth_call:block:24000000"
    assert result.positions[0].observation_freshness is ProtocolObservationFreshness.FRESH


def test_compound_base_receipt_is_excluded_only_for_positive_supply() -> None:
    evidence = ProtocolRawEvidence(
        kind="compound_v3_base_position",
        reference="eth_call:base-position:block:24000000",
        contract_address=_COMET,
        tx_hash=None,
        signature="balanceOf(address),borrowBalanceOf(address)",
        values=(ProtocolEvidenceValue("supplied_base", "100000000"),),
    )
    snapshot = _snapshot()
    positive = replace(snapshot, result=replace(snapshot.result, raw_evidence=(evidence,)))
    zero = replace(
        positive,
        result=replace(
            positive.result,
            raw_evidence=(
                replace(
                    evidence,
                    values=(ProtocolEvidenceValue("supplied_base", "0"),),
                ),
            ),
        ),
    )

    assert protocol_receipt_token_addresses(positive) == {_COMET}
    assert protocol_receipt_token_addresses(zero) == set()


def test_protocol_observation_age_reports_stale_and_future_times() -> None:
    threshold = _valuator(
        _PricingProvider({_UNDERLYING: Decimal("1")}),
        now=_OBSERVED_AT + timedelta(seconds=3_600),
    ).value((ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), _snapshot()),))
    over_threshold = _valuator(
        _PricingProvider({_UNDERLYING: Decimal("1")}),
        now=_OBSERVED_AT + timedelta(seconds=3_601),
    ).value((ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), _snapshot()),))
    stale = _valuator(
        _PricingProvider({_UNDERLYING: Decimal("1")}),
        now=_OBSERVED_AT + timedelta(hours=2),
    ).value((ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), _snapshot()),))
    future = _valuator(
        _PricingProvider({_UNDERLYING: Decimal("1")}),
        now=_OBSERVED_AT - timedelta(seconds=30),
    ).value((ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), _snapshot()),))

    assert threshold.stale_snapshot_count == 0
    assert threshold.risk_reports[0].observation_freshness is ProtocolObservationFreshness.FRESH
    assert over_threshold.stale_snapshot_count == 1
    assert stale.stale_snapshot_count == 1
    assert stale.risk_reports[0].observation_age_seconds == 7_200
    assert stale.risk_reports[0].observation_freshness is ProtocolObservationFreshness.STALE
    assert future.future_observation_count == 1
    assert future.risk_reports[0].observation_age_seconds == -30
    assert future.risk_reports[0].observation_freshness is ProtocolObservationFreshness.FUTURE


def test_protocol_risk_report_distinguishes_no_debt_and_missing_risk() -> None:
    snapshot = _snapshot()
    assert snapshot.result.risk_snapshot is not None
    no_debt_snapshot = replace(
        snapshot,
        result=replace(
            snapshot.result,
            risk_snapshot=replace(
                snapshot.result.risk_snapshot,
                total_debt_base="0",
                health_factor_wad=str((1 << 256) - 1),
                state=ProtocolRiskState.NO_DEBT,
            ),
        ),
    )
    missing_risk_snapshot = replace(
        snapshot,
        result=replace(snapshot.result, risk_snapshot=None),
    )

    no_debt = _valuator(_PricingProvider({})).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), no_debt_snapshot),)
    )
    missing = _valuator(_PricingProvider({})).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), missing_risk_snapshot),)
    )

    assert no_debt.risk_reports[0].health_factor is None
    assert no_debt.risk_reports[0].risk_state is ProtocolRiskState.NO_DEBT
    assert missing.missing_risk_snapshot_count == 1
    assert missing.risk_reports[0].risk_state is None


def test_protocol_valuation_keeps_missing_prices_and_partial_state_explicit() -> None:
    snapshot = _snapshot(status=ProtocolAdapterStatus.PARTIAL)

    result = _valuator(_PricingProvider({})).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), snapshot),)
    )

    assert result.partial_snapshot_count == 1
    assert result.unpriced_position_count == 2
    assert result.asset_usd_total == Decimal("0")
    assert result.liability_usd_total == Decimal("0")
    assert all(item.value_usd is None for item in result.positions)


def test_protocol_valuation_rejects_non_finite_amounts_and_prices() -> None:
    snapshot = _snapshot()
    supplied = snapshot.result.positions[0]
    invalid_asset = replace(supplied.assets[0], raw_amount="NaN")
    invalid_snapshot = replace(
        snapshot,
        result=replace(
            snapshot.result,
            positions=(replace(supplied, assets=(invalid_asset,)), *snapshot.result.positions[1:]),
        ),
    )

    invalid_amount = _valuator(_PricingProvider({_UNDERLYING: Decimal("1")})).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), invalid_snapshot),)
    )
    invalid_price = _valuator(_PricingProvider({_UNDERLYING: Decimal("Infinity")})).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), snapshot),)
    )

    assert invalid_amount.unpriced_position_count == 1
    assert invalid_amount.liability_usd_total == Decimal("20")
    assert invalid_price.unpriced_position_count == 2
    assert invalid_price.net_usd == Decimal("0")


def test_portfolio_integration_uses_net_protocol_value_without_double_counting() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
        protocol_snapshot_reader=_SnapshotReader(_snapshot()),
        protocol_valuator=_valuator(_PricingProvider({_UNDERLYING: Decimal("1")})),
    )

    result = service.load_portfolio()

    assert result.total_usd == Decimal("130")
    assert result.known_total_usd == Decimal("130")
    assert result.protocol_asset_usd_total == Decimal("100")
    assert result.protocol_liability_usd_total == Decimal("20")
    assert result.protocol_net_usd == Decimal("80")
    assert result.excluded_receipt_token_count == 1
    assert [item.symbol for item in result.token_aggregates] == ["DAI"]
    assert result.wallet_results[0].adjusted_wallet_total_usd == Decimal("50")


def test_portfolio_total_is_incomplete_when_protocol_price_is_missing() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
        protocol_snapshot_reader=_SnapshotReader(_snapshot()),
        protocol_valuator=_valuator(_PricingProvider({})),
    )

    result = service.load_portfolio()

    assert result.total_usd is None
    assert result.known_total_usd == Decimal("50")
    assert result.protocol_unpriced_position_count == 2


def test_portfolio_total_is_incomplete_when_protocol_observation_is_stale() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
        protocol_snapshot_reader=_SnapshotReader(_snapshot()),
        protocol_valuator=_valuator(
            _PricingProvider({_UNDERLYING: Decimal("1")}),
            now=_OBSERVED_AT + timedelta(hours=2),
        ),
    )

    result = service.load_portfolio()

    assert result.total_usd is None
    assert result.known_total_usd == Decimal("130")
    assert result.stale_protocol_snapshot_count == 1
    assert result.protocol_risk_reports[0].health_factor == Decimal("4.125")


def test_protocol_storage_failure_keeps_wallet_value_but_marks_total_partial() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
        protocol_snapshot_reader=_FailingSnapshotReader(),
        protocol_valuator=_valuator(_PricingProvider({})),
    )

    result = service.load_portfolio()

    assert result.total_usd is None
    assert result.known_total_usd == Decimal("150")
    assert result.protocol_failed_wallet_count == 1
    assert result.wallet_results[0].protocol_error == "protocol database unavailable"


def test_portfolio_can_refresh_and_load_one_exact_protocol_block() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    snapshot = _snapshot()
    collector = _SnapshotCollector(snapshot.result)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
        protocol_snapshot_reader=_SnapshotReader(snapshot),
        protocol_valuator=_valuator(_PricingProvider({_UNDERLYING: Decimal("1")})),
        protocol_snapshot_collector=collector,
    )

    result = service.load_portfolio(
        protocol_snapshot_block_number=24_000_000,
        refresh_protocol_snapshots=True,
    )

    assert collector.calls == [(_WALLET, Chain.ETHEREUM, 24_000_000, True)]
    assert result.protocol_snapshot_mode == "exact"
    assert result.requested_protocol_block_number == 24_000_000
    assert result.protocol_snapshot_count == 1
    assert result.total_usd is None
    assert result.known_total_usd == Decimal("130")


def test_portfolio_marks_a_missing_exact_protocol_snapshot() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
        protocol_snapshot_reader=_SnapshotReader(_snapshot()),
        protocol_valuator=_valuator(_PricingProvider({})),
    )

    result = service.load_portfolio(protocol_snapshot_block_number=23_000_000)

    assert result.total_usd is None
    assert result.known_total_usd == Decimal("150")
    assert result.protocol_missing_snapshot_wallet_count == 1
    assert result.wallet_results[0].protocol_error == (
        "No protocol snapshot is stored at block 23000000."
    )


def test_portfolio_protocol_refresh_requires_an_exact_block() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
    )

    with pytest.raises(ValidationError, match="exact block"):
        service.load_portfolio(refresh_protocol_snapshots=True)


class _PricingProvider:
    def __init__(self, quotes: dict[str, Decimal]) -> None:
        self._quotes = quotes
        self.calls: list[tuple[Chain, list[str]]] = []

    def get_token_prices(
        self,
        chain: Chain,
        contract_addresses: list[str],
    ) -> dict[str, Decimal]:
        self.calls.append((chain, contract_addresses))
        return {
            address: self._quotes[address]
            for address in contract_addresses
            if address in self._quotes
        }


class _WatchlistReader:
    def __init__(self, entry: WatchlistEntry) -> None:
        self._entry = entry

    def list_entries(self, chain: Chain | None = None) -> list[WatchlistEntry]:
        if chain is not None and chain is not self._entry.chain:
            return []
        return [self._entry]


class _WalletLoader:
    def __init__(self, overview: WalletOverviewResult) -> None:
        self._overview = overview

    def load_wallet_overview(
        self,
        address: str,
        chain: Chain,
        hide_unverified: bool = True,
        hide_dust: bool = False,
        dust_threshold_usd: str | Decimal = "1",
        force_refresh: bool = False,
    ) -> WalletOverviewResult:
        return self._overview


class _SnapshotReader:
    def __init__(self, snapshot: StoredProtocolSnapshot) -> None:
        self._snapshot = snapshot

    def list_latest_snapshots(
        self,
        wallet_address: str,
        chain: Chain,
    ) -> tuple[StoredProtocolSnapshot, ...]:
        return (self._snapshot,)

    def list_snapshots_at_block(
        self,
        wallet_address: str,
        chain: Chain,
        block_number: int,
    ) -> tuple[StoredProtocolSnapshot, ...]:
        if block_number != self._snapshot.block_number:
            return ()
        return (self._snapshot,)


class _FailingSnapshotReader:
    def list_latest_snapshots(
        self,
        wallet_address: str,
        chain: Chain,
    ) -> tuple[StoredProtocolSnapshot, ...]:
        raise RuntimeError("protocol database unavailable")

    def list_snapshots_at_block(
        self,
        wallet_address: str,
        chain: Chain,
        block_number: int,
    ) -> tuple[StoredProtocolSnapshot, ...]:
        raise RuntimeError("protocol database unavailable")


class _SnapshotCollector:
    def __init__(self, result: ProtocolAdapterResult) -> None:
        self._result = result
        self.calls: list[tuple[str, Chain, int, bool]] = []

    def refresh_supported_positions(
        self,
        wallet_address: str,
        chain: Chain,
        block_number: int,
        force_refresh: bool = False,
    ) -> tuple[ProtocolAdapterResult, ...]:
        self.calls.append((wallet_address, chain, block_number, force_refresh))
        return (self._result,)


def _snapshot(
    status: ProtocolAdapterStatus = ProtocolAdapterStatus.MATCHED,
) -> StoredProtocolSnapshot:
    provenance = ProtocolPositionProvenance(
        adapter_id="oracle41.aave-v3",
        adapter_version="1",
        source_provider="alchemy",
        source_reference="eth_call:block:24000000",
        observed_at=_OBSERVED_AT,
    )
    positions = (
        _position(
            "supply",
            ProtocolPositionKind.COLLATERAL,
            ProtocolAssetRole.COLLATERAL,
            "100000000",
            provenance,
        ),
        _position(
            "debt",
            ProtocolPositionKind.DEBT,
            ProtocolAssetRole.BORROWED,
            "20000000",
            provenance,
        ),
    )
    evidence = ProtocolRawEvidence(
        kind="aave_v3_reserve_position",
        reference="eth_call:getUserReserveData:block:24000000",
        contract_address="0x0a16f2fcc0d44fae41cc54e079281d84a363becd",
        tx_hash=None,
        signature="getUserReserveData(address,address)",
        values=(
            ProtocolEvidenceValue("current_a_token_balance", "100000000"),
            ProtocolEvidenceValue("current_stable_debt", "0"),
            ProtocolEvidenceValue("current_variable_debt", "20000000"),
            ProtocolEvidenceValue("a_token_contract", _A_TOKEN),
            ProtocolEvidenceValue(
                "variable_debt_token_contract",
                _VARIABLE_DEBT_TOKEN,
            ),
        ),
    )
    adapter_result = ProtocolAdapterResult(
        schema_version=1,
        status=status,
        adapter_id="oracle41.aave-v3",
        adapter_version="1",
        protocol_id="aave-v3",
        protocol_name="Aave V3",
        positions=positions,
        source_actions=(),
        source_balances=(),
        source_events=(),
        raw_evidence=(evidence,),
        warnings=("partial fixture",) if status is ProtocolAdapterStatus.PARTIAL else (),
        risk_snapshot=ProtocolRiskSnapshot(
            wallet_address=_WALLET,
            chain=Chain.ETHEREUM,
            block_number=24_000_000,
            protocol_id="aave-v3",
            total_collateral_base="12500000000",
            total_debt_base="2500000000",
            available_borrow_base="6500000000",
            liquidation_threshold_bps=8250,
            ltv_bps=8000,
            health_factor_wad="4125000000000000000",
            base_currency_unit="100000000",
            state=ProtocolRiskState.ABOVE_OR_EQUAL_LIQUIDATION_THRESHOLD,
            provenance=provenance,
        ),
    )
    return StoredProtocolSnapshot(
        wallet_address=_WALLET,
        chain=Chain.ETHEREUM,
        protocol_id="aave-v3",
        block_number=24_000_000,
        result=adapter_result,
        source_provider="alchemy",
        observed_at=_OBSERVED_AT,
        saved_at=_OBSERVED_AT,
    )


def _position(
    suffix: str,
    kind: ProtocolPositionKind,
    role: ProtocolAssetRole,
    raw_amount: str,
    provenance: ProtocolPositionProvenance,
) -> ProtocolPosition:
    return ProtocolPosition(
        schema_version=1,
        position_id=f"ethereum:aave-v3:{_WALLET}:{suffix}",
        wallet_address=_WALLET,
        chain=Chain.ETHEREUM,
        block_number=24_000_000,
        protocol_id="aave-v3",
        protocol_name="Aave V3",
        kind=kind,
        label=f"Aave V3 {kind.value} USDC",
        assets=(
            ProtocolAsset(
                role=role,
                standard="ERC-20",
                contract_address=_UNDERLYING,
                symbol="USDC",
                token_id=None,
                raw_amount=raw_amount,
                decimals=6,
            ),
        ),
        contract_addresses=(_UNDERLYING,),
        completeness=ProtocolPositionCompleteness.COMPLETE,
        warnings=(),
        provenance=provenance,
    )


def _overview() -> WalletOverviewResult:
    return WalletOverviewResult(
        native_balance=Decimal("0"),
        native_price_usd=Decimal("2000"),
        token_balances=[
            _balance(_A_TOKEN, "aUSDC", Decimal("100"), Decimal("1")),
            _balance(_DAI, "DAI", Decimal("50"), Decimal("1")),
        ],
        total_usd=Decimal("150"),
        updated_at=_OBSERVED_AT,
    )


def _balance(
    contract: str,
    symbol: str,
    amount: Decimal,
    price: Decimal,
) -> TokenBalance:
    return TokenBalance(
        token=Token(contract, symbol, symbol, 6, True),
        balance_decimal=amount,
        price_usd=price,
    )
