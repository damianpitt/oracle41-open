"""Test normalization of common EVM trace response formats.

The cases cover nested Geth calls, flat Parity traces, contract creation, reverts, and invalid frames.
They confirm partial mappings keep valid calls instead of claiming complete execution history.
"""

from oracle41_open.core.models import TraceStatus
from oracle41_open.providers.trace_mapper import map_debug_call_trace, map_parity_trace

_FROM = "0x1111111111111111111111111111111111111111"
_TO = "0x2222222222222222222222222222222222222222"
_CREATED = "0x3333333333333333333333333333333333333333"


def test_maps_nested_debug_call_tree_in_execution_order() -> None:
    payload = {
        "type": "CALL",
        "from": _FROM,
        "to": _TO,
        "value": "0x5",
        "gas": "0x100",
        "gasUsed": "0x80",
        "input": "0x1234",
        "output": "0x",
        "calls": [
            {
                "type": "CREATE2",
                "from": _TO,
                "to": _CREATED,
                "gas": "0x40",
                "gasUsed": "0x20",
                "input": "0x6000",
                "output": "0x6001",
            },
            {
                "type": "STATICCALL",
                "from": _TO,
                "to": _FROM,
                "gas": "0x20",
                "gasUsed": "0x10",
                "input": "0x",
                "output": "0x",
                "error": "execution reverted",
                "revertReason": "not allowed",
            },
        ],
    }

    result = map_debug_call_trace(payload)

    assert result.status is TraceStatus.COMPLETE
    assert [call.trace_address for call in result.calls] == [(), (0,), (1,)]
    assert result.calls[0].value_wei == 5
    assert result.calls[1].created_contract == _CREATED
    assert result.calls[2].revert_reason == "not allowed"


def test_maps_parity_trace_and_marks_invalid_frame_as_partial() -> None:
    payload = [
        {
            "type": "call",
            "traceAddress": [0],
            "action": {
                "callType": "delegatecall",
                "from": _FROM,
                "to": _TO,
                "gas": "0x20",
                "input": "0x1234",
                "value": "0x0",
            },
            "result": {"gasUsed": "0x10", "output": "0x"},
        },
        {"type": "call", "traceAddress": [1], "action": "invalid"},
        {
            "type": "create",
            "traceAddress": [],
            "action": {"from": _FROM, "gas": "0x40", "init": "0x6000", "value": "0x2"},
            "result": {"address": _CREATED, "code": "0x6001", "gasUsed": "0x20"},
        },
    ]

    result = map_parity_trace(payload)

    assert result.status is TraceStatus.PARTIAL
    assert [call.trace_address for call in result.calls] == [(), (0,)]
    assert result.calls[0].created_contract == _CREATED
    assert result.calls[1].call_type == "DELEGATECALL"
    assert result.error == "Skipped 1 invalid trace frame."
