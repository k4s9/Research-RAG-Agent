"""Small retry primitive for transient model and storage requests."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


def retry_sync(
    operation: Callable[[], T],
    *,
    attempts: int = 1,
    backoff_seconds: float = 0.0,
    retry_exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """Run a synchronous operation with bounded exponential backoff."""

    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    if backoff_seconds < 0:
        raise ValueError("backoff_seconds must not be negative")
    for attempt in range(attempts):
        try:
            return operation()
        except retry_exceptions:
            if attempt == attempts - 1:
                raise
            if backoff_seconds:
                time.sleep(backoff_seconds * (2**attempt))
    raise RuntimeError("retry operation did not return")
