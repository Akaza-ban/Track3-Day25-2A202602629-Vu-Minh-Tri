"""Scores alert events against labelled incidents. DO NOT EDIT."""

from __future__ import annotations

from typing import Any

from gameday_lab.slo.alerting import AlertEvent
from gameday_lab.slo.data import Incident

FALSE_PAGE_GRACE_MIN = 120  # a page this long after a real incident still counts as "for" it


class NaiveThresholdAlerter:
    """What most teams start with: page whenever the 5-minute error ratio > 5%."""

    def __init__(self, threshold: float = 0.05, window_min: int = 5) -> None:
        self.threshold = threshold
        self.window_min = window_min

    def evaluate(self, total: list[int], bad: list[int]) -> list[AlertEvent]:
        events: list[AlertEvent] = []
        start: int | None = None
        t_sum = b_sum = 0
        for i in range(len(total)):
            t_sum += total[i]
            b_sum += bad[i]
            if i >= self.window_min:
                t_sum -= total[i - self.window_min]
                b_sum -= bad[i - self.window_min]
            firing = t_sum > 0 and b_sum / t_sum > self.threshold
            if firing and start is None:
                start = i
            elif not firing and start is not None:
                events.append(AlertEvent("error_ratio_5m", "page", start, i))
                start = None
        if start is not None:
            events.append(AlertEvent("error_ratio_5m", "page", start, len(total)))
        return events


def _overlaps(e: AlertEvent, lo: int, hi: int) -> bool:
    return e.start_min < hi and e.end_min > lo


def score_alerts(events: list[AlertEvent], incidents: list[Incident]) -> dict[str, Any]:
    pages = [e for e in events if e.severity == "page"]
    tickets = [e for e in events if e.severity == "ticket"]
    rows = []
    ok = True
    for inc in incidents:
        row: dict[str, Any] = {"incident": inc.name, "expect": inc.expect}
        if inc.expect == "page":
            hits = [e for e in pages if inc.start_min <= e.start_min <= inc.start_min + inc.max_delay_min]
            row["detected"] = bool(hits)
            row["delay_min"] = min(e.start_min for e in hits) - inc.start_min if hits else None
            ok &= bool(hits)
        elif inc.expect == "ticket":
            hits = [e for e in tickets if inc.start_min <= e.start_min <= inc.start_min + inc.max_delay_min]
            wrong = [e for e in pages if _overlaps(e, inc.start_min, inc.end_min)]
            row["detected"] = bool(hits)
            row["delay_min"] = min(e.start_min for e in hits) - inc.start_min if hits else None
            row["paged_instead"] = bool(wrong)
            ok &= bool(hits) and not wrong
        else:
            wrong = [e for e in pages if _overlaps(e, inc.start_min, inc.end_min + 60)]
            row["paged"] = bool(wrong)
            ok &= not wrong
        rows.append(row)
    page_windows = [(i.start_min, i.end_min + FALSE_PAGE_GRACE_MIN) for i in incidents if i.expect == "page"]
    false_pages = [e for e in pages if not any(_overlaps(e, lo, hi) for lo, hi in page_windows)]
    ok &= not false_pages
    return {
        "pass": ok,
        "incidents": rows,
        "pages": len(pages),
        "tickets": len(tickets),
        "false_pages": len(false_pages),
    }
