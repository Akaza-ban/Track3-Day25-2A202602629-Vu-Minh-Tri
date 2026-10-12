"""Evaluate an alerter on 30 days of per-minute SLI data.

    python scripts/run_slo.py --alerter both
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without pip install

import argparse
import json
from dataclasses import asdict

from gameday_lab.factory import build_alerter
from gameday_lab.slo.data import load, load_labels
from gameday_lab.slo.scoring import score_alerts


def run(name: str, data: str, labels: str, slo: float) -> dict:
    total, bad = load(data)
    incidents = load_labels(labels)
    try:
        events = build_alerter(name, slo).evaluate(total, bad)
    except NotImplementedError as exc:
        print(f"  {name:8s} NOT IMPLEMENTED: {exc}")
        return {"error": str(exc), "pass": False}
    score = score_alerts(events, incidents)
    print(f"  {name:8s} pages={score['pages']} tickets={score['tickets']} "
          f"false_pages={score['false_pages']} -> {'PASS' if score['pass'] else 'FAIL'}")
    for row in score["incidents"]:
        print(f"      {row}")
    score["events"] = [asdict(e) for e in events]
    if name == "burnrate":
        try:
            from gameday_lab.slo.alerting import error_budget_report

            score["budget"] = error_budget_report(total, bad, slo)
            print(f"      budget: {score['budget']}")
        except NotImplementedError as exc:
            print(f"      budget: NOT IMPLEMENTED ({exc})")
    return score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--alerter", default="burnrate", choices=["naive", "burnrate", "both"])
    ap.add_argument("--data", default="data/sli_30d.csv")
    ap.add_argument("--labels", default="data/sli_30d_labels.json")
    ap.add_argument("--slo", type=float, default=0.995)
    ap.add_argument("--out", default="reports/slo.json")
    args = ap.parse_args()
    names = ["naive", "burnrate"] if args.alerter == "both" else [args.alerter]
    results = {n: run(n, args.data, args.labels, args.slo) for n in names}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
