"""Test deterministic wallet-action normalization.

The fixtures cover token effects, swaps, deployments, internal value movement, failed intent, and unknown calldata.
They verify action order and evidence references without relying on provider-specific response shapes.
"""

from dataclasses import replace
from datetime import UTC, datetime

from eth_abi.abi import encode as abi_encode

from oracle41_open.core.models import (
    ActionAssetDirection,
    Chain,
    InternalCall,
    RawTransactionLog,
    TraceDialect,
    TraceStatus,
    TransactionInspection,
    TransactionTrace,
    WalletActionKind,
    WalletActionStatus,
)
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
from oracle41_open.core.services.action_normalizer import WalletActionNormalizer

_TX_HASH = "0x" + "ab" * 32
_ACTOR = "0x1111111111111111111111111111111111111111"
_ROUTER = "0x2222222222222222222222222222222222222222"
_OTHER = "0x3333333333333333333333333333333333333333"
_TOKEN_A = "0x4444444444444444444444444444444444444444"
_TOKEN_B = "0x5555555555555555555555555555555555555555"
_CREATED = "0x6666666666666666666666666666666666666666"
_TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
_APPROVAL_TOPIC = "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925"


def test_normalizes_transfer_and_approval_events_in_log_order() -> None:
    inspection = _inspection(
        logs=(
            _erc20_log(2, _TOKEN_A, _TRANSFER_TOPIC, _ACTOR, _OTHER, 25),
            _erc20_log(3, _TOKEN_A, _APPROVAL_TOPIC, _ACTOR, _ROUTER, 100),
        )
    )

    actions = _normalize(inspection)

    assert [action.kind for action in actions] == [
        WalletActionKind.TRANSFER,
        WalletActionKind.APPROVAL,
    ]
    assert [action.action_index for action in actions] == [0, 1]
    assert actions[0].assets[0].direction is ActionAssetDirection.OUT
    assert actions[0].evidence[0].reference == "log:2"
    assert actions[1].participants[1].address == _ROUTER
    assert actions[1].evidence[0].reference == "log:3"


def test_collapses_opposite_token_transfers_into_simple_swap() -> None:
    inspection = _inspection(
        input_data="0x12345678",
        logs=(
            _erc20_log(4, _TOKEN_A, _TRANSFER_TOPIC, _ACTOR, _ROUTER, 50),
            _erc20_log(7, _TOKEN_B, _TRANSFER_TOPIC, _ROUTER, _ACTOR, 75),
        ),
    )

    actions = _normalize(inspection)

    assert len(actions) == 1
    assert actions[0].kind is WalletActionKind.SWAP
    assert [asset.direction for asset in actions[0].assets] == [
        ActionAssetDirection.OUT,
        ActionAssetDirection.IN,
    ]
    assert [item.reference for item in actions[0].evidence] == ["log:4", "log:7"]


def test_collapses_native_outflow_and_token_inflow_into_simple_swap() -> None:
    inspection = _inspection(
        input_data="0x12345678",
        value_wei=10,
        logs=(
            _erc20_log(9, _TOKEN_B, _TRANSFER_TOPIC, _ROUTER, _ACTOR, 75),
        ),
    )

    actions = _normalize(inspection)

    assert len(actions) == 1
    assert actions[0].kind is WalletActionKind.SWAP
    assert actions[0].assets[0].standard == "native"
    assert actions[0].assets[1].contract_address == _TOKEN_B
    assert [item.reference for item in actions[0].evidence] == ["call:value", "log:9"]


def test_normalizes_deployment_and_nested_native_transfer() -> None:
    inspection = _inspection(to_address=None, contract_address=_CREATED, value_wei=0)
    trace = TransactionTrace(
        chain=Chain.ETHEREUM,
        tx_hash=_TX_HASH,
        status=TraceStatus.COMPLETE,
        calls=(
            InternalCall(
                trace_address=(0,),
                depth=1,
                call_type="CALL",
                from_address=_CREATED,
                to_address=_OTHER,
                created_contract=None,
                value_wei=10,
                gas_limit=10_000,
                gas_used=9_000,
                input_data="0x",
                output_data="0x",
            ),
        ),
        raw_json="{}",
        source_provider="test",
        fetched_at=datetime(2026, 8, 15, tzinfo=UTC),
        dialect=TraceDialect.DEBUG_CALL_TRACER,
    )

    actions = _normalize(inspection, trace)

    assert [action.kind for action in actions] == [
        WalletActionKind.DEPLOYMENT,
        WalletActionKind.TRANSFER,
    ]
    assert actions[0].participants[1].address == _CREATED
    assert actions[1].evidence[0].reference == "trace:0"


