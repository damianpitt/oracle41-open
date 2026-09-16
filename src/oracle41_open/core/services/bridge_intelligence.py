"""Classify Robinhood canonical bridge transactions from local evidence.

The classifier accepts only published Robinhood bridge contracts together with known Arbitrum calls or events.
It records one observed lifecycle stage and never assumes that the matching transaction on the other chain succeeded.
"""

from __future__ import annotations

from collections.abc import Mapping

from oracle41_open.core.models import (
    BridgeDirection,
    BridgeObservation,
    BridgeStage,
    BridgeTransferKind,
    Chain,
    DecodedArgument,
    DecodeStatus,
    TransactionDecoding,
    TransactionInspection,
)

_BRIDGE_ID = "robinhood-canonical"
_BRIDGE_NAME = "Robinhood canonical bridge"

# Addresses are published in Robinhood Chain's protocol-contract registry.
_L1_GATEWAYS = frozenset({
    "0x6a2e3a1e16fc29f27ce61429746d558d656975bb",  # Gateway Router
    "0x85001cc4867c5e1c22da4b79bb8852b9e2a06da0",  # ERC-20 Gateway
    "0x9368eaebfe6e063c69dcf8126711a6997e0ecee1",  # Custom Gateway
    "0xf7e12b9614b509c747ab4423bc4acf923759cf1b",  # WETH Gateway
})
_L2_GATEWAYS = frozenset({
    "0x1e324b9316138ca9a73f960213621ad1aaf01b89",  # Gateway Router
    "0xfd9b17206278c16ddaacf6ac8f05dbf97edcb31e",  # ERC-20 Gateway
    "0x912285144fc0f6e89d3ed16f5ab72f87a1878959",  # Custom Gateway
    "0x1d187c3e2da52d72bc9c41e3aba0fdfa6a7bf055",  # WETH Gateway
})
_ARBSYS = "0x0000000000000000000000000000000000000064"

_EVENT_SEMANTICS: Mapping[
    tuple[Chain, str], tuple[BridgeDirection, BridgeStage]
] = {
    (Chain.ETHEREUM, "DepositInitiated"): (
        BridgeDirection.L1_TO_L2,
        BridgeStage.INITIATED,
    ),
    (Chain.ETHEREUM, "WithdrawalFinalized"): (
        BridgeDirection.L2_TO_L1,
        BridgeStage.FINALIZED,
    ),
    (Chain.ROBINHOOD, "DepositFinalized"): (
        BridgeDirection.L1_TO_L2,
        BridgeStage.FINALIZED,
    ),
    (Chain.ROBINHOOD, "WithdrawalInitiated"): (
        BridgeDirection.L2_TO_L1,
        BridgeStage.INITIATED,
    ),
}


