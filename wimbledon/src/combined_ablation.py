"""
combined_ablation.py — Run several pre-defined modifier-removal scenarios
in one pass to test whether single-ablation gains are additive.

Usage:
    python src/combined_ablation.py --n_sims 500 --out /tmp/combined_ablation.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

import monte_carlo_phased as mcp
from ablation_study import evaluate_backtest


SCENARIOS = [
    ("baseline",
     "All modifiers ON (current production phased model)",
     set()),

    ("rallyCurve_only",
     "Drop rallyCurve (the single biggest drag)",
     {"rallyCurve"}),

    ("drop_harmful",
     "Drop all clearly harmful modifiers (Δacc≥+0.30 OR ΔBrier>0)",
     {"rallyCurve", "bpConversion", "rallyVolatility",
      "courtSideRally", "courtSideServe", "firstServePressure",
      "momentum"}),

    ("drop_harmful_plus_marginal",
     "Drop harmful + marginal-noise modifiers (attrition, tiebreak)",
     {"rallyCurve", "bpConversion", "rallyVolatility",
      "courtSideRally", "courtSideServe", "firstServePressure",
      "momentum", "attrition", "tiebreak"}),

    ("drop_everything_except_keepers",
     "Keep only spci, setTransition, serveEntropy (drop everything else)",
     mcp.ABLATABLE - {"spci", "setTransition", "serveEntropy"}),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n_sims", type=int, default=500)
    ap.add_argument("--out", default="/tmp/combined_ablation.json")
    args = ap.parse_args()

    print(f"Combined ablation study: n_sims={args.n_sims}")
    print(f"Scenarios: {len(SCENARIOS)}")
    print()

    results = {}
    baseline_metrics = None

    for name, desc, ablated in SCENARIOS:
        print(f"─── {name} ───")
        print(f"    {desc}")
        if ablated:
            print(f"    Ablating: {sorted(ablated)}")
        else:
            print(f"    (full model)")

        t0 = time.time()
        mcp.set_ablation(ablated)
        m = evaluate_backtest(args.n_sims)
        m["dt_sec"] = round(time.time() - t0, 1)
        m["ablated"] = sorted(ablated)
        m["description"] = desc

        if name == "baseline":
            baseline_metrics = m
            print(f"    acc={m['accuracy']*100:.2f}%  brier={m['brier']:.4f}  "
                  f"log_loss={m['log_loss']:.4f}  n={m['n']}  ({m['dt_sec']}s)")
        else:
            d_acc = (m["accuracy"] - baseline_metrics["accuracy"]) * 100
            d_brier = m["brier"] - baseline_metrics["brier"]
            d_loss = m["log_loss"] - baseline_metrics["log_loss"]
            m["delta_acc"] = round(d_acc, 3)
            m["delta_brier"] = round(d_brier, 5)
            m["delta_loss"] = round(d_loss, 5)
            print(f"    acc={m['accuracy']*100:.2f}%  brier={m['brier']:.4f}  "
                  f"log_loss={m['log_loss']:.4f}  ({m['dt_sec']}s)")
            print(f"    Δacc={d_acc:+.2f}pp  Δbrier={d_brier:+.5f}  Δloss={d_loss:+.5f}")
        print()

        results[name] = m

    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"Results written → {args.out}")

    # Summary
    print()
    print("=" * 88)
    print("Summary — combined ablation results")
    print("=" * 88)
    print(f"{'Scenario':<35}  {'acc':>7}  {'Δacc':>8}  {'brier':>8}  {'Δbrier':>10}  {'log_loss':>9}")
    print("─" * 88)
    for name, _, _ in SCENARIOS:
        m = results[name]
        d_acc = m.get("delta_acc", 0)
        d_brier = m.get("delta_brier", 0)
        marker = "—" if name == "baseline" else f"{d_acc:+.2f}pp"
        bmarker = "—" if name == "baseline" else f"{d_brier:+.5f}"
        print(f"{name:<35}  {m['accuracy']*100:>6.2f}%  {marker:>8}  "
              f"{m['brier']:>8.4f}  {bmarker:>10}  {m['log_loss']:>9.4f}")

    print()
    # Reference: aggregate baseline
    print("For reference (from previous run):")
    print(f"{'Aggregate engine baseline':<35}  {'67.10%':>7}  {'':>8}  {'0.2146':>8}")


if __name__ == "__main__":
    main()
