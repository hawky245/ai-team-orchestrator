"""Reusable async retry utilities: configurable retries with exponential backoff and jitter.

Delays follow the "full jitter" strategy: the wait before retry N is drawn
uniformly from [0, min(base_delay * backoff_factor ** (N - 1), max_delay)],
which spreads concurrent retry waves instead of synchronizing them.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import random
from typing import Awaitable, Callable

IsRetryable = Callable[[Exception], bool]
OnRetry = Callable[[int, float, Exception], Awaitable[None]]


def compute_delay(
    failed_attempt: int,
    *,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    jitter: bool = True,
) -> float:
    """Backoff ceiling for the wait after `failed_attempt` consecutive failures."""
    ceiling = min(base_delay * (backoff_factor ** (failed_attempt - 1)), max_delay)
    return random.uniform(0, ceiling) if jitter else ceiling


def retry_async(
    func=None,
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 30.0,
    jitter: bool = True,
    is_retryable: IsRetryable = lambda exc: True,
    on_retry: OnRetry | None = None,
):
    """Retry a sync or async callable with exponential backoff + jitter.

    Usable directly as a decorator or as a parameterized factory. `max_retries`
    counts total tries (1 = no retry). `on_retry(attempt, delay, exc)` is
    awaited before each sleep, where `attempt` is the number of the try that
    just failed; if the callback itself raises (e.g. a websocket send to a
    disconnected client), the retry loop aborts immediately.
    """
    if max_retries < 1:
        raise ValueError("max_retries must be >= 1")

    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            for attempt in range(1, max_retries + 1):
                try:
                    result = fn(*args, **kwargs)
                    if inspect.isawaitable(result):
                        result = await result
                    return result
                except Exception as exc:
                    if not is_retryable(exc) or attempt >= max_retries:
                        raise
                    delay = compute_delay(
                        attempt,
                        base_delay=base_delay,
                        backoff_factor=backoff_factor,
                        max_delay=max_delay,
                        jitter=jitter,
                    )
                    if on_retry is not None:
                        await on_retry(attempt, delay, exc)
                    await asyncio.sleep(delay)

        return wrapper

    if func is not None:
        return decorator(func)
    return decorator
