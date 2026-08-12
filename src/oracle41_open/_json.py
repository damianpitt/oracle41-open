"""Provide one JSON interface for the application.

This module uses the fast orjson library when it is available and keeps consistent byte and object behavior.
Callers do not need to know which JSON implementation is active.
"""

from __future__ import annotations

from typing import Any

try:
    import orjson
except ModuleNotFoundError:  # pragma: no cover - fallback branch
    orjson = None  # type: ignore[assignment]


def loads(data: bytes | str) -> Any:
    if orjson is not None:
        return orjson.loads(data)

    import json

    if isinstance(data, bytes):
        return json.loads(data.decode("utf-8"))
    return json.loads(data)


def dumps(value: Any, pretty: bool = False) -> bytes:
    if orjson is not None:
        option = orjson.OPT_INDENT_2 if pretty else 0
        return orjson.dumps(value, option=option)

    import json

    if pretty:
        text = json.dumps(value, indent=2, ensure_ascii=False)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8")
