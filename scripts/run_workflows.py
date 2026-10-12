"""Run 500 refund workflows with crash injection.

    python scripts/run_workflows.py --executor both
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without pip install

import argparse
import json

from gameday_lab.factory import build_executor_factory
from gameday_lab.workflows.runner import run_workflows

CHECKS = {
    "duplicate_refunds_max": 0,
    "duplicate_emails_max": 0,
    "lost_refunds_max": 0,
    "completion_rate_min": 0.99,
    "wasted_llm_per_crash_max": 0.6,
}
SHOW = ["completion_rate", "worker_crashes", "duplicate_refunds", "duplicate_emails",
        "lost_refunds", "overpaid_cents", "wasted_llm_calls", "wasted_llm_per_crash"]


def check(metrics: dict) -> dict[str, bool]:
    out = {}
    for key, bound in CHECKS.items():
        name, _, kind = key.rpartition("_")
        out[key] = metrics[name] <= bound if kind == "max" else metrics[name] >= bound
    return out


def run(name: str, n: int, seed: int) -> dict:
    try:
        metrics = run_workflows(build_executor_factory(name), n=n, seed=seed)
    except NotImplementedError as exc:
        print(f"  {name:6s} NOT IMPLEMENTED: {exc}")
        return {"error": str(exc), "pass": False, "checks": {}}
    checks = check(metrics)
    metrics["checks"] = checks
    metrics["pass"] = all(checks.values())
    cells = " ".join(f"{k}={metrics[k]}" for k in SHOW)
    failed = [k for k, v in checks.items() if not v]
    print(f"  {name:6s} {cells} -> {'PASS' if metrics['pass'] else 'FAIL ' + ','.join(failed)}")
    return metrics


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--executor", default="yours", choices=["naive", "yours", "both"])
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--seed", type=int, default=25)
    ap.add_argument("--out", default="reports/workflows.json")
    args = ap.parse_args()
    names = ["naive", "yours"] if args.executor == "both" else [args.executor]
    results = {name: run(name, args.n, args.seed) for name in names}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
