"""
momentum_hmm.py — Per-player within-service-game momentum profile.

Replaces the legacy "continuous catch-fire" momentum (disabled in
PRODUCTION_ABLATED) with a state-conditional baseline.

State definition (within current service game, reset at game boundary):

  hot      — server has won 2 or more consecutive points in the current
             service game
  neutral  — otherwise

For each player and serve type (1st / 2nd serve), we compute the
posterior mean win rate in each state using Beta-Binomial with weak
prior (α=β=2).  Empirical-Bayes shrinkage toward the field mean uses
k=50 pseudo-observations — looser than pressure_states (k=30) because
the hot/neutral sample sizes are larger (~30-40% of points are "hot").

Output: data/{year}_momentum_hmm.json with, per player:
  fspw_hot_pct, fspw_neutral_pct, n_hot, n_neutral (1st-serve points)
  sspw_hot_pct, sspw_neutral_pct, n_hot, n_neutral (2nd-serve points)
  ret_hot_pct, ret_neutral_pct (returner equivalents — when RETURNING
                                  and on a 2+ streak of return wins)
  shrinkage info for each metric

The engine reads this in simulate_point: when state["streakCount"] >= 2
(2+ consecutive points won within current service game), substitute
the hot baseline for the neutral one.

NOTE on streakCount semantics in the existing engine:
  state["streakCount"]  > 0 means server is on a winning streak
                        < 0 means server is on a losing streak (returner streak)
                        Resets each game in _simulate_game().
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
SHRINKAGE_K = 50.0
HOT_STREAK_MIN = 2          # streak length to count as "hot"


def _player_streaks_in_game(rows: pd.DataFrame) -> List[dict]:
    """For one match, walk every point and emit a record per point with:
       server_name, returner_name, server, ServeNumber, won, server_streak,
       returner_streak (streak going INTO this point, within current game).

    A streak is tracked per service game.  When GameNo changes, both
    streaks reset to 0.  The streak is incremented after each point.
    """
    out = []
    cur_game = None
    srv_streak = ret_streak = 0
    for _, row in rows.iterrows():
        try:
            server = int(row.get("PointServer"))
            sn = int(row.get("ServeNumber"))
            winner = int(row.get("PointWinner"))
            game_no = (row.get("SetNo"), row.get("GameNo"))
        except (TypeError, ValueError):
            continue
        if server not in (1, 2) or sn not in (1, 2) or winner not in (1, 2):
            continue
        if game_no != cur_game:
            cur_game = game_no
            srv_streak = ret_streak = 0
        # streak going INTO this point
        out.append({
            "server":       server,
            "sn":           sn,
            "won":          (winner == server),
            "srv_streak":   srv_streak,
            "ret_streak":   ret_streak,
        })
        if winner == server:
            srv_streak += 1
            ret_streak = 0
        else:
            ret_streak += 1
            srv_streak = 0
    return out


def _compute_player_states(pts: pd.DataFrame, matches: pd.DataFrame
                           ) -> Dict[str, Dict[str, int]]:
    """name -> {fspw_hot_n, fspw_hot_w, fspw_neutral_n, fspw_neutral_w, ...}"""
    men_ids = set(men_match_ids(matches))
    pts = pts[pts["match_id"].isin(men_ids)]
    m_idx = matches.set_index("match_id")
    counts: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for mid, group in pts.groupby("match_id", sort=False):
        if mid not in m_idx.index:
            continue
        mrow = m_idx.loc[mid]
        p1, p2 = mrow["player1"], mrow["player2"]
        if pd.isna(p1) or pd.isna(p2):
            continue
        names = {1: normalize_name(str(p1)), 2: normalize_name(str(p2))}
        for rec in _player_streaks_in_game(group):
            srv_name = names[rec["server"]]
            ret_name = names[3 - rec["server"]]
            srv_state = "hot" if rec["srv_streak"] >= HOT_STREAK_MIN else "neutral"
            ret_state = "hot" if rec["ret_streak"] >= HOT_STREAK_MIN else "neutral"
            srv_key = "fspw" if rec["sn"] == 1 else "sspw"

            counts[srv_name][f"{srv_key}_{srv_state}_n"] += 1
            if rec["won"]:
                counts[srv_name][f"{srv_key}_{srv_state}_w"] += 1
            # Returner side: track win rate while returning, broken out by
            # the returner's own consecutive-returner-win streak.
            ret_key = "rpw_vs_1st" if rec["sn"] == 1 else "rpw_vs_2nd"
            counts[ret_name][f"{ret_key}_{ret_state}_n"] += 1
            if not rec["won"]:
                counts[ret_name][f"{ret_key}_{ret_state}_w"] += 1
    return counts


def _beta_mean(w: int, n: int) -> float:
    a = PRIOR_ALPHA + w
    b = PRIOR_BETA + (n - w)
    return 100.0 * a / (a + b)


def fit_year(year: int) -> Dict[str, dict]:
    try:
        pts, mat = load_year(year)
    except FileNotFoundError:
        print(f"  [{year}] no raw PBP — skipping")
        return {}

    raw = _compute_player_states(pts, mat)
    if not raw:
        return {}

    metrics = [
        "fspw_hot",       "fspw_neutral",
        "sspw_hot",       "sspw_neutral",
        "rpw_vs_1st_hot", "rpw_vs_1st_neutral",
        "rpw_vs_2nd_hot", "rpw_vs_2nd_neutral",
    ]
    rows: List[dict] = []
    for name, cnts in raw.items():
        rec = {"player": name}
        for m in metrics:
            w = cnts.get(f"{m}_w", 0)
            n = cnts.get(f"{m}_n", 0)
            rec[f"{m}_raw_pct"] = _beta_mean(w, n)
            rec[f"{m}_n"]       = n
        rows.append(rec)

    # Field means (weighted by sample size, players with n>=5 only).
    field_mean: Dict[str, float] = {}
    for m in metrics:
        vals = [r[f"{m}_raw_pct"] for r in rows if r[f"{m}_n"] >= 5]
        wts  = [r[f"{m}_n"]       for r in rows if r[f"{m}_n"] >= 5]
        field_mean[m] = float(np.average(vals, weights=wts)) if vals else 50.0

    out: Dict[str, dict] = {}
    for r in rows:
        rec: Dict[str, float] = {}
        for m in metrics:
            n = r[f"{m}_n"]
            raw_pct = r[f"{m}_raw_pct"]
            prior = field_mean[m]
            smoothed = (n * raw_pct + SHRINKAGE_K * prior) / (n + SHRINKAGE_K)
            rec[f"{m}_pct"]       = round(smoothed, 2)
            rec[f"{m}_raw_pct"]   = round(raw_pct, 2)
            rec[f"{m}_n"]         = n
            rec[f"{m}_shrinkage"] = round(n / (n + SHRINKAGE_K), 3)
        out[r["player"]] = rec
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="")
    parser.add_argument("--out_dir", default=str(DATA_DIR))
    args = parser.parse_args()

    years = [int(y) for y in args.years.split(",")] if args.years else YEARS
    out_dir = Path(args.out_dir)

    for year in years:
        result = fit_year(year)
        path = out_dir / f"{year}_momentum_hmm.json"
        path.write_text(json.dumps(result, indent=2))
        if result:
            sample = next(iter(result.values()))
            diff = sample["fspw_hot_pct"] - sample["fspw_neutral_pct"]
            print(f"  [{year}] {len(result):3d} players  -> {path.name}  "
                  f"sample: fspw_hot={sample['fspw_hot_pct']}  "
                  f"neutral={sample['fspw_neutral_pct']}  Δ={diff:+.1f}")


if __name__ == "__main__":
    main()
