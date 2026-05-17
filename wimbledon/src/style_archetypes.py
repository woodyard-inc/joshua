"""
style_archetypes.py — Sketch: cluster grass-court players into style archetypes.

Loads merged fingerprints (PBP + grass profiles) and runs k-means on a small,
principled feature set covering serve, return, and rally style. The goal is
to see whether natural archetypes fall out — if they do, we can build a
matchup-matrix layer on top of the per-point Monte Carlo.

Usage:
    python src/style_archetypes.py --year 2024 --k 5
"""
from __future__ import annotations

import argparse
import json
import sys
import statistics
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from comparison import load_all_fingerprints

# ── feature extraction ─────────────────────────────────────────────────────
#
# Critical design choice: cluster on STYLE, not LEVEL.
#
# Earlier version used absolute z-scores of fspw, sspw, rpw_vs_1st, etc.
# Result: elite players ended up in one cluster regardless of style because
# they're +1 SD on every axis.  Qualifiers in another regardless of style.
# That's a quality classifier, not a style classifier.
#
# Fix: derive RELATIVE features that stay roughly constant for "same style,
# different level" players:
#
#   serve_lean       = (fspw_z + sspw_z) - (rpw1_z + rpw2_z)
#                       Positive = relies on serve more than return
#                       Negative = relies on return more than serve
#                       (Tour-relative; cancels out absolute level somewhat.)
#
#   first_serve_reliance = fspw - sspw
#                       High = "all-or-nothing" serve (Karlovic, Isner-style)
#                       Low  = balanced first/second serve (Federer-style)
#
#   return_aggression = (rpw_vs_2nd - 40) - (rpw_vs_1st - 25)
#                       Positive = punishes 2nd serves disproportionately
#                       Negative = neutralizes 1st serves disproportionately
#
#   rally_skew       = short_rally_win% - long_rally_win%
#                       Positive = first-strike (short-rally specialist)
#                       Negative = grinder (long-rally specialist)
#
# Four orthogonal style dimensions, each level-independent enough to let
# style emerge.  Keep the level information (overall quality) for the
# matchup baseline, not the style classifier.

# Tour averages — measured on 2024 dataset of merged fingerprints
TOUR_AVG = {
    "fspw":         71.5,
    "fsp":          63.0,
    "sspw":         54.5,
    "rpw_vs_1st":   24.5,
    "rpw_vs_2nd":   40.0,
    "rally_skew":    0.0,
    "rally_vol":     2.2,
}
TOUR_SD = {
    "fspw":         5.5,
    "fsp":          4.0,
    "sspw":         5.0,
    "rpw_vs_1st":   3.5,
    "rpw_vs_2nd":   4.5,
    "rally_skew":   8.0,
    "rally_vol":    1.0,
}

RALLY_BANDS = ["1_3", "4_6", "7_9", "10+"]


def _t1(fp: dict, key: str):
    v = fp.get("tier1", {}).get(key, {}) or {}
    return v.get("value") if isinstance(v, dict) else None


def extract_features(fp: dict) -> dict | None:
    """Return raw style features, or None if the player has insufficient data."""
    fspw = _t1(fp, "fspw_pct")
    fsp  = _t1(fp, "fsp_pct")
    sspw = _t1(fp, "sspw_pct")
    rv1  = _t1(fp, "rpw_vs_1st_pct")
    rv2  = _t1(fp, "rpw_vs_2nd_pct")
    if any(v is None for v in [fspw, fsp, sspw, rv1, rv2]):
        return None

    rwc = (fp.get("tier2") or {}).get("rally_win_curve") or {}
    short_win = (rwc.get("1_3") or {}).get("win_pct")
    long_win  = (rwc.get("10+") or {}).get("win_pct")
    rally_skew = (short_win - long_win) if (short_win is not None and long_win is not None) else 0.0

    return {
        "fspw":       fspw,
        "fsp":        fsp,
        "sspw":       sspw,
        "rpw_vs_1st": rv1,
        "rpw_vs_2nd": rv2,
        "rally_skew": rally_skew,
    }


