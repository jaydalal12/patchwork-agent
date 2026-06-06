import pytest

from patchwork.errors import ConfigError, RateLimitError, TransientServiceError
from patchwork.resilience.ratelimit import RateLimiter
from patchwork.resilience.retry import BackoffPolicy, retry_call


def test_retry_succeeds_after_transient_failures():
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise TransientServiceError("boom")
        return "ok"

    slept = []
    out = retry_call(flaky, policy=BackoffPolicy(max_attempts=5), sleep=slept.append)
    assert out == "ok"
    assert calls["n"] == 3
    assert len(slept) == 2  # two retries before success


def test_retry_gives_up_after_max_attempts():
    def always():
        raise RateLimitError("nope")

    with pytest.raises(RateLimitError):
        retry_call(always, policy=BackoffPolicy(max_attempts=3), sleep=lambda _: None)


def test_non_retryable_raises_immediately():
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise ConfigError("not transient")

    with pytest.raises(ConfigError):
        retry_call(bad, policy=BackoffPolicy(max_attempts=5), sleep=lambda _: None)
    assert calls["n"] == 1  # never retried


def test_retry_honours_retry_after():
    policy = BackoffPolicy(max_attempts=2, base_delay=10.0)
    err = RateLimitError("slow down", retry_after=0.25)
    # delay_for should prefer the server hint over the backoff curve.
    assert policy.delay_for(1, retry_after=err.retry_after) == 0.25


def test_token_bucket_depletes_and_refills():
    clock = {"t": 0.0}
    rl = RateLimiter(rate=2.0, burst=2, now=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    assert rl.try_acquire() is True
    assert rl.try_acquire() is True
    assert rl.try_acquire() is False  # bucket empty
    clock["t"] += 1.0  # 1s -> +2 tokens
    assert rl.try_acquire() is True


def test_token_bucket_acquire_blocks_for_deficit():
    clock = {"t": 0.0}
    rl = RateLimiter(rate=1.0, burst=1, now=lambda: clock["t"], sleep=lambda s: clock.__setitem__("t", clock["t"] + s))
    rl.acquire()  # takes the one token
    waited = rl.acquire()  # must wait ~1s for a refill
    assert waited == pytest.approx(1.0, abs=0.01)
