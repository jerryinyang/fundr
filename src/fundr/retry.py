"""Exponential backoff for bulk historical fetches.

Hyperliquid's info endpoint returned HTTP 429 during Phase 2a over ~234 sequential calls; a
backfill makes tens of thousands of calls. Retry only what a retry can fix: 429, 5xx, and
transport/timeout errors. A permanent 4xx must surface at once — Lighter answers an unsupported
funding resolution with HTTP 400 `{"code":20001,"message":"invalid param "}` (measured
2026-09-21, market 1, resolution 15m), and burning six backoffs on that hides the real fault."""
import random
import time
from collections.abc import Callable
from typing import Any

import httpx

TRANSPORT_ERRORS = (httpx.TransportError, TimeoutError)  # TimeoutException subclasses the former


def is_retryable(e: BaseException) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        response = getattr(e, "response", None)
        if response is None:
            return False
        return response.status_code == 429 or response.status_code >= 500
    return isinstance(e, TRANSPORT_ERRORS)


def with_retries(fn: Callable[[], Any], *, attempts: int = 6, base_delay: float = 1.0,
                 max_delay: float = 20.0,
                 retryable: Callable[[BaseException], bool] = is_retryable,
                 sleep: Callable[[float], None] = time.sleep) -> Any:
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            if i == attempts - 1 or not retryable(e):
                raise
            sleep(min(base_delay * (2 ** i), max_delay) + random.uniform(0, 0.25))
    raise AssertionError("unreachable")
