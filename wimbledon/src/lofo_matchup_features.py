"""
lofo_matchup_features.py — Leave-one-feature-out ablation for the
matchup-neighbors corpus, with bootstrap CIs.

For each feature in the corpus, drop that single feature, run the engine
against per-match per-fp dumps, and compute the Δ Brier (mean + 95% CI
via percentile bootstrap on per-match deltas).

This tells us which features carry real signal (CI clearly below 0 =
dropping HURTS = feature is helping) vs which are dead weight (CI
overlaps 0 = no detectable signal) vs which are net harmful (CI clearly
above 0 = dropping HELPS).

Implementation note: rather than re-run the full MC backtest for each
feature (would take hours), we re-do ONLY the matchup-neighbor lookup
with the feature dropped, then re-blend at the same weight.  The MC
component is identical across runs — what changes is only the K-NN
neighbour set.

Inputs needed:
  - data/matchup_corpus.json  (current 44-feature corpus)
  - data/per_match_baseline.json  (from --save_per_match on baseline)
  - For each feature to test, we compute new neighbour win-rates without
    that feature dimension, then re-blend with the baseline p_cal.

Usage:
    # First make sure per-match dump exists with the v12.1 / 12.2 stack:
    python3 src/backtest_fingerprints.py --engine phased --n_sims 1000 \
        --no_matchup_neighbors --save_per_match data/per_match_no_neighbors.json
    # Then:
    python3 src/lofo_matchup_features.py
"""

from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
EPSILON = 1e-9
BOOTSTRAP_DRAWS = 2000


def _safe_log(p: float) -> float:
    return math.log(max(p, EPSILON))


def _brier(p: float, a: int) -> float:
    return (p - a) ** 2


def _bootstrap_ci(deltas: List[float], n_draws: int = BOOTSTRAP_DRAWS,
                  alpha: float = 0.05) -> Dict[str, float]:
    n = len(deltas)
    rng = random.Random(42)
    means = []
    for _ in range(n_draws):
        s = sum(deltas[rng.randrange(n)] for _ in range(n))
        means.append(s / n)
    means.sort()
    lo = means[int(n_draws * alpha / 2)]
    hi = means[int(n_draws * (1 - alpha / 2))]
    mu = sum(deltas) / n
    return {"mean": mu, "ci_lo": lo, "ci_hi": hi}


def _lookup_with_dropped_features(query_full: List[float],
                                  entries: List[dict],
                                  exclude_year: int,
                                  drop_idx: int,
                                  k: int = 30) -> Optional[float]:
    """Re-do the K-NN with feature[drop_idx] masked out of both query and corpus."""
    distances: List[Tuple[float, int]] = []
    for e in entries:
        if e["year"] == exclude_year:
            continue
        f = e["features"]
        d2 = 0.0
        for i, q in enumerate(query_full):
            if i == drop_idx:
                continue
            diff = q - f[i]
            d2 += diff * diff
        distances.append((d2, e["won_left"]))
    if len(distances) < k:
        return None
    distances.sort(key=lambda x: x[0])
    top = distances[:k]
    weights = [1.0 / (math.sqrt(d) + 0.5) for d, _ in top]
    total_w = sum(weights)
    return sum(w * won for w, (_, won) in zip(weights, top)) / total_w


