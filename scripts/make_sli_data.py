"""Regenerate data/sli_30d.csv + data/sli_30d_labels.json (deterministic)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without pip install

import argparse

from gameday_lab.slo.data import write

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data")
    ap.add_argument("--seed", type=int, default=2025)
    args = ap.parse_args()
    print("wrote", write(args.out, args.seed))
