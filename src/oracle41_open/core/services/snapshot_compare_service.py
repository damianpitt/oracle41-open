from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from oracle41_open.core.models import ValidationError
from oracle41_open.storage.db.models import WalletSnapshot


@dataclass(frozen=True)
class SnapshotTokenDelta:
    key: str
    contract_address: str | None
    symbol: str
    before_balance: Decimal
    after_balance: Decimal
    balance_delta: Decimal
    before_usd: Decimal | None
    after_usd: Decimal | None
    usd_delta: Decimal | None
    change_type: str


@dataclass(frozen=True)
class SnapshotComparisonResult:
    older_snapshot: WalletSnapshot
    newer_snapshot: WalletSnapshot
    native_balance_delta: Decimal
    native_usd_delta: Decimal | None
    total_usd_delta: Decimal | None
    token_count_delta: int
    token_deltas: list[SnapshotTokenDelta]
    added_token_count: int
    removed_token_count: int
    changed_token_count: int


@dataclass(frozen=True)
class _SnapshotTokenEntry:
    key: str
    contract_address: str | None
    symbol: str
    balance_decimal: Decimal
    balance_usd: Decimal | None


class SnapshotCompareService:
    def compare_snapshots(
        self,
        first: WalletSnapshot,
        second: WalletSnapshot,
        include_unchanged_tokens: bool = False,
    ) -> SnapshotComparisonResult:
        if first.address != second.address or first.chain is not second.chain:
            raise ValidationError("Snapshots must belong to the same wallet address and chain.")

        older, newer = _ordered_snapshots(first, second)
        older_tokens = _decode_token_entries(older)
        newer_tokens = _decode_token_entries(newer)

        token_deltas: list[SnapshotTokenDelta] = []
        added = 0
        removed = 0
        changed = 0
        keys = sorted(set(older_tokens.keys()) | set(newer_tokens.keys()))
        for key in keys:
            before = older_tokens.get(key)
            after = newer_tokens.get(key)
            before_balance = before.balance_decimal if before is not None else Decimal("0")
            after_balance = after.balance_decimal if after is not None else Decimal("0")
            balance_delta = after_balance - before_balance
            before_usd = before.balance_usd if before is not None else None
            after_usd = after.balance_usd if after is not None else None
            usd_delta = _delta_optional_decimal(before_usd, after_usd)
            change_type = _change_type(before, after, balance_delta, usd_delta)
            if change_type == "added":
                added += 1
            elif change_type == "removed":
                removed += 1
            elif change_type == "changed":
                changed += 1
            if change_type == "unchanged" and not include_unchanged_tokens:
                continue

            reference = after if after is not None else before
            if reference is None:
                continue
            token_deltas.append(
                SnapshotTokenDelta(
                    key=key,
                    contract_address=reference.contract_address,
                    symbol=reference.symbol,
                    before_balance=before_balance,
                    after_balance=after_balance,
                    balance_delta=balance_delta,
                    before_usd=before_usd,
                    after_usd=after_usd,
                    usd_delta=usd_delta,
                    change_type=change_type,
                )
            )
        token_deltas.sort(key=_token_delta_sort_key)

        return SnapshotComparisonResult(
            older_snapshot=older,
            newer_snapshot=newer,
            native_balance_delta=newer.native_balance - older.native_balance,
            native_usd_delta=_delta_optional_decimal(
                _native_usd(older),
                _native_usd(newer),
            ),
            total_usd_delta=_delta_optional_decimal(older.total_usd, newer.total_usd),
            token_count_delta=newer.token_count - older.token_count,
            token_deltas=token_deltas,
            added_token_count=added,
            removed_token_count=removed,
            changed_token_count=changed,
        )


def _ordered_snapshots(first: WalletSnapshot, second: WalletSnapshot) -> tuple[WalletSnapshot, WalletSnapshot]:
    if first.captured_at < second.captured_at:
        return first, second
    if second.captured_at < first.captured_at:
        return second, first
    if first.id <= second.id:
        return first, second
    return second, first


def _decode_token_entries(snapshot: WalletSnapshot) -> dict[str, _SnapshotTokenEntry]:
    raw_payload = snapshot.payload
    raw_balances = raw_payload.get("token_balances")
    if not isinstance(raw_balances, list):
        return {}

    decoded: dict[str, _SnapshotTokenEntry] = {}
    for raw_entry in raw_balances:
        if not isinstance(raw_entry, dict):
            continue
        contract_address = _normalized_optional_address(raw_entry.get("contract_address"))
        symbol = _normalized_symbol(raw_entry.get("symbol"))
        key = contract_address or f"symbol:{symbol.lower()}"
        balance_decimal = _parse_decimal(raw_entry.get("balance_decimal")) or Decimal("0")
        balance_usd = _parse_decimal(raw_entry.get("balance_usd"))
        decoded[key] = _SnapshotTokenEntry(
            key=key,
            contract_address=contract_address,
            symbol=symbol,
            balance_decimal=balance_decimal,
            balance_usd=balance_usd,
        )
    return decoded


def _normalized_optional_address(raw: Any) -> str | None:
    if not isinstance(raw, str):
        return None
    trimmed = raw.strip().lower()
    if not trimmed.startswith("0x") or len(trimmed) != 42:
        return None
    return trimmed


def _normalized_symbol(raw: Any) -> str:
    if not isinstance(raw, str):
        return "UNKNOWN"
    trimmed = raw.strip()
    if not trimmed:
        return "UNKNOWN"
    return trimmed.upper()


def _parse_decimal(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError):
        return None


def _delta_optional_decimal(before: Decimal | None, after: Decimal | None) -> Decimal | None:
    if before is None and after is None:
        return None
    if before is None or after is None:
        return None
    return after - before


def _native_usd(snapshot: WalletSnapshot) -> Decimal | None:
    if snapshot.native_price_usd is None:
        return None
    return snapshot.native_balance * snapshot.native_price_usd


def _change_type(
    before: _SnapshotTokenEntry | None,
    after: _SnapshotTokenEntry | None,
    balance_delta: Decimal,
    usd_delta: Decimal | None,
) -> str:
    if before is None and after is not None:
        return "added"
    if before is not None and after is None:
        return "removed"
    if balance_delta != Decimal("0"):
        return "changed"
    if usd_delta is not None and usd_delta != Decimal("0"):
        return "changed"
    return "unchanged"


def _token_delta_sort_key(delta: SnapshotTokenDelta) -> tuple[int, Decimal, Decimal, str]:
    change_rank = {
        "changed": 0,
        "added": 1,
        "removed": 2,
        "unchanged": 3,
    }.get(delta.change_type, 4)
    magnitude_usd = abs(delta.usd_delta) if delta.usd_delta is not None else Decimal("0")
    magnitude_balance = abs(delta.balance_delta)
    return (change_rank, -magnitude_usd, -magnitude_balance, delta.symbol)
