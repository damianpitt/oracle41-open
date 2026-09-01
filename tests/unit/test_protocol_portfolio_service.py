"""Test protocol pricing, liabilities, and receipt-token double-count protection.

The cases use immutable stored snapshots and local prices. They prove that supplied assets add to
portfolio value, debt subtracts from it, receipt tokens are excluded, and missing evidence keeps
the final total explicitly incomplete.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

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
    StoredProtocolSnapshot,
    Token,
    TokenBalance,
    WalletOverviewResult,
    WatchlistEntry,
)
from oracle41_open.core.services.portfolio_service import PortfolioService
from oracle41_open.core.services.protocol_portfolio_service import (
    ProtocolPortfolioInput,
    ProtocolPortfolioService,
)

_WALLET = "0x1111111111111111111111111111111111111111"
_UNDERLYING = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_A_TOKEN = "0x98c23e9d8f34fefeaaba7d1bfee3b5b2720c4a39"
_VARIABLE_DEBT_TOKEN = "0x72e95bcba2c5d0fe2291e84a3081dbe01fabd900"
_DAI = "0x6b175474e89094c44da98b954eedeac495271d0f"
_OBSERVED_AT = datetime(2026, 9, 1, tzinfo=UTC)


def test_protocol_valuation_adds_assets_subtracts_debt_and_excludes_receipts() -> None:
    pricing = _PricingProvider({_UNDERLYING: Decimal("1")})
    overview = _overview()

    result = ProtocolPortfolioService(pricing).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, overview, _snapshot()),)
    )

    assert result.snapshot_count == 1
    assert result.asset_usd_total == Decimal("100")
    assert result.liability_usd_total == Decimal("20")
    assert result.net_usd == Decimal("80")
    assert result.unpriced_position_count == 0
    assert result.excluded_receipt_token_count == 1
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


def test_protocol_valuation_keeps_missing_prices_and_partial_state_explicit() -> None:
    snapshot = _snapshot(status=ProtocolAdapterStatus.PARTIAL)

    result = ProtocolPortfolioService(_PricingProvider({})).value(
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

    invalid_amount = ProtocolPortfolioService(
        _PricingProvider({_UNDERLYING: Decimal("1")})
    ).value((ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), invalid_snapshot),))
    invalid_price = ProtocolPortfolioService(
        _PricingProvider({_UNDERLYING: Decimal("Infinity")})
    ).value((ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), snapshot),))

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
        protocol_valuator=ProtocolPortfolioService(
            _PricingProvider({_UNDERLYING: Decimal("1")})
        ),
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
        protocol_valuator=ProtocolPortfolioService(_PricingProvider({})),
    )

    result = service.load_portfolio()

    assert result.total_usd is None
    assert result.known_total_usd == Decimal("50")
    assert result.protocol_unpriced_position_count == 2


def test_protocol_storage_failure_keeps_wallet_value_but_marks_total_partial() -> None:
    entry = WatchlistEntry(1, _WALLET, Chain.ETHEREUM, "Main", _OBSERVED_AT)
    service = PortfolioService(
        watchlist_reader=_WatchlistReader(entry),
        wallet_loader=_WalletLoader(_overview()),
        protocol_snapshot_reader=_FailingSnapshotReader(),
        protocol_valuator=ProtocolPortfolioService(_PricingProvider({})),
    )

    result = service.load_portfolio()

    assert result.total_usd is None
    assert result.known_total_usd == Decimal("150")
    assert result.protocol_failed_wallet_count == 1
    assert result.wallet_results[0].protocol_error == "protocol database unavailable"


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


class _FailingSnapshotReader:
    def list_latest_snapshots(
        self,
        wallet_address: str,
        chain: Chain,
    ) -> tuple[StoredProtocolSnapshot, ...]:
        raise RuntimeError("protocol database unavailable")


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
