"""Part E — multi-window, multi-burn-rate alerting and error budget."""

from __future__ import annotations

from gameday_lab.slo.alerting import BurnRateAlerter, WindowSums, error_budget_report

DAY = 1440


def series(days: int = 4, per_min: int = 1000, base_bad: int = 0) -> tuple[list[int], list[int]]:
    n = days * DAY
    return [per_min] * n, [base_bad] * n


def inject(bad: list[int], start: int, minutes: int, per_min_bad: int) -> None:
    for i in range(start, start + minutes):
        bad[i] = per_min_bad


def test_window_sums() -> None:
    w = WindowSums([1, 2, 3, 4, 5])
    assert w.sum(4, 2) == 9
    assert w.sum(4, 5) == 15
    assert w.sum(1, 10) == 3  # clipped at the start


def test_burn_rate_value() -> None:
    a = BurnRateAlerter(slo=0.995)
    total, bad = WindowSums([1000] * 60), WindowSums([10] * 60)  # 1% errors
    assert abs(a.burn_rate(total, bad, 59, 60) - 2.0) < 1e-9


def test_full_outage_pages_within_minutes() -> None:
    total, bad = series()
    inject(bad, 2 * DAY, 15, 1000)  # 100% errors for 15 min
    events = BurnRateAlerter().evaluate(total, bad)
    pages = [e for e in events if e.severity == "page"]
    assert pages and 2 * DAY <= pages[0].start_min <= 2 * DAY + 6


def test_page_resets_after_recovery() -> None:
    total, bad = series()
    inject(bad, 2 * DAY, 15, 1000)
    pages = [e for e in BurnRateAlerter().evaluate(total, bad) if e.severity == "page"]
    # the 5-minute short window makes the fast-burn page stop soon after recovery
    assert min(e.end_min for e in pages) <= 2 * DAY + 15 + 6


def test_short_blip_does_not_page() -> None:
    total, bad = series()
    inject(bad, 2 * DAY, 4, 300)  # 30% for 4 minutes
    assert not [e for e in BurnRateAlerter().evaluate(total, bad) if e.severity == "page"]


def test_slow_leak_tickets_but_does_not_page() -> None:
    total, bad = series(days=4)
    inject(bad, 0, 4 * DAY, 15)  # 1.5% errors, all the time
    events = BurnRateAlerter().evaluate(total, bad)
    assert not [e for e in events if e.severity == "page"]
    assert [e for e in events if e.severity == "ticket"]


def test_error_budget_report() -> None:
    total, bad = [1000] * 100, [0] * 100
    bad[0] = 250  # 250 bad of 100k; budget at 99.5% = 500
    r = error_budget_report(total, bad, slo=0.995)
    assert r["allowed_bad"] == 500.0
    assert r["budget_consumed"] == 0.5
    assert r["budget_remaining"] == 0.5
    assert r["freeze_releases"] is False
    bad[1] = 300
    assert error_budget_report(total, bad, slo=0.995)["freeze_releases"] is True
