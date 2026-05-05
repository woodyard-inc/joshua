"""
ablation_study.py — Disable one phased-MC modifier at a time and measure
the change in backtest accuracy / Brier vs the full-model baseline.

This is run AS THE BACKTEST so each ablation gets a fresh full-pass over
all 915 matches.  Slow but conclusive.

Usage:
    python src/ablation_study.py --n_sims 500 --out /tmp/ablation.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from build_player_profiles import load_year, men_match_ids
from comparison import load_all_fingerprints
import monte_carlo_phased as mcp

EPSILON = 1e-9
SUPPORTED_YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019,
                   2021, 2022, 2023, 2024]
MIN_TEST_YEAR = 2014


def _safe_log(p):
    return math.log(max(p, EPSILON))


def _winner_from_pts(pts, mid):
    m = pts[pts["match_id"] == mid]
    if m.empty:
        return None
    last = m.iloc[-1]
    pw = last.get("PointWinner")
    try:
        return int(pw)
    except (TypeError, ValueError):
        return None


def _prior_year(y):
    past = [yy for yy in SUPPORTED_YEARS if yy < y]
    return past[-1] if past else None


def evaluate_backtest(n_sims: int) -> dict:
    """Run the full backtest under current ablation flags. Returns metrics."""
    test_years = [y for y in SUPPORTED_YEARS if y >= MIN_TEST_YEAR]
    fp_cache = {}
    correct = brier = log_loss = 0
    n = 0

    for year in test_years:
        prior = _prior_year(year)
        if prior is None:
            continue
        if prior not in fp_cache:
            fp_cache[prior] = load_all_fingerprints(prior, ROOT / "data")
        prior_fps = fp_cache[prior]

        try:
            pts, mat = load_year(year)
        except FileNotFoundError:
            continue

        for _, row in mat[mat["match_id"].isin(men_match_ids(mat))].iterrows():
            mid = row["match_id"]
            p1, p2 = str(row["player1"]), str(row["player2"])
            winner = _winner_from_pts(pts, mid)
            if winner is None:
                continue
            fp1, fp2 = prior_fps.get(p1), prior_fps.get(p2)
            if fp1 is None or fp2 is None:
                continue
            try:
                sim = mcp.simulate_match_phased(fp1, fp2, n=n_sims, seed=42)
            except Exception:
                continue
            p = sim.p_win_a
            actual = 1 if winner == 1 else 0
            correct  += int((p > 0.5) == (actual == 1))
            brier    += (p - actual) ** 2
            log_loss += -(actual * _safe_log(p) +
                          (1 - actual) * _safe_log(1 - p))
            n += 1

    return {
        "n":        n,
        "accuracy": correct / n if n else 0,
        "brier":    brier / n if n else 0,
        "log_loss": log_loss / n if n else 0,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n_sims", type=int, default=500)
    ap.add_argument("--out", default="/tmp/ablation.json")
    ap.add_argument("--only", default=None,
                    help="Comma-separated subset of modifiers to ablate (default: all)")
    args = ap.parse_args()

    modifiers = sorted(mcp.ABLATABLE) if not args.only else args.only.split(",")

    print(f"Ablation study: n_sims={args.n_sims}, modifiers={len(modifiers)}")
    print(f"Each ablation pass evaluates ~915 matches.")
    print()

    results = {}

    # Baseline (no ablation)
    print("─── Running baseline (all modifiers ON) ───")
    t0 = time.time()
    mcp.set_ablation(set())
    baseline = evaluate_backtest(args.n_sims)
    baseline["dt_sec"] = round(time.time() - t0, 1)
    print(f"  acc={baseline['accuracy']*100:.2f}%  brier={baseline['brier']:.4f}  "
          f"log_loss={baseline['log_loss']:.4f}  n={baseline['n']}  ({baseline['dt_sec']}s)")
    print()
    results["__baseline__"] = baseline

    # Per-modifier ablations
    for mod in modifiers:
        print(f"─── Ablating: {mod} ───")
        t0 = time.time()
        mcp.set_ablation({mod})
        m = evaluate_backtest(args.n_sims)
        m["dt_sec"]      = round(time.time() - t0, 1)
        m["delta_acc"]   = round((m["accuracy"] - baseline["accuracy"]) * 100, 3)
        m["delta_brier"] = round(m["brier"] - baseline["brier"], 5)
        m["delta_loss"]  = round(m["log_loss"] - baseline["log_loss"], 5)
        # Positive delta_acc / negative delta_brier = ablation HELPED (modifier was hurting)
        results[mod] = m
        print(f"  acc={m['accuracy']*100:.2f}%  brier={m['brier']:.4f}  "
              f"log_loss={m['log_loss']:.4f}  "
              f"Δacc={m['delta_acc']:+.2f}pp  Δbrier={m['delta_brier']:+.5f}  "
              f"({m['dt_sec']}s)")
        print()

    # Save
    Path(args.out).write_text(json.dumps(results, indent=2))
    print(f"Results written → {args.out}")

    # Summary table
    print()
    print("=" * 78)
    print("Summary — ablation impact (positive Δacc means modifier was HURTING)")
    print("=" * 78)
    print(f"{'Modifier':<22}  {'acc':>7}  {'Δacc':>8}  {'brier':>8}  {'Δbrier':>10}")
    print("─" * 78)
    print(f"{'__baseline__':<22}  {baseline['accuracy']*100:>6.2f}%  {'—':>8}  "
          f"{baseline['brier']:>8.4f}  {'—':>10}")
    sorted_mods = sorted(modifiers, key=lambda m: results[m]["delta_brier"])
    for mod in sorted_mods:
        m = results[mod]
        print(f"{mod:<22}  {m['accuracy']*100:>6.2f}%  {m['delta_acc']:>+7.2f}pp  "
              f"{m['brier']:>8.4f}  {m['delta_brier']:>+10.5f}")


if __name__ == "__main__":
    main()
