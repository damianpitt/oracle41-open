"""Test Uniswap V3 position collection with recorded ABI responses.

The suite covers NFT enumeration, pool and fee-growth reads, exact-block provenance, optional token
metadata failures, finished snapshot reuse, and per-NFT resume. No test contacts a live provider.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from eth_abi.abi import encode as abi_encode

from oracle41_open.core.models import (
    Chain,
    ContractReadResult,
    ProtocolAdapterStatus,
    ProtocolPositionKind,
    ProviderResponseError,
    ProviderTimeoutError,
)
from oracle41_open.core.protocols import uniswap_v3_deployment
from oracle41_open.core.services.protocol_collection import call_data
from oracle41_open.core.services.uniswap_v3_position_service import UniswapV3PositionService
from oracle41_open.storage.db import ProtocolPositionRepository, SQLiteDatabase

_WALLET = "0x1111111111111111111111111111111111111111"
_TOKEN0 = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_TOKEN1 = "0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2"
_POOL = "0x2222222222222222222222222222222222222222"
_BLOCK = 24_000_000
_OBSERVED_AT = datetime(2026, 9, 6, tzinfo=UTC)
_Q96 = 2**96
_Q128 = 2**128
_POSITION_TYPES = (
    "uint96",
    "address",
    "address",
    "address",
    "uint24",
    "int24",
    "int24",
    "uint128",
    "uint256",
    "uint256",
    "uint128",
    "uint128",
)
_TICK_TYPES = (
    "uint128",
    "int128",
    "uint256",
    "uint256",
    "int56",
    "uint160",
    "uint32",
    "bool",
)


def test_uniswap_collector_builds_position_at_exact_block() -> None:
    provider = _FakeContractReader(_responses((42,)))

    result = UniswapV3PositionService(provider).load_positions(
        _WALLET.upper(), Chain.ETHEREUM, _BLOCK
    )

    assert result.status is ProtocolAdapterStatus.MATCHED
    assert len(result.positions) == 1
    assert result.positions[0].kind is ProtocolPositionKind.LIQUIDITY
    assert result.positions[0].metadata_value("range_state") == "in_range"
    assert {call[2] for call in provider.calls} == {_BLOCK}


def test_uniswap_collector_keeps_position_when_symbol_read_fails() -> None:
    deployment = uniswap_v3_deployment(Chain.ETHEREUM)
    provider = _FakeContractReader(
        _responses((42,)),
        errors={(_TOKEN0, call_data("symbol()"))},
    )

    result = UniswapV3PositionService(provider).load_positions(
        _WALLET, Chain.ETHEREUM, _BLOCK
    )

    assert result.status is ProtocolAdapterStatus.PARTIAL
    assert len(result.positions) == 1
    assert result.positions[0].assets[0].symbol.startswith("0xa0b869")
    assert any(item.contract_address == deployment.position_manager for item in result.raw_evidence)


def test_uniswap_collector_records_an_empty_owned_position_list() -> None:
    result = UniswapV3PositionService(_FakeContractReader(_responses(()))).load_positions(
        _WALLET, Chain.ETHEREUM, _BLOCK
    )

    assert result.status is ProtocolAdapterStatus.MATCHED
    assert result.positions == ()
    assert result.risk_snapshot is not None


def test_uniswap_collector_reuses_finished_snapshot(tmp_path: Path) -> None:
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    first = UniswapV3PositionService(
        _FakeContractReader(_responses((42,))), repository=repository
    ).load_positions(_WALLET, Chain.ETHEREUM, _BLOCK)
    offline = _FakeContractReader({})

    second = UniswapV3PositionService(offline, repository=repository).load_positions(
        _WALLET, Chain.ETHEREUM, _BLOCK
    )

    assert second == first
    assert offline.calls == []


def test_uniswap_collector_force_refresh_replaces_snapshot(tmp_path: Path) -> None:
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    UniswapV3PositionService(
        _FakeContractReader(_responses((42,))), repository=repository
    ).load_positions(_WALLET, Chain.ETHEREUM, _BLOCK)
    refreshed = _FakeContractReader(_responses((42,)))

    result = UniswapV3PositionService(refreshed, repository=repository).load_positions(
        _WALLET, Chain.ETHEREUM, _BLOCK, force_refresh=True
    )

    assert result.status is ProtocolAdapterStatus.MATCHED
    assert refreshed.calls


def test_uniswap_collector_resumes_after_completed_nft(tmp_path: Path) -> None:
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    deployment = uniswap_v3_deployment(Chain.ETHEREUM)
    second_position = (
        deployment.position_manager,
        call_data("positions(uint256)", ("uint256",), (43,)),
    )
    responses = _responses((42, 43))
    interrupted = _InterruptingContractReader(responses, second_position)

    try:
        UniswapV3PositionService(interrupted, repository=repository).load_positions(
            _WALLET, Chain.ETHEREUM, _BLOCK
        )
    except RuntimeError as error:
        assert str(error) == "simulated interruption"
    else:
        raise AssertionError("Expected the recorded interruption.")

    checkpoint = repository.get_checkpoint(
        _WALLET, Chain.ETHEREUM, "uniswap-v3", _BLOCK
    )
    assert checkpoint is not None
    assert checkpoint.next_reserve_index == 1
    resumed = _FakeContractReader(responses)
    result = UniswapV3PositionService(resumed, repository=repository).load_positions(
        _WALLET, Chain.ETHEREUM, _BLOCK
    )

    first_position_call = call_data("positions(uint256)", ("uint256",), (42,))
    assert len(result.positions) == 2
    assert all(call[1] != first_position_call for call in resumed.calls)
    assert all(call[1] != call_data("symbol()") for call in resumed.calls)
    assert all(call[1] != call_data("decimals()") for call in resumed.calls)
    assert repository.get_checkpoint(
        _WALLET, Chain.ETHEREUM, "uniswap-v3", _BLOCK
    ) is None


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
            raise ProviderResponseError(f"Missing fixture response for {key}.") from error
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


def _responses(token_ids: tuple[int, ...]) -> dict[tuple[str, str], str]:
    deployment = uniswap_v3_deployment(Chain.ETHEREUM)
    manager = deployment.position_manager
    responses = {
        (
            manager,
            call_data("balanceOf(address)", ("address",), (_WALLET,)),
        ): _encoded(("uint256",), (len(token_ids),)),
        (_TOKEN0, call_data("symbol()")): _encoded(("string",), ("USDC",)),
        (_TOKEN0, call_data("decimals()")): _encoded(("uint8",), (6,)),
        (_TOKEN1, call_data("symbol()")): _encoded(("string",), ("WETH",)),
        (_TOKEN1, call_data("decimals()")): _encoded(("uint8",), (18,)),
        (_POOL, call_data("slot0()")): _encoded(
            ("uint160", "int24", "uint16", "uint16", "uint16", "uint8", "bool"),
            (_Q96, 0, 0, 1, 1, 0, True),
        ),
        (_POOL, call_data("feeGrowthGlobal0X128()")): _encoded(("uint256",), (10 * _Q128,)),
        (_POOL, call_data("feeGrowthGlobal1X128()")): _encoded(("uint256",), (20 * _Q128,)),
        (_POOL, call_data("ticks(int24)", ("int24",), (-60,))): _encoded(
            _TICK_TYPES, (1, 1, 1 * _Q128, 3 * _Q128, 0, 0, 0, True)
        ),
        (_POOL, call_data("ticks(int24)", ("int24",), (60,))): _encoded(
            _TICK_TYPES, (1, 1, 2 * _Q128, 4 * _Q128, 0, 0, 0, True)
        ),
    }
    for index, token_id in enumerate(token_ids):
        responses[
            (
                manager,
                call_data(
                    "tokenOfOwnerByIndex(address,uint256)",
                    ("address", "uint256"),
                    (_WALLET, index),
                ),
            )
        ] = _encoded(("uint256",), (token_id,))
        responses[
            (manager, call_data("positions(uint256)", ("uint256",), (token_id,)))
        ] = _encoded(
            _POSITION_TYPES,
            (
                0,
                "0x0000000000000000000000000000000000000000",
                _TOKEN0,
                _TOKEN1,
                3000,
                -60,
                60,
                10**18,
                5 * _Q128,
                12 * _Q128,
                100,
                200,
            ),
        )
    responses[
        (
            deployment.factory,
            call_data(
                "getPool(address,address,uint24)",
                ("address", "address", "uint24"),
                (_TOKEN0, _TOKEN1, 3000),
            ),
        )
    ] = _encoded(("address",), (_POOL,))
    return responses


def _encoded(types: tuple[str, ...], values: tuple[object, ...]) -> str:
    return "0x" + abi_encode(types, values).hex()
