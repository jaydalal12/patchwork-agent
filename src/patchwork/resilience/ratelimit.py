"""Client-side token-bucket rate limiter.

Guards external calls (GitHub, LLM) from tripping server limits in the first
place — cheaper than retrying after a 429. Thread-safe; ``now`` is injectable
so tests don't sleep in real time.
"""
from __future__ import annotations

import threading
import time
from typing import Callable


class RateLimiter:
    """Token bucket: ``rate`` tokens added per second, capacity ``burst``."""

    def __init__(
        self,
        rate: float,
        burst: int,
        *,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate <= 0 or burst <= 0:
            raise ValueError("rate and burst must be positive")
        self._rate = rate
        self._capacity = float(burst)
        self._tokens = float(burst)
        self._now = now
        self._sleep = sleep
        self._last = now()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        t = self._now()
        elapsed = t - self._last
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last = t

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Non-blocking: take ``tokens`` if available, else False."""
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0) -> float:
        """Block until ``tokens`` are available. Returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return waited
                deficit = tokens - self._tokens
                wait = deficit / self._rate
            self._sleep(wait)
            waited += wait
