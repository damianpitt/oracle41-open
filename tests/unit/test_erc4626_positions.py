"""Test reviewed ERC-4626 vault collection and portfolio accounting without live keys.

Synthetic ABI responses cover independent share/asset units, failed and zero conversions,
redemption limits, deployment identity, source consistency, storage reuse, exports, and receipt
double-count protection. No test submits a transaction or contacts a network service.
"""

from __future__ import annotations

import csv
import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path

import pytest
from eth_abi.abi import encode

from oracle41_open.core.models import (
    Chain,
    ContractReadResult,
    ProtocolAdapterContext,
    ProtocolAdapterStatus,
    ProtocolAssetRole,
    ProtocolPositionCompleteness,
    ProtocolRiskState,
    ProviderResponseError,
    ProviderTimeoutError,
    Token,
    TokenBalance,
    ValidationError,
    WalletOverviewResult,
    WatchlistEntry,
)
from oracle41_open.core.protocols import erc4626_vault, erc4626_vaults, production_protocol_registry
from oracle41_open.core.services.erc4626_position_service import ERC4626PositionService
from oracle41_open.core.services.portfolio_service import PortfolioService
from oracle41_open.core.services.protocol_collection import call_data, evidence_values
from oracle41_open.core.services.protocol_portfolio_service import (
    ProtocolPortfolioInput,
    ProtocolPortfolioService,
    protocol_receipt_token_addresses,
)
from oracle41_open.core.services.protocol_position_service import ProtocolPositionService
from oracle41_open.exports.csv_export import portfolio_csv_text
from oracle41_open.exports.json_export import portfolio_json_bytes
from oracle41_open.exports.templates import PortfolioExportTemplate
from oracle41_open.gui.views.portfolio_view import _render_result
from oracle41_open.storage.db import ProtocolPositionRepository, SQLiteDatabase

_WALLET = "0x" + "11" * 20
_BLOCK = 24_000_000
_NOW = datetime(2026, 10, 23, tzinfo=UTC)
_VAULT = erc4626_vault(Chain.ETHEREUM, "sdai")
_SHARES = 2 * 10**18
_ASSETS = 2_500_001


def _encoded(kind: str, value: object) -> str:
    return "0x" + encode((kind,), (value,)).hex()


def _responses(shares: int = _SHARES) -> dict[tuple[str, str], str]:
    # Deliberately use different decimals; share units never determine underlying units.
    return {
        (_VAULT.address, call_data("asset()")): _encoded("address", _VAULT.asset_address),
        (_VAULT.address, call_data("decimals()")): _encoded("uint8", 18),
        (_VAULT.asset_address, call_data("decimals()")): _encoded("uint8", 6),
        (_VAULT.address, call_data("balanceOf(address)", ("address",), (_WALLET,))): _encoded(
            "uint256", shares
        ),
        (_VAULT.address, call_data("convertToAssets(uint256)", ("uint256",), (shares,))): _encoded(
            "uint256", _ASSETS
        ),
        (_VAULT.address, call_data("previewRedeem(uint256)", ("uint256",), (shares,))): _encoded(
            "uint256", 2_400_000
        ),
        (_VAULT.address, call_data("maxRedeem(address)", ("address",), (_WALLET,))): _encoded(
            "uint256", shares
        ),
    }


class _Reader:
    def __init__(self, responses: dict[tuple[str, str], str] | None = None) -> None:
        self.responses = _responses() if responses is None else responses
        self.calls: list[tuple[str, str, Chain, int]] = []
        self.fail_signature: str | None = None
        self.mismatch: str | None = None

    def read_contract(
        self,
        contract_address: str,
        data: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        self.calls.append((contract_address, data, chain, block_number))
        if self.fail_signature and data.startswith(call_data(self.fail_signature)[:10]):
            raise ProviderTimeoutError("Synthetic unavailable contract read")
        result = ContractReadResult(
            chain=chain,
            contract_address=contract_address,
            block_number=block_number,
            data=self.responses[(contract_address, data)],
            source_provider="recorded-json-rpc",
            fetched_at=_NOW,
        )
        if self.mismatch and len(self.calls) == 5:
            return replace(
                result,
                **{
                    "provider": {"source_provider": "other-provider"},
                    "chain": {"chain": Chain.BASE},
                    "block": {"block_number": block_number + 1},
                    "contract": {"contract_address": _WALLET},
                }[self.mismatch],
            )
        return result


def _collect(reader: _Reader):
    return ERC4626PositionService(reader).load_vault_positions(
        _WALLET.upper(),
        Chain.ETHEREUM,
        "sdai",
        _BLOCK,
    )


def _repository(tmp_path: Path) -> ProtocolPositionRepository:
    return ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))