def test_failed_approval_call_keeps_intent_and_failed_status() -> None:
    calldata = "0x095ea7b3" + abi_encode(("address", "uint256"), (_ROUTER, 500)).hex()
    inspection = _inspection(input_data=calldata, status=False)

    actions = _normalize(inspection)

    assert len(actions) == 1
    assert actions[0].kind is WalletActionKind.APPROVAL
    assert actions[0].status is WalletActionStatus.FAILED
    assert actions[0].assets[0].raw_amount == "500"
    assert actions[0].evidence[0].reference == "call"


def test_erc721_approval_keeps_token_id_separate_from_spender() -> None:
    inspection = _inspection(
        logs=(
            RawTransactionLog(
                log_index=8,
                address=_TOKEN_A,
                topics=(
                    _APPROVAL_TOPIC,
                    _address_topic(_ACTOR),
                    _address_topic(_OTHER),
                    "0x" + (42).to_bytes(32, "big").hex(),
                ),
                data="0x",
                removed=False,
            ),
        )
    )

    actions = _normalize(inspection)

    assert actions[0].kind is WalletActionKind.APPROVAL
    assert actions[0].participants[1].address == _OTHER
    assert actions[0].assets[0].token_id == "42"
    assert actions[0].assets[0].raw_amount == "1"


def test_unknown_call_remains_explicit_and_keeps_raw_reference() -> None:
    inspection = _inspection(input_data="0x12345678deadbeef")

    actions = _normalize(inspection)

    assert len(actions) == 1
    assert actions[0].kind is WalletActionKind.UNKNOWN
    assert actions[0].status is WalletActionStatus.SUCCESS
    assert actions[0].confidence.value == "low"
    assert actions[0].evidence[0].signature == "0x12345678"


def test_same_evidence_is_provider_independent() -> None:
    first = _inspection(
        logs=(
            _erc20_log(4, _TOKEN_A, _TRANSFER_TOPIC, _ACTOR, _ROUTER, 50),
            _erc20_log(7, _TOKEN_B, _TRANSFER_TOPIC, _ROUTER, _ACTOR, 75),
        )
    )
    second = replace(
        first,
        source_provider="another-provider",
        fetched_at=datetime(2026, 8, 15, 1, tzinfo=UTC),
    )

    assert _normalize(first) == _normalize(second)


def _normalize(
    inspection: TransactionInspection,
    trace: TransactionTrace | None = None,
) -> tuple:
    decoding = StandardABIDecoder().decode(inspection)
    return WalletActionNormalizer().normalize(inspection, decoding, trace)


def _inspection(
    *,
    input_data: str = "0x",
    logs: tuple[RawTransactionLog, ...] = (),
    status: bool | None = True,
    to_address: str | None = _ROUTER,
    contract_address: str | None = None,
    value_wei: int = 0,
) -> TransactionInspection:
    return TransactionInspection(
        chain=Chain.ETHEREUM,
        tx_hash=_TX_HASH,
        block_number=24_000_000,
        block_hash="0x" + "cd" * 32,
        transaction_index=1,
        from_address=_ACTOR,
        to_address=to_address,
        contract_address=contract_address,
        nonce=1,
        value_wei=value_wei,
        input_data=input_data,
        gas_limit=200_000,
        gas_price=2_000_000_000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        status=status,
        gas_used=100_000,
        cumulative_gas_used=100_000,
        effective_gas_price=2_000_000_000,
        transaction_type=2,
        logs_bloom="0x" + "00" * 256,
        logs=logs,
        source_provider="fixture",
        fetched_at=datetime(2026, 8, 15, tzinfo=UTC),
    )


def _erc20_log(
    index: int,
    contract: str,
    topic0: str,
    first: str,
    second: str,
    value: int,
) -> RawTransactionLog:
    return RawTransactionLog(
        log_index=index,
        address=contract,
        topics=(topic0, _address_topic(first), _address_topic(second)),
        data="0x" + abi_encode(("uint256",), (value,)).hex(),
        removed=False,
    )


def _address_topic(address: str) -> str:
    return "0x" + address.removeprefix("0x").rjust(64, "0")
