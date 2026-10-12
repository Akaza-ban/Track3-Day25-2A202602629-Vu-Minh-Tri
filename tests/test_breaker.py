"""Part B — sliding-window circuit breaker."""

from __future__ import annotations

from gameday_lab.breaker import BreakerState, Permit, SlidingWindowBreaker


def make(**kw: object) -> SlidingWindowBreaker:
    defaults: dict[str, object] = dict(window_size=10, min_calls=5, failure_rate_threshold=0.5,
                                       slow_call_s=5.0, slow_rate_threshold=0.6, open_duration_s=10.0,
                                       half_open_max_probes=2, half_open_successes=2)
    defaults.update(kw)
    return SlidingWindowBreaker("alpha", **defaults)  # type: ignore[arg-type]


def fail(b: SlidingWindowBreaker, now: float, n: int = 1) -> None:
    for _ in range(n):
        p = b.allow(now)
        assert p is not None
        b.on_failure(p, now)


def ok(b: SlidingWindowBreaker, now: float, n: int = 1, latency: float = 1.0) -> None:
    for _ in range(n):
        p = b.allow(now)
        assert p is not None
        b.on_success(p, now, latency)


def trip(b: SlidingWindowBreaker, now: float = 0.0) -> None:
    fail(b, now, 5)
    assert b.state is BreakerState.OPEN


def test_closed_allows_and_returns_permit() -> None:
    p = make().allow(0.0)
    assert isinstance(p, Permit) and not p.probe


def test_does_not_trip_before_min_calls() -> None:
    b = make()
    fail(b, 0.0, 4)
    assert b.state is BreakerState.CLOSED


def test_trips_on_failure_rate_not_on_consecutive_count() -> None:
    b = make()
    fail(b, 0.0)
    ok(b, 1.0)
    fail(b, 2.0)
    ok(b, 3.0)
    assert b.state is BreakerState.CLOSED  # only 4 results < min_calls
    fail(b, 4.0)  # 3/5 = 60% failed, never two failures in a row
    assert b.state is BreakerState.OPEN
    assert b.transitions[-1].reason == "failure_rate"


def test_old_successes_do_not_dilute_new_failures() -> None:
    b = make()  # window_size = 10
    ok(b, 0.0, 20)
    fail(b, 1.0, 4)
    assert b.state is BreakerState.CLOSED  # 4/10 in the window
    fail(b, 2.0, 1)  # 5/10 in the window (it would be 5/25 if the breaker never forgot)
    assert b.state is BreakerState.OPEN


def test_slow_calls_trip_the_breaker() -> None:
    b = make()
    ok(b, 0.0, 5, latency=7.0)  # all "successful", all slow
    assert b.state is BreakerState.OPEN
    assert b.transitions[-1].reason == "slow_call_rate"


def test_open_fails_fast_until_duration_elapsed() -> None:
    b = make()
    trip(b, now=100.0)
    assert b.allow(105.0) is None
    assert b.allow(109.9) is None
    assert b.allow(110.0) is not None
    assert b.state is BreakerState.HALF_OPEN


def test_half_open_limits_concurrent_probes() -> None:
    b = make(half_open_max_probes=2)
    trip(b)
    permits = [b.allow(20.0) for _ in range(50)]  # 50 concurrent requests arrive
    granted = [p for p in permits if p is not None]
    assert len(granted) == 2 and all(p.probe for p in granted)


def test_probe_failure_reopens() -> None:
    b = make()
    trip(b)
    p = b.allow(20.0)
    assert p is not None
    b.on_failure(p, 21.0)
    assert b.state is BreakerState.OPEN
    assert b.allow(25.0) is None  # a fresh open period started at t=21


def test_enough_probe_successes_close() -> None:
    b = make(half_open_successes=2)
    trip(b)
    p1, p2 = b.allow(20.0), b.allow(20.0)
    assert p1 is not None and p2 is not None
    b.on_success(p1, 21.0, 1.0)
    assert b.state is BreakerState.HALF_OPEN
    b.on_success(p2, 21.5, 1.0)
    assert b.state is BreakerState.CLOSED
    assert b.allow(22.0) is not None


def test_slow_probe_reopens() -> None:
    b = make()
    trip(b)
    p = b.allow(20.0)
    assert p is not None
    b.on_success(p, 28.0, 8.0)
    assert b.state is BreakerState.OPEN


def test_release_frees_a_probe_slot_without_recording() -> None:
    b = make(half_open_max_probes=1)
    trip(b)
    p = b.allow(20.0)
    assert p is not None and b.allow(20.0) is None
    b.release(p, 20.5)  # e.g. the probe hit a 400 or a rate limit
    assert b.state is BreakerState.HALF_OPEN
    assert b.allow(20.6) is not None


def test_stale_results_are_ignored() -> None:
    b = make()
    old = b.allow(0.0)  # issued while CLOSED
    assert old is not None
    trip(b, now=1.0)
    b.allow(11.0)  # -> HALF_OPEN
    b.on_failure(old, 11.5)  # late result from before the trip
    assert b.state is BreakerState.HALF_OPEN


def test_transitions_are_logged() -> None:
    b = make()
    trip(b)
    p = b.allow(20.0)
    b.on_success(p, 21.0, 1.0)  # type: ignore[arg-type]
    b.on_success(b.allow(21.0), 21.5, 1.0)  # type: ignore[arg-type]
    path = [(t.frm.value, t.to.value) for t in b.transitions]
    assert path == [("closed", "open"), ("open", "half_open"), ("half_open", "closed")]
