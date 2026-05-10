"""
archetype_matchup.py — Build a style-archetype matchup matrix from historical
matches and test whether it predicts outcomes.

Method:
  1. Pool all years' merged fingerprints into one population
  2. Cluster into k archetypes using the same style-feature pipeline as
     style_archetypes.py
  3. For each match in the backtest set (2014–2024), assign both players
     archetypes from their PRIOR-year fingerprints (no leakage)
  4. Tally outcomes into a (k × k) matrix → conditional win-rates
  5. Evaluate accuracy/Brier of "archetype-only" predictor vs:
       - Coin flip (50%)
       - Current MC model (67% accuracy / 0.214 Brier)

If the matrix shows real asymmetric structure AND the archetype-only
predictor beats coin flip non-trivially, the prior carries signal worth
wiring into the MC pipeline as a soft prior.

Usage:
    python src/archetype_matchup.py --k 5
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from collections import defaultdict, Counter

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from comparison import load_all_fingerprints
from style_archetypes import (
    extract_features, z_scale, kmeans, label_centroid,
    FEATURE_NAMES, _euclid,
)
from build_player_profiles import load_year, men_match_ids

SUPPORTED_YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019,
                   2021, 2022, 2023, 2024]
TEST_YEARS = [y for y in SUPPORTED_YEARS if y >= 2014]


def _prior_year(y: int) -> int | None:
    past = [yy for yy in SUPPORTED_YEARS if yy < y]
    return past[-1] if past else None


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


def filter_outliers(features: dict) -> bool:
    """Drop players with sparse-data fingerprints that produce nonsensical features."""
    if features["rpw_vs_1st"] < 10:
        return False
    if abs(features["rally_skew"]) > 25:
        return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--cluster-year", type=int, default=2024,
                    help="year whose merged fingerprints define the archetype centroids")
    args = ap.parse_args()

    # ── Step 1: build archetype centroids from one stable population ────────
    # We use a single year's merged fingerprints (PBP + grass) to define the
    # centroids. Style is stable enough that this gives consistent labels
    # across years. The matchup matrix is built from PRIOR-year fingerprints
    # so there's no outcome leakage.
    print(f"Training archetype centroids on {args.cluster_year} merged fingerprints...")
    train_fps = load_all_fingerprints(args.cluster_year)
    train_vectors, train_features, train_names = [], [], []
    for name, fp in train_fps.items():
        f = extract_features(fp)
        if f is None or not filter_outliers(f):
            continue
        train_vectors.append(z_scale(f))
        train_features.append(f)
        train_names.append(name)
    print(f"  {len(train_vectors)} training players (after filtering)")

    labels, centroids = kmeans(train_vectors, k=args.k, seed=42)
    archetype_names = [label_centroid(c) for c in centroids]
    print(f"  k={args.k} archetypes:")
    for i, an in enumerate(archetype_names):
        n = labels.count(i)
        print(f"    [{i}] {an}  (n_train={n})")
    print()

    def assign_archetype(fp_dict) -> int | None:
        f = extract_features(fp_dict)
        if f is None or not filter_outliers(f):
            return None
        v = z_scale(f)
        best, best_d = -1, float("inf")
        for ci, c in enumerate(centroids):
            d = _euclid(v, c)
            if d < best_d:
                best_d, best = d, ci
        return best

    # ── Step 2: gather all backtest matches with archetype labels ───────────
    print(f"Building matchup matrix from {len(TEST_YEARS)} years...")
    fp_cache = {}
    matches = []  # list of (arch_A, arch_B, A_won)
    for y in TEST_YEARS:
        prior = _prior_year(y)
        if prior is None:
            continue
        if prior not in fp_cache:
            fp_cache[prior] = load_all_fingerprints(prior)
        prior_fps = fp_cache[prior]
        try:
            pts, mat = load_year(y)
        except FileNotFoundError:
            continue
        for _, row in mat[mat["match_id"].isin(men_match_ids(mat))].iterrows():
            mid = row["match_id"]
            p1, p2 = str(row["player1"]), str(row["player2"])
            winner = _winner_from_pts(pts, mid)
            if winner is None: continue
            fp1, fp2 = prior_fps.get(p1), prior_fps.get(p2)
            if fp1 is None or fp2 is None: continue
            a1 = assign_archetype(fp1)
            a2 = assign_archetype(fp2)
            if a1 is None or a2 is None: continue
            matches.append((a1, a2, 1 if winner == 1 else 0))
    print(f"  {len(matches)} matches with both archetypes assigned\n")

    # ── Step 3: build the matchup matrix ────────────────────────────────────
    # For each (arch_A, arch_B) pair, count wins for A.
    # By symmetry we can also count the reverse (arch_B, arch_A) flipping the win.
    # That doubles the data per cell.
    counts = defaultdict(lambda: [0, 0])  # [wins_A, total]
    for a1, a2, won_1 in matches:
        counts[(a1, a2)][1] += 1
        counts[(a1, a2)][0] += won_1
        # Also add the symmetric perspective
        counts[(a2, a1)][1] += 1
        counts[(a2, a1)][0] += (1 - won_1)

    print("=" * 92)
    print(f"Archetype matchup matrix — P(row archetype beats column archetype)")
    print("=" * 92)
    print(f"{'':>30}", end="")
    for j in range(args.k):
        print(f"{archetype_names[j][:14]:>15}", end="")
    print()
    print("-" * 92)

    # Compute matrix and print
    matrix = [[None] * args.k for _ in range(args.k)]
    n_matrix = [[0] * args.k for _ in range(args.k)]
    for i in range(args.k):
        print(f"{archetype_names[i][:28]:<30}", end="")
        for j in range(args.k):
            wins, total = counts[(i, j)]
            if total < 8:
                print(f"{'·':>15}", end="")
                matrix[i][j] = None
            else:
                p = wins / total
                matrix[i][j] = p
                n_matrix[i][j] = total
                marker = ""
                if abs(p - 0.5) > 0.07:
                    marker = "*"  # noticeably off 50%
                print(f"{p*100:>10.1f}%{marker:<2}(n={total})"[:15].rjust(15), end="")
        print()
    print()
    print("Cells with n<8 marked · ; cells with |p-50%|>7pp marked *")
    print()

    # ── Step 4: evaluate archetype-only predictor ────────────────────────────
    # For each match, look up the matrix cell (arch_A, arch_B) and predict
    # P(A wins) = matrix[a1][a2]. Compare to actual outcome.
    print("=" * 92)
    print("Archetype-only predictor evaluation")
    print("=" * 92)
    correct = brier = log_loss = 0
    n_eval = 0
    EPS = 1e-9
    for a1, a2, won in matches:
        if matrix[a1][a2] is None:
            continue
        p = matrix[a1][a2]
        # Squeeze toward 0.5 to avoid extreme predictions; ~Laplace smoothing
        n = n_matrix[a1][a2]
        p = (p * n + 0.5 * 4) / (n + 4)   # weak prior toward 50%
        correct += int((p > 0.5) == (won == 1))
        brier += (p - won) ** 2
        log_loss += -(won * math.log(max(p, EPS)) + (1 - won) * math.log(max(1 - p, EPS)))
        n_eval += 1
    if n_eval > 0:
        print(f"  Evaluated on {n_eval} matches")
        print(f"  Accuracy   : {correct/n_eval*100:.2f}%")
        print(f"  Brier score: {brier/n_eval:.4f}")
        print(f"  Log loss   : {log_loss/n_eval:.4f}")
    print()
    print(f"For comparison:")
    print(f"  Coin flip       : 50.00% accuracy / 0.2500 Brier / 0.6931 log-loss")
    print(f"  Current MC v3   : 68.50% accuracy / 0.2140 Brier / 0.7374 log-loss")

    # ── Step 5: dispersion check — how asymmetric is the matrix? ────────────
    print()
    print("=" * 92)
    print("Matrix asymmetry (variance of off-diagonal values)")
    print("=" * 92)
    off_diag = [matrix[i][j] for i in range(args.k) for j in range(args.k)
                if i != j and matrix[i][j] is not None]
    if off_diag:
        mean = sum(off_diag) / len(off_diag)
        var  = sum((x - 0.5) ** 2 for x in off_diag) / len(off_diag)
        print(f"  Off-diagonal cells: n={len(off_diag)}")
        print(f"  Mean win-rate    : {mean*100:.1f}%  (50% = no signal)")
        print(f"  RMS deviation from 50% : {(var ** 0.5)*100:.2f}pp")
        print(f"  Largest |p-50%|  : {max(abs(x-0.5) for x in off_diag)*100:.1f}pp")


if __name__ == "__main__":
    main()
