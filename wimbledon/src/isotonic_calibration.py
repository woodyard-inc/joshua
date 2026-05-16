"""
isotonic_calibration.py — Leave-one-year-out isotonic calibration of
raw MC match probabilities.

Replacement for the parametric Platt sigmoid + [0.20, 0.80] clamp.

Isotonic regression fits a non-decreasing piecewise-constant mapping
from raw probability to calibrated probability.  Unlike Platt's single
parameter (temperature A), isotonic can be aggressive in the middle
(where most match-up evidence is) and tight at the tails (where
genuine blowouts live).  Tail probabilities can reach 90%+ without
miscalibrating the middle.

Leave-one-year-out CV: for predicting year Y, fit the calibration
mapping on ALL OTHER years' (raw_p, actual_outcome) pairs, then apply
that mapping to year Y's predictions.  Guarantees no leakage of the
test year into the calibration fit.

Usage:
    python3 src/isotonic_calibration.py \\
        --per_match data/per_match_baseline.json \\
        --label baseline_isotonic

    python3 src/isotonic_calibration.py \\
        --per_match data/per_match_pressure.json \\
        --label pressure_isotonic
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Dict, List

import numpy as np
from sklearn.isotonic import IsotonicRegression

ROOT = Path(__file__).parent.parent
EPSILON = 1e-9


def _safe_log(p: float) -> float:
    return math.log(max(p, EPSILON))


def _brier(p: float, a: int) -> float:
    return (p - a) ** 2


def _log_loss(p: float, a: int) -> float:
    return -(a * _safe_log(p) + (1 - a) * _safe_log(1 - p))


def fit_and_predict(per_match: List[dict]) -> Dict[str, dict]:
    """Leave-one-year-out isotonic calibration.

    Each match record needs: year, p_win_1_raw (uncalibrated MC),
    actual_1_wins.

    Returns: per-year metrics dict.
    """
    by_year = defaultdict(list)
    for r in per_match:
        if r.get("p_win_1_raw") is None:
            continue
        by_year[r["year"]].append(r)

    years = sorted(by_year.keys())
    print(f"Per-year coverage: {[(y, len(by_year[y])) for y in years]}")

    per_year = {}
    all_calibrated = []
    all_raw = []
    all_actual = []
    all_platt = []  # also keep Platt-calibrated for direct comparison

    for test_year in years:
        train_rows = []
        for y in years:
            if y == test_year:
                continue
            train_rows.extend(by_year[y])
        if not train_rows:
            continue

        X = np.array([r["p_win_1_raw"]      for r in train_rows])
        y = np.array([r["actual_1_wins"]    for r in train_rows])
        iso = IsotonicRegression(y_min=0.005, y_max=0.995, out_of_bounds="clip")
        iso.fit(X, y)

        test_rows = by_year[test_year]
        test_raw  = np.array([r["p_win_1_raw"]   for r in test_rows])
        test_act  = np.array([r["actual_1_wins"] for r in test_rows])
        test_cal  = iso.predict(test_raw)

        # Per-year metrics (isotonic + raw + platt-equivalent already in p_win_1)
        briers     = (test_cal - test_act) ** 2
        log_losses = -(test_act * np.log(np.clip(test_cal, EPSILON, 1))
                       + (1 - test_act) * np.log(np.clip(1 - test_cal, EPSILON, 1)))
        correct    = (test_cal > 0.5) == (test_act == 1)

        per_year[str(test_year)] = {
            "n_matches": len(test_rows),
            "accuracy":  round(float(correct.mean()), 3),
            "brier":     round(float(briers.mean()), 4),
            "log_loss":  round(float(log_losses.mean()), 4),
        }

        for r, c in zip(test_rows, test_cal):
            all_calibrated.append(float(c))
            all_raw.append(r["p_win_1_raw"])
            all_actual.append(r["actual_1_wins"])
            all_platt.append(r["p_win_1"])      # already-Platt-calibrated

    print()
    return {
        "per_year":         per_year,
        "calibrated":       all_calibrated,
        "raw":              all_raw,
        "actual":           all_actual,
        "platt_calibrated": all_platt,
    }


def _summary(label: str, preds: List[float], acts: List[int]) -> dict:
    n = len(preds)
    correct = sum((p > 0.5) == (a == 1) for p, a in zip(preds, acts))
    brier   = sum((p - a) ** 2 for p, a in zip(preds, acts)) / n
    log_l   = sum(_log_loss(p, a) for p, a in zip(preds, acts)) / n
    print(f"  {label:<22}  n={n:4d}  acc={correct/n:.3%}  "
          f"brier={brier:.4f}  log_loss={log_l:.4f}")
    return {"n": n, "accuracy": correct/n, "brier": brier, "log_loss": log_l}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--per_match", required=True,
                        help="Per-match JSON saved by backtest_fingerprints --save_per_match")
    parser.add_argument("--label", default="isotonic",
                        help="Label for output file: data/iso_<label>.json")
    args = parser.parse_args()

    rows = json.loads(Path(args.per_match).read_text())
    print(f"Loaded {len(rows)} matches from {args.per_match}\n")

    result = fit_and_predict(rows)
    cal = result["calibrated"]
    raw = result["raw"]
    plt = result["platt_calibrated"]
    act = result["actual"]

    print("Comparison on the same 908-match LOYO set:")
    iso_summary    = _summary("isotonic",        cal, act)
    raw_summary    = _summary("raw MC (no cal)", raw, act)
    platt_summary  = _summary("Platt+clamp",     plt, act)

    print()
    d_brier_vs_platt = iso_summary["brier"]    - platt_summary["brier"]
    d_acc_vs_platt   = iso_summary["accuracy"] - platt_summary["accuracy"]
    print(f"  isotonic vs Platt+clamp:  Δ brier = {d_brier_vs_platt:+.4f}   "
          f"Δ acc = {d_acc_vs_platt:+.3%}")

    out = {
        "method": "leave-one-year-out isotonic regression on raw MC probs",
        "per_year_isotonic": result["per_year"],
        "overall_isotonic":  iso_summary,
        "overall_raw":       raw_summary,
        "overall_platt":     platt_summary,
        "delta_isotonic_vs_platt": {
            "brier":    d_brier_vs_platt,
            "accuracy": d_acc_vs_platt,
        },
    }
    out_path = ROOT / "data" / f"iso_{args.label}.json"
    out_path.write_text(json.dumps(out, indent=2, default=lambda v: round(v, 6) if isinstance(v, float) else v))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
