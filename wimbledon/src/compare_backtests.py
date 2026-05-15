"""compare_backtests.py — Summarise A/B results across multiple backtest runs.

Reads model_metrics-style JSONs and prints a side-by-side comparison
of accuracy, Brier, and log-loss (overall + per-year), with Δ vs the
first config (baseline).

Usage:
    python3 src/compare_backtests.py baseline=data/backtest_baseline.json \\
        latent=data/backtest_latent.json \\
        pressure=data/backtest_pressure.json \\
        both=data/backtest_both.json
"""

from __future__ import annotations
import json
import sys
from pathlib import Path


def load(path: str) -> dict:
    d = json.loads(Path(path).read_text())
    return d.get("mc_fingerprint_backtest", d)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    configs = []
    for arg in sys.argv[1:]:
        if "=" not in arg:
            sys.exit(f"bad arg: {arg!r}")
        label, path = arg.split("=", 1)
        configs.append((label, load(path)))

    baseline_brier = configs[0][1].get("brier") or 0.0
    baseline_acc   = configs[0][1].get("accuracy") or 0.0
    baseline_ll    = configs[0][1].get("log_loss") or 0.0

    print(f"\n{'='*84}")
    print(f"BACKTEST COMPARISON  (n_total = {configs[0][1].get('n_total')})")
    print(f"{'='*84}")
    print(f"{'config':<22}  {'acc':>6}  {'Δ':>7}    {'brier':>7}  {'Δ':>7}    {'log_loss':>8}  {'Δ':>7}")
    print("-" * 84)
    for label, d in configs:
        a, b, l = d.get("accuracy", 0), d.get("brier", 0), d.get("log_loss", 0)
        da = a - baseline_acc
        db = b - baseline_brier
        dl = l - baseline_ll
        print(f"{label:<22}  {a:6.3%}  {da:+7.3%}    {b:7.4f}  {db:+7.4f}    {l:8.4f}  {dl:+7.4f}")

    # Per-year
    print()
    years = sorted(set(y for _, d in configs for y in (d.get("per_year") or {}).keys()))
    print(f"{'year':<6} " + "  ".join(f"{lbl:>18}" for lbl, _ in configs))
    print("-" * (6 + 20 * len(configs)))
    for y in years:
        cells = []
        for _, d in configs:
            py = (d.get("per_year") or {}).get(y, {})
            if py:
                cells.append(f"{py.get('accuracy', 0):5.3f}/{py.get('brier', 0):5.3f}")
            else:
                cells.append("       —       ")
        print(f"{y:<6} " + "  ".join(f"{c:>18}" for c in cells))

    # Vs Elo baseline anchor
    print()
    print("Reference points (from CLAUDE.md):")
    print(f"  Elo-only Brier            : 0.2086")
    print(f"  Previous engine Brier     : 0.2098")
    print(f"  ML-baseline (LR) Brier    : 0.192   (different dataset/split, ceiling proxy)")


if __name__ == "__main__":
    main()
