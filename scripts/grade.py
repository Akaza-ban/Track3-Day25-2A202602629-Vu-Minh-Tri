"""Autograder: 88 automatic points + 12 for the report (graded by hand).

    python scripts/grade.py                      # what students run
    python scripts/grade.py --seeds 25,26,27 \\
        --scenarios instructor/scenarios_hidden.yaml \\
        --cache-scenarios instructor/scenarios_cache_hidden.yaml \\
        --hidden-slo data/sli_30d_hidden.csv     # what instructors run
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without pip install

import argparse
import contextlib
import io
import json
from collections import defaultdict
from typing import Any

import pytest

from gameday_lab.factory import build_alerter, build_client, build_executor_factory
from gameday_lab.sim.config import load_config
from gameday_lab.sim.engine import evaluate_checks, run_scenario
from gameday_lab.slo.data import load, load_labels
from gameday_lab.slo.scoring import score_alerts
from gameday_lab.workflows.runner import run_workflows

UNIT_TESTS = {  # file -> (part, points)
    "tests/test_errors_retry.py": ("A", 12),
    "tests/test_breaker.py": ("B", 12),
    "tests/test_client.py": ("C", 6),
    "tests/test_executor.py": ("D", 4),
    "tests/test_alerting.py": ("E", 4),
    "tests/test_cache.py": ("G", 4),
}
POINTS_PER_TAPE = 4  # 6 tapes -> 24
WORKFLOW_POINTS = 8
SLO_PUBLIC_POINTS = 4
SLO_HIDDEN_POINTS = 4
CACHE_TAPE_POINTS = 6
REPORT_POINTS = 12
WORKFLOW_CHECKS = {"duplicate_refunds": 0, "duplicate_emails": 0, "lost_refunds": 0}


class _Collector:
    def __init__(self) -> None:
        self.results: dict[str, list[bool]] = defaultdict(list)

    def pytest_runtest_logreport(self, report: Any) -> None:
        if report.when == "call" or (report.when == "setup" and report.failed):
            self.results[report.nodeid.split("::")[0]].append(report.passed)


def unit_tests() -> dict[str, Any]:
    collector = _Collector()
    with contextlib.redirect_stdout(io.StringIO()):
        pytest.main(["-q", "-p", "no:cacheprovider", *UNIT_TESTS], plugins=[collector])
    out = {}
    for path, (part, pts) in UNIT_TESTS.items():
        res = collector.results.get(path, [])
        passed = sum(res)
        out[path] = {"part": part, "passed": passed, "total": len(res),
                     "points": round(pts * passed / len(res), 2) if res else 0.0, "max": pts}
    return out


def gameday(cfg_path: str, scen_path: str, seeds: list[int], client: str = "resilient",
            points: int = POINTS_PER_TAPE) -> dict[str, Any]:
    cfg = load_config(cfg_path, scen_path)
    out = {}
    for key, sc in cfg.scenarios.items():
        runs = []
        for seed in seeds:
            try:
                m = run_scenario(cfg, key, build_client(client, cfg), seed).metrics()
                checks = evaluate_checks(m, sc.checks)
                runs.append({"seed": seed, "pass": all(checks.values()),
                             "failed": [k for k, v in checks.items() if not v]})
            except NotImplementedError as exc:
                runs.append({"seed": seed, "pass": False, "failed": [f"NotImplementedError: {exc}"]})
        ok = all(r["pass"] for r in runs)
        out[key] = {"pass": ok, "points": points if ok else 0, "max": points, "runs": runs}
    return out


def workflows(seeds: list[int]) -> dict[str, Any]:
    runs = []
    for seed in seeds:
        try:
            m = run_workflows(build_executor_factory("yours"), n=500, seed=seed)
        except NotImplementedError as exc:
            runs.append({"seed": seed, "pass": False, "error": str(exc)})
            continue
        ok = (all(m[k] <= v for k, v in WORKFLOW_CHECKS.items())
              and m["completion_rate"] >= 0.99 and m["wasted_llm_per_crash"] <= 0.6)
        runs.append({"seed": seed, "pass": ok, **{k: m[k] for k in
                     ["duplicate_refunds", "duplicate_emails", "lost_refunds", "completion_rate",
                      "wasted_llm_per_crash"]}})
    ok = all(r["pass"] for r in runs)
    return {"pass": ok, "points": WORKFLOW_POINTS if ok else 0, "max": WORKFLOW_POINTS, "runs": runs}


def slo(data: str, labels: str, points: int) -> dict[str, Any]:
    total, bad = load(data)
    try:
        events = build_alerter("burnrate").evaluate(total, bad)
    except NotImplementedError as exc:
        return {"pass": False, "points": 0, "max": points, "error": str(exc)}
    s = score_alerts(events, load_labels(labels))
    return {"pass": s["pass"], "points": points if s["pass"] else 0, "max": points,
            "false_pages": s["false_pages"], "incidents": s["incidents"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="configs/gameday.yaml")
    ap.add_argument("--scenarios", default="configs/scenarios.yaml")
    ap.add_argument("--cache-scenarios", default="configs/scenarios_cache.yaml")
    ap.add_argument("--seeds", default="25")
    ap.add_argument("--hidden-slo", default=None)
    ap.add_argument("--out", default="reports/scoreboard.json")
    args = ap.parse_args()
    seeds = [int(s) for s in args.seeds.split(",")]

    board: dict[str, Any] = {"unit_tests": unit_tests(),
                             "gameday": gameday(args.config, args.scenarios, seeds),
                             "workflows": workflows(seeds),
                             "slo_public": slo("data/sli_30d.csv", "data/sli_30d_labels.json", SLO_PUBLIC_POINTS),
                             "cache_tape": gameday(args.config, args.cache_scenarios, seeds, "cached",
                                                   CACHE_TAPE_POINTS)}
    if args.hidden_slo:
        labels = args.hidden_slo.replace(".csv", "_labels.json")
        board["slo_hidden"] = slo(args.hidden_slo, labels, SLO_HIDDEN_POINTS)
    else:
        board["slo_hidden"] = {"pass": None, "points": 0, "max": SLO_HIDDEN_POINTS,
                               "note": "graded by the instructor on a hidden dataset"}

    parts: dict[str, float] = defaultdict(float)
    for r in board["unit_tests"].values():
        parts[r["part"]] += r["points"]
    parts["C"] += sum(r["points"] for r in board["gameday"].values())
    parts["D"] += board["workflows"]["points"]
    parts["E"] += board["slo_public"]["points"] + board["slo_hidden"]["points"]
    parts["G"] += sum(r["points"] for r in board["cache_tape"].values())
    auto = round(sum(parts.values()), 2)
    board["summary"] = {"parts": {k: round(v, 2) for k, v in sorted(parts.items())},
                        "automatic": auto, "automatic_max": 100 - REPORT_POINTS,
                        "report_max": REPORT_POINTS}

    print("\nUnit tests")
    for path, r in board["unit_tests"].items():
        print(f"  [{r['part']}] {path:30s} {r['passed']:>2}/{r['total']:<2} -> {r['points']:>5}/{r['max']}")
    print("Game day tapes (resilient client, seeds " + args.seeds + ")")
    for key, r in board["gameday"].items():
        failed = sorted({f for run in r["runs"] for f in run["failed"]})
        print(f"  [C] {key:28s} {'PASS' if r['pass'] else 'FAIL'} {', '.join(failed)}")
    print(f"Workflows         [D] {'PASS' if board['workflows']['pass'] else 'FAIL'}")
    print(f"SLO alerting      [E] public {'PASS' if board['slo_public']['pass'] else 'FAIL'}"
          f" | hidden {board['slo_hidden']['pass']}")
    print("Cache tape (cached client, seeds " + args.seeds + ")")
    for key, r in board["cache_tape"].items():
        failed = sorted({f for run in r["runs"] for f in run["failed"]})
        print(f"  [G] {key:28s} {'PASS' if r['pass'] else 'FAIL'} {', '.join(failed)}")
    print(f"\nParts: {board['summary']['parts']}")
    print(f"AUTOMATIC SCORE: {auto} / {100 - REPORT_POINTS}   (+ report & postmortem: /{REPORT_POINTS})")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(board, indent=2, default=str))


if __name__ == "__main__":
    main()
