"""Collect historical token balances and audit saved movement and price inputs.

The opening balance is never assumed to be zero: both endpoints are read at explicit blocks.
Local ERC-20 movements are replayed using raw integers, with duplicates and conflicting evidence
handled explicitly. Matching endpoint balances do not prove complete intermediate activity.
Fees remain native-asset context and are never deducted from ERC-20 balances.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal, DecimalException, InvalidOperation, localcontext
from typing import Any
from uuid import uuid4

from oracle41_open._json import dumps
from oracle41_open.core.models import Chain, ProviderResponseError, ValidationError
from oracle41_open.core.models.historical_holdings import (
    HistoricalHoldingsReport,
    HistoricalHoldingsReportSummary,
    HistoricalHoldingsRequest,
)
from oracle41_open.core.models.historical_price import time_text, utc_time
from oracle41_open.core.services.historical_price_service import HistoricalPriceService
from oracle41_open.core.services.protocol_collection import (
    ContractStateReader,
    SnapshotReadTracker,
    call_data,
    decode_call_result,
)
from oracle41_open.storage.db.historical_holdings_repository import HistoricalHoldingsRepository


class HistoricalHoldingsService:
    def __init__(
        self,
        provider: ContractStateReader,
        repository: HistoricalHoldingsRepository,
        prices: HistoricalPriceService,
        now_func: Callable[[], datetime] | None = None,
        rpc_available: bool = True,
    ) -> None:
        self._provider = provider
        self._repository = repository
        self._prices = prices
        self._now = now_func or (lambda: datetime.now(tz=UTC))
        self.can_collect = rpc_available

    def collect(self, request: HistoricalHoldingsRequest) -> HistoricalHoldingsReport:
        """Capture endpoints, local evidence, and a pinned price without synchronizing activity."""
        if not self.can_collect:
            raise ProviderResponseError(
                "Configure Alchemy, Ankr, or a custom historical JSON-RPC endpoint, then restart."
            )
        if request.price_at > utc_time(self._now()):
            raise ValidationError("The requested historical price time cannot be in the future.")
        opening = self._anchor(request, request.opening_block)
        closing = self._anchor(request, request.closing_block, opening["source_provider"])
        if opening["decimals"] != closing["decimals"]:
            raise ProviderResponseError(
                "Token decimals changed between the requested blocks; one-unit replay is not supported."
            )
        ledger = self._repository.capture(request)
        created = utc_time(self._now())
        selection = self._prices.select(
            request.chain,
            request.contract_address,
            request.price_at,
            max_age_seconds=request.max_price_age_seconds,
            known_by=created,
        )
        price_batch = self._prices.get_fetch(selection.fetch_id) if selection.fetch_id else None
        price = {
            "status": selection.status,
            "fetch_id": selection.fetch_id,
            "source_provider": selection.source_provider,
            "source_reference": price_batch.source_reference if price_batch else None,
            "fetched_at": time_text(price_batch.fetched_at) if price_batch else None,
            "price_at": time_text(selection.point.timestamp) if selection.point else None,
            "value_usd": str(selection.point.value_usd) if selection.point else None,
            "usable": selection.usable_value_usd is not None,
            "known_by": time_text(created),
        }
        inputs = {
            "request": {
                "wallet_address": request.wallet_address,
                "chain": request.chain.value,
                "contract_address": request.contract_address,
                "opening_block": request.opening_block,
                "closing_block": request.closing_block,
                "price_at": time_text(request.price_at),
                "max_price_age_seconds": request.max_price_age_seconds,
                "native_symbol": request.chain.native_symbol,
            },
            "opening": opening,
            "closing": closing,
            "ledger": ledger,
            "price": price,
        }
        report_id = str(uuid4())
        document = {
            "format": "oracle41-historical-holdings",
            "format_version": 1,
            "report_id": report_id,
            "created_at": time_text(created),
            "scope": "single_erc20_end_of_block_inputs_not_portfolio_or_accounting",
            "inputs": inputs,
            "result": analyze_holdings_inputs(inputs),
        }
        report = HistoricalHoldingsReport(
            report_id, created, dumps(document, pretty=True).decode("utf-8")
        )
        self._repository.save(report)
        return report

    def _anchor(
        self, request: HistoricalHoldingsRequest, block: int, source: str | None = None
    ) -> dict[str, Any]:
        tracker = SnapshotReadTracker(self._provider, request.chain, block, source_provider=source)
        balance = tracker.read(
            request.contract_address,
            call_data("balanceOf(address)", ("address",), (request.wallet_address,)),
        )
        amount = decode_call_result(balance.data, ("uint256",), "Historical token balance")[0]
        units = tracker.read(request.contract_address, call_data("decimals()"))
        decimals = decode_call_result(units.data, ("uint8",), "Historical token decimals")[0]
        return {
            "block_number": block,
            "raw_balance": str(amount),
            "decimals": int(str(decimals)),
            "source_provider": tracker.source_provider,
            "observed_at": time_text(tracker.observed_at),
            "method": "balanceOf(address),decimals()",
            "block_reference": "number_not_hash_pinned",
        }

    def load(self, report_id: str) -> HistoricalHoldingsReport | None:
        return self._repository.get(report_id)

    def list_saved(
        self, wallet: str, chain: Chain, token: str
    ) -> tuple[HistoricalHoldingsReportSummary, ...]:
        return self._repository.list_reports(wallet, chain, token)


def raw_uint(value: object) -> int:
    """Accept provider decimal or hexadecimal integer text without floating-point conversion."""
    if not isinstance(value, str) or len(value) > 78:
        raise ValueError("Invalid raw integer")
    if re.fullmatch(r"0x[0-9a-fA-F]+", value):
        parsed = int(value, 16)
    elif value.isascii() and value.isdigit():
        parsed = int(value)
    else:
        raise ValueError("Invalid raw integer")
    if not 0 <= parsed < 2**256:
        raise ValueError("Raw integer outside uint256")
    return parsed


def token_units(raw: int, decimals: int) -> str:
    """Format integer units exactly, even beyond the default Decimal precision."""
    sign = "-" if raw < 0 else ""
    digits = str(abs(raw)).rjust(decimals + 1, "0")
    return sign + (digits if decimals == 0 else digits[:-decimals] + "." + digits[-decimals:])


def analyze_holdings_inputs(inputs: dict[str, Any]) -> dict[str, Any]:
    """Reproduce derived values from frozen inputs without touching providers or the ledger."""
    request, ledger = inputs["request"], inputs["ledger"]
    wallet = request["wallet_address"]
    decimals = inputs["closing"]["decimals"]
    opening = raw_uint(inputs["opening"]["raw_balance"])
    closing = raw_uint(inputs["closing"]["raw_balance"])
    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    excluded: list[dict[str, str]] = []
    ignored = 0
    for event in ledger["events"]:
        # Approval values are allowances, not movements. NFTs are not fungible token units.
        if event["category"] != "erc20":
            ignored += 1
            continue
        try:
            if event["block_number"] is None:
                raise ValueError("missing_block")
            if not request["opening_block"] < event["block_number"] <= request["closing_block"]:
                raise ValueError("outside_block_window")
            if event["receipt_status"] == 0:
                raise ValueError("failed_receipt")
            if (
                event["receipt_block_number"] is not None
                and event["receipt_block_number"] != event["block_number"]
            ):
                raise ValueError("receipt_block_mismatch")
            tx_hash = event["tx_hash"].lower()
            if not re.fullmatch(r"0x[0-9a-f]{64}", tx_hash):
                raise ValueError("invalid_transaction_hash")
            index = raw_uint(event["log_index"])
            raw_uint(event["raw_value"])
            participants = (event["from_address"].lower(), event["to_address"].lower())
            if (
                any(not re.fullmatch(r"0x[0-9a-f]{40}", address) for address in participants)
                or wallet not in participants
            ):
                raise ValueError("invalid_participants")
            groups[(tx_hash, index)].append(event)
        except (ValueError, TypeError, AttributeError) as error:
            excluded.append({"event_id": str(event["event_id"]), "reason": str(error)})
    movements: list[dict[str, Any]] = []
    duplicates = 0
    metadata_mismatches = 0
    unknown_receipts = 0
    for (tx_hash, index), events in sorted(groups.items()):
        fingerprints = {
            (
                event["block_number"],
                event["from_address"].lower(),
                event["to_address"].lower(),
                raw_uint(event["raw_value"]),
            )
            for event in events
        }
        if len(fingerprints) != 1:
            excluded.extend(
                {"event_id": event["event_id"], "reason": "conflicting_canonical_log"}
                for event in events
            )
            continue
        duplicates += len(events) - 1
        event = events[0]
        raw = raw_uint(event["raw_value"])
        sender, recipient = event["from_address"].lower(), event["to_address"].lower()
        delta = raw * (int(recipient == wallet) - int(sender == wallet))
        for original in events:
            try:
                metadata_mismatches += int(
                    Decimal(original["value_decimal"]) != Decimal(token_units(raw, decimals))
                )
            except (InvalidOperation, ValueError):
                metadata_mismatches += 1
        unknown_receipts += int(any(item["receipt_status"] is None for item in events))
        movements.append(
            {
                "tx_hash": tx_hash,
                "log_index": str(index),
                "block_number": event["block_number"],
                "event_ids": sorted(item["event_id"] for item in events),
                "raw_value": str(raw),
                "signed_raw_delta": str(delta),
                "direction": "self" if sender == recipient else "in" if delta >= 0 else "out",
            }
        )
    movements.sort(key=lambda item: (item["block_number"], item["tx_hash"], int(item["log_index"])))
    # Only block-end running balances are checked: transaction order within a block is not available.
    block_deltas: dict[int, int] = defaultdict(int)
    for movement in movements:
        block_deltas[movement["block_number"]] += int(movement["signed_raw_delta"])
    projected = opening
    negative_blocks = []
    for block, delta in sorted(block_deltas.items()):
        projected += delta
        if projected < 0:
            negative_blocks.append(block)
    difference = closing - projected
    warnings = [
        "Saved ledger rows do not prove complete intermediate activity, even when endpoint balances match.",
        "Price time is user supplied and is not verified against the closing block timestamp.",
        "Block-number reads are not hash-pinned or a finality guarantee.",
        "Native holdings, NFT and protocol positions, accounting lots, and P&L are outside this report.",
    ]
    if difference:
        warnings.append(
            "Saved transfer replay differs from the closing balance; missing activity, token mechanics, or changed chain history need investigation."
        )
    if excluded:
        warnings.append("Some candidate ERC-20 rows were excluded; inspect their reasons.")
    if negative_blocks:
        warnings.append(
            "Replay produces negative block-end holdings. Values were not clamped to zero."
        )
    if metadata_mismatches:
        warnings.append(
            "Some provider decimal amounts disagree with raw units and the observed token decimals."
        )
    if unknown_receipts:
        warnings.append(
            "Some used logs have no saved receipt status; they remain provider observations."
        )
    if any(
        item["completeness"] != "complete" or item["next_cursor"] is not None
        for item in ledger["checkpoints"]
    ):
        warnings.append(
            "Some saved query scopes are unfinished or limited; scope details are retained as context only."
        )
    fee_rows = []
    for fee in ledger["fees"]:
        try:
            amount = raw_uint(fee["raw_value"])
            if fee["block_number"] is None or fee["asset_symbol"] != request["native_symbol"]:
                raise ValueError("Fee cannot be assigned to this window and native asset.")
            fee_rows.append(
                {
                    "tx_hash": fee["tx_hash"],
                    "raw_native_fee": str(amount),
                    "block_number": fee["block_number"],
                    "receipt_status": fee["receipt_status"],
                }
            )
        except ValueError:
            warnings.append(
                "A saved fee has unknown block or asset units and is excluded from the known native-fee sum."
            )
    price = inputs["price"]
    value = None
    eligible_price = False
    try:
        quote = Decimal(price["value_usd"])
        point_time = utc_time(datetime.fromisoformat(price["price_at"]))
        age = (utc_time(datetime.fromisoformat(request["price_at"])) - point_time).total_seconds()
        fetched_at = utc_time(datetime.fromisoformat(price["fetched_at"]))
        known_by = utc_time(datetime.fromisoformat(price["known_by"]))
        eligible_price = (
            price["usable"]
            and price["status"] in {"exact", "prior"}
            and quote.is_finite()
            and quote >= 0
            and 0 <= age <= request["max_price_age_seconds"]
            and point_time <= fetched_at <= known_by
            and price["fetch_id"] is not None
        )
    except (ValueError, TypeError, InvalidOperation, ValidationError):
        quote = Decimal("0")
    # Recheck pinned price timing during replay; a flag alone must never authorize a later quote.
    if eligible_price:
        try:
            with localcontext() as context:
                context.prec = max(128, len(str(closing)) + len(quote.as_tuple().digits) + 4)
                value = str(Decimal(token_units(closing, decimals)) * quote)
        except DecimalException:
            eligible_price = False
            warnings.append(
                "Price arithmetic exceeded the supported numeric range; no USD value was produced."
            )
    if not eligible_price:
        warnings.append(
            "No eligible historical price input: missing, stale, invalid, or later evidence is not used."
        )
    return {
        "opening_raw_balance": str(opening),
        "observed_closing_raw_balance": str(closing),
        "projected_closing_raw_balance": str(projected),
        "reconciliation_difference_raw": str(difference),
        "observed_closing_amount": token_units(closing, decimals),
        "reconciliation_status": "matches_saved_transfers"
        if difference == 0 and not excluded and not negative_blocks and not metadata_mismatches
        else "unreconciled",
        "is_complete_history": False,
        "price_time_source": "user_supplied_not_block_verified",
        "closing_balance_estimate_usd": value,
        "price_eligible": bool(eligible_price),
        "price_status": price["status"],
        "applied_movements": movements,
        "excluded_events": excluded,
        "ignored_non_erc20_rows": ignored,
        "duplicate_log_rows": duplicates,
        "decimal_metadata_mismatches": metadata_mismatches,
        "unknown_receipt_count": unknown_receipts,
        "negative_block_end_balances": negative_blocks,
        "saved_native_fees": fee_rows,
        "known_native_fee_total_raw": str(sum(int(row["raw_native_fee"]) for row in fee_rows))
        if fee_rows
        else None,
        "fee_coverage": "saved_native_records_only_not_complete_not_deducted_from_erc20",
        "warnings": warnings,
    }