def test_vault_positions_use_contract_conversion_and_independent_decimals() -> None:
    reader = _Reader()
    result = _collect(reader)
    assert result.status is ProtocolAdapterStatus.MATCHED
    assert len(result.positions) == 1
    position = result.positions[0]
    assert position.assets[0].role is ProtocolAssetRole.UNDERLYING
    assert position.assets[0].raw_amount == str(_ASSETS)
    assert position.assets[0].decimals == 6
    assert position.metadata_value("raw_shares") == str(_SHARES)
    assert position.metadata_value("share_decimals") == "18"
    assert position.metadata_value("preview_redeem_assets") == "2400000"
    assert result.risk_snapshot.state is ProtocolRiskState.NOT_APPLICABLE
    assert {call[2:] for call in reader.calls} == {(Chain.ETHEREUM, _BLOCK)}
    assert len(reader.calls) == 7


def test_zero_shares_do_not_invent_a_position_or_request_conversion() -> None:
    reader = _Reader(_responses(shares=0))
    result = _collect(reader)
    assert result.status is ProtocolAdapterStatus.MATCHED
    assert result.positions == ()
    assert len(reader.calls) == 4


@pytest.mark.parametrize(
    "signature",
    [
        "convertToAssets(uint256)",
        "previewRedeem(uint256)",
        "maxRedeem(address)",
    ],
)
def test_optional_failures_are_explicit_partial_evidence(signature: str) -> None:
    reader = _Reader()
    reader.fail_signature = signature
    result = _collect(reader)
    assert result.status is ProtocolAdapterStatus.PARTIAL
    assert result.positions[0].completeness is ProtocolPositionCompleteness.PARTIAL
    assert any(signature in warning for warning in result.warnings)
    if signature.startswith("convertToAssets"):
        assert result.positions[0].assets == ()
    else:
        assert result.positions[0].assets[0].raw_amount == str(_ASSETS)


@pytest.mark.parametrize("maximum", [0, _SHARES - 1, _SHARES + 1])
def test_restricted_or_invalid_redemption_limits_do_not_reprice_accounting_value(
    maximum: int,
) -> None:
    responses = _responses()
    responses[(_VAULT.address, call_data("maxRedeem(address)", ("address",), (_WALLET,)))] = (
        _encoded("uint256", maximum)
    )
    result = _collect(_Reader(responses))
    assert result.status is ProtocolAdapterStatus.PARTIAL
    assert result.positions[0].assets[0].raw_amount == str(_ASSETS)
    assert any("redemption" in warning for warning in result.warnings)


@pytest.mark.parametrize("mismatch", ["provider", "chain", "block", "contract"])
def test_mixed_snapshot_context_never_becomes_an_optional_warning(mismatch: str) -> None:
    reader = _Reader()
    reader.mismatch = mismatch
    with pytest.raises(ProviderResponseError):
        _collect(reader)


def test_wrong_asset_or_malformed_required_data_is_rejected() -> None:
    for response in (_encoded("address", _WALLET), "0x12"):
        responses = _responses()
        responses[(_VAULT.address, call_data("asset()"))] = response
        with pytest.raises(ProviderResponseError):
            _collect(_Reader(responses))


@pytest.mark.parametrize(("wallet", "block"), [("bad", _BLOCK), (_WALLET, -1)])
def test_input_validation_precedes_provider_calls(wallet: str, block: int) -> None:
    reader = _Reader()
    with pytest.raises(ValidationError):
        ERC4626PositionService(reader).load_vault_positions(wallet, Chain.ETHEREUM, "sdai", block)
    assert reader.calls == []


