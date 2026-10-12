"""Synthetic 30-day per-minute SLI data with labelled incidents. DO NOT EDIT.

``make sli-data`` writes data/sli_30d.csv + data/sli_30d_labels.json.
The grader uses a *different* seed and incident schedule of the same kinds.
"""

from __future__ import annotations

import csv
import json
import math
import random
from dataclasses import asdict, dataclass
from pathlib import Path

MIN_PER_DAY = 1440
DAYS = 30


@dataclass(frozen=True)
class Incident:
    name: str
    start_min: int
    duration_min: int
    error_ratio: float
    expect: str  # "page" | "ticket" | "none"
    max_delay_min: int  # for page/ticket: alert must START within this many minutes
    note: str = ""

    @property
    def end_min(self) -> int:
        return self.start_min + self.duration_min


def _t(day: int, hh: int, mm: int = 0) -> int:
    return day * MIN_PER_DAY + hh * 60 + mm


def default_incidents() -> list[Incident]:
    return [
        Incident("checkout_bug", _t(2, 10), 45, 0.15, "page", 35, "15% errors for 45 min"),
        Incident("provider_outage", _t(6, 2), 10, 1.00, "page", 8, "full outage at night"),
        Incident("deploy_blip", _t(10, 14), 4, 0.30, "none", 0, "4-min blip, self-healed"),
        Incident("slow_leak", _t(14, 9), 2 * MIN_PER_DAY, 0.015, "ticket", 30 * 60, "1.5% for 2 days"),
        Incident("degraded_evening", _t(20, 20), 6 * 60, 0.04, "page", 330, "4% for 6 hours"),
        Incident("night_spike", _t(24, 3), 3, 0.60, "none", 0, "3-min spike, low traffic"),
        Incident("small_bump", _t(27, 11), 20, 0.12, "none", 0, "20 min at 12% ~ 1% of budget"),
    ]


def generate(seed: int = 2025, incidents: list[Incident] | None = None,
             slo: float = 0.995) -> tuple[list[int], list[int], list[Incident]]:
    rng = random.Random(seed)
    incidents = default_incidents() if incidents is None else incidents
    total: list[int] = []
    bad: list[int] = []
    for m in range(DAYS * MIN_PER_DAY):
        hour = (m % MIN_PER_DAY) / 60
        mean = 600 + 300 * math.sin((hour - 9) / 24 * 2 * math.pi)  # 300..900 req/min
        n = max(1, int(rng.gauss(mean, math.sqrt(mean))))
        ratio = 0.0005
        for inc in incidents:
            if inc.start_min <= m < inc.end_min:
                ratio = max(ratio, inc.error_ratio)
        k = sum(1 for _ in range(n) if rng.random() < ratio) if ratio > 0.05 else _binomial_approx(rng, n, ratio)
        total.append(n)
        bad.append(min(n, k))
    return total, bad, incidents


def _binomial_approx(rng: random.Random, n: int, p: float) -> int:
    mu = n * p
    if mu < 20:  # Poisson for small means
        k, l, prod = 0, math.exp(-mu), rng.random()
        while prod > l:
            k += 1
            prod *= rng.random()
        return k
    return max(0, int(round(rng.gauss(mu, math.sqrt(mu * (1 - p))))))


def write(out_dir: str | Path = "data", seed: int = 2025, incidents: list[Incident] | None = None,
          name: str = "sli_30d") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    total, bad, incidents = generate(seed, incidents)
    with (out / f"{name}.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["minute", "total", "bad"])
        for i, (t, b) in enumerate(zip(total, bad)):
            w.writerow([i, t, b])
    (out / f"{name}_labels.json").write_text(json.dumps([asdict(i) for i in incidents], indent=2))
    return out / f"{name}.csv"


def load(path: str | Path) -> tuple[list[int], list[int]]:
    total: list[int] = []
    bad: list[int] = []
    with Path(path).open() as f:
        for row in csv.DictReader(f):
            total.append(int(row["total"]))
            bad.append(int(row["bad"]))
    return total, bad


def load_labels(path: str | Path) -> list[Incident]:
    return [Incident(**d) for d in json.loads(Path(path).read_text())]
