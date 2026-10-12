"""Replay incident tapes against a client and print a scoreboard.

    python scripts/run_gameday.py --client naive        # the baseline
    python scripts/run_gameday.py --client resilient    # your client
    python scripts/run_gameday.py --client both --scenario G2_overload_storm
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without pip install

import argparse
import csv
import json
import time

from gameday_lab.factory import build_client
from gameday_lab.sim.config import load_config
from gameday_lab.sim.engine import evaluate_checks, run_scenario

COLUMNS = [
    ("availability", "avail"),
    ("latency_p95_s", "p95(s)"),
    ("amplification", "ampl"),
    ("retry_ratio", "retry"),
    ("cost_per_good_turn", "cost/turn"),
    ("recovery_s", "recov(s)"),
]


def run(client_name: str, scenarios: list[str], cfg_path: str, scen_path: str, out: Path,
        seed: int | None) -> dict[str, dict]:
    cfg = load_config(cfg_path, scen_path)
    results: dict[str, dict] = {}
    for key in scenarios:
        if key not in cfg.scenarios:
            sys.exit(f"unknown scenario {key!r}; choose from {list(cfg.scenarios)}")
        t0 = time.perf_counter()
        try:
            sim = run_scenario(cfg, key, build_client(client_name, cfg), seed)
        except NotImplementedError as exc:
            print(f"  {key:28s} {client_name:9s} NOT IMPLEMENTED: {exc}")
            results[key] = {"error": f"NotImplementedError: {exc}", "pass": False, "checks": {}}
            continue
        metrics = sim.metrics()
        checks = evaluate_checks(metrics, cfg.scenarios[key].checks)
        metrics["checks"] = checks
        metrics["pass"] = all(checks.values())
        metrics["runtime_s"] = round(time.perf_counter() - t0, 2)
        results[key] = metrics
        out.mkdir(parents=True, exist_ok=True)
        with (out / f"{client_name}_{key}_timeseries.csv").open("w", newline="") as f:
            rows = sim.timeseries()
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        cells = " ".join(f"{label}={metrics[name]}" for name, label in COLUMNS)
        failed = [k for k, v in checks.items() if not v]
        verdict = "PASS" if metrics["pass"] else "FAIL " + ",".join(failed)
        print(f"  {key:28s} {client_name:9s} {cells}  -> {verdict}")
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{client_name}.json").write_text(json.dumps(results, indent=2))
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="resilient", choices=["naive", "resilient", "both"])
    ap.add_argument("--scenario", default="all")
    ap.add_argument("--config", default="configs/gameday.yaml")
    ap.add_argument("--scenarios", default="configs/scenarios.yaml")
    ap.add_argument("--out", default="reports/gameday")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()
    cfg = load_config(args.config, args.scenarios)
    keys = list(cfg.scenarios) if args.scenario == "all" else args.scenario.split(",")
    clients = ["naive", "resilient"] if args.client == "both" else [args.client]
    for c in clients:
        print(f"\n== client: {c}")
        res = run(c, keys, args.config, args.scenarios, Path(args.out), args.seed)
        passed = sum(1 for r in res.values() if r.get("pass"))
        print(f"   {passed}/{len(res)} tapes passed -> {args.out}/{c}.json")


if __name__ == "__main__":
    main()