def _all_features_lookup(query_full: List[float], entries: List[dict],
                         exclude_year: int, k: int = 30) -> Optional[float]:
    distances: List[Tuple[float, int]] = []
    for e in entries:
        if e["year"] == exclude_year:
            continue
        f = e["features"]
        d2 = 0.0
        for i, q in enumerate(query_full):
            diff = q - f[i]
            d2 += diff * diff
        distances.append((d2, e["won_left"]))
    if len(distances) < k:
        return None
    distances.sort(key=lambda x: x[0])
    top = distances[:k]
    weights = [1.0 / (math.sqrt(d) + 0.5) for d, _ in top]
    total_w = sum(weights)
    return sum(w * won for w, (_, won) in zip(weights, top)) / total_w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per_match",
                    default=str(DATA_DIR / "per_match_v122.json"),
                    help="Per-match dump with the matchup-neighbors-OFF MC predictions "
                         "(p_win_1 = MC Platt-calibrated, no neighbor blend).")
    ap.add_argument("--corpus", default=str(DATA_DIR / "matchup_corpus.json"))
    ap.add_argument("--out",    default=str(DATA_DIR / "lofo_matchup.json"))
    ap.add_argument("--blend",  type=float, default=0.15)
    args = ap.parse_args()

    print(f"Loading corpus from {args.corpus}")
    corpus = json.loads(Path(args.corpus).read_text())
    feature_names = corpus["feature_names"]
    entries = corpus["entries"]
    mean    = corpus["mean"]
    std     = corpus["std"]
    print(f"  {len(feature_names)} features, {len(entries)} entries\n")

    print(f"Loading per-match (MC-only, no neighbors) from {args.per_match}")
    per_match = json.loads(Path(args.per_match).read_text())
    print(f"  {len(per_match)} matches\n")

    # For each match, we'll need the raw matchup features.  These aren't in
    # per_match — we'd have to re-derive them.  Instead, we exploit that
    # the corpus already has every match (as both sides).  For each match
    # in per_match, find its corpus entries and use them as the query.
    by_match: Dict[str, dict] = {}
    for r in per_match:
        by_match[r["match_id"]] = r

    # Build a corpus index: for each (match_id, left_player), find the entry
    corpus_by_match: Dict[Tuple[str, str], dict] = {}
    for e in entries:
        corpus_by_match[(e["match_id"], e["left"])] = e

    # Compute baseline (all features) and per-feature-dropped neighbor
    # predictions for each match.  Blend at args.blend with the MC p.
    print(f"Running LOFO on {len(feature_names)} features...")
    feature_deltas: Dict[str, List[float]] = {f: [] for f in feature_names}
    baseline_blended: List[Tuple[float, int]] = []

    for mid, r in by_match.items():
        actual = r["actual_1_wins"]
        mc_p   = r["p_win_1"]          # MC platt-calibrated, NO neighbor blend
        # query feature vector: use the corpus entry for (mid, player1)
        e_left = corpus_by_match.get((mid, r["player1"]))
        if e_left is None:
            continue
        query = e_left["features"]      # already z-scored
        exclude_year = r["year"]

        # Baseline (all features)
        p_n_full = _all_features_lookup(query, entries, exclude_year, k=30)
        if p_n_full is None:
            continue
        p_blended_full = (1 - args.blend) * mc_p + args.blend * p_n_full
        b_full = (p_blended_full - actual) ** 2
        baseline_blended.append((p_blended_full, actual))

        # Per-feature dropped
        for i, fname in enumerate(feature_names):
            p_n_drop = _lookup_with_dropped_features(query, entries, exclude_year, drop_idx=i, k=30)
            if p_n_drop is None:
                continue
            p_blended_drop = (1 - args.blend) * mc_p + args.blend * p_n_drop
            b_drop = (p_blended_drop - actual) ** 2
            feature_deltas[fname].append(b_drop - b_full)   # positive = dropping HURT

    # Aggregate via bootstrap
    print(f"\nLOFO results (positive Δbrier means dropping the feature HURTS — i.e. the feature was contributing):")
    print(f"{'feature':<28} {'Δbrier':>9}  {'95% CI':>22}  verdict")
    print("-" * 78)
    summary = {}
    for fname in feature_names:
        deltas = feature_deltas[fname]
        if len(deltas) < 100:
            print(f"{fname:<28} {'too few samples':>9}")
            summary[fname] = {"n": len(deltas)}
            continue
        ci = _bootstrap_ci(deltas)
        if ci["ci_lo"] > 0:
            verdict = "CARRIES SIGNAL"
        elif ci["ci_hi"] < 0:
            verdict = "DEAD WEIGHT (drop helps)"
        else:
            verdict = "noise"
        ci_str = f"[{ci['ci_lo']:+.5f}, {ci['ci_hi']:+.5f}]"
        print(f"{fname:<28} {ci['mean']:>+.5f}  {ci_str:>22}  {verdict}")
        summary[fname] = {**ci, "n": len(deltas), "verdict": verdict}

    Path(args.out).write_text(json.dumps({
        "method": "LOFO with neighbor-blend post-process; bootstrap CI",
        "blend_weight": args.blend,
        "k": 30,
        "n_bootstrap_draws": BOOTSTRAP_DRAWS,
        "features": summary,
    }, indent=2, default=lambda v: round(v, 6) if isinstance(v, float) else v))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