def z_scale(features: dict) -> list[float]:
    """Convert raw features to a 4-dim STYLE vector (level-independent)."""
    fspw_z = (features["fspw"]       - TOUR_AVG["fspw"])       / TOUR_SD["fspw"]
    fsp_z  = (features["fsp"]        - TOUR_AVG["fsp"])        / TOUR_SD["fsp"]
    sspw_z = (features["sspw"]       - TOUR_AVG["sspw"])       / TOUR_SD["sspw"]
    rv1_z  = (features["rpw_vs_1st"] - TOUR_AVG["rpw_vs_1st"]) / TOUR_SD["rpw_vs_1st"]
    rv2_z  = (features["rpw_vs_2nd"] - TOUR_AVG["rpw_vs_2nd"]) / TOUR_SD["rpw_vs_2nd"]

    # Style features — CENTRED on tour means and SD-scaled so each axis
    # contributes equally to k-means distance. Each is roughly N(0, 1).
    serve_lean        = (fspw_z + sspw_z) / 2 - (rv1_z + rv2_z) / 2
    # Measured on 2024 dataset: mean(fspw - sspw) = 20.3, sd = 9.4
    first_serve_reliance = ((features["fspw"] - features["sspw"]) - 20.3) / 9.4
    # Measured on 2024 dataset: mean(rpw_vs_2nd - rpw_vs_1st) = 15.6, sd = 6.5
    return_aggression = ((features["rpw_vs_2nd"] - features["rpw_vs_1st"]) - 15.6) / 6.5
    rally_skew_z      = features["rally_skew"] / TOUR_SD["rally_skew"]
    serve_consistency = fsp_z

    return [serve_lean, first_serve_reliance, return_aggression, rally_skew_z, serve_consistency]


FEATURE_NAMES = ["serve_lean", "1st_reliance", "return_aggro", "rally_skew", "serve_consist"]


# ── lightweight k-means (no sklearn dependency) ────────────────────────────

import random as _random

