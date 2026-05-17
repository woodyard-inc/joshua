"""
tiebreak_baselines.py — Per-player tiebreak-vs-regular serve/return baselines.

Tiebreaks are ~5% of points but decide ~15% of matches.  Klaassen-Magnus
(2004) show empirically that win rates in tiebreaks differ measurably
from win rates in regular games for the same players — tiebreak
psychology is a distinct regime.

For each player and serve type we fit two Beta-Binomial posteriors:

  fspw_regular   /  fspw_tiebreak
  sspw_regular   /  sspw_tiebreak
  rpw_vs_1st_regular  /  rpw_vs_1st_tiebreak
  rpw_vs_2nd_regular  /  rpw_vs_2nd_tiebreak

Tiebreak-state estimates shrink toward the player's ARCHETYPE-mean
tiebreak baseline (not toward zero), so sparse tiebreak observations
still carry style information.  Same shrinkage strength k=30 as
pressure_states.py.

Tiebreak game classification: a service game whose recorded scores
include any value outside {0, 15, 30, 40, AD} is a tiebreak.  Regular
games never use single-digit non-40 scores like "1", "2", "3" — these
are unique to tiebreaks.

Output: data/{year}_tiebreak_baselines.json with per player:
  fspw_regular_pct,  fspw_tiebreak_pct,  fspw_regular_n,  fspw_tiebreak_n
  ... (8 metrics total, all with shrinkage applied)
  archetype_id
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

sys.path.insert(0, str(Path(__file__).parent))
from build_player_profiles import load_year, men_match_ids   # noqa: E402
from canonical_names import normalize_name                    # noqa: E402

YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018,
         2019, 2021, 2022, 2023]

PRIOR_ALPHA = 2.0
PRIOR_BETA  = 2.0
SHRINKAGE_K = 30.0

REGULAR_SCORES = {"0", "15", "30", "40", "AD", "nan", ""}


def _classify_games(pts: pd.DataFrame) -> set:
    """Return the set of (match_id, SetNo, GameNo) tuples that are tiebreak
    games.  A game is a tiebreak if at any point during the game either
    player's score is outside the regular-game set {0, 15, 30, 40, AD}."""
    tb_games: set = set()
    for (mid, sn, gn), grp in pts.groupby(["match_id", "SetNo", "GameNo"], sort=False):
        scores = (set(grp["P1Score"].astype(str)) |
                  set(grp["P2Score"].astype(str)))
        if scores - REGULAR_SCORES:
            tb_games.add((mid, sn, gn))
    return tb_games


def _compute_player_states(pts: pd.DataFrame, matches: pd.DataFrame
                           ) -> Dict[str, Dict[str, int]]:
    """name -> {fspw_regular_n, fspw_regular_w, fspw_tiebreak_n, ...}"""
    men_ids = set(men_match_ids(matches))
    pts = pts[pts["match_id"].isin(men_ids)]
    m_idx = matches.set_index("match_id")
    tb_games = _classify_games(pts)
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for _, row in pts.iterrows():
        mid = row.get("match_id")
        if mid not in m_idx.index:
            continue
        try:
            server = int(row.get("PointServer"))
            sn = int(row.get("ServeNumber"))
            winner = int(row.get("PointWinner"))
        except (TypeError, ValueError):
            continue
        if server not in (1, 2) or sn not in (1, 2) or winner not in (1, 2):
            continue

        mrow = m_idx.loc[mid]
        p_srv = mrow[f"player{server}"]
        p_ret = mrow[f"player{3 - server}"]
        if pd.isna(p_srv) or pd.isna(p_ret):
            continue
        srv_name = normalize_name(str(p_srv))
        ret_name = normalize_name(str(p_ret))

        state = "tiebreak" if (mid, row["SetNo"], row["GameNo"]) in tb_games else "regular"
        srv_key = "fspw" if sn == 1 else "sspw"
        ret_key = "rpw_vs_1st" if sn == 1 else "rpw_vs_2nd"
        srv_won = (winner == server)

        counts[srv_name][f"{srv_key}_{state}_n"] += 1
        if srv_won:
            counts[srv_name][f"{srv_key}_{state}_w"] += 1
        counts[ret_name][f"{ret_key}_{state}_n"] += 1
        if not srv_won:
            counts[ret_name][f"{ret_key}_{state}_w"] += 1

    return counts


