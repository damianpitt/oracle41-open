"""Test live Aave V3 snapshot collection without making network requests.

The fixtures encode real contract ABI shapes and exercise exact-block reads, provenance, zero
positions, partial reserve failures, mandatory discovery, malformed data, and input validation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from eth_abi.abi import encode as abi_encode
from eth_utils.crypto import keccak

from oracle41_open.core.models import (
    Chain,
    ContractReadResult,
    ProtocolAdapterStatus,
    ProtocolPositionKind,
    ProviderResponseError,
    ProviderTimeoutError,
    ValidationError,
)
from oracle41_open.core.protocols import aave_v3_deployment
from oracle41_open.core.services.protocol_position_service import ProtocolPositionService
from oracle41_open.storage.db import ProtocolPositionRepository, SQLiteDatabase

_WALLET = "0x1111111111111111111111111111111111111111"
_RESERVE = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
_A_TOKEN = "0x98c23e9d8f34fefeaaba7d1bfee3b5b2720c4a39"
_S_TOKEN = "0x0000000000000000000000000000000000000000"
_V_TOKEN = "0x72e95bcba2c5d0fe2291e84a3081dbe01fabd900"
_ORACLE = "0x54586be62e3c3580375ae3723c145253060ca0c2"
_BLOCK = 24_000_000
_OBSERVED_AT = datetime(2026, 8, 29, tzinfo=UTC)


def test_aave_collector_builds_positions_and_health_from_exact_block_reads() -> None:
    provider = _FakeContractReader(_responses())
    service = ProtocolPositionService(provider)

    result = service.load_aave_v3_positions(_WALLET.upper(), Chain.ETHEREUM, _BLOCK)

    assert result.status is ProtocolAdapterStatus.MATCHED
    assert [position.kind for position in result.positions] == [
        ProtocolPositionKind.COLLATERAL,
        ProtocolPositionKind.DEBT,
    ]
    assert [position.assets[0].raw_amount for position in result.positions] == [
        "125000000",
        "25000000",
    ]
    assert result.risk_snapshot is not None
    assert result.risk_snapshot.health_factor_wad == "4125000000000000000"
    assert result.risk_snapshot.base_currency_unit == "100000000"
    assert result.adapter_id == "oracle41.aave-v3"
    assert result.raw_evidence
    assert {call[2] for call in provider.calls} == {_BLOCK}
    assert result.source_balances == ()


def test_aave_collector_skips_receipt_token_call_for_zero_position() -> None:
    responses = _responses()
    deployment = aave_v3_deployment(Chain.ETHEREUM)
    user_call = _call_data(
        "getUserReserveData(address,address)",
        ("address", "address"),
        (_RESERVE, _WALLET),
    )
    responses[(deployment.protocol_data_provider, user_call)] = _encoded(
        ("uint256",) * 7 + ("uint40", "bool"),
        (0, 0, 0, 0, 0, 0, 0, 0, False),
    )
    provider = _FakeContractReader(responses)

    result = ProtocolPositionService(provider).load_aave_v3_positions(
        _WALLET,
        Chain.ETHEREUM,
        _BLOCK,
    )

    token_call = _call_data(
        "getReserveTokensAddresses(address)",
        ("address",),
        (_RESERVE,),
    )
    assert result.status is ProtocolAdapterStatus.MATCHED
    assert result.positions == ()
    assert all(call[1] != token_call for call in provider.calls)


def test_aave_collector_marks_optional_reserve_failure_partial() -> None:
    deployment = aave_v3_deployment(Chain.ETHEREUM)
    config_call = _call_data(
        "getReserveConfigurationData(address)",
        ("address",),
        (_RESERVE,),
    )
    provider = _FakeContractReader(
        _responses(),
        errors={(deployment.protocol_data_provider, config_call)},
    )

    result = ProtocolPositionService(provider).load_aave_v3_positions(
        _WALLET,
        Chain.ETHEREUM,
        _BLOCK,
    )

    assert result.status is ProtocolAdapterStatus.PARTIAL
    assert result.positions == ()
    assert result.risk_snapshot is not None
    assert any("reserve configuration" in warning for warning in result.warnings)
    issue = next(item for item in result.raw_evidence if item.kind == "aave_v3_collection_issue")
    assert issue.value("error_type") == "ProviderTimeoutError"


def test_aave_collector_rejects_mixed_provider_snapshot() -> None:
    deployment = aave_v3_deployment(Chain.ETHEREUM)
    user_call = _call_data(
        "getUserReserveData(address,address)",
        ("address", "address"),
        (_RESERVE, _WALLET),
    )
    provider = _FakeContractReader(
        _responses(),
        source_overrides={(deployment.protocol_data_provider, user_call): "secondary"},
    )

    result = ProtocolPositionService(provider).load_aave_v3_positions(
        _WALLET,
        Chain.ETHEREUM,
        _BLOCK,
    )

    assert result.status is ProtocolAdapterStatus.PARTIAL
    assert result.risk_snapshot is not None
    assert result.risk_snapshot.provenance.source_provider == "primary"
    issue = next(item for item in result.raw_evidence if item.kind == "aave_v3_collection_issue")
    assert issue.value("error_type") == "ProviderResponseError"


def test_aave_collector_requires_reserve_discovery() -> None:
    deployment = aave_v3_deployment(Chain.ETHEREUM)
    discovery = _call_data("getAllReservesTokens()")
    provider = _FakeContractReader(
        _responses(),
        errors={(deployment.protocol_data_provider, discovery)},
    )

    with pytest.raises(ProviderTimeoutError):
        ProtocolPositionService(provider).load_aave_v3_positions(
            _WALLET,
            Chain.ETHEREUM,
            _BLOCK,
        )


def test_aave_collector_rejects_malformed_reserve_list() -> None:
    responses = _responses()
    deployment = aave_v3_deployment(Chain.ETHEREUM)
    responses[(deployment.protocol_data_provider, _call_data("getAllReservesTokens()"))] = "0x12"

    with pytest.raises(ProviderResponseError, match="malformed ABI data"):
        ProtocolPositionService(_FakeContractReader(responses)).load_aave_v3_positions(
            _WALLET,
            Chain.ETHEREUM,
            _BLOCK,
        )


@pytest.mark.parametrize(
    ("wallet", "block_number"),
    (("not-an-address", _BLOCK), (_WALLET, -1)),
)
def test_aave_collector_validates_context(wallet: str, block_number: int) -> None:
    with pytest.raises(ValidationError):
        ProtocolPositionService(_FakeContractReader(_responses())).load_aave_v3_positions(
            wallet,
            Chain.ETHEREUM,
            block_number,
        )


def test_aave_collector_resumes_after_completed_reserve(tmp_path: Path) -> None:
    deployment = aave_v3_deployment(Chain.ETHEREUM)
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    interrupted_provider = _InterruptingContractReader(
        _responses(),
        interrupt_contract=deployment.pool,
    )

    with pytest.raises(RuntimeError, match="simulated interruption"):
        ProtocolPositionService(
            interrupted_provider,
            repository=repository,
        ).load_aave_v3_positions(_WALLET, Chain.ETHEREUM, _BLOCK)

    checkpoint = repository.get_checkpoint(_WALLET, Chain.ETHEREUM, "aave-v3", _BLOCK)
    assert checkpoint is not None
    assert checkpoint.next_reserve_index == 1
    resumed_provider = _FakeContractReader(_responses())
    result = ProtocolPositionService(
        resumed_provider,
        repository=repository,
    ).load_aave_v3_positions(_WALLET, Chain.ETHEREUM, _BLOCK)

    assert result.status is ProtocolAdapterStatus.MATCHED
    assert all(call[0] != deployment.protocol_data_provider for call in resumed_provider.calls)
    assert repository.get_checkpoint(_WALLET, Chain.ETHEREUM, "aave-v3", _BLOCK) is None
    assert repository.get_snapshot(_WALLET, Chain.ETHEREUM, "aave-v3", _BLOCK) is not None


def test_aave_collector_reuses_finished_snapshot_without_network(tmp_path: Path) -> None:
    repository = ProtocolPositionRepository(SQLiteDatabase(tmp_path / "state.sqlite3"))
    initial_provider = _FakeContractReader(_responses())
    first = ProtocolPositionService(
        initial_provider,
        repository=repository,
    ).load_aave_v3_positions(_WALLET, Chain.ETHEREUM, _BLOCK)
    offline_provider = _FakeContractReader({})

    second = ProtocolPositionService(
        offline_provider,
        repository=repository,
    ).load_aave_v3_positions(_WALLET, Chain.ETHEREUM, _BLOCK)

    assert second == first
    assert offline_provider.calls == []


class _FakeContractReader:
    def __init__(
        self,
        responses: dict[tuple[str, str], str],
        errors: set[tuple[str, str]] | None = None,
        source_overrides: dict[tuple[str, str], str] | None = None,
    ) -> None:
        self._responses = responses
        self._errors = errors or set()
        self._source_overrides = source_overrides or {}
        self.calls: list[tuple[str, str, int]] = []

    def read_contract(
        self,
        contract_address: str,
        call_data: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        key = (contract_address, call_data)
        self.calls.append((contract_address, call_data, block_number))
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
            source_provider=self._source_overrides.get(key, "primary"),
            fetched_at=_OBSERVED_AT,
        )


class _InterruptingContractReader(_FakeContractReader):
    def __init__(self, responses: dict[tuple[str, str], str], interrupt_contract: str) -> None:
        super().__init__(responses)
        self._interrupt_contract = interrupt_contract

    def read_contract(
        self,
        contract_address: str,
        call_data: str,
        chain: Chain,
        block_number: int,
    ) -> ContractReadResult:
        if contract_address == self._interrupt_contract:
            raise RuntimeError("simulated interruption")
        return super().read_contract(contract_address, call_data, chain, block_number)


def _responses() -> dict[tuple[str, str], str]:
    deployment = aave_v3_deployment(Chain.ETHEREUM)
    return {
        (
            deployment.protocol_data_provider,
            _call_data("getAllReservesTokens()"),
        ): _encoded(("(string,address)[]",), ((("USDC", _RESERVE),),)),
        (
            deployment.protocol_data_provider,
            _call_data(
                "getUserReserveData(address,address)",
                ("address", "address"),
                (_RESERVE, _WALLET),
            ),
        ): _encoded(
            ("uint256",) * 7 + ("uint40", "bool"),
            (125_000_000, 5_000_000, 20_000_000, 0, 0, 0, 0, 0, True),
        ),
        (
            deployment.protocol_data_provider,
            _call_data(
                "getReserveConfigurationData(address)",
                ("address",),
                (_RESERVE,),
            ),
        ): _encoded(
            ("uint256",) * 5 + ("bool",) * 5,
            (6, 8_000, 8_250, 10_500, 1_000, True, True, False, True, False),
        ),
        (
            deployment.protocol_data_provider,
            _call_data(
                "getReserveTokensAddresses(address)",
                ("address",),
                (_RESERVE,),
            ),
        ): _encoded(("address", "address", "address"), (_A_TOKEN, _S_TOKEN, _V_TOKEN)),
        (
            deployment.pool,
            _call_data("getUserAccountData(address)", ("address",), (_WALLET,)),
        ): _encoded(
            ("uint256",) * 6,
            (
                12_500_000_000,
                2_500_000_000,
                6_500_000_000,
                8_250,
                8_000,
                4_125_000_000_000_000_000,
            ),
        ),
        (
            deployment.pool_addresses_provider,
            _call_data("getPriceOracle()"),
        ): _encoded(("address",), (_ORACLE,)),
        (_ORACLE, _call_data("BASE_CURRENCY_UNIT()")): _encoded(
            ("uint256",),
            (100_000_000,),
        ),
    }


def _call_data(
    signature: str,
    input_types: tuple[str, ...] = (),
    values: tuple[object, ...] = (),
) -> str:
    selector = keccak(text=signature)[:4]
    arguments = abi_encode(input_types, values) if input_types else b""
    return "0x" + (selector + arguments).hex()


def _encoded(types: tuple[str, ...], values: tuple[object, ...]) -> str:
    return "0x" + abi_encode(types, values).hex()
