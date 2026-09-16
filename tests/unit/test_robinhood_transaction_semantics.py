"""Test Robinhood fee separation and canonical bridge classification.

The fixtures use only published Robinhood contracts and deterministic ABI payloads.
They verify complete and partial Nitro fees, bridge lifecycle stages, normalized actions, and false-positive resistance.
"""

from dataclasses import replace
from datetime import UTC, datetime

from eth_abi.abi import encode as abi_encode
from eth_utils.crypto import keccak

from oracle41_open.core.models import (
    BridgeDirection,
    BridgeStage,
    Chain,
    FeeBreakdownCompleteness,
    RawTransactionLog,
    TransactionInspection,
    WalletActionKind,
)
from oracle41_open.core.services.abi_decoder import StandardABIDecoder
from oracle41_open.core.services.action_normalizer import WalletActionNormalizer
from oracle41_open.core.services.bridge_intelligence import RobinhoodBridgeIntelligence

_TX_HASH = "0x" + "ab" * 32
_ACTOR = "0x1111111111111111111111111111111111111111"
_RECIPIENT = "0x2222222222222222222222222222222222222222"
_L1_TOKEN = "0x3333333333333333333333333333333333333333"
_L2_GATEWAY = "0xfd9b17206278c16ddaacf6ac8f05dbf97edcb31e"
_ARBSYS = "0x0000000000000000000000000000000000000064"


def test_robinhood_fee_breakdown_separates_execution_and_l1_data() -> None:
    inspection = _inspection(l1_gas_used=20_000)

    breakdown = inspection.fee_breakdown

    assert breakdown.completeness is FeeBreakdownCompleteness.COMPLETE
    assert breakdown.total_fee_wei == 100_000_000_000_000
    assert breakdown.execution_gas_used == 80_000
    assert breakdown.execution_fee_wei == 80_000_000_000_000
    assert breakdown.l1_data_fee_wei == 20_000_000_000_000


def test_robinhood_fee_breakdown_keeps_total_when_receipt_has_no_l1_split() -> None:
    breakdown = _inspection().fee_breakdown

    assert breakdown.completeness is FeeBreakdownCompleteness.TOTAL_ONLY
    assert breakdown.total_fee_wei == 100_000_000_000_000
    assert breakdown.execution_fee_wei is None
    assert breakdown.l1_data_fee_wei is None
    assert "gasUsedForL1" in (breakdown.note or "")


def test_deposit_finalization_requires_official_robinhood_gateway() -> None:
    inspection = _inspection(logs=(_deposit_finalized_log(_L2_GATEWAY),))
    decoding = StandardABIDecoder().decode(inspection)

    observations = RobinhoodBridgeIntelligence().analyze(inspection, decoding)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.direction is BridgeDirection.L1_TO_L2
    assert observation.stage is BridgeStage.FINALIZED
    assert observation.source_chain is Chain.ETHEREUM
    assert observation.destination_chain is Chain.ROBINHOOD
    assert observation.token_address == _L1_TOKEN
    assert observation.raw_amount == "250"
    assert observation.evidence_reference == "log:4"

    actions = WalletActionNormalizer().normalize(inspection, decoding, None)
    assert len(actions) == 1
    assert actions[0].kind is WalletActionKind.BRIDGE
    assert actions[0].protocol_hint == "robinhood-canonical"

    unknown_contract = replace(
        inspection,
        logs=(_deposit_finalized_log("0x4444444444444444444444444444444444444444"),),
    )
    unknown_decoding = StandardABIDecoder().decode(unknown_contract)
    assert not RobinhoodBridgeIntelligence().analyze(unknown_contract, unknown_decoding)


def test_arbsys_message_call_is_l2_to_l1_initiation() -> None:
    input_data = _call_data(
        "sendTxToL1(address,bytes)",
        ("address", "bytes"),
        (_RECIPIENT, b"payload"),
    )
    inspection = _inspection(to_address=_ARBSYS, input_data=input_data, value_wei=12)
    decoding = StandardABIDecoder().decode(inspection)

    observations = RobinhoodBridgeIntelligence().analyze(inspection, decoding)

    assert len(observations) == 1
    observation = observations[0]
    assert observation.direction is BridgeDirection.L2_TO_L1
    assert observation.stage is BridgeStage.INITIATED
    assert observation.recipient_address == _RECIPIENT
    assert observation.raw_amount == "12"
    assert observation.evidence_reference == "call"


def _inspection(
    *,
    to_address: str = _L2_GATEWAY,
    input_data: str = "0x",
    value_wei: int = 0,
    logs: tuple[RawTransactionLog, ...] = (),
    l1_gas_used: int | None = None,
) -> TransactionInspection:
    return TransactionInspection(
        chain=Chain.ROBINHOOD,
        tx_hash=_TX_HASH,
        block_number=20_000,
        block_hash="0x" + "cd" * 32,
        transaction_index=1,
        from_address=_ACTOR,
        to_address=to_address,
        contract_address=None,
        nonce=1,
        value_wei=value_wei,
        input_data=input_data,
        gas_limit=150_000,
        gas_price=1_000_000_000,
        max_fee_per_gas=None,
        max_priority_fee_per_gas=None,
        status=True,
        gas_used=100_000,
        cumulative_gas_used=100_000,
        effective_gas_price=1_000_000_000,
        transaction_type=2,
        logs_bloom="0x" + "00" * 256,
        logs=logs,
        source_provider="fixture",
        fetched_at=datetime(2026, 9, 17, tzinfo=UTC),
        l1_gas_used=l1_gas_used,
    )


def _deposit_finalized_log(contract_address: str) -> RawTransactionLog:
    signature = "DepositFinalized(address,address,address,uint256)"
    return RawTransactionLog(
        log_index=4,
        address=contract_address,
        topics=(
            "0x" + keccak(text=signature).hex(),
            _address_topic(_L1_TOKEN),
            _address_topic(_ACTOR),
            _address_topic(_RECIPIENT),
        ),
        data="0x" + abi_encode(("uint256",), (250,)).hex(),
        removed=False,
    )


def _call_data(signature: str, types: tuple[str, ...], values: tuple[object, ...]) -> str:
    return "0x" + keccak(text=signature)[:4].hex() + abi_encode(types, values).hex()


def _address_topic(address: str) -> str:
    return "0x" + address.removeprefix("0x").rjust(64, "0")

