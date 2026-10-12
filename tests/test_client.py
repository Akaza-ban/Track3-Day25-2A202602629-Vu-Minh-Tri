"""Part C — ResilientClient decisions, tested without the simulator."""

from __future__ import annotations

import pytest

from gameday_lab.breaker import BreakerState
from gameday_lab.client import ResilientClient
from gameday_lab.sim.contracts import Attempt, CallContext, GiveUp, ProviderError, ProviderInfo, Send

PROVIDERS = {
    "alpha": ProviderInfo("alpha", "A", "us", True, 1.0),
    "alpha-eu": ProviderInfo("alpha-eu", "A", "eu", True, 1.0),
    "beta": ProviderInfo("beta", "B", "us", True, 1.4),
    "gamma-mini": ProviderInfo("gamma-mini", "C", "us", False, 0.2),
}
ORDER = ["alpha", "alpha-eu", "beta", "gamma-mini"]
SETTINGS = {
    "attempt_timeout_s": 8.0, "max_attempts_per_provider": 2, "min_time_left_s": 1.0,
    "backoff_base_s": 0.25, "backoff_cap_s": 4.0, "retry_budget_ratio": 0.1,
    "retry_budget_min_per_s": 0.5, "retry_budget_max_tokens": 20, "quota_cooldown_s": 300,
    "breaker": {"window_size": 10, "min_calls": 5, "failure_rate_threshold": 0.5, "slow_call_s": 5.0,
                "slow_rate_threshold": 0.6, "open_duration_s": 10.0, "half_open_max_probes": 1,
                "half_open_successes": 1},
}


@pytest.fixture
def client() -> ResilientClient:
    return ResilientClient(PROVIDERS, ORDER, SETTINGS, seed=7)


def call(task: str = "tool", now: float = 0.0, deadline: float = 30.0, cid: int = 1) -> CallContext:
    return CallContext(cid, cid, task, now, deadline)


def first_send(client: ResilientClient, c: CallContext, now: float = 0.0) -> Send:
    action = client.start(c, now)
    assert isinstance(action, Send), action
    return action


def error_on(client: ResilientClient, c: CallContext, send: Send, err: ProviderError, now: float,
             number: int = 1) -> object:
    return client.on_error(Attempt(c, send.provider, number, now, send.tag), err, now)


def test_ladder_respects_tool_capability(client: ResilientClient) -> None:
    assert client.ladder("tool") == ["alpha", "alpha-eu", "beta"]
    assert client.ladder("chat") == ORDER


def test_first_attempt_goes_to_primary_with_timeout(client: ResilientClient) -> None:
    s = first_send(client, call())
    assert s.provider == "alpha" and s.delay_s == 0.0 and 0 < s.timeout_s <= 8.0


def test_client_error_gives_up_without_retry(client: ResilientClient) -> None:
    c = call()
    s = first_send(client, c)
    action = error_on(client, c, s, ProviderError("alpha", 400, "invalid_request_error"), 0.1)
    assert isinstance(action, GiveUp)


def test_client_errors_do_not_open_the_breaker(client: ResilientClient) -> None:
    for i in range(20):  # a bad deploy: every request is malformed
        c = call(cid=100 + i)
        s = first_send(client, c, now=float(i))
        assert s.provider == "alpha"
        error_on(client, c, s, ProviderError("alpha", 400, "invalid_request_error"), i + 0.1)
    assert client.breakers["alpha"].state is BreakerState.CLOSED


def test_rate_limit_spills_over_and_cools_down(client: ResilientClient) -> None:
    c = call()
    s = first_send(client, c)
    action = error_on(client, c, s, ProviderError("alpha", 429, "rate_limit_exceeded", retry_after=2.0), 0.1)
    assert isinstance(action, Send) and action.provider == "alpha-eu" and action.delay_s == 0.0
    # other calls also avoid alpha until the retry-after has passed ...
    assert first_send(client, call(cid=2), now=1.0).provider == "alpha-eu"
    # ... and come back afterwards
    assert first_send(client, call(cid=3), now=2.5).provider == "alpha"


def test_quota_exhausted_moves_on_for_a_long_time(client: ResilientClient) -> None:
    c = call()
    s = first_send(client, c)
    action = error_on(client, c, s, ProviderError("alpha", 429, "insufficient_quota"), 0.1)
    assert isinstance(action, Send) and action.provider == "alpha-eu"
    assert first_send(client, call(cid=2, now=200.0, deadline=230.0), now=200.0).provider == "alpha-eu"


def test_transient_error_retries_same_provider_with_backoff_then_fails_over(client: ResilientClient) -> None:
    c = call()
    s = first_send(client, c)
    a1 = error_on(client, c, s, ProviderError("alpha", 529, "overloaded_error"), 0.1)
    assert isinstance(a1, Send) and a1.provider == "alpha" and 0.0 <= a1.delay_s <= 4.0
    a2 = error_on(client, c, a1, ProviderError("alpha", 529, "overloaded_error"), 0.5, number=2)
    assert isinstance(a2, Send) and a2.provider == "alpha-eu"


def test_open_breaker_is_skipped(client: ResilientClient) -> None:
    for i in range(5):  # 5 timeouts on alpha -> breaker opens
        c = call(cid=10 + i)
        s = first_send(client, c)
        assert s.provider == "alpha"
        client.on_error(Attempt(c, "alpha", 1, 0.0, s.tag), ProviderError("alpha", 0, "client_timeout"), 8.0)
    assert client.breakers["alpha"].state is BreakerState.OPEN
    assert first_send(client, call(cid=99), now=9.0).provider == "alpha-eu"


def test_never_sends_past_the_deadline(client: ResilientClient) -> None:
    c = call(deadline=10.0)
    s = first_send(client, c, now=0.0)
    action = error_on(client, c, s, ProviderError("alpha", 0, "client_timeout"), 9.5)
    if isinstance(action, Send):
        assert 9.5 + action.delay_s + action.timeout_s <= 10.0 + 1e-9
    else:
        assert isinstance(action, GiveUp)


def test_slow_successes_are_reported_to_breaker(client: ResilientClient) -> None:
    for i in range(6):  # alpha answers, but every answer takes 7 s (slow_call_s = 5)
        c = call(cid=40 + i)
        s = first_send(client, c, now=float(i))
        client.on_success(Attempt(c, "alpha", 1, float(i), s.tag), i + 7.0, 7.0)
    assert client.breakers["alpha"].state is BreakerState.OPEN


def test_cancel_releases_half_open_probe(client: ResilientClient) -> None:
    b = client.breakers["alpha"]
    for i in range(5):
        c = call(cid=20 + i)
        s = first_send(client, c)
        client.on_error(Attempt(c, "alpha", 1, 0.0, s.tag), ProviderError("alpha", 500, "api_error"), 1.0)
    assert b.state is BreakerState.OPEN
    c = call(cid=30, now=20.0, deadline=50.0)
    s = first_send(client, c, now=20.0)
    assert s.provider == "alpha" and b.state is BreakerState.HALF_OPEN  # the single probe
    client.on_cancel(c, s.tag, 21.0)
    assert b.allow(21.0) is not None  # slot was freed
