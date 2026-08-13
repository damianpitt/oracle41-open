"""Convert common EVM trace formats into one internal call model.

The mapper supports Geth callTracer trees and Parity-style flat trace lists.
Invalid frames make a trace partial, while the complete raw payload remains available.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from oracle41_open.core.models import InternalCall, TraceStatus


@dataclass(frozen=True)
class MappedTrace:
    calls: tuple[InternalCall, ...]
    status: TraceStatus
    error: str | None = None


def map_debug_call_trace(payload: object) -> MappedTrace:
    if not isinstance(payload, dict):
        return MappedTrace((), TraceStatus.PARTIAL, "Call tracer returned no root call.")

    calls: list[InternalCall] = []
    invalid_frames = 0

    def visit(frame: object, trace_address: tuple[int, ...]) -> None:
        nonlocal invalid_frames
        if not isinstance(frame, dict):
            invalid_frames += 1
            return
        try:
            calls.append(_map_debug_frame(frame, trace_address))
        except (TypeError, ValueError):
            invalid_frames += 1
        children = frame.get("calls", [])
        if not isinstance(children, list):
            invalid_frames += 1
            return
        for index, child in enumerate(children):
            visit(child, (*trace_address, index))

    visit(payload, ())
    return _mapped_result(calls, invalid_frames)


def map_parity_trace(payload: object) -> MappedTrace:
    if not isinstance(payload, list):
        return MappedTrace((), TraceStatus.PARTIAL, "Trace API returned no call list.")

    calls: list[InternalCall] = []
    invalid_frames = 0
    ordered = sorted(payload, key=_parity_sort_key)
    for frame in ordered:
        try:
            calls.append(_map_parity_frame(frame))
        except (TypeError, ValueError):
            invalid_frames += 1
    return _mapped_result(calls, invalid_frames)


def _map_debug_frame(
    frame: Mapping[str, Any],
    trace_address: tuple[int, ...],
) -> InternalCall:
    call_type = _text(frame.get("type"), "CALL").upper()
    created_contract = _optional_address(frame.get("to")) if call_type.startswith("CREATE") else None
    return InternalCall(
        trace_address=trace_address,
        depth=len(trace_address),
        call_type=call_type,
        from_address=_optional_address(frame.get("from")),
        to_address=None if created_contract is not None else _optional_address(frame.get("to")),
        created_contract=created_contract,
        value_wei=_quantity(frame.get("value"), default=0),
        gas_limit=_optional_quantity(frame.get("gas")),
        gas_used=_optional_quantity(frame.get("gasUsed")),
        input_data=_hex_data(frame.get("input"), default="0x"),
        output_data=_hex_data(frame.get("output"), default="0x"),
        error=_optional_text(frame.get("error")),
        revert_reason=_optional_text(frame.get("revertReason")),
    )


def _map_parity_frame(frame: object) -> InternalCall:
    if not isinstance(frame, dict):
        raise TypeError("Trace frame is not an object.")
    action = frame.get("action")
    result = frame.get("result")
    if not isinstance(action, dict):
        raise TypeError("Trace action is missing.")
    if result is not None and not isinstance(result, dict):
        raise TypeError("Trace result is invalid.")
    safe_result: Mapping[str, Any] = result or {}
    raw_address = frame.get("traceAddress")
    if not isinstance(raw_address, list) or not all(
        isinstance(item, int) and item >= 0 for item in raw_address
    ):
        raise ValueError("Trace address is invalid.")
    trace_address = tuple(raw_address)
    frame_type = _text(frame.get("type"), "call").upper()
    call_type = _text(action.get("callType"), frame_type).upper()
    is_create = frame_type == "CREATE"
    return InternalCall(
        trace_address=trace_address,
        depth=len(trace_address),
        call_type=call_type,
        from_address=_optional_address(action.get("from")),
        to_address=None if is_create else _optional_address(action.get("to")),
        created_contract=_optional_address(safe_result.get("address")) if is_create else None,
        value_wei=_quantity(action.get("value"), default=0),
        gas_limit=_optional_quantity(action.get("gas")),
        gas_used=_optional_quantity(safe_result.get("gasUsed")),
        input_data=_hex_data(
            action.get("init") if is_create else action.get("input"),
            default="0x",
        ),
        output_data=_hex_data(
            safe_result.get("code") if is_create else safe_result.get("output"),
            default="0x",
        ),
        error=_optional_text(frame.get("error")),
    )


def _mapped_result(calls: list[InternalCall], invalid_frames: int) -> MappedTrace:
    if invalid_frames == 0:
        return MappedTrace(tuple(calls), TraceStatus.COMPLETE)
    note = f"Skipped {invalid_frames} invalid trace frame{'s' if invalid_frames != 1 else ''}."
    return MappedTrace(tuple(calls), TraceStatus.PARTIAL, note)


def _parity_sort_key(frame: object) -> tuple[int, ...]:
    if not isinstance(frame, dict):
        return (2**31,)
    raw = frame.get("traceAddress")
    if not isinstance(raw, list) or not all(isinstance(item, int) for item in raw):
        return (2**31,)
    return tuple(raw)


def _optional_address(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("Trace address is not text.")
    normalized = value.strip().lower()
    if len(normalized) != 42 or not normalized.startswith("0x"):
        raise ValueError("Trace address has an invalid length.")
    int(normalized[2:], 16)
    return normalized


def _optional_quantity(value: object) -> int | None:
    if value is None:
        return None
    return _quantity(value)


def _quantity(value: object, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise TypeError("Boolean is not a trace quantity.")
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str):
        base = 16 if value.lower().startswith("0x") else 10
        parsed = int(value, base)
        if parsed >= 0:
            return parsed
    raise ValueError("Trace quantity is invalid.")


def _hex_data(value: object, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise TypeError("Trace data is not text.")
    normalized = value.strip().lower()
    if not normalized.startswith("0x") or len(normalized) % 2 != 0:
        raise ValueError("Trace data is not hexadecimal.")
    bytes.fromhex(normalized[2:])
    return normalized


def _text(value: object, default: str) -> str:
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise TypeError("Trace text is invalid.")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
