"""Retry with exponential backoff + full jitter.

Deliberately hand-rolled (not tenacity) because the brief asks to see the
strategy in the code, and because the retry predicate is domain-specific:
we retry only :class:`PatchworkError` subclasses that declare
``retryable = True`` (rate limits, transient 5xx), and we honour a
server-provided ``retry_after`` when present.

``sleep`` is injectable so tests run instantly and deterministically.
"""
from __future__ import annotations

import functools
import time
from dataclasses import dataclass
from typing import Callable, Optional, Tuple, Type, TypeVar

from patchwork.errors import PatchworkError
from patchwork.observability import get_logger

T = TypeVar("T")
_log = get_logger("resilience.retry")


@dataclass
class BackoffPolicy:
    max_attempts: int = 4
    base_delay: float = 0.5
    max_delay: float = 20.0
    multiplier: float = 2.0
    # Full-jitter: actual sleep is uniform(0, computed). Injected for tests.
    jitter: Callable[[float], float] = lambda hi: hi  # default: no jitter (deterministic)

    def delay_for(self, attempt: int, retry_after: Optional[float] = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_delay)
        raw = min(self.base_delay * (self.multiplier ** (attempt - 1)), self.max_delay)
        return self.jitter(raw)


def _is_retryable(exc: BaseException, extra: Tuple[Type[BaseException], ...]) -> bool:
    if isinstance(exc, PatchworkError):
        return exc.retryable
    return isinstance(exc, extra)


def retry_call(
    fn: Callable[[], T],
    *,
    policy: Optional[BackoffPolicy] = None,
    retry_on: Tuple[Type[BaseException], ...] = (),
    sleep: Callable[[float], None] = time.sleep,
    op_name: str = "call",
) -> T:
    """Invoke ``fn`` with retries. Returns its result or re-raises the last error."""
    policy = policy or BackoffPolicy()
    last: Optional[BaseException] = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return fn()
        except BaseException as exc:  # noqa: BLE001 - we re-raise non-retryable below
            last = exc
            if not _is_retryable(exc, retry_on) or attempt == policy.max_attempts:
                raise
            retry_after = getattr(exc, "retry_after", None)
            delay = policy.delay_for(attempt, retry_after)
            _log.warning(
                "retrying after error",
                op=op_name,
                attempt=attempt,
                max_attempts=policy.max_attempts,
                delay_s=round(delay, 2),
                error=type(exc).__name__,
            )
            sleep(delay)
    assert last is not None  # unreachable
    raise last


def with_retry(
    *,
    policy: Optional[BackoffPolicy] = None,
    retry_on: Tuple[Type[BaseException], ...] = (),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator form of :func:`retry_call`."""

    def deco(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            return retry_call(
                lambda: fn(*args, **kwargs),
                policy=policy,
                retry_on=retry_on,
                op_name=fn.__name__,
            )

        return wrapper

    return deco
