"""
Grass-season profile builder.

Builds Tier 1 player profiles from ALL ATP grass-court matches
(Queen's, Halle, 's-Hertogenbosch, Eastbourne, Newport, Stuttgart, Mallorca,
Antalya, Nottingham — plus Wimbledon itself) using match-level statistics
from data/processed/all_grass_matches.csv.

Complements the Wimbledon PBP fingerprints:
  • For players WITH Wimbledon PBP data: grass profile enriches Tier 1
    with a larger sample, but Tier 2 still comes from PBP fingerprint.
  • For players WITHOUT Wimbledon PBP data: grass profile provides the
    only available Tier 1 signal for the Monte Carlo engine.

Window rules (same philosophy as fingerprints, no temporal weighting):
  CAREER_WINDOW   = 10 most recent grass matches
  CAREER_YEARS    = 3 calendar years of data (exclusive of target year)
    e.g. for target_year=2024: uses matches from 2021, 2022, 2023

Output:
  data/processed/{year}_grass_profiles.json
  data/{year}_grass_profiles.json

Usage:
    python src/build_grass_profiles.py --year 2024
    python src/build_grass_profiles.py --year all
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from elo import GrassElo, build_from_csv as build_elo_from_csv
from fingerprint import (
    SUPPORTED_YEARS, WIMBLEDON_STARTS,
    CAREER_WINDOW, CAREER_EDITIONS,
    confidence_flag, pct_metric,
)

# ── paths ─────────────────────────────────────────────────────────────────────
ROOT     = Path(__file__).parent.parent
PROC_DIR = ROOT / "data" / "processed"
OUT_DIR  = ROOT / "data"
GRASS_CSV = PROC_DIR / "all_grass_matches.csv"

# ── constants ─────────────────────────────────────────────────────────────────
# Exclude Davis Cup / low-quality tournaments
EXCLUDED_TOURNEY_KEYWORDS = ["Davis Cup", "Olympics", "Hopman"]

BETA_A = 2.0   # Beta(2,2) prior for credible intervals
BETA_B = 2.0


# ── helpers ───────────────────────────────────────────────────────────────────

def _is_excluded(tourney_name: str) -> bool:
    return any(kw in tourney_name for kw in EXCLUDED_TOURNEY_KEYWORDS)


def _safe_pct(num: float, denom: float) -> Optional[float]:
    if denom and denom > 0:
        return round(num / denom * 100, 1)
    return None


def _conf(n: float) -> str:
    return confidence_flag(round(n))


def _elo_win_prob(r_a: Optional[float], r_b: Optional[float]) -> Optional[float]:
    """Standard Elo expected win probability for player A vs player B."""
    if r_a is None or r_b is None:
        return None
    return round(1 / (1 + 10 ** ((r_b - r_a) / 400)), 4)


# ── per-player profile ────────────────────────────────────────────────────────

def build_player_profile(player_name: str,
                         matches: List[dict],
                         year: int) -> dict:
    """
    Aggregate match-level stats into a Tier 1 grass profile.

    Each item in `matches` is a dict of raw match stats
    (from one row of all_grass_matches.csv, normalised to player perspective).
    """
    n = len(matches)
    if n == 0:
        return {}

    # Accumulate totals (equal weight — no temporal decay)
    srv_pts = srv_1st_in = srv_1st_won = srv_2nd_won = 0.0
    aces = dfs = 0.0
    srv_games = bp_faced = bp_saved = 0.0
    ret_pts = ret_won = ret_games = ret_games_won = 0.0

    for m in matches:
        srv_pts    += m.get("srv_pts",    0) or 0
        srv_1st_in += m.get("srv_1st_in", 0) or 0
        srv_1st_won+= m.get("srv_1st_won",0) or 0
        srv_2nd_won+= m.get("srv_2nd_won",0) or 0
        aces       += m.get("aces",       0) or 0
        dfs        += m.get("dfs",        0) or 0
        srv_games  += m.get("srv_games",  0) or 0
        bp_faced   += m.get("bp_faced",   0) or 0
        bp_saved   += m.get("bp_saved",   0) or 0
        ret_pts    += m.get("ret_pts",    0) or 0
        ret_won    += m.get("ret_won",    0) or 0
        ret_games  += m.get("ret_games",  0) or 0
        ret_games_won += m.get("ret_games_won", 0) or 0

    # Tier 1 percentages
    srv_2nd_in = max(0, srv_pts - srv_1st_in)
    breaks_conceded = max(0, bp_faced - bp_saved)
    srv_games_won = max(0, srv_games - breaks_conceded)

    fsp  = _safe_pct(srv_1st_in, srv_pts)
    fspw = _safe_pct(srv_1st_won, srv_1st_in)
    sspw = _safe_pct(srv_2nd_won, srv_2nd_in)
    rpw  = _safe_pct(ret_won, ret_pts)
    sgw  = _safe_pct(srv_games_won, srv_games)
    rgw  = _safe_pct(ret_games_won, ret_games)

    # Confidence based on serve points (primary sample size proxy)
    conf = _conf(srv_pts)

    def _t1(value, n_eff):
        if value is None:
            return {"value": None, "n_eff": round(n_eff), "confidence": "UNRELIABLE"}
        return {"value": value, "n_eff": round(n_eff), "confidence": _conf(n_eff)}

    return {
        "player":          player_name,
        "surface":         "grass",
        "year":            year,
        "source":          "grass_atp",
        "n_matches":       n,
        "n_srv_points":    round(srv_pts),
        "confidence":      conf,
        "tier2_available": False,
        "tier1": {
            "fsp_pct":  _t1(fsp,  srv_pts),
            "fspw_pct": _t1(fspw, srv_1st_in),
            "sspw_pct": _t1(sspw, srv_2nd_in),
            "rpw_pct":  _t1(rpw,  ret_pts),
            "sgw_pct":  _t1(sgw,  srv_games),
            "rgw_pct":  _t1(rgw,  ret_games),
        },
        "training_matches": [m.get("_meta", {}) for m in matches],
    }


# ── main builder ──────────────────────────────────────────────────────────────

def build_year_grass_profiles(year: int,
                               elo: Optional[GrassElo] = None,
                               career_window: int = CAREER_WINDOW,
                               career_years: int = CAREER_EDITIONS) -> dict:
    """
    Build grass profiles for every player in `year`'s Wimbledon draw
    using all grass-court matches from the `career_years` preceding years.

    Returns {player_name: profile_dict}.
    """
    print(f"\n── Grass Profiles {year}  "
          f"(window={career_window} matches / {career_years} prior years) "
          f"──────────────────────────────────────")

    # Load grass match data
    if not GRASS_CSV.exists():
        raise FileNotFoundError(f"Missing: {GRASS_CSV}")
    df = pd.read_csv(GRASS_CSV, low_memory=False)
    df = df[~df["tourney_name"].apply(_is_excluded)]

    # Eligible calendar years: the `career_years` years immediately before `year`
    eligible_grass_years = list(range(year - career_years, year))
    df = df[df["year"].isin(eligible_grass_years)].copy()
    df["tourney_date_parsed"] = pd.to_datetime(df["tourney_date"], errors="coerce")

    print(f"  Grass matches available ({eligible_grass_years}): {len(df)}")

    # Build profiles for EVERY player who has grass match data in the window.
    # This includes players not in the current draw — when used as prior-year
    # profiles in the backtest, these extra players improve coverage for
    # next-year predictions (a player who skips 2023 Wimbledon but plays
    # Queen's/Halle will have a grass profile available for 2024 predictions).
    all_grass_players = set(df["winner_name"].dropna().astype(str)) | \
                        set(df["loser_name"].dropna().astype(str))

    # Also include draw players (even if they have 0 grass matches, for logging)
    fp_file = PROC_DIR / f"{year}_fingerprints.json"
    alt_fp  = OUT_DIR  / f"{year}_fingerprints.json"
    if fp_file.exists():
        draw_players = set(json.loads(fp_file.read_text()).keys())
    elif alt_fp.exists():
        draw_players = set(json.loads(alt_fp.read_text()).keys())
    else:
        draw_players = set()

    # Union: all grass players + draw players
    target_players = all_grass_players | draw_players
    print(f"  Target players: {len(target_players)} ({len(draw_players)} draw + "
          f"{len(all_grass_players - draw_players)} additional from grass circuit)")

    # Index grass matches by player for fast lookup
    # Expand each row into (winner_record, loser_record) with player perspective
    records: Dict[str, List[dict]] = {}

    def _add(name, d):
        if name not in records:
            records[name] = []
        records[name].append(d)

    for _, row in df.iterrows():
        date = row.get("tourney_date_parsed") or pd.NaT
        tourney = row.get("tourney_name", "")
        rnd     = row.get("round", "")
        meta_base = {"tourney": tourney, "round": rnd,
                     "year": int(row.get("year", 0)),
                     "date": str(date)[:10] if pd.notna(date) else None}

        # Winner perspective
        w = row.get("winner_name")
        if pd.notna(w):
            w = str(w)
            _add(w, {
                "date":         date,
                "srv_pts":      _f(row, "w_svpt"),
                "srv_1st_in":   _f(row, "w_1stIn"),
                "srv_1st_won":  _f(row, "w_1stWon"),
                "srv_2nd_won":  _f(row, "w_2ndWon"),
                "aces":         _f(row, "w_ace"),
                "dfs":          _f(row, "w_df"),
                "srv_games":    _f(row, "w_SvGms"),
                "bp_faced":     _f(row, "w_bpFaced"),
                "bp_saved":     _f(row, "w_bpSaved"),
                # Return: opponent serve stats
                "ret_pts":      _f(row, "l_svpt"),
                "ret_won":      max(0, (_f(row,"l_svpt") or 0)
                                    - (_f(row,"l_1stWon") or 0)
                                    - (_f(row,"l_2ndWon") or 0)),
                "ret_games":    _f(row, "l_SvGms"),
                "ret_games_won": max(0, (_f(row,"l_bpFaced") or 0)
                                      - (_f(row,"l_bpSaved") or 0)),
                "_meta": {**meta_base, "result": "W",
                          "opponent": str(row.get("loser_name",""))},
            })

        # Loser perspective
        l = row.get("loser_name")
        if pd.notna(l):
            l = str(l)
            _add(l, {
                "date":         date,
                "srv_pts":      _f(row, "l_svpt"),
                "srv_1st_in":   _f(row, "l_1stIn"),
                "srv_1st_won":  _f(row, "l_1stWon"),
                "srv_2nd_won":  _f(row, "l_2ndWon"),
                "aces":         _f(row, "l_ace"),
                "dfs":          _f(row, "l_df"),
                "srv_games":    _f(row, "l_SvGms"),
                "bp_faced":     _f(row, "l_bpFaced"),
                "bp_saved":     _f(row, "l_bpSaved"),
                # Return: opponent (winner) serve stats
                "ret_pts":      _f(row, "w_svpt"),
                "ret_won":      max(0, (_f(row,"w_svpt") or 0)
                                    - (_f(row,"w_1stWon") or 0)
                                    - (_f(row,"w_2ndWon") or 0)),
                "ret_games":    _f(row, "w_SvGms"),
                "ret_games_won": max(0, (_f(row,"w_bpFaced") or 0)
                                      - (_f(row,"w_bpSaved") or 0)),
                "_meta": {**meta_base, "result": "L",
                          "opponent": str(row.get("winner_name",""))},
            })

    # Elo for adding to profiles
    mean_r = elo.mean_active_rating() if elo else None

    # Build profile for every target player
    all_profiles: Dict[str, dict] = {}

    for player_name in sorted(target_players):
        player_records = records.get(player_name, [])
        if not player_records:
            continue

        # Sort by date desc, take career_window most recent
        with_date = [(r["date"], r) for r in player_records if pd.notna(r["date"])]
        no_date   = [(pd.NaT, r) for r in player_records if not pd.notna(r["date"])]
        sorted_recs = sorted(with_date, key=lambda x: x[0], reverse=True) + no_date
        selected = [r for _, r in sorted_recs[:career_window]]

        profile = build_player_profile(player_name, selected, year)
        if not profile:
            continue

        # Attach Elo snapshot
        if elo is not None:
            snap = elo.get_snapshot_by_name(player_name)
            if snap:
                profile["elo_snapshot"] = snap
                r_a = snap.get("R_adjusted")
                profile["elo_win_prob_vs_avg"] = (
                    _elo_win_prob(r_a, mean_r) if r_a else None
                )

        all_profiles[player_name] = profile
        print(f"  {player_name:<30}  {len(selected):2d} matches  "
              f"(srv_pts={profile['n_srv_points']:4d}  conf={profile['confidence']})")

    return all_profiles


def _f(row, col) -> Optional[float]:
    """Safe float from DataFrame row."""
    v = row.get(col)
    try:
        return float(v) if v is not None and not (isinstance(v, float) and math.isnan(v)) else None
    except (TypeError, ValueError):
        return None


# ── save ──────────────────────────────────────────────────────────────────────

def save_profiles(profiles: dict, year: int) -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{year}_grass_profiles.json"
    for dest in [PROC_DIR / fname, OUT_DIR / fname]:
        dest.write_text(json.dumps(profiles, indent=2))
        print(f"Saved → {dest}")


# ── Elo loader (reuse fingerprint's) ─────────────────────────────────────────

def load_elo() -> Optional[GrassElo]:
    cached = PROC_DIR / "elo_ratings.json"
    if cached.exists():
        try:
            return GrassElo.load(cached)
        except Exception:
            pass
    grass_csv = PROC_DIR / "all_grass_matches.csv"
    if grass_csv.exists():
        return build_elo_from_csv(grass_csv)
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Build Tier 1 grass-season profiles from ATP match data"
    )
    parser.add_argument("--year", default="all",
                        help="Target year or 'all'")
    args = parser.parse_args()

    elo = load_elo()
    years = SUPPORTED_YEARS if args.year == "all" else [int(args.year)]

    for year in years:
        try:
            profiles = build_year_grass_profiles(year, elo=elo)
            save_profiles(profiles, year)
            print(f"  → {len(profiles)} grass profiles built for {year}")
        except Exception as exc:
            print(f"  [{year}] Error: {exc}")


if __name__ == "__main__":
    main()
