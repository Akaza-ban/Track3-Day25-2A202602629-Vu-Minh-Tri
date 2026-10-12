"""Part A — error classification, backoff, retry budget."""

from __future__ import annotations

import random

import pytest

from gameday_lab.errors import ErrorKind, classify, counts_toward_breaker, failover_helps, is_retryable
from gameday_lab.retry import FullJitterBackoff, RetryBudget
from gameday_lab.sim.contracts import ProviderError


def err(status: int, code: str = "x", retry_after: float | None = None) -> ProviderError:
    return ProviderError("alpha", status, code, retry_after=retry_after)


@pytest.mark.parametrize(
    ("status", "code", "kind"),
    [
        (0, "client_timeout", ErrorKind.TIMEOUT),
        (408, "request_timeout", ErrorKind.TIMEOUT),
        (429, "rate_limit_exceeded", ErrorKind.RATE_LIMITED),
        (429, "insufficient_quota", ErrorKind.QUOTA_EXHAUSTED),
        (529, "overloaded_error", ErrorKind.OVERLOADED),
        (503, "service_unavailable", ErrorKind.OVERLOADED),
        (500, "api_error", ErrorKind.SERVER_ERROR),
        (502, "bad_gateway", ErrorKind.SERVER_ERROR),
        (400, "invalid_request_error", ErrorKind.CLIENT_ERROR),
        (401, "authentication_error", ErrorKind.CLIENT_ERROR),
        (422, "unprocessable", ErrorKind.CLIENT_ERROR),
    ],
)
def test_classify(status: int, code: str, kind: ErrorKind) -> None:
    assert classify(err(status, code)) is kind


def test_retryable_kinds() -> None:
    assert {k for k in ErrorKind if is_retryable(k)} == {
        ErrorKind.RATE_LIMITED, ErrorKind.OVERLOADED, ErrorKind.SERVER_ERROR, ErrorKind.TIMEOUT}


def test_breaker_relevant_kinds() -> None:
    # rate limits, quota and our own bad requests say nothing about provider health
    assert {k for k in ErrorKind if counts_toward_breaker(k)} == {
        ErrorKind.OVERLOADED, ErrorKind.SERVER_ERROR, ErrorKind.TIMEOUT}


def test_failover_does_not_help_client_errors() -> None:
    assert not failover_helps(ErrorKind.CLIENT_ERROR)
    assert failover_helps(ErrorKind.QUOTA_EXHAUSTED)
    assert failover_helps(ErrorKind.OVERLOADED)


def test_backoff_is_bounded_and_grows() -> None:
    b = FullJitterBackoff(base_s=0.5, cap_s=4.0, rng=random.Random(1))
    for n, ceiling in [(1, 0.5), (2, 1.0), (3, 2.0), (4, 4.0), (8, 4.0)]:
        samples = [b.delay(n) for _ in range(300)]
        assert all(0.0 <= s <= ceiling + 1e-9 for s in samples)
        assert max(samples) > 0.8 * ceiling  # it actually uses the whole range


def test_backoff_has_jitter() -> None:
    b = FullJitterBackoff(base_s=1.0, cap_s=8.0, rng=random.Random(2))
    assert len({round(b.delay(3), 6) for _ in range(50)}) > 40  # not a fixed delay


def test_backoff_honours_retry_after() -> None:
    b = FullJitterBackoff(base_s=0.25, cap_s=8.0, rng=random.Random(3))
    samples = [b.delay(1, retry_after=2.0) for _ in range(200)]
    assert min(samples) >= 2.0
    assert max(samples) <= 2.25 + 1e-9
    assert len(set(samples)) > 150  # jittered on top of retry-after


def test_backoff_rejects_bad_retry_number() -> None:
    with pytest.raises(ValueError):
        FullJitterBackoff().delay(0)


def test_budget_caps_retries_to_a_ratio_of_requests() -> None:
    budget = RetryBudget(ratio=0.1, min_per_s=0.0, max_tokens=5.0)
    allowed = 0
    for i in range(1000):  # 1000 requests, all failing, all wanting a retry, same instant
        budget.record_request(now=0.0)
        if budget.try_acquire_retry(now=0.0):
            allowed += 1
    # initial 5 tokens + 10% of 1000 requests
    assert 100 <= allowed <= 106


def test_budget_refills_over_time() -> None:
    budget = RetryBudget(ratio=0.0, min_per_s=1.0, max_tokens=2.0)
    assert budget.try_acquire_retry(0.0)
    assert budget.try_acquire_retry(0.0)
    assert not budget.try_acquire_retry(0.0)  # empty
    assert not budget.try_acquire_retry(0.5)  # only half a token back
    assert budget.try_acquire_retry(1.1)  # one token refilled


def test_budget_never_exceeds_max_tokens() -> None:
    budget = RetryBudget(ratio=1.0, min_per_s=10.0, max_tokens=3.0)
    for _ in range(100):
        budget.record_request(0.0)
    budget.try_acquire_retry(1000.0)
    assert sum(budget.try_acquire_retry(1000.0) for _ in range(10)) == 2