def test_unknown_deployments_are_not_probed() -> None:
    reader = _Reader()
    service = ERC4626PositionService(reader)
    with pytest.raises(ValueError, match="not configured"):
        service.load_vault_positions(_WALLET, Chain.BASE, "sdai", _BLOCK)
    with pytest.raises(ValueError, match="not configured"):
        service.load_vault_positions(_WALLET, Chain.ETHEREUM, "unknown", _BLOCK)
    assert erc4626_vaults(Chain.ROBINHOOD) == ()
    assert service.load_positions(_WALLET, Chain.BASE, _BLOCK) == ()
    assert reader.calls == []


def test_saved_snapshot_reuse_force_refresh_and_failure_retention(tmp_path: Path) -> None:
    reader = _Reader()
    repository = _repository(tmp_path)
    service = ERC4626PositionService(reader, repository=repository)
    first = service.load_vault_positions(_WALLET, Chain.ETHEREUM, "sdai", _BLOCK)
    reader.calls.clear()
    reopened = ERC4626PositionService(reader, repository=_repository(tmp_path))
    assert reopened.load_vault_positions(_WALLET, Chain.ETHEREUM, "sdai", _BLOCK) == first
    assert reader.calls == []
    reopened.load_vault_positions(_WALLET, Chain.ETHEREUM, "sdai", _BLOCK, force_refresh=True)
    assert len(reader.calls) == 7
    reader.fail_signature = "asset()"
    with pytest.raises(ProviderTimeoutError):
        reopened.load_vault_positions(_WALLET, Chain.ETHEREUM, "sdai", _BLOCK, force_refresh=True)
    assert (
        repository.get_snapshot(_WALLET, Chain.ETHEREUM, _VAULT.protocol_id, _BLOCK).result == first
    )


@pytest.mark.parametrize(
    "field",
    ["raw_shares", "asset_decimals", "asset_contract", "vault_contract", "converted_assets"],
)
def test_adapter_rejects_malformed_fields_without_fabricating_amounts(field: str) -> None:
    result = _collect(_Reader())
    evidence = result.raw_evidence[0]
    values = {item.name: item.value for item in evidence.values}
    values[field] = "not-valid"
    context = ProtocolAdapterContext(
        _WALLET,
        Chain.ETHEREUM,
        _BLOCK,
        (_VAULT.address,),
        (),
        (),
        (),
        (replace(evidence, values=evidence_values(values)),),
        "recorded-json-rpc",
        _NOW,
    )
    partial = production_protocol_registry().analyze(context)
    assert partial.status is ProtocolAdapterStatus.PARTIAL
    assert all(not position.assets for position in partial.positions)


def test_common_protocol_service_exposes_vault_collection() -> None:
    result = ProtocolPositionService(_Reader()).load_erc4626_positions(
        _WALLET, Chain.ETHEREUM, _BLOCK
    )
    assert len(result) == 1 and result[0].protocol_id == _VAULT.protocol_id


@pytest.mark.parametrize("chain", [Chain.ETHEREUM, Chain.BASE])
def test_registry_does_not_recognize_unreviewed_chain_contract_pairs(chain: Chain) -> None:
    address = _WALLET if chain is Chain.ETHEREUM else _VAULT.address
    result = production_protocol_registry().analyze(
        ProtocolAdapterContext(
            _WALLET,
            chain,
            _BLOCK,
            (address,),
            (),
            (),
            (),
            (),
            "fixture",
            _NOW,
        )
    )
    assert result.status is ProtocolAdapterStatus.UNKNOWN_PROTOCOL


def test_protocol_refresh_includes_reviewed_vaults(monkeypatch: pytest.MonkeyPatch) -> None:
    reader = _Reader()
    service = ProtocolPositionService(reader)
    other_result = _collect(_Reader())
    monkeypatch.setattr(service, "load_aave_v3_positions", lambda *args, **kwargs: other_result)
    monkeypatch.setattr(service, "load_compound_v3_positions", lambda *args, **kwargs: ())
    monkeypatch.setattr(service, "load_uniswap_v3_positions", lambda *args, **kwargs: other_result)
    results = service.refresh_supported_positions(_WALLET, Chain.ETHEREUM, _BLOCK)
    assert len(results) == 3
    assert results[-1].protocol_id == _VAULT.protocol_id
    assert len(reader.calls) == 7


class _Prices:
    def get_token_prices(self, chain: Chain, contract_addresses: list[str]) -> dict[str, Decimal]:
        return (
            {_VAULT.asset_address: Decimal("2")}
            if _VAULT.asset_address in contract_addresses
            else {}
        )


