"""
matchup_neighbors.py — Build & query a corpus of historical matchups for
nearest-neighbor matchup priors.

Concept (Sprint 5a, per user feedback):
  Instead of generic style archetypes, use the ACTUAL fingerprint metric
  values to find historical matchups most similar to a query.  The query's
  prior is the win rate of those neighbors.

  Features per matchup (A is the "left" side):
    For each metric m in MATCHUP_METRICS:
      d_m = (A.m - B.m)         differential (captures who has the edge)
      s_m = (A.m + B.m) / 2     level (Federer vs Gasquet: same style
                                       differential but different level)
  All features are z-scored across the corpus at build time so e.g. fspw
  and rally_volatility contribute on comparable scales.

  Each historical match generates TWO corpus entries (one per side as
  "A"), so the lookup is symmetric.

  At query time we filter out the predicting year (LOYO) and return the
  average `won` of the K nearest entries.

The neighbor prior is a SUPPLEMENTARY signal — the MC engine continues
to drive the main prediction; the engine blends in the neighbor prior
at a small weight (default 0.15) at match-level finalisation.

Usage:
    python3 src/matchup_neighbors.py    # build & save data/matchup_corpus.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

sys.path.insert(0, str(Path(__file__).parent))
from build_player_profiles import load_year, men_match_ids   # noqa: E402
from comparison import load_all_fingerprints                 # noqa: E402

# Years and prior-edition map (matches backtest harness)
SUPPORTED_YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018,
                   2019, 2021, 2022, 2023, 2024]
MIN_TEST_YEAR = 2014


def _prior_edition(year: int) -> Optional[int]:
    past = [y for y in SUPPORTED_YEARS if y < year]
    return past[-1] if past else None


# Metrics used for the matchup vector.  Tier-1 first (always available);
# tier-2 selectively where most players have data.  Each metric produces
# two features: differential (A-B) and level ((A+B)/2).
MATCHUP_METRICS = [
    # (display_name,         tier1_key,           tier2_key,                value_path)
    ("fspw",        "fspw_pct",        None,                       None),
    ("sspw",        "sspw_pct",        None,                       None),
    ("rpw_vs_1st",  "rpw_vs_1st_pct",  None,                       None),
    ("rpw_vs_2nd",  "rpw_vs_2nd_pct",  None,                       None),
    ("sgw",         "sgw_pct",         None,                       None),
    ("rgw",         "rgw_pct",         None,                       None),
    ("serve_entropy",  None, "serve_entropy",          "pct_of_max"),
    ("clutch_diff",    None, "clutch_differential",    "value"),
    ("tiebreak_diff",  None, "tiebreak_differential",  "value"),
    ("attrition",      None, "attrition_slope",        "value"),
    ("rally_volat",    None, "rally_volatility",       "value"),
]


# Field-mean fallbacks (computed once at module load from grass averages)
_FALLBACK = {
    "fspw":           72.0,
    "sspw":           56.0,
    "rpw_vs_1st":     25.0,
    "rpw_vs_2nd":     40.0,
    "sgw":            80.0,
    "rgw":            16.0,
    "serve_entropy":  75.0,
    "clutch_diff":     0.0,
    "tiebreak_diff":   0.0,
    "attrition":       0.1,
    "rally_volat":     3.0,
}


def _fp_value(fp: dict, t1_key: Optional[str], t2_key: Optional[str],
              path: Optional[str], fallback: float) -> float:
    if t1_key is not None:
        node = (fp.get("tier1") or {}).get(t1_key)
        if isinstance(node, dict) and node.get("value") is not None:
            return float(node["value"])
        return fallback
    # tier-2
    node = (fp.get("tier2") or {}).get(t2_key)
    if not isinstance(node, dict):
        return fallback
    if node.get("available") is False:
        return fallback
    v = node.get(path)
    return float(v) if v is not None else fallback


def matchup_features(fp_a: dict, fp_b: dict) -> List[float]:
    """Raw (un-normalised) matchup feature vector.  Length = 2 * len(MATCHUP_METRICS)."""
    feats: List[float] = []
    for name, t1, t2, path in MATCHUP_METRICS:
        fb = _FALLBACK[name]
        a = _fp_value(fp_a, t1, t2, path, fb)
        b = _fp_value(fp_b, t1, t2, path, fb)
        feats.append(a - b)
        feats.append((a + b) / 2.0)
    return feats


def _match_winner_from_pts(pts: pd.DataFrame, match_id: str) -> Optional[int]:
    mpts = pts[pts["match_id"] == match_id]
    if mpts.empty:
        return None
    last = mpts.iloc[-1]
    try:
        return int(last["PointWinner"])
    except (TypeError, ValueError):
        return None


def build_corpus() -> dict:
    """Walk all matches 2014-2024; produce (features, won, year, fp_year)
    entries — TWO per match for symmetric lookup."""
    fps_cache: Dict[int, dict] = {}
    entries: List[dict] = []

    test_years = [y for y in SUPPORTED_YEARS if y >= MIN_TEST_YEAR]
    for year in test_years:
        prior = _prior_edition(year)
        if prior is None:
            continue
        if prior not in fps_cache:
            fps_cache[prior] = load_all_fingerprints(prior, data_dir=DATA_DIR,
                                                    merge_grass=True)
        fps_prior = fps_cache[prior]
        try:
            pts, mat = load_year(year)
        except FileNotFoundError:
            continue
        men_ids = set(men_match_ids(mat))
        men_rows = mat[mat["match_id"].isin(men_ids)]

        for _, row in men_rows.iterrows():
            mid = row["match_id"]
            p1 = str(row["player1"])
            p2 = str(row["player2"])
            fp1 = fps_prior.get(p1)
            fp2 = fps_prior.get(p2)
            if fp1 is None or fp2 is None:
                continue
            winner = _match_winner_from_pts(pts, mid)
            if winner is None:
                continue
            won1 = (winner == 1)
            # Two entries: one per side as "A"
            entries.append({
                "year":     year,
                "fp_year":  prior,
                "match_id": mid,
                "left":     p1,
                "right":    p2,
                "features": matchup_features(fp1, fp2),
                "won_left": 1 if won1 else 0,
            })
            entries.append({
                "year":     year,
                "fp_year":  prior,
                "match_id": mid,
                "left":     p2,
                "right":    p1,
                "features": matchup_features(fp2, fp1),
                "won_left": 0 if won1 else 1,
            })
        print(f"  [{year}] cum entries: {len(entries)}")

    # Normalise across entire corpus
    import numpy as np
    F = np.array([e["features"] for e in entries], dtype=float)
    mean = F.mean(axis=0)
    std  = F.std(axis=0) + 1e-6
    for e in entries:
        e["features"] = ((np.array(e["features"]) - mean) / std).round(6).tolist()

    return {
        "n_entries":     len(entries),
        "feature_names": [f"{n}_{kind}"
                          for (n, *_) in MATCHUP_METRICS
                          for kind in ("diff", "level")],
        "mean":          mean.tolist(),
        "std":           std.tolist(),
        "entries":       entries,
    }


# ── Query API (used by engine via attached corpus) ────────────────────────

def normalise(features: List[float], mean: List[float],
              std: List[float]) -> List[float]:
    return [(f - m) / s for f, m, s in zip(features, mean, std)]


def lookup(fp_a: dict, fp_b: dict, corpus: dict,
           exclude_year: Optional[int] = None, k: int = 30) -> Optional[float]:
    """K-NN matchup lookup.  Returns weighted average win rate for the
    'left' (A) side.  None if too few neighbours."""
    if not corpus or not corpus.get("entries"):
        return None
    query = matchup_features(fp_a, fp_b)
    query = normalise(query, corpus["mean"], corpus["std"])

    distances: List[Tuple[float, int]] = []
    for e in corpus["entries"]:
        if exclude_year is not None and e["year"] == exclude_year:
            continue
        f = e["features"]
        d2 = 0.0
        for i, q in enumerate(query):
            diff = q - f[i]
            d2 += diff * diff
        distances.append((d2, e["won_left"]))
    if len(distances) < k:
        return None
    distances.sort(key=lambda x: x[0])
    top = distances[:k]
    # Distance-weighted average: 1 / (d + eps)
    weights = [1.0 / (math.sqrt(d) + 0.5) for d, _ in top]
    total_w = sum(weights)
    weighted_won = sum(w * won for w, (_, won) in zip(weights, top))
    return weighted_won / total_w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA_DIR / "matchup_corpus.json"))
    args = ap.parse_args()

    print(f"Building matchup corpus from {MIN_TEST_YEAR}-{SUPPORTED_YEARS[-1]}...")
    corpus = build_corpus()
    print(f"\nCorpus: {corpus['n_entries']} entries, "
          f"{len(corpus['feature_names'])} features")
    print(f"Feature names: {corpus['feature_names']}")
    Path(args.out).write_text(json.dumps(corpus, separators=(',', ':')))
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