def _euclid(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5

def kmeans(vectors: list[list[float]], k: int, n_iter: int = 100, seed: int = 42):
    """Simple k-means.  Returns (labels, centroids)."""
    rng = _random.Random(seed)
    n, d = len(vectors), len(vectors[0])

    # k-means++ init: pick first centre at random, then bias toward distant points
    centroids = [vectors[rng.randrange(n)]]
    for _ in range(k - 1):
        d2 = [min(_euclid(v, c) ** 2 for c in centroids) for v in vectors]
        total = sum(d2)
        if total == 0:
            centroids.append(vectors[rng.randrange(n)])
            continue
        r = rng.uniform(0, total)
        cum = 0
        for i, w in enumerate(d2):
            cum += w
            if cum >= r:
                centroids.append(vectors[i])
                break

    labels = [0] * n
    for _ in range(n_iter):
        new_labels = []
        for v in vectors:
            best, best_d = 0, float("inf")
            for ci, c in enumerate(centroids):
                d_c = _euclid(v, c)
                if d_c < best_d:
                    best_d, best = d_c, ci
            new_labels.append(best)
        if new_labels == labels:
            break
        labels = new_labels
        # Recompute centroids
        sums = [[0.0] * d for _ in range(k)]
        counts = [0] * k
        for v, lab in zip(vectors, labels):
            for j in range(d):
                sums[lab][j] += v[j]
            counts[lab] += 1
        centroids = [
            [sums[i][j] / counts[i] if counts[i] else centroids[i][j] for j in range(d)]
            for i in range(k)
        ]
    return labels, centroids


# ── archetype labelling — interpret centroids in plain English ──────────────

def label_centroid(c: list[float]) -> str:
    """Heuristic archetype label from a centroid in style-feature space.

    Style space dimensions (z-scored):
      [0] serve_lean       — positive: serve-dominant; negative: return-dominant
      [1] first_serve_reliance — positive: large fspw-sspw gap (1st serve carries them)
      [2] return_aggression — positive: punishes 2nd serves > neutralizes 1st
      [3] rally_skew        — positive: short-rally specialist; negative: grinder
      [4] serve_consistency — positive: gets more 1st serves in
    """
    serve_lean, reliance, return_aggro, rally_skew, consist = c

    # Outlier guard
    if abs(serve_lean) > 5 or abs(reliance) > 5:
        return "Outlier (sparse data)"

    # Most distinctive labels first
    if serve_lean > 0.5:
        # Serve-dominant cluster — split by aggression style
        if rally_skew > 0.4:
            return "Big-Serve First-Striker"
        return "Power Server"

    if serve_lean < -0.5:
        # Return-dominant cluster — split by where the return strength lies
        if return_aggro > 0.3:
            return "Counterpuncher (2nd-serve attacker)"
        return "Return Specialist (1st-serve neutralizer)"

    # Mid serve_lean — balanced players
    if return_aggro > 0.5 and reliance > 0.5:
        return "Two-Way Aggressor"      # strong 1st + punishes 2nd serves (Sinner-type)
    if rally_skew < -0.5:
        return "Grinder"
    if rally_skew > 0.5:
        return "First-Strike Attacker"
    return "All-Courter"


# ── main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, default=2024)
    ap.add_argument("--k", type=int, default=5, help="number of clusters")
    ap.add_argument("--multi-year", action="store_true",
                    help="aggregate features across multiple years (more stable)")
    args = ap.parse_args()

    # Load fingerprints
    if args.multi_year:
        years = [2019, 2021, 2022, 2023]
        all_fps = {}
        for y in years:
            fps = load_all_fingerprints(y)
            for name, fp in fps.items():
                # Keep latest year's profile if duplicated
                all_fps[name] = fp
        print(f"Loaded merged fingerprints across {years}: {len(all_fps)} unique players")
    else:
        all_fps = load_all_fingerprints(args.year)
        print(f"Loaded fingerprints for {args.year}: {len(all_fps)} players")

    # Extract feature vectors
    players, raw_features, vectors = [], [], []
    for name, fp in all_fps.items():
        f = extract_features(fp)
        if f is None:
            continue
        players.append(name)
        raw_features.append(f)
        vectors.append(z_scale(f))
    print(f"With sufficient data: {len(players)} players\n")

    # k-means
    labels, centroids = kmeans(vectors, k=args.k, seed=42)

    # Print centroids as profiles
    print("=" * 78)
    print(f"Cluster centroids (k={args.k}) — z-scores vs grass tour average")
    print("=" * 78)
    print(f"{'#':>3}  {'Archetype':<22}  ", end="")
    for nm in FEATURE_NAMES:
        print(f"{nm:>11}", end="")
    print(f"  {'n':>4}")
    print("-" * 130)
    for i, c in enumerate(centroids):
        archetype = label_centroid(c)
        n = labels.count(i)
        print(f"{i:>3}  {archetype:<22}  ", end="")
        for v in c:
            mark = "+" if v > 0 else ""
            print(f"{mark}{v:>10.2f}", end="")
        print(f"  {n:>4}")
    print()

    # Show a few representative players per cluster (closest to centroid)
    cluster_members = defaultdict(list)
    for p, v, lab in zip(players, vectors, labels):
        d = _euclid(v, centroids[lab])
        cluster_members[lab].append((p, d, raw_features[players.index(p)]))

    print("=" * 78)
    print("Representative players per cluster (closest to centroid)")
    print("=" * 78)
    for i in range(args.k):
        members = sorted(cluster_members[i], key=lambda x: x[1])
        archetype = label_centroid(centroids[i])
        print(f"\nCluster {i} — {archetype}  (n={len(members)})")
        for p, d, f in members[:6]:
            print(f"  {p:<28}  fspw={f['fspw']:>5.1f}  rpw1={f['rpw_vs_1st']:>5.1f}  "
                  f"rpw2={f['rpw_vs_2nd']:>5.1f}  skew={f['rally_skew']:>+5.1f}")

    # Spot-check: where do the famous names land?
    print()
    print("=" * 78)
    print("Spot check — famous players")
    print("=" * 78)
    famous = ["Carlos Alcaraz", "Jannik Sinner", "Novak Djokovic", "Daniil Medvedev",
              "Alexander Zverev", "Hubert Hurkacz", "Giovanni Mpetshi Perricard",
              "Ben Shelton", "Casper Ruud", "Andrey Rublev",
              "Jack Draper", "Taylor Fritz", "Stefanos Tsitsipas"]
    for fn in famous:
        idx = next((j for j, p in enumerate(players) if p == fn), None)
        if idx is None:
            continue
        lab = labels[idx]
        archetype = label_centroid(centroids[lab])
        f = raw_features[idx]
        print(f"  {fn:<28}  → Cluster {lab} ({archetype})")
        print(f"      fspw={f['fspw']:.1f}  fsp={f['fsp']:.1f}  sspw={f['sspw']:.1f}  "
              f"rpw1={f['rpw_vs_1st']:.1f}  rpw2={f['rpw_vs_2nd']:.1f}  "
              f"skew={f['rally_skew']:+.1f}")


if __name__ == "__main__":
    main()
