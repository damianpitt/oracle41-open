from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


def retry_with_backoff(
    operation: Callable[[], _T],
    should_retry: Callable[[Exception], bool],
    attempts: int,
    initial_delay_seconds: float,
    backoff_multiplier: float,
    max_delay_seconds: float,
    sleep_func: Callable[[float], None] | None = None,
) -> _T:
    total_attempts = max(1, attempts)
    delay = max(0.0, initial_delay_seconds)
    multiplier = max(1.0, backoff_multiplier)
    max_delay = max(delay, max_delay_seconds)
    sleeper = sleep_func or time.sleep

    for index in range(total_attempts):
        try:
            return operation()
        except Exception as error:
            is_last = index >= total_attempts - 1
            if is_last or not should_retry(error):
                raise
            if delay > 0:
                sleeper(delay)
            delay = min(max_delay, delay * multiplier if delay > 0 else 0.0)

    raise RuntimeError("retry_with_backoff reached an impossible state.")
