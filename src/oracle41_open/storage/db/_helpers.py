from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from oracle41_open.core.models import ValidationError


def utc_now() -> datetime:
    return datetime.now(tz=UTC)


def utc_now_iso() -> str:
    return utc_now().isoformat()


def parse_datetime(raw: object) -> datetime:
    if not isinstance(raw, str):
        raise ValueError("Expected ISO datetime string.")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_decimal(raw: object) -> Decimal:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError) as error:
        raise ValueError("Expected decimal-compatible value.") from error


def parse_optional_decimal(raw: object) -> Decimal | None:
    if raw is None:
        return None
    return parse_decimal(raw)


def normalize_address_or_raise(address: str) -> str:
    normalized = address.strip().lower()
    if not _is_valid_address(normalized):
        raise ValidationError(
            "Invalid wallet address. Expected 0x + 40 hex characters."
        )
    return normalized


def normalize_label(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        return None
    return trimmed


def normalize_non_empty(value: str, field_name: str) -> str:
    trimmed = value.strip()
    if trimmed:
        return trimmed
    raise ValidationError(f"{field_name} cannot be empty.")


def normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in tags:
        trimmed = value.strip()
        if not trimmed:
            continue
        key = trimmed.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(trimmed)
    return normalized


def expect_int(raw: object, field_name: str) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError(f"Expected integer for {field_name}.")
    return raw


def _is_valid_address(value: str) -> bool:
    if not value.startswith("0x"):
        return False
    hex_part = value[2:]
    if len(hex_part) != 40:
        return False
    return all(char in "0123456789abcdef" for char in hex_part)