class RobinhoodBridgeIntelligence:
    """Extract canonical Robinhood bridge facts from decoded transaction evidence."""

    def analyze(
        self,
        inspection: TransactionInspection,
        decoding: TransactionDecoding,
    ) -> tuple[BridgeObservation, ...]:
        observations = self._event_observations(inspection, decoding)
        if observations:
            return tuple(observations)
        fallback = self._call_observation(inspection, decoding)
        return (fallback,) if fallback is not None else ()

    def _event_observations(
        self,
        inspection: TransactionInspection,
        decoding: TransactionDecoding,
    ) -> list[BridgeObservation]:
        logs_by_index = {log.log_index: log for log in inspection.logs}
        observations: list[BridgeObservation] = []
        for event in decoding.events:
            semantics = _EVENT_SEMANTICS.get((inspection.chain, event.name or ""))
            log = logs_by_index.get(event.log_index)
            if (
                semantics is None
                or log is None
                or event.status is not DecodeStatus.DECODED
                or not _is_gateway(inspection.chain, log.address)
            ):
                continue
            direction, stage = semantics
            arguments = _arguments_by_name(event.arguments)
            observations.append(
                _observation(
                    inspection,
                    direction=direction,
                    stage=stage,
                    transfer_kind=BridgeTransferKind.TOKEN,
                    contract_address=log.address,
                    sender=arguments.get("from"),
                    recipient=arguments.get("to"),
                    token=arguments.get("l1Token"),
                    amount=arguments.get("amount"),
                    message_id=(
                        arguments.get("sequenceNumber")
                        or arguments.get("l2ToL1Id")
                        or arguments.get("exitNum")
                    ),
                    evidence_reference=f"log:{event.log_index}",
                    evidence_signature=event.canonical_signature or event.topic0 or "unknown",
                )
            )
        return observations

    def _call_observation(
        self,
        inspection: TransactionInspection,
        decoding: TransactionDecoding,
    ) -> BridgeObservation | None:
        call = decoding.call
        target = (inspection.to_address or "").lower()
        if call.status is not DecodeStatus.DECODED or call.name is None:
            return None
        arguments = _arguments_by_name(call.arguments)
        if inspection.chain is Chain.ROBINHOOD and target == _ARBSYS and call.name == "sendTxToL1":
            return _observation(
                inspection,
                direction=BridgeDirection.L2_TO_L1,
                stage=BridgeStage.INITIATED,
                transfer_kind=BridgeTransferKind.MESSAGE,
                contract_address=target,
                sender=inspection.from_address,
                recipient=arguments.get("destination"),
                token=None,
                amount=str(inspection.value_wei) if inspection.value_wei else None,
                message_id=None,
                evidence_reference="call",
                evidence_signature=call.canonical_signature or call.selector or "unknown",
            )
        if call.name != "outboundTransfer" or not _is_gateway(inspection.chain, target):
            return None
        direction = (
            BridgeDirection.L1_TO_L2
            if inspection.chain is Chain.ETHEREUM
            else BridgeDirection.L2_TO_L1
        )
        return _observation(
            inspection,
            direction=direction,
            stage=BridgeStage.INITIATED,
            transfer_kind=BridgeTransferKind.TOKEN,
            contract_address=target,
            sender=inspection.from_address,
            recipient=arguments.get("to"),
            token=arguments.get("l1Token"),
            amount=arguments.get("amount"),
            message_id=None,
            evidence_reference="call",
            evidence_signature=call.canonical_signature or call.selector or "unknown",
        )


def _observation(
    inspection: TransactionInspection,
    *,
    direction: BridgeDirection,
    stage: BridgeStage,
    transfer_kind: BridgeTransferKind,
    contract_address: str,
    sender: str | None,
    recipient: str | None,
    token: str | None,
    amount: str | None,
    message_id: str | None,
    evidence_reference: str,
    evidence_signature: str,
) -> BridgeObservation:
    source_chain, destination_chain = (
        (Chain.ETHEREUM, Chain.ROBINHOOD)
        if direction is BridgeDirection.L1_TO_L2
        else (Chain.ROBINHOOD, Chain.ETHEREUM)
    )
    return BridgeObservation(
        chain=inspection.chain,
        tx_hash=inspection.tx_hash,
        bridge_id=_BRIDGE_ID,
        bridge_name=_BRIDGE_NAME,
        direction=direction,
        stage=stage,
        transfer_kind=transfer_kind,
        source_chain=source_chain,
        destination_chain=destination_chain,
        contract_address=contract_address.lower(),
        sender_address=sender.lower() if sender is not None else None,
        recipient_address=recipient.lower() if recipient is not None else None,
        token_address=token.lower() if token is not None else None,
        raw_amount=amount,
        message_id=message_id,
        evidence_reference=evidence_reference,
        evidence_signature=evidence_signature,
    )


def _is_gateway(chain: Chain, address: str) -> bool:
    normalized = address.lower()
    if chain is Chain.ETHEREUM:
        return normalized in _L1_GATEWAYS
    if chain is Chain.ROBINHOOD:
        return normalized in _L2_GATEWAYS
    return False


def _arguments_by_name(arguments: tuple[DecodedArgument, ...]) -> dict[str, str]:
    return {argument.name: argument.value for argument in arguments}
