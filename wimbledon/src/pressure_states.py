"""
pressure_states.py — Per-player state-conditional serve baselines.

Replaces the current "single baseline + Phase-3 modifier" structure for
pressure points with a separately-fit baseline per pressure regime.

Motivation
----------
The current engine computes p = fspw, then nudges it by ±1-2pp via clutch,
spci, firstServePressure, etc. at break/deuce points. Two structural
problems:

  1. The deltas are usually wash-outs in expectation (server's spci - returner's
     clutch is symmetric across the field), reducing to Elo-equivalence.
  2. Underlying baselines (fspw, sspw) absorb all pressure points into one
     average, so the "neutral" baseline used 95% of the time is contaminated
     by the 5% of pressure points.

Per-state baselines decouple these regimes:

  fspw_neutral   — 1st serve win % at non-pressure points
  fspw_pressure  — 1st serve win % at deuce / AD / BP-against
  sspw_neutral   — analog
  sspw_pressure  — analog

Each is fit with Beta-Binomial empirical-Bayes shrinkage toward the player's
ARCHETYPE-level mean (not toward zero). This is the key improvement over the
existing pressure modifiers, which collapse to neutral when UNRELIABLE.

Output: per-year data/{year}_pressure_states.json with, per player:
  fspw_neutral_pct, fspw_pressure_pct (smoothed)
  sspw_neutral_pct, sspw_pressure_pct (smoothed)
  n_neutral, n_pressure (raw point counts under each state)
  shrinkage_neutral, shrinkage_pressure
  archetype_id

Pressure state definition
-------------------------
A point is "pressure" if for the server it is:
  (a) a break-point-against (opponent has P{opp}BreakPoint=1), OR
  (b) deuce or advantage (P1Score and P2Score both in {40, AD}).

Otherwise neutral. This is consistent with monte_carlo_phased.simulate_point's
state.isBreakPoint / state.isDeuce flags.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

sys.path.insert(0, str(Path(__file__).parent))
from build_player_profiles import load_year, men_match_ids   # noqa: E402
from canonical_names import normalize_name                    # noqa: E402

YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018,
         2019, 2021, 2022, 2023]

# Beta(α, β) weak prior (matches fingerprint.py BETA_PRIOR convention)
PRIOR_ALPHA = 2.0
PRIOR_BETA  = 2.0

# Empirical-Bayes shrinkage strength toward archetype prior.  k = effective
# pseudo-observations from the prior.  Smaller k = trust raw more.
# Pressure samples are small (~20-40 points/player/year), so we want
# meaningful shrinkage.
SHRINKAGE_K = 30.0


def _classify_state(row: pd.Series) -> str:
    """Return 'pressure' if the SERVER is at break-point-against or
    deuce/AD, else 'neutral'."""
    server = row.get("PointServer")
    if server not in (1, 2):
        return "neutral"
    opp = 3 - int(server)
    bp_against = bool(row.get(f"P{opp}BreakPoint", 0))
    if bp_against:
        return "pressure"
    s1 = str(row.get("P1Score", "")).strip()
    s2 = str(row.get("P2Score", "")).strip()
    if (s1 in ("40", "AD")) and (s2 in ("40", "AD")):
        return "pressure"
    return "neutral"


def _compute_player_states(pts: pd.DataFrame, matches: pd.DataFrame
                           ) -> Dict[str, Dict[str, int]]:
    """
    For each player in this tournament, count points-served in each state
    bucketed by (state, serve_number, won).

    Returns: name -> {
        'fspw_neutral_w':  int (1st serve wins on neutral points)
        'fspw_neutral_n':  int (1st serve attempts on neutral points)
        'fspw_pressure_w', 'fspw_pressure_n',
        'sspw_neutral_w',  'sspw_neutral_n',
        'sspw_pressure_w', 'sspw_pressure_n',
    }
    """
    men_ids = set(men_match_ids(matches))
    pts = pts[pts["match_id"].isin(men_ids)]

    # Map match_id -> (player1, player2)
    m_idx = matches.set_index("match_id")
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for _, row in pts.iterrows():
        mid = row.get("match_id")
        if mid not in m_idx.index:
            continue
        try:
            server = int(row.get("PointServer"))
        except (TypeError, ValueError):
            continue
        if server not in (1, 2):
            continue
        try:
            sn = int(row.get("ServeNumber"))
        except (TypeError, ValueError):
            continue
        if sn not in (1, 2):
            continue
        try:
            winner = int(row.get("PointWinner"))
        except (TypeError, ValueError):
            continue
        if winner not in (1, 2):
            continue

        mrow = m_idx.loc[mid]
        name = mrow[f"player{server}"]
        if pd.isna(name):
            continue
        name = normalize_name(str(name))

        state = _classify_state(row)
        srv_key = "fspw" if sn == 1 else "sspw"
        ret_key = "rpw_vs_1st" if sn == 1 else "rpw_vs_2nd"
        srv_won = (winner == server)

        counts[name][f"{srv_key}_{state}_n"] += 1
        if srv_won:
            counts[name][f"{srv_key}_{state}_w"] += 1

        # Mirror: same point as a returner observation.
        ret_player = mrow[f"player{3 - server}"]
        if pd.isna(ret_player):
            continue
        ret_name = normalize_name(str(ret_player))
        counts[ret_name][f"{ret_key}_{state}_n"] += 1
        if not srv_won:
            counts[ret_name][f"{ret_key}_{state}_w"] += 1

    return counts


def _archetype_for_player(name: str, year: int,
                          archetypes_cache: Dict[int, dict]) -> Optional[int]:
    a = archetypes_cache.get(year)
    if not a:
        path = DATA_DIR / f"{year}_archetypes.json"
        if not path.exists():
            archetypes_cache[year] = {}
            return None
        a = json.loads(path.read_text())
        archetypes_cache[year] = a
    rec = a.get(name)
    return rec.get("id") if rec else None


def _beta_mean(w: int, n: int) -> Tuple[float, float]:
    """Beta-Binomial posterior mean with weak prior. Returns (mean_pct, n_eff)."""
    a = PRIOR_ALPHA + w
    b = PRIOR_BETA + (n - w)
    return 100.0 * a / (a + b), float(n)


def fit_year(year: int, archetypes_cache: Dict[int, dict]) -> Dict[str, dict]:
    """
    Fit per-player pressure-state baselines for one year.

    Two-stage:
      1. Raw Beta-Binomial posterior per (player, state, serve_type).
      2. Empirical-Bayes shrinkage toward the player's ARCHETYPE-mean baseline
         for that state and serve_type.  Players without an archetype get
         shrinkage toward the field-mean.
    """
    try:
        pts, mat = load_year(year)
    except FileNotFoundError:
        print(f"  [{year}] no raw PBP — skipping")
        return {}

    raw_counts = _compute_player_states(pts, mat)
    if not raw_counts:
        return {}

    # Compute Beta posterior means per metric.  Eight per player:
    # serve-side (fspw, sspw) × {neutral, pressure}
    # return-side (rpw_vs_1st, rpw_vs_2nd) × {neutral, pressure}
    metrics = [
        "fspw_neutral",       "fspw_pressure",
        "sspw_neutral",       "sspw_pressure",
        "rpw_vs_1st_neutral", "rpw_vs_1st_pressure",
        "rpw_vs_2nd_neutral", "rpw_vs_2nd_pressure",
    ]
    rows: List[dict] = []
    for name, cnts in raw_counts.items():
        rec = {"player": name, "year": year}
        for m in metrics:
            w = cnts.get(f"{m}_w", 0)
            n = cnts.get(f"{m}_n", 0)
            mean_pct, _ = _beta_mean(w, n)
            rec[f"{m}_raw_pct"] = mean_pct
            rec[f"{m}_n"]       = n
        rec["archetype_id"] = _archetype_for_player(name, year, archetypes_cache)
        rows.append(rec)

    # Compute archetype-level means (within this year only — recency-stable).
    arch_means = defaultdict(lambda: defaultdict(list))
    arch_weights = defaultdict(lambda: defaultdict(list))
    for r in rows:
        aid = r["archetype_id"]
        if aid is None:
            continue
        for m in metrics:
            n = r[f"{m}_n"]
            if n < 5:
                continue
            arch_means[aid][m].append(r[f"{m}_raw_pct"])
            arch_weights[aid][m].append(n)

    arch_mean = {
        aid: {
            m: float(np.average(vals, weights=arch_weights[aid][m]))
            for m, vals in d.items() if vals
        }
        for aid, d in arch_means.items()
    }

    # Field-mean fallback for players without archetype or sparse archetype.
    field_mean = {}
    for m in metrics:
        vals = [r[f"{m}_raw_pct"] for r in rows if r[f"{m}_n"] >= 5]
        wts  = [r[f"{m}_n"]       for r in rows if r[f"{m}_n"] >= 5]
        field_mean[m] = float(np.average(vals, weights=wts)) if vals else 50.0

    # Apply shrinkage: smoothed = (n*raw + k*prior) / (n + k)
    out: Dict[str, dict] = {}
    for r in rows:
        aid = r["archetype_id"]
        rec = {"archetype_id": aid}
        for m in metrics:
            n = r[f"{m}_n"]
            raw = r[f"{m}_raw_pct"]
            prior = (arch_mean.get(aid) or {}).get(m, field_mean[m])
            smoothed = (n * raw + SHRINKAGE_K * prior) / (n + SHRINKAGE_K)
            rec[f"{m}_pct"]        = round(smoothed, 2)
            rec[f"{m}_raw_pct"]    = round(raw, 2)
            rec[f"{m}_n"]          = n
            rec[f"{m}_shrinkage"]  = round(n / (n + SHRINKAGE_K), 3)
            rec[f"{m}_prior_pct"]  = round(prior, 2)
        out[r["player"]] = rec
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="", help="comma-separated; default = all")
    parser.add_argument("--out_dir", default=str(DATA_DIR))
    args = parser.parse_args()

    years = [int(y) for y in args.years.split(",")] if args.years else YEARS

    archetypes_cache: Dict[int, dict] = {}
    out_dir = Path(args.out_dir)
    for year in years:
        result = fit_year(year, archetypes_cache)
        path = out_dir / f"{year}_pressure_states.json"
        path.write_text(json.dumps(result, indent=2))
        if result:
            sample = next(iter(result.values()))
            print(f"  [{year}] {len(result):3d} players  -> {path.name}")
            print(f"           sample:  fspw_neutral={sample['fspw_neutral_pct']}  "
                  f"fspw_pressure={sample['fspw_pressure_pct']}  "
                  f"delta={sample['fspw_pressure_pct'] - sample['fspw_neutral_pct']:+.1f}")


if __name__ == "__main__":
    main()
