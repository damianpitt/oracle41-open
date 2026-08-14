"""Convert transaction evidence into deterministic wallet actions.

The normalizer recognizes deployments, token transfers, approvals, simple swaps, native value movement, and decoded calls.
Rules are deliberately conservative: unmatched evidence becomes an explicit unknown action instead of a guessed protocol label.
"""

from __future__ import annotations

import ast
from dataclasses import replace

from oracle41_open.core.models import (
    ActionAsset,
    ActionAssetDirection,
    ActionConfidence,
    ActionEvidence,
    ActionEvidenceKind,
    ActionParticipant,
    DecodedArgument,
    DecodedEvent,
    DecodeStatus,
    InternalCall,
    TransactionDecoding,
    TransactionInspection,
    TransactionTrace,
    WalletAction,
    WalletActionKind,
    WalletActionStatus,
)


class WalletActionNormalizer:
    version = "1"

    def normalize(
        self,
        inspection: TransactionInspection,
        decoding: TransactionDecoding,
        trace: TransactionTrace | None,
    ) -> tuple[WalletAction, ...]:
        status = _action_status(inspection.status)
        actions: list[WalletAction] = []

        if inspection.to_address is None or inspection.contract_address is not None:
            actions.append(self._deployment_action(inspection, status))
        if inspection.value_wei > 0:
            actions.append(self._native_value_action(inspection, status))

        event_actions = self._event_actions(inspection, decoding, status)
        actions.extend(event_actions)
        actions = _collapse_simple_swap(inspection, decoding, actions, status, self.version)

        if not event_actions:
            call_action = self._call_action(inspection, decoding, status)
            if call_action is not None:
                actions.append(call_action)

        actions.extend(self._trace_actions(inspection, trace, status, actions))

        if not actions:
            actions.append(self._unknown_action(inspection, decoding, status))

        return tuple(replace(action, action_index=index) for index, action in enumerate(actions))

    def _event_actions(
        self,
        inspection: TransactionInspection,
        decoding: TransactionDecoding,
        status: WalletActionStatus,
    ) -> list[WalletAction]:
        logs_by_index = {log.log_index: log for log in inspection.logs}
        actions: list[WalletAction] = []
        for event in decoding.events:
            if event.status is not DecodeStatus.DECODED:
                continue
            log = logs_by_index.get(event.log_index)
            if log is None:
                continue
            if event.name in {"Transfer", "TransferSingle", "TransferBatch"}:
                actions.append(
                    _transfer_event_action(inspection, event, log.address, status, self.version)
                )
            elif event.name in {"Approval", "ApprovalForAll"}:
                actions.append(
                    _approval_event_action(inspection, event, log.address, status, self.version)
                )
        return actions

    def _call_action(
        self,
        inspection: TransactionInspection,
        decoding: TransactionDecoding,
        status: WalletActionStatus,
    ) -> WalletAction | None:
        call = decoding.call
        if call.status is not DecodeStatus.DECODED or call.name is None:
            return None
        arguments = _arguments_by_name(call.arguments)
        evidence = (_call_evidence(decoding),)
        if call.name in {"approve", "setApprovalForAll"}:
            spender = arguments.get("spender") or arguments.get("operator")
            amount = arguments.get("value") or arguments.get("approved") or "unknown"
            return WalletAction(
                chain=inspection.chain,
                tx_hash=inspection.tx_hash,
                action_index=0,
                kind=WalletActionKind.APPROVAL,
                status=status,
                summary=f"Approve {spender or 'unknown spender'}: {amount}",
                participants=_participants(
                    ("owner", inspection.from_address),
                    ("spender", spender),
                ),
                assets=(
                    ActionAsset(
                        ActionAssetDirection.NEUTRAL,
                        "token",
                        inspection.to_address,
                        None,
                        arguments.get("tokenId"),
                        amount,
                    ),
                ),
                protocol_hint=None,
                confidence=ActionConfidence.HIGH,
                evidence=evidence,
                normalizer_version=self.version,
            )
        if call.name in {
            "transfer",
            "transferFrom",
            "safeTransferFrom",
            "safeBatchTransferFrom",
        }:
            sender = arguments.get("from") or inspection.from_address
            recipient = arguments.get("to")
            amount = (
                arguments.get("value")
                or arguments.get("valueOrTokenId")
                or arguments.get("values")
                or "unknown"
            )
            return WalletAction(
                chain=inspection.chain,
                tx_hash=inspection.tx_hash,
                action_index=0,
                kind=WalletActionKind.TRANSFER,
                status=status,
                summary=f"Transfer token from {_short(sender)} to {_short(recipient)}",
                participants=_participants(("sender", sender), ("recipient", recipient)),
                assets=(
                    ActionAsset(
                        _direction(sender, recipient, inspection.from_address),
                        "token",
                        inspection.to_address,
                        None,
                        arguments.get("tokenId") or arguments.get("id") or arguments.get("ids"),
                        amount,
                    ),
                ),
                protocol_hint=None,
                confidence=ActionConfidence.HIGH,
                evidence=evidence,
                normalizer_version=self.version,
            )
        return WalletAction(
            chain=inspection.chain,
            tx_hash=inspection.tx_hash,
            action_index=0,
            kind=WalletActionKind.CONTRACT_CALL,
            status=status,
            summary=f"Call {call.name}",
            participants=_participants(
                ("caller", inspection.from_address),
                ("contract", inspection.to_address),
            ),
            assets=(),
            protocol_hint=call.name,
            confidence=ActionConfidence.HIGH,
            evidence=evidence,
            normalizer_version=self.version,
        )

    def _native_value_action(
        self,
        inspection: TransactionInspection,
        status: WalletActionStatus,
    ) -> WalletAction:
        return WalletAction(
            chain=inspection.chain,
            tx_hash=inspection.tx_hash,
            action_index=0,
            kind=WalletActionKind.TRANSFER,
            status=status,
            summary=f"Transfer {inspection.chain.native_symbol}",
            participants=_participants(
                ("sender", inspection.from_address),
                ("recipient", inspection.to_address or inspection.contract_address),
            ),
            assets=(
                ActionAsset(
                    ActionAssetDirection.OUT,
                    "native",
                    None,
                    inspection.chain.native_symbol,
                    None,
                    str(inspection.value_wei),
                ),
            ),
            protocol_hint=None,
            confidence=ActionConfidence.HIGH,
            evidence=(ActionEvidence(ActionEvidenceKind.CALL, "call:value"),),
            normalizer_version=self.version,
        )

    def _trace_actions(
        self,
        inspection: TransactionInspection,
        trace: TransactionTrace | None,
        status: WalletActionStatus,
        existing: list[WalletAction],
    ) -> list[WalletAction]:
        if trace is None:
            return []
        actions: list[WalletAction] = []
        existing_deployments = {
            participant.address
            for action in existing
            if action.kind is WalletActionKind.DEPLOYMENT
            for participant in action.participants
            if participant.role == "contract"
        }
        for call in trace.calls:
            reference = "trace:" + ("root" if not call.trace_address else ".".join(map(str, call.trace_address)))
            call_status = WalletActionStatus.FAILED if call.error or call.revert_reason else status
            if call.created_contract is not None and call.created_contract not in existing_deployments:
                actions.append(
                    WalletAction(
                        chain=inspection.chain,
                        tx_hash=inspection.tx_hash,
                        action_index=0,
                        kind=WalletActionKind.DEPLOYMENT,
                        status=call_status,
                        summary=f"Deploy contract {_short(call.created_contract)}",
                        participants=_participants(
                            ("deployer", call.from_address),
                            ("contract", call.created_contract),
                        ),
                        assets=(),
                        protocol_hint=None,
                        confidence=ActionConfidence.MEDIUM,
                        evidence=(ActionEvidence(ActionEvidenceKind.TRACE, reference),),
                        normalizer_version=self.version,
                    )
                )
                existing_deployments.add(call.created_contract)
            if call.value_wei <= 0 or _duplicates_top_level_value(inspection, call):
                continue
            actions.append(
                WalletAction(
                    chain=inspection.chain,
                    tx_hash=inspection.tx_hash,
                    action_index=0,
                    kind=WalletActionKind.TRANSFER,
                    status=call_status,
                    summary=f"Internal {inspection.chain.native_symbol} transfer",
                    participants=_participants(
                        ("sender", call.from_address),
                        ("recipient", call.to_address or call.created_contract),
                    ),
                    assets=(
                        ActionAsset(
                            _direction(
                                call.from_address,
                                call.to_address or call.created_contract,
                                inspection.from_address,
                            ),
                            "native",
                            None,
                            inspection.chain.native_symbol,
                            None,
                            str(call.value_wei),
                        ),
                    ),
                    protocol_hint=None,
                    confidence=ActionConfidence.MEDIUM,
                    evidence=(ActionEvidence(ActionEvidenceKind.TRACE, reference),),
                    normalizer_version=self.version,
                )
            )
        return actions

    def _deployment_action(
        self,
        inspection: TransactionInspection,
        status: WalletActionStatus,
    ) -> WalletAction:
        target = inspection.contract_address
        return WalletAction(
            chain=inspection.chain,
            tx_hash=inspection.tx_hash,
            action_index=0,
            kind=WalletActionKind.DEPLOYMENT,
            status=status,
            summary=f"Deploy contract {_short(target)}",
            participants=_participants(
                ("deployer", inspection.from_address),
                ("contract", target),
            ),
            assets=(),
            protocol_hint=None,
            confidence=ActionConfidence.HIGH,
            evidence=(ActionEvidence(ActionEvidenceKind.RECEIPT, "receipt"),),
            normalizer_version=self.version,
        )

    def _unknown_action(
        self,
        inspection: TransactionInspection,
        decoding: TransactionDecoding,
        status: WalletActionStatus,
    ) -> WalletAction:
        signature = decoding.call.canonical_signature or decoding.call.selector
        return WalletAction(
            chain=inspection.chain,
            tx_hash=inspection.tx_hash,
            action_index=0,
            kind=WalletActionKind.UNKNOWN,
            status=status,
            summary="Unknown transaction action",
            participants=_participants(
                ("caller", inspection.from_address),
                ("target", inspection.to_address),
            ),
            assets=(),
            protocol_hint=None,
            confidence=ActionConfidence.LOW,
            evidence=(
                ActionEvidence(
                    ActionEvidenceKind.CALL,
                    "call",
                    contract_address=inspection.to_address,
                    signature=signature,
                    source_id=(
                        decoding.call.provenance.source_id
                        if decoding.call.provenance is not None
                        else None
                    ),
                ),
            ),
            normalizer_version=self.version,
        )


