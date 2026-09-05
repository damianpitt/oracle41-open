"""Test Compound V3 collection with recorded ABI responses.

The suite covers exact-block base and collateral reads, native account-risk flags, partial optional
failures, mandatory market discovery, durable reuse, forced refresh, and per-collateral resume.
No test contacts a live provider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from eth_abi.abi import encode as abi_encode

from oracle41_open.core.models import (
    Chain,
    ContractReadResult,
    ProtocolAdapterStatus,
    ProtocolPositionKind,
    ProtocolRiskState,
    ProviderResponseError,
    ProviderTimeoutError,
    ValidationError,
)
from oracle41_open.core.protocols import compound_v3_market
from oracle41_open.core.services.compound_v3_position_service import (
    CompoundV3PositionService,
)
from oracle41_open.core.services.protocol_collection import call_data
from oracle41_open.storage.db import ProtocolPositionRepository, SQLiteDatabase

_WALLET = "0x1111111111111111111111111111111111111111"
_USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_WETH = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
_WBTC = "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599"
_WETH_FEED = "0x2222222222222222222222222222222222222222"
_WBTC_FEED = "0x3333333333333333333333333333333333333333"
_BLOCK = 24_000_000
_OBSERVED_AT = datetime(2026, 9, 4, tzinfo=UTC)
_ASSET_INFO_TYPE = "(uint8,address,address,uint64,uint64,uint64,uint64,uint128)"


def test_compound_collector_builds_positions_and_risk_at_exact_block() -> None:
    provider = _FakeContractReader(_responses())
    service = CompoundV3PositionService(provider)
    market = compound_v3_market(Chain.ETHEREUM, "usdc")

    result = service.load_market_positions(_WALLET.upper(), market, _BLOCK)

    assert result.status is ProtocolAdapterStatus.MATCHED
    assert [position.kind for position in result.positions] == [
        ProtocolPositionKind.DEBT,
        ProtocolPositionKind.COLLATERAL,
    ]
    assert [position.assets[0].raw_amount for position in result.positions] == [
        "25000000",
        "2000000000000000000",
    ]
    assert result.risk_snapshot is not None
    assert result.risk_snapshot.state is ProtocolRiskState.ABOVE_OR_EQUAL_LIQUIDATION_THRESHOLD
    assert result.risk_snapshot.health_factor_wad is None
    assert result.risk_snapshot.is_borrow_collateralized is True
    assert result.risk_snapshot.is_liquidatable is False
    assert {call[2] for call in provider.calls} == {_BLOCK}


def test_compound_collector_marks_collateral_failure_partial() -> None:
    market = compound_v3_market(Chain.ETHEREUM, "usdc")
    collateral_call = call_data(
        "userCollateral(address,address)",
        ("address", "address"),
        (_WALLET, _WETH),
    )
    provider = _FakeContractReader(
        _responses(),
        errors={(market.comet, collateral_call)},
    )

    result = CompoundV3PositionService(provider).load_market_positions(
        _WALLET,
        market,
        _BLOCK,
    )

    assert result.status is ProtocolAdapterStatus.PARTIAL
    assert [position.kind for position in result.positions] == [
        ProtocolPositionKind.DEBT,
    ]
    assert any("collateral balance" in warning for warning in result.warnings)


def test_compound_collector_requires_valid_market_configuration() -> None:
    market = compound_v3_market(Chain.ETHEREUM, "usdc")
    responses = _responses()
    responses[(market.comet, call_data("getAssetInfo(uint8)", ("uint8",), (0,)))] = "0x12"

    with pytest.raises(ProviderResponseError, match="malformed ABI data"):
        CompoundV3PositionService(_FakeContractReader(responses)).load_market_positions(
            _WALLET,
            market,
            _BLOCK,
        )


@pytest.mark.parametrize(
    ("wallet", "block_number"),
    (("not-an-address", _BLOCK), (_WALLET, -1)),
)
def test_compound_collector_validates_context(wallet: str, block_number: int) -> None:
    market = compound_v3_market(Chain.ETHEREUM, "usdc")

    with pytest.raises(ValidationError):
        CompoundV3PositionService(_FakeContractReader(_responses())).load_market_positions(
            wallet,
            market,
            block_number,
        )


def test_compound_collector_resumes_after_completed_collateral(tmp_path: Path) -> None:
    market = compound_v3_market(Chain.ETHEREUM, "usdc")
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    second_collateral_call = call_data(
        "userCollateral(address,address)",
        ("address", "address"),
        (_WALLET, _WBTC),
    )
    responses = _responses(asset_count=2)
    interrupted = _InterruptingContractReader(
        responses,
        interrupt_key=(market.comet, second_collateral_call),
    )

    with pytest.raises(RuntimeError, match="simulated interruption"):
        CompoundV3PositionService(
            interrupted,
            repository=repository,
        ).load_market_positions(_WALLET, market, _BLOCK)

    checkpoint = repository.get_checkpoint(
        _WALLET,
        Chain.ETHEREUM,
        market.protocol_id,
        _BLOCK,
    )
    assert checkpoint is not None
    assert checkpoint.next_reserve_index == 1
    resumed = _FakeContractReader(responses)
    result = CompoundV3PositionService(
        resumed,
        repository=repository,
    ).load_market_positions(_WALLET, market, _BLOCK)

    first_collateral_call = call_data(
        "userCollateral(address,address)",
        ("address", "address"),
        (_WALLET, _WETH),
    )
    assert result.status is ProtocolAdapterStatus.MATCHED
    assert all(call[1] != first_collateral_call for call in resumed.calls)
    assert repository.get_checkpoint(
        _WALLET,
        Chain.ETHEREUM,
        market.protocol_id,
        _BLOCK,
    ) is None


def test_compound_collector_reuses_finished_snapshot_without_network(tmp_path: Path) -> None:
    market = compound_v3_market(Chain.ETHEREUM, "usdc")
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    first = CompoundV3PositionService(
        _FakeContractReader(_responses()),
        repository=repository,
    ).load_market_positions(_WALLET, market, _BLOCK)
    offline = _FakeContractReader({})

    second = CompoundV3PositionService(
        offline,
        repository=repository,
    ).load_market_positions(_WALLET, market, _BLOCK)

    assert second == first
    assert offline.calls == []


def test_compound_collector_force_refresh_replaces_snapshot(tmp_path: Path) -> None:
    market = compound_v3_market(Chain.ETHEREUM, "usdc")
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    CompoundV3PositionService(
        _FakeContractReader(_responses()),
        repository=repository,
    ).load_market_positions(_WALLET, market, _BLOCK)
    refreshed = _FakeContractReader(_responses())

    result = CompoundV3PositionService(
        refreshed,
        repository=repository,
    ).load_market_positions(_WALLET, market, _BLOCK, force_refresh=True)

    assert result.status is ProtocolAdapterStatus.MATCHED
    assert refreshed.calls


class _FakeContractReader:
    def __init__(
        self,
        responses: dict[tuple[str, str], str],
        errors: set[tuple[str, str]] | None = None,
    ) -> None:
        self._responses = responses
        self._errors = errors or set()
        self.calls: list[tuple[str, str, int]] = []

    def read_contract(
        self,
        contract_address: str,
        call_data_value: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        key = (contract_address, call_data_value)
        self.calls.append((contract_address, call_data_value, block_number))
        if key in self._errors:
            raise ProviderTimeoutError("Fixture timeout.")
        try:
            data = self._responses[key]
        except KeyError as error:
            raise AssertionError(f"Unexpected contract read: {key}") from error
        return ContractReadResult(
            chain=chain,
            contract_address=contract_address,
            block_number=block_number,
            data=data,
            source_provider="primary",
            fetched_at=_OBSERVED_AT,
        )


class _InterruptingContractReader(_FakeContractReader):
    def __init__(
        self,
        responses: dict[tuple[str, str], str],
        interrupt_key: tuple[str, str],
    ) -> None:
        super().__init__(responses)
        self._interrupt_key = interrupt_key

    def read_contract(
        self,
        contract_address: str,
        call_data_value: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        if (contract_address, call_data_value) == self._interrupt_key:
            raise RuntimeError("simulated interruption")
        return super().read_contract(contract_address, call_data_value, chain, block_number)


def _responses(asset_count: int = 1) -> dict[tuple[str, str], str]:
    market = compound_v3_market(Chain.ETHEREUM, "usdc")
    responses = {
        (market.comet, call_data("baseToken()")): _encoded(("address",), (_USDC,)),
        (market.comet, call_data("baseScale()")): _encoded(("uint64",), (1_000_000,)),
        (
            market.comet,
            call_data("balanceOf(address)", ("address",), (_WALLET,)),
        ): _encoded(("uint256",), (0,)),
        (
            market.comet,
            call_data("borrowBalanceOf(address)", ("address",), (_WALLET,)),
        ): _encoded(("uint256",), (25_000_000,)),
        (
            market.comet,
            call_data("isBorrowCollateralized(address)", ("address",), (_WALLET,)),
        ): _encoded(("bool",), (True,)),
        (
            market.comet,
            call_data("isLiquidatable(address)", ("address",), (_WALLET,)),
        ): _encoded(("bool",), (False,)),
        (market.comet, call_data("numAssets()")): _encoded(("uint8",), (asset_count,)),
    }
    assets = (
        (_WETH, _WETH_FEED, "WETH", 10**18, 2 * 10**18),
        (_WBTC, _WBTC_FEED, "WBTC", 10**8, 0),
    )
    for index, (asset, price_feed, symbol, scale, balance) in enumerate(assets[:asset_count]):
        responses[
            (market.comet, call_data("getAssetInfo(uint8)", ("uint8",), (index,)))
        ] = _encoded(
            (_ASSET_INFO_TYPE,),
            (
                (
                    index,
                    asset,
                    price_feed,
                    scale,
                    830_000_000_000_000_000,
                    900_000_000_000_000_000,
                    950_000_000_000_000_000,
                    500_000 * scale,
                ),
            ),
        )
        responses[(asset, call_data("symbol()"))] = _encoded(("string",), (symbol,))
        responses[
            (
                market.comet,
                call_data(
                    "userCollateral(address,address)",
                    ("address", "address"),
                    (_WALLET, asset),
                ),
            )
        ] = _encoded(("uint128", "uint128"), (balance, 0))
    return responses


def _encoded(types: tuple[str, ...], values: tuple[object, ...]) -> str:
    return "0x" + abi_encode(types, values).hex()
