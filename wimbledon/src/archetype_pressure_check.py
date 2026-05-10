"""
archetype_pressure_check.py — Proof-of-concept on 1-2 high-data-quality years.

Tests two questions:
  (1) When the MC model is confidently wrong, does the archetype matchup
      matrix call the upset direction?
  (2) Does a simple linear blend of MC + archetype priors beat MC alone?

If either yields a meaningful gain on the test years, archetypes are worth
wiring into the prediction pipeline.  If neither does, they're a UI-only
narrative layer (still valuable, but not a model input).

Methodology guards against leakage:
  - Archetype centroids are trained on a holdout year (2024)
  - Matchup matrix is built ONLY from years OUTSIDE the test set
  - Test-year predictions use prior-year fingerprints (same as MC backtest)

Usage:
    python src/archetype_pressure_check.py --test-years 2018 2019
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from comparison import load_all_fingerprints
import monte_carlo_phased as mcp
from style_archetypes import (
    extract_features, z_scale, kmeans, label_centroid, _euclid,
)
from build_player_profiles import load_year, men_match_ids
from archetype_matchup import (
    SUPPORTED_YEARS, _prior_year, _winner_from_pts, filter_outliers
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-years", nargs="+", type=int, default=[2018, 2019])
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--n-sims", type=int, default=500)
    ap.add_argument("--cluster-year", type=int, default=2024,
                    help="year used to define archetype centroids (must NOT be in test-years)")
    args = ap.parse_args()

    test_years = set(args.test_years)
    if args.cluster_year in test_years:
        print(f"ERROR: cluster-year {args.cluster_year} cannot also be a test year")
        sys.exit(1)

    print(f"Pressure check on test years: {sorted(test_years)}")
    print(f"  k={args.k} archetypes")
    print(f"  MC sims per match: {args.n_sims}")
    print(f"  Centroids trained on: {args.cluster_year}")
    print()

    # ── Train archetype centroids ────────────────────────────────────────────
    train_fps = load_all_fingerprints(args.cluster_year)
    train_vectors = []
    for fp in train_fps.values():
        f = extract_features(fp)
        if f is None or not filter_outliers(f):
            continue
        train_vectors.append(z_scale(f))
    print(f"Training centroids on {len(train_vectors)} {args.cluster_year} players")
    _, centroids = kmeans(train_vectors, k=args.k, seed=42)
    archetype_names = [label_centroid(c) for c in centroids]
    for i, an in enumerate(archetype_names):
        print(f"  [{i}] {an}")
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

    # ── Build matchup matrix from NON-TEST years only ────────────────────────
    print(f"Building matchup matrix from non-test years (excluding {sorted(test_years)})...")
    fp_cache = {}
    matrix_matches = []  # (a1, a2, won_1) — for matrix construction
    for y in [yy for yy in SUPPORTED_YEARS if yy not in test_years and yy >= 2014]:
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
            a1, a2 = assign_archetype(fp1), assign_archetype(fp2)
            if a1 is None or a2 is None: continue
            matrix_matches.append((a1, a2, 1 if winner == 1 else 0))
    print(f"  {len(matrix_matches)} matches feeding the matrix")

    counts = defaultdict(lambda: [0, 0])
    for a1, a2, w1 in matrix_matches:
        counts[(a1, a2)][1] += 1
        counts[(a1, a2)][0] += w1
        counts[(a2, a1)][1] += 1
        counts[(a2, a1)][0] += (1 - w1)

    matrix = [[None] * args.k for _ in range(args.k)]
    n_mat  = [[0] * args.k for _ in range(args.k)]
    for i in range(args.k):
        for j in range(args.k):
            wins, tot = counts[(i, j)]
            if tot >= 8:
                # Laplace-smoothed toward 50%
                matrix[i][j] = (wins + 2) / (tot + 4)
                n_mat[i][j]  = tot
            else:
                matrix[i][j] = 0.5
                n_mat[i][j]  = tot

    # ── Run MC + archetype predictions on test years ────────────────────────
    print()
    print(f"Predicting test years with both MC and archetype prior...")
    test_records = []  # one per match: dict with predictions + outcome + metadata
    for y in sorted(test_years):
        prior = _prior_year(y)
        if prior not in fp_cache:
            fp_cache[prior] = load_all_fingerprints(prior)
        prior_fps = fp_cache[prior]
        try:
            pts, mat = load_year(y)
        except FileNotFoundError:
            continue
        n_done = 0
        for _, row in mat[mat["match_id"].isin(men_match_ids(mat))].iterrows():
            mid = row["match_id"]
            p1, p2 = str(row["player1"]), str(row["player2"])
            winner = _winner_from_pts(pts, mid)
            if winner is None: continue
            fp1, fp2 = prior_fps.get(p1), prior_fps.get(p2)
            if fp1 is None or fp2 is None: continue
            a1, a2 = assign_archetype(fp1), assign_archetype(fp2)
            if a1 is None or a2 is None: continue

            try:
                sim = mcp.simulate_match_phased(fp1, fp2, n=args.n_sims, seed=42)
                p_mc = sim.p_win_a
            except Exception:
                continue

            p_arch = matrix[a1][a2]
            actual = 1 if winner == 1 else 0
            test_records.append({
                "year":  y, "match_id": mid,
                "p1": p1, "p2": p2,
                "arch1": a1, "arch2": a2,
                "p_mc": p_mc, "p_arch": p_arch,
                "actual_1": actual,
            })
            n_done += 1
        print(f"  {y}: {n_done} matches predicted")

    n = len(test_records)
    print(f"\n{n} total test-year matches with both predictions\n")

    # ── Q1: Where MC was confidently wrong, does archetype call the upset? ──
    print("=" * 92)
    print("Q1 — When MC is confidently WRONG, does archetype call the upset direction?")
    print("=" * 92)
    confident_threshold = 0.10  # |p - 0.5| > 0.10 (i.e., predicts >60% or <40%)
    wrong_confident = [r for r in test_records
                       if abs(r["p_mc"] - 0.5) > confident_threshold
                       and (r["p_mc"] > 0.5) != (r["actual_1"] == 1)]
    print(f"Confident MC threshold: |p_MC - 0.5| > {confident_threshold}")
    print(f"  Confident MC predictions: "
          f"{sum(1 for r in test_records if abs(r['p_mc']-0.5) > confident_threshold)}")
    print(f"  Of those, MC was WRONG : {len(wrong_confident)}")

    if len(wrong_confident) > 0:
        archetype_calls_upset = sum(
            1 for r in wrong_confident
            if (r["p_arch"] > 0.5) == (r["actual_1"] == 1)
        )
        print(f"  Archetype called the upset correctly: "
              f"{archetype_calls_upset} / {len(wrong_confident)} "
              f"({archetype_calls_upset/len(wrong_confident)*100:.1f}%)")
        print()
        print("  If random: would expect ~50%. Above 60% = meaningful diagnostic signal.")
    print()
    if wrong_confident:
        print("  Top 10 MC-wrong-confident matches with archetype predictions:")
        for r in sorted(wrong_confident, key=lambda r: abs(r["p_mc"] - 0.5), reverse=True)[:10]:
            mc_winner = r["p1"] if r["p_mc"] > 0.5 else r["p2"]
            actual_winner = r["p1"] if r["actual_1"] == 1 else r["p2"]
            arch_p = r["p_arch"] * 100
            mc_p   = r["p_mc"] * 100
            arch_dir = "agrees w MC" if (r["p_arch"] > 0.5) == (r["p_mc"] > 0.5) else "DISAGREES"
            arch_correct = "✓" if (r["p_arch"] > 0.5) == (r["actual_1"] == 1) else "✗"
            print(f"    {r['year']} {r['p1'][:18]:<18} vs {r['p2'][:18]:<18}  "
                  f"MC={mc_p:5.1f}%  Arch={arch_p:5.1f}%  ({arch_dir} {arch_correct})  "
                  f"Won: {actual_winner[:18]}")

    # ── Q2: Does blending (MC + archetype) beat MC alone? ────────────────────
    print()
    print("=" * 92)
    print("Q2 — Linear blend: p_blend = α·p_MC + (1-α)·p_arch")
    print("=" * 92)

    def evaluate(alpha):
        c = b = ll = 0
        EPS = 1e-9
        for r in test_records:
            p = alpha * r["p_mc"] + (1 - alpha) * r["p_arch"]
            actual = r["actual_1"]
            c += int((p > 0.5) == (actual == 1))
            b += (p - actual) ** 2
            ll += -(actual * math.log(max(p, EPS)) + (1 - actual) * math.log(max(1 - p, EPS)))
        return c / n, b / n, ll / n

    best_a = 1.0
    best_brier = None
    print(f"  {'alpha':>6}  {'accuracy':>10}  {'brier':>10}  {'log_loss':>10}")
    print(f"  {'-'*6}  {'-'*10}  {'-'*10}  {'-'*10}")
    for alpha_int in range(0, 11):
        alpha = alpha_int / 10
        acc, brier, ll = evaluate(alpha)
        if best_brier is None or brier < best_brier:
            best_brier, best_a = brier, alpha
        marker = "  ← MC alone" if alpha == 1.0 else ("  ← archetype alone" if alpha == 0.0 else "")
        print(f"  {alpha:>6.2f}  {acc*100:>9.2f}%  {brier:>10.4f}  {ll:>10.4f}{marker}")

    # Fine sweep
    print()
    print(f"  Fine sweep around best alpha = {best_a}:")
    print(f"  {'alpha':>6}  {'accuracy':>10}  {'brier':>10}  {'log_loss':>10}")
    print(f"  {'-'*6}  {'-'*10}  {'-'*10}  {'-'*10}")
    for offset in range(-5, 6):
        a = best_a + offset * 0.02
        if not 0 <= a <= 1: continue
        acc, brier, ll = evaluate(a)
        marker = "  ← best" if a == best_a else ""
        print(f"  {a:>6.2f}  {acc*100:>9.2f}%  {brier:>10.4f}  {ll:>10.4f}{marker}")

    # ── Verdict ─────────────────────────────────────────────────────────────
    print()
    print("=" * 92)
    print("Verdict")
    print("=" * 92)
    mc_acc, mc_brier, mc_ll = evaluate(1.0)
    print(f"  MC alone     : acc={mc_acc*100:.2f}%  brier={mc_brier:.4f}  log_loss={mc_ll:.4f}")
    arch_acc, arch_brier, arch_ll = evaluate(0.0)
    print(f"  Arch alone   : acc={arch_acc*100:.2f}%  brier={arch_brier:.4f}  log_loss={arch_ll:.4f}")
    best_acc, best_b, best_ll = evaluate(best_a)
    print(f"  Best blend   : acc={best_acc*100:.2f}%  brier={best_b:.4f}  log_loss={best_ll:.4f}  (alpha={best_a:.2f})")
    print()
    delta_acc   = (best_acc - mc_acc) * 100
    delta_brier = best_b - mc_brier
    print(f"  Blend vs MC  : Δacc = {delta_acc:+.2f}pp   ΔBrier = {delta_brier:+.5f}")
    if delta_brier < -0.001 or delta_acc > 0.5:
        print(f"  → MEANINGFUL improvement. Wire archetype prior into the model.")
    elif delta_brier < 0:
        print(f"  → Marginal improvement. Worth a bigger validation sample before committing.")
    else:
        print(f"  → No meaningful improvement. Archetypes are UI-only.")


if __name__ == "__main__":
    main()