def _transfer_event_action(
    inspection: TransactionInspection,
    event: DecodedEvent,
    contract_address: str,
    status: WalletActionStatus,
    version: str,
) -> WalletAction:
    arguments = _arguments_by_name(event.arguments)
    sender = arguments.get("from")
    recipient = arguments.get("to")
    assets = _transfer_assets(event, contract_address, inspection.from_address, sender, recipient)
    return WalletAction(
        chain=inspection.chain,
        tx_hash=inspection.tx_hash,
        action_index=0,
        kind=WalletActionKind.TRANSFER,
        status=status,
        summary=f"Transfer {event.standard or 'token'} from {_short(sender)} to {_short(recipient)}",
        participants=_participants(
            ("sender", sender),
            ("recipient", recipient),
            ("operator", arguments.get("operator")),
        ),
        assets=assets,
        protocol_hint=None,
        confidence=ActionConfidence.HIGH,
        evidence=(_event_evidence(event, contract_address),),
        normalizer_version=version,
    )


def _approval_event_action(
    inspection: TransactionInspection,
    event: DecodedEvent,
    contract_address: str,
    status: WalletActionStatus,
    version: str,
) -> WalletAction:
    arguments = _arguments_by_name(event.arguments)
    owner = arguments.get("owner")
    spender = arguments.get("spender") or arguments.get("approved") or arguments.get("operator")
    token_id = arguments.get("tokenId")
    if token_id is not None:
        amount = "1"
    else:
        amount = arguments.get("value") or arguments.get("approved") or "unknown"
    return WalletAction(
        chain=inspection.chain,
        tx_hash=inspection.tx_hash,
        action_index=0,
        kind=WalletActionKind.APPROVAL,
        status=status,
        summary=f"Approve {_short(spender)}: {amount}",
        participants=_participants(("owner", owner), ("spender", spender)),
        assets=(
            ActionAsset(
                ActionAssetDirection.NEUTRAL,
                event.standard or "token",
                contract_address,
                None,
                token_id,
                amount,
            ),
        ),
        protocol_hint=None,
        confidence=ActionConfidence.HIGH,
        evidence=(_event_evidence(event, contract_address),),
        normalizer_version=version,
    )


