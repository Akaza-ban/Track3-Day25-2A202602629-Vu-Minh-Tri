"""Plot a tape for the postmortem: attempts/s per provider and call success ratio.

    python scripts/run_gameday.py --client both --scenario G2_overload_storm
    python scripts/plot_timeseries.py --scenario G2_overload_storm
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))  # run without pip install

import argparse
import csv


def load(path: Path) -> list[dict[str, float]]:
    with path.open() as f:
        return [{k: float(v) for k, v in row.items()} for row in csv.DictReader(f)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="G2_overload_storm")
    ap.add_argument("--dir", default="reports/gameday")
    args = ap.parse_args()
    try:
        import matplotlib.pyplot as plt
    except ImportError:
        raise SystemExit('matplotlib missing: pip install -e ".[plot]"')

    clients = [c for c in ("naive", "resilient") if (Path(args.dir) / f"{c}_{args.scenario}_timeseries.csv").exists()]
    if not clients:
        raise SystemExit("run scripts/run_gameday.py --client both first")
    fig, axes = plt.subplots(2, len(clients), figsize=(6 * len(clients), 6), sharex=True, squeeze=False)
    for col, client in enumerate(clients):
        rows = load(Path(args.dir) / f"{client}_{args.scenario}_timeseries.csv")
        t = [r["t"] for r in rows]
        for name in [k for k in rows[0] if k.startswith("attempts_")]:
            axes[0][col].plot(t, [r[name] for r in rows], label=name.removeprefix("attempts_"), linewidth=1)
        axes[0][col].set_title(f"{client}: attempts per second")
        axes[0][col].legend(fontsize=8)
        ratio = []
        for i in range(len(rows)):
            win = rows[max(0, i - 9): i + 1]
            done = sum(r["calls_done"] for r in win)
            ratio.append(sum(r["calls_ok"] for r in win) / done if done else float("nan"))
        axes[1][col].plot(t, ratio, color="black", linewidth=1)
        axes[1][col].set_ylim(0, 1.05)
        axes[1][col].set_title("call success ratio (10 s rolling)")
        axes[1][col].set_xlabel("virtual seconds")
    fig.tight_layout()
    out = Path(args.dir) / f"{args.scenario}.png"
    fig.savefig(out, dpi=130)
    print("wrote", out)


if __name__ == "__main__":
    main()