def _overview() -> WalletOverviewResult:
    return WalletOverviewResult(
        native_balance=Decimal("10"),
        native_price_usd=Decimal("1"),
        total_usd=Decimal("110"),
        token_balance_page_count=1,
        token_balances_truncated=False,
        updated_at=_NOW,
        token_balances=[
            TokenBalance(
                token=Token(_VAULT.address, "sDAI", "Savings DAI", 18, True),
                balance_decimal=Decimal("2"),
                price_usd=Decimal("50"),
            )
        ],
    )


def test_portfolio_prices_underlying_once_and_exports_share_metadata(tmp_path: Path) -> None:
    repository = _repository(tmp_path)
    ERC4626PositionService(_Reader(), repository=repository).load_vault_positions(
        _WALLET,
        Chain.ETHEREUM,
        "sdai",
        _BLOCK,
    )
    snapshot = repository.get_snapshot(_WALLET, Chain.ETHEREUM, _VAULT.protocol_id, _BLOCK)
    valuation = ProtocolPortfolioService(_Prices(), now_func=lambda: _NOW).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), snapshot),)
    )
    assert valuation.asset_usd_total == Decimal("5.000002")
    assert valuation.wallets[0].adjusted_wallet_total_usd == Decimal("10")
    assert valuation.excluded_receipt_token_count == 1
    assert protocol_receipt_token_addresses(snapshot) == {_VAULT.address}

    class Wallets:
        def load_wallet_overview(self, **kwargs):
            return _overview()

    class Watchlist:
        def list_entries(self, chain=None):
            return [
                WatchlistEntry(
                    id=1, address=_WALLET, chain=Chain.ETHEREUM, label="Vault", created_at=_NOW
                )
            ]

    result = PortfolioService(
        Watchlist(),
        Wallets(),
        protocol_snapshot_reader=repository,
        protocol_valuator=ProtocolPortfolioService(_Prices(), now_func=lambda: _NOW),
    ).load_portfolio()
    payload = json.loads(portfolio_json_bytes(result, PortfolioExportTemplate.PROTOCOL_POSITIONS))
    assert payload["format_version"] == 5
    row = payload["items"][0]
    assert row["position_metadata"]["raw_shares"] == str(_SHARES)
    rows = list(
        csv.DictReader(
            StringIO(
                portfolio_csv_text(
                    result,
                    PortfolioExportTemplate.PROTOCOL_POSITIONS,
                )
            )
        )
    )
    assert json.loads(rows[0]["position_metadata"])["converted_assets"] == str(_ASSETS)
    summary = _render_result(result)
    assert f"raw_shares={_SHARES} sDAI" in summary
    assert "redemption_preview_raw=2400000" in summary
    assert "accounting estimate, not a withdrawal quote" in summary

    # A missing USD quote is not treated as a stablecoin peg or a zero-dollar position.
    class NoPrices:
        def get_token_prices(self, chain, contract_addresses):
            return {}

    unpriced = ProtocolPortfolioService(NoPrices(), now_func=lambda: _NOW).value(
        (ProtocolPortfolioInput(_WALLET, Chain.ETHEREUM, _overview(), snapshot),)
    )
    assert unpriced.unpriced_position_count == 1
    assert unpriced.positions[0].value_usd is None
    assert unpriced.wallets[0].adjusted_wallet_total_usd == Decimal("10")


@pytest.mark.parametrize("converted", [None, 0])
def test_receipts_remain_only_when_underlying_conversion_is_unknown(
    tmp_path: Path, converted: int | None
) -> None:
    reader = _Reader()
    if converted is None:
        reader.fail_signature = "convertToAssets(uint256)"
    else:
        reader.responses[
            (_VAULT.address, call_data("convertToAssets(uint256)", ("uint256",), (_SHARES,)))
        ] = _encoded("uint256", 0)
    repository = _repository(tmp_path)
    ERC4626PositionService(reader, repository=repository).load_vault_positions(
        _WALLET,
        Chain.ETHEREUM,
        "sdai",
        _BLOCK,
    )
    snapshot = repository.get_snapshot(_WALLET, Chain.ETHEREUM, _VAULT.protocol_id, _BLOCK)
    assert protocol_receipt_token_addresses(snapshot) == (
        set() if converted is None else {_VAULT.address}
    )