def _collapse_simple_swap(
    inspection: TransactionInspection,
    decoding: TransactionDecoding,
    actions: list[WalletAction],
    status: WalletActionStatus,
    version: str,
) -> list[WalletAction]:
    transfers = [action for action in actions if action.kind is WalletActionKind.TRANSFER]
    outbound = [action for action in transfers if _has_direction(action, ActionAssetDirection.OUT)]
    inbound = [action for action in transfers if _has_direction(action, ActionAssetDirection.IN)]
    asset_identities = {
        (asset.standard, asset.contract_address)
        for action in (*outbound, *inbound)
        for asset in action.assets
    }
    if len(outbound) != 1 or len(inbound) != 1 or len(asset_identities) < 2:
        return actions
    used = {*outbound, *inbound}
    assets = tuple(asset for action in (*outbound, *inbound) for asset in action.assets)
    evidence = tuple(item for action in (*outbound, *inbound) for item in action.evidence)
    swap = WalletAction(
        chain=inspection.chain,
        tx_hash=inspection.tx_hash,
        action_index=0,
        kind=WalletActionKind.SWAP,
        status=status,
        summary=f"Swap {len(outbound)} outgoing asset(s) for {len(inbound)} incoming asset(s)",
        participants=_participants(
            ("actor", inspection.from_address),
            ("contract", inspection.to_address),
        ),
        assets=assets,
        protocol_hint=_swap_protocol_hint(decoding),
        confidence=ActionConfidence.MEDIUM,
        evidence=evidence,
        normalizer_version=version,
    )
    remaining = [action for action in actions if action not in used]
    first_index = min(actions.index(action) for action in used)
    remaining.insert(first_index, swap)
    return remaining