def _archetype_for_player(name: str, year: int,
                          cache: Dict[int, dict]) -> Optional[int]:
    a = cache.get(year)
    if a is None:
        path = DATA_DIR / f"{year}_archetypes.json"
        a = json.loads(path.read_text()) if path.exists() else {}
        cache[year] = a
    rec = a.get(name) or {}
    return rec.get("id")


def _beta_mean(w: int, n: int) -> float:
    a = PRIOR_ALPHA + w
    b = PRIOR_BETA + (n - w)
    return 100.0 * a / (a + b)


def fit_year(year: int, archetypes_cache: Dict[int, dict]) -> Dict[str, dict]:
    try:
        pts, mat = load_year(year)
    except FileNotFoundError:
        print(f"  [{year}] no raw PBP — skipping")
        return {}

    raw = _compute_player_states(pts, mat)
    if not raw:
        return {}

    metrics = [
        "fspw_regular",       "fspw_tiebreak",
        "sspw_regular",       "sspw_tiebreak",
        "rpw_vs_1st_regular", "rpw_vs_1st_tiebreak",
        "rpw_vs_2nd_regular", "rpw_vs_2nd_tiebreak",
    ]
    rows: List[dict] = []
    for name, cnts in raw.items():
        rec = {"player": name}
        for m in metrics:
            w = cnts.get(f"{m}_w", 0)
            n = cnts.get(f"{m}_n", 0)
            rec[f"{m}_raw_pct"] = _beta_mean(w, n)
            rec[f"{m}_n"]       = n
        rec["archetype_id"] = _archetype_for_player(name, year, archetypes_cache)
        rows.append(rec)

    # Archetype-level means (this-year only).
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
        aid: {m: float(np.average(v, weights=arch_weights[aid][m]))
              for m, v in d.items() if v}
        for aid, d in arch_means.items()
    }

    # Field-mean fallback when archetype is sparse.
    field_mean = {}
    for m in metrics:
        vals = [r[f"{m}_raw_pct"] for r in rows if r[f"{m}_n"] >= 5]
        wts  = [r[f"{m}_n"]       for r in rows if r[f"{m}_n"] >= 5]
        field_mean[m] = float(np.average(vals, weights=wts)) if vals else 50.0

    out: Dict[str, dict] = {}
    for r in rows:
        aid = r["archetype_id"]
        rec = {"archetype_id": aid}
        for m in metrics:
            n = r[f"{m}_n"]
            raw_pct = r[f"{m}_raw_pct"]
            prior = (arch_mean.get(aid) or {}).get(m, field_mean[m])
            smoothed = (n * raw_pct + SHRINKAGE_K * prior) / (n + SHRINKAGE_K)
            rec[f"{m}_pct"]        = round(smoothed, 2)
            rec[f"{m}_raw_pct"]    = round(raw_pct, 2)
            rec[f"{m}_n"]          = n
            rec[f"{m}_shrinkage"]  = round(n / (n + SHRINKAGE_K), 3)
        out[r["player"]] = rec
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="")
    parser.add_argument("--out_dir", default=str(DATA_DIR))
    args = parser.parse_args()

    years = [int(y) for y in args.years.split(",")] if args.years else YEARS
    out_dir = Path(args.out_dir)
    archetypes_cache: Dict[int, dict] = {}

    for year in years:
        result = fit_year(year, archetypes_cache)
        path = out_dir / f"{year}_tiebreak_baselines.json"
        path.write_text(json.dumps(result, indent=2))
        if result:
            sample = next(iter(result.values()))
            n_tb = sample.get("fspw_tiebreak_n", 0)
            delta = sample.get("fspw_tiebreak_pct", 0) - sample.get("fspw_regular_pct", 0)
            print(f"  [{year}] {len(result):3d} players  -> {path.name}  "
                  f"sample: fspw_reg={sample.get('fspw_regular_pct')}  "
                  f"fspw_tb={sample.get('fspw_tiebreak_pct')}  "
                  f"Δ={delta:+.1f}  n_tb={n_tb}")


if __name__ == "__main__":
    main()