def _transfer_assets(
    event: DecodedEvent,
    contract_address: str,
    actor: str,
    sender: str | None,
    recipient: str | None,
) -> tuple[ActionAsset, ...]:
    arguments = _arguments_by_name(event.arguments)
    direction = _direction(sender, recipient, actor)
    if event.name == "TransferBatch":
        ids = _array_values(arguments.get("ids"))
        values = _array_values(arguments.get("values"))
        if len(ids) == len(values) and ids:
            return tuple(
                ActionAsset(direction, event.standard or "ERC-1155", contract_address, None, token_id, amount)
                for token_id, amount in zip(ids, values, strict=True)
            )
    token_id = arguments.get("tokenId") or arguments.get("id")
    amount = arguments.get("value") or ("1" if token_id is not None else "unknown")
    return (
        ActionAsset(
            direction,
            event.standard or "token",
            contract_address,
            None,
            token_id,
            amount,
        ),
    )


def _call_evidence(decoding: TransactionDecoding) -> ActionEvidence:
    call = decoding.call
    return ActionEvidence(
        ActionEvidenceKind.CALL,
        "call",
        contract_address=decoding.contract_address,
        signature=call.canonical_signature or call.selector,
        source_id=call.provenance.source_id if call.provenance is not None else None,
    )


def _event_evidence(event: DecodedEvent, contract_address: str) -> ActionEvidence:
    return ActionEvidence(
        ActionEvidenceKind.EVENT,
        f"log:{event.log_index}",
        contract_address=contract_address,
        signature=event.canonical_signature or event.topic0,
        source_id=event.provenance.source_id if event.provenance is not None else None,
    )


def _arguments_by_name(arguments: tuple[DecodedArgument, ...]) -> dict[str, str]:
    return {argument.name: argument.value for argument in arguments}


def _participants(*entries: tuple[str, str | None]) -> tuple[ActionParticipant, ...]:
    return tuple(
        ActionParticipant(role, address.lower())
        for role, address in entries
        if address is not None
    )


def _direction(
    sender: str | None,
    recipient: str | None,
    actor: str,
) -> ActionAssetDirection:
    normalized_actor = actor.lower()
    if sender is not None and sender.lower() == normalized_actor:
        return ActionAssetDirection.OUT
    if recipient is not None and recipient.lower() == normalized_actor:
        return ActionAssetDirection.IN
    return ActionAssetDirection.NEUTRAL


def _has_direction(action: WalletAction, direction: ActionAssetDirection) -> bool:
    return any(asset.direction is direction for asset in action.assets)


def _action_status(status: bool | None) -> WalletActionStatus:
    if status is True:
        return WalletActionStatus.SUCCESS
    if status is False:
        return WalletActionStatus.FAILED
    return WalletActionStatus.UNKNOWN


def _array_values(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return ()
    if not isinstance(parsed, list):
        return ()
    return tuple(str(item) for item in parsed)


def _swap_protocol_hint(decoding: TransactionDecoding) -> str | None:
    name = decoding.call.name
    if name is None:
        return None
    lowered = name.lower()
    if "swap" in lowered or lowered.startswith(("exactinput", "exactoutput")):
        return name
    return None


def _duplicates_top_level_value(
    inspection: TransactionInspection,
    call: InternalCall,
) -> bool:
    return (
        not call.trace_address
        and call.from_address == inspection.from_address
        and call.to_address == inspection.to_address
        and call.value_wei == inspection.value_wei
    )


def _short(address: str | None) -> str:
    if address is None:
        return "unknown"
    if len(address) <= 14:
        return address
    return f"{address[:8]}...{address[-6:]}"
