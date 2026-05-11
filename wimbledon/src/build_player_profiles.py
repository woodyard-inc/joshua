"""
Build per-player profiles from Wimbledon point-by-point data.
Supports 2017, 2018, 2019 (Jeff Sackmann / tennis_slam_pointbypoint).

Key columns used (all 100% populated in 2017-2019):
  ServeNumber   1=first, 2=second, 0=warmup/unknown
  RallyCount    actual shot count per point
  Speed_KMH     serve speed
  ServeWidth    W=Wide, C=Centre, B=Body, BW=BodyWide, BC=BodyCentre (55-77%)
  ServeDepth    NCTL / CTL (55-77%)
  P1/P2 Winner, UnfErr, BreakPoint*, PointsWon, Ace, DoubleFault  — all 100%

ServingTo is 0% populated → deuce/ad direction split marked not available.
Serve_Direction is 0% populated → direction comes from ServeWidth.

Output:
  docs/data/{year}_men_profiles.json
  docs/data/{year}_men_tournament.json

Usage:
    python src/build_player_profiles.py --year 2017
    python src/build_player_profiles.py --year 2018
    python src/build_player_profiles.py --year 2019
    python src/build_player_profiles.py --year all
"""

import argparse, json, math
from pathlib import Path
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
# Phase B: canonical name normalisation — applied when reading PBP player names
# so player_profiles keys match the canonical form used by all other outputs.
try:
    from canonical_names import normalize_name
except ImportError:
    def normalize_name(n): return n  # graceful fallback during testing


_ROUND_LABELS = {11: "R128", 12: "R64", 13: "R32", 14: "R16", 15: "QF", 16: "SF", 17: "F"}

def match_round(match_id: str) -> str:
    prefix = int(match_id.split("-")[-1]) // 100
    return _ROUND_LABELS.get(prefix, "?")


def parse_elapsed_mins(s):
    """Convert 'H:MM:SS' elapsed-time string to float minutes."""
    try:
        parts = str(s).strip().split(":")
        if len(parts) == 3:
            return round(int(parts[0]) * 60 + int(parts[1]) + int(parts[2]) / 60, 1)
    except Exception:
        pass
    return None

DATA_DIR        = Path(__file__).parent.parent / "data" / "raw"
OUT_DIR         = Path(__file__).parent.parent / "data"
MEN_MAX_MATCH   = 1999   # men's draw: 1101–1701; women's: 2101+
SUPPORTED_YEARS = [2017, 2018, 2019]

# ── helpers ────────────────────────────────────────────────────────────────

def safe(v):
    if v is None: return None
    if isinstance(v, float) and math.isnan(v): return None
    try:
        if np.isnan(v): return None
    except (TypeError, ValueError): pass
    if isinstance(v, np.integer): return int(v)
    if isinstance(v, np.floating): return float(v)
    return v

def r(v, n=1):
    if v is None: return None
    return round(float(v), n)

def pct(num, denom):
    if not denom: return None
    return round(num / denom * 100, 1)

def col_available(df, col):
    """True if column exists and has any non-null values."""
    return col in df.columns and df[col].notna().any()


# ── data loading ───────────────────────────────────────────────────────────

def load_year(year: int):
    pts_file = DATA_DIR / f"{year}-wimbledon-points.csv"
    mat_file = DATA_DIR / f"{year}-wimbledon-matches.csv"
    if not pts_file.exists():
        raise FileNotFoundError(f"Missing: {pts_file}")
    pts = pd.read_csv(pts_file, low_memory=False)
    mat = pd.read_csv(mat_file, low_memory=False)
    return pts, mat


def men_match_ids(mat: pd.DataFrame) -> set:
    mat = mat.copy()
    mat["_num"] = mat["match_id"].str.split("-").str[-1].astype(int)
    return set(mat[mat["_num"] <= MEN_MAX_MATCH]["match_id"])


def player_list(mat: pd.DataFrame, men_ids: set) -> dict:
    rows = mat[mat["match_id"].isin(men_ids)]
    players = {}
    for _, row in rows.iterrows():
        for side, num in [("player1", 1), ("player2", 2)]:
            raw = row[side]
            if pd.isna(raw): continue
            # Phase B: normalise PBP player name to the canonical spelling.
            name = normalize_name(str(raw))
            if name not in players:
                players[name] = {"matches": [], "side_in_match": {}}
            players[name]["matches"].append(row["match_id"])
            players[name]["side_in_match"][row["match_id"]] = num
    return players


# ── serve direction from ServeWidth ───────────────────────────────────────

def classify_serve_width(sw_series: pd.Series):
    """
    Map ServeWidth to Wide / Body / Centre counts.
    W=Wide, BW=Body-Wide (→Wide), C=Centre, B=Body, BC=Body-Centre (→Body)
    """
    sw = sw_series.dropna().astype(str).str.strip()
    wide   = int(sw.isin(["W", "BW"]).sum())
    body   = int(sw.isin(["B", "BC"]).sum())
    centre = int((sw == "C").sum())
    total  = wide + body + centre
    return wide, body, centre, total


# ── per-match profile ──────────────────────────────────────────────────────

def compute_player_profile(name: str, pn: int,
                           match_pts: pd.DataFrame) -> dict:
    op = 3 - pn

    # Deuce court = even point index within game, Ad court = odd
    match_pts = match_pts.copy()
    match_pts["_court"] = match_pts.groupby(["SetNo", "GameNo"]).cumcount() % 2
    # 0 → Deuce, 1 → Ad

    serving   = match_pts[match_pts["PointServer"] == pn].copy()
    returning = match_pts[match_pts["PointServer"] == op].copy()

    srv_total = len(serving)
    aces = int(serving[f"P{pn}Ace"].sum())
    dfs  = int(serving[f"P{pn}DoubleFault"].sum())

    # ── 1st / 2nd serve using ServeNumber (1=first, 2=second) ────────────
    sn = serving["ServeNumber"] if "ServeNumber" in serving.columns else pd.Series(dtype=float)
    is_1st = (sn == 1)
    is_2nd = (sn == 2)

    srv_1st_pts   = serving[is_1st]
    srv_2nd_pts   = serving[is_2nd]

    srv_1st_in    = int(is_1st.sum())
    srv_1st_won   = int((is_1st & (serving["PointWinner"] == pn)).sum())
    srv_2nd_in    = int(is_2nd.sum())            # 2nd serves that went in (ServeNumber==2 means it went in)
    srv_2nd_total = srv_2nd_in + dfs             # total 2nd-serve attempts
    srv_2nd_won   = int((is_2nd & (serving["PointWinner"] == pn)).sum())

    # ── serve speed ───────────────────────────────────────────────────────
    # Double faults have ServeNumber==0 so they are NOT in srv_1st_pts or
    # srv_2nd_pts.  Zero speeds are radar misses on valid serves.  Only omit
    # them when the match had at least one tracked (non-zero) speed reading;
    # all-zero matches → speed marked not available.
    if "Speed_KMH" in serving.columns:
        match_has_speed = bool((match_pts["Speed_KMH"] > 0).any())
        if match_has_speed:
            spd1_raw = srv_1st_pts["Speed_KMH"].dropna()
            spd2_raw = srv_2nd_pts["Speed_KMH"].dropna()
            spd1 = spd1_raw[spd1_raw > 0]
            spd2 = spd2_raw[spd2_raw > 0]
            spd_omitted = int((spd1_raw == 0).sum()) + int((spd2_raw == 0).sum())
        else:
            spd1 = spd2 = pd.Series(dtype=float)
            spd_omitted = 0
    else:
        match_has_speed = False
        spd1 = spd2 = pd.Series(dtype=float)
        spd_omitted = 0

    # ── serve direction (ServeWidth) ──────────────────────────────────────
    dir_avail = col_available(serving, "ServeWidth")
    if dir_avail:
        dir_wide, dir_body, dir_centre, dir_total = classify_serve_width(serving["ServeWidth"])
        # 1st / 2nd direction split (overall)
        d1_w, d1_b, d1_c, _ = classify_serve_width(srv_1st_pts.get("ServeWidth", pd.Series(dtype=str)))
        d2_w, d2_b, d2_c, _ = classify_serve_width(srv_2nd_pts.get("ServeWidth", pd.Series(dtype=str)))
        # Per-court direction: Deuce (even index) / Ad (odd index)
        is_deuce = serving["_court"] == 0
        is_ad    = serving["_court"] == 1
        dc1_w, dc1_b, dc1_c, _ = classify_serve_width(serving[is_deuce & is_1st].get("ServeWidth", pd.Series(dtype=str)))
        dc2_w, dc2_b, dc2_c, _ = classify_serve_width(serving[is_deuce & is_2nd].get("ServeWidth", pd.Series(dtype=str)))
        ac1_w, ac1_b, ac1_c, _ = classify_serve_width(serving[is_ad   & is_1st].get("ServeWidth", pd.Series(dtype=str)))
        ac2_w, ac2_b, ac2_c, _ = classify_serve_width(serving[is_ad   & is_2nd].get("ServeWidth", pd.Series(dtype=str)))
    else:
        dir_wide = dir_body = dir_centre = dir_total = 0
        d1_w = d1_b = d1_c = d2_w = d2_b = d2_c = 0
        dc1_w = dc1_b = dc1_c = dc2_w = dc2_b = dc2_c = 0
        ac1_w = ac1_b = ac1_c = ac2_w = ac2_b = ac2_c = 0

    # ── rally count (RallyCount — 100% populated in 2017-2019) ───────────
    rc_avail = col_available(match_pts, "RallyCount")
    if rc_avail:
        srv_rc   = serving["RallyCount"].dropna()
        ret_rc   = returning["RallyCount"].dropna()
        srv_rc1  = srv_1st_pts["RallyCount"].dropna()
        srv_rc2  = srv_2nd_pts["RallyCount"].dropna()
        ret_rc1  = returning[returning["ServeNumber"] == 1]["RallyCount"].dropna() if "ServeNumber" in returning.columns else pd.Series(dtype=float)
        ret_rc2  = returning[returning["ServeNumber"] == 2]["RallyCount"].dropna() if "ServeNumber" in returning.columns else pd.Series(dtype=float)
    else:
        srv_rc = ret_rc = srv_rc1 = srv_rc2 = ret_rc1 = ret_rc2 = pd.Series(dtype=float)

    # ── winners / UEs ─────────────────────────────────────────────────────
    winners = int(match_pts[f"P{pn}Winner"].sum())
    unf_err = int(match_pts[f"P{pn}UnfErr"].sum())
    net_pts = int(match_pts[f"P{pn}NetPoint"].sum())
    net_won = int(match_pts[f"P{pn}NetPointWon"].sum())

    # ── break points ──────────────────────────────────────────────────────
    bp_created   = int(match_pts[f"P{pn}BreakPoint"].sum())
    bp_converted = int(match_pts[(match_pts[f"P{pn}BreakPoint"] == 1) &
                                  (match_pts["PointWinner"] == pn)].shape[0])
    opp_sg       = match_pts[match_pts["PointServer"] == op].groupby(
                   ["SetNo", "GameNo"]).ngroups
    bp_faced     = int(match_pts[f"P{op}BreakPoint"].sum())
    bp_saved     = int(match_pts[(match_pts[f"P{op}BreakPoint"] == 1) &
                                  (match_pts["PointWinner"] == pn)].shape[0])

    # ── total points ──────────────────────────────────────────────────────
    pts_won   = int(match_pts[f"P{pn}PointsWon"].iloc[-1]) if len(match_pts) else 0
    pts_total = int(match_pts["P1PointsWon"].iloc[-1] +
                    match_pts["P2PointsWon"].iloc[-1]) if len(match_pts) else 0

    # ── match duration (ElapsedTime of last point) ────────────────────
    duration_mins = None
    if "ElapsedTime" in match_pts.columns:
        et_series = match_pts["ElapsedTime"].replace("0:00:00", None).dropna()
        if len(et_series):
            duration_mins = parse_elapsed_mins(et_series.iloc[-1])

    # ── distance run (P{n}DistanceRun — zeros = tracking not available) ──
    dist_col = f"P{pn}DistanceRun"
    distance_km = None
    if dist_col in match_pts.columns:
        dist_vals = match_pts[dist_col].fillna(0)
        if (dist_vals > 0).any():
            distance_km = round(float(dist_vals.sum()) / 1000, 2)

    # ── streaks (3+ consecutive wins within a single game) ───────────────
    # Run resets at every game boundary so a streak can't bridge games.
    streaks = 0
    for (_, _), game_pts in match_pts.groupby(["SetNo", "GameNo"]):
        wins_seq = (game_pts["PointWinner"] == pn).astype(int).tolist()
        run = 0
        for w in wins_seq:
            if w:
                run += 1
            else:
                if run >= 3: streaks += 1
                run = 0
        if run >= 3: streaks += 1

    # ── clean games ───────────────────────────────────────────────────────
    srv_games_clean = srv_games_total = 0
    ret_games_clean = ret_games_total = 0
    for (_, _), game_pts in match_pts.groupby(["SetNo", "GameNo"]):
        gw = game_pts["GameWinner"].iloc[-1] if "GameWinner" in game_pts else 0
        if gw == 0: continue
        server = game_pts["PointServer"].iloc[0]
        is_server = (server == pn)
        scores_p1 = game_pts["P1Score"].tolist()
        scores_p2 = game_pts["P2Score"].tolist()
        had_deuce = any(
            (str(s1) == "40" and str(s2) == "40") or str(s1) == "AD" or str(s2) == "AD"
            for s1, s2 in zip(scores_p1, scores_p2)
        )
        won = (gw == pn)
        if is_server:
            srv_games_total += 1
            if won and not had_deuce: srv_games_clean += 1
        elif won:
            ret_games_total += 1
            if not had_deuce: ret_games_clean += 1

    return {
        # serve counts
        "srv_total": srv_total, "aces": aces, "dfs": dfs,
        "srv_1st_in": srv_1st_in, "srv_1st_won": srv_1st_won,
        "srv_2nd_in": srv_2nd_in, "srv_2nd_total": srv_2nd_total, "srv_2nd_won": srv_2nd_won,
        # serve speed (sum / sumsq / n for later aggregation; zeros excluded)
        "spd1_sum": float(spd1.sum()), "spd1_sumsq": float((spd1**2).sum()), "spd1_n": int(len(spd1)),
        "spd2_sum": float(spd2.sum()), "spd2_sumsq": float((spd2**2).sum()), "spd2_n": int(len(spd2)),
        "spd_omitted": spd_omitted, "spd_match_has_speed": int(match_has_speed),
        # direction
        "dir_avail": int(dir_avail),
        "dir_wide": dir_wide, "dir_body": dir_body, "dir_centre": dir_centre, "dir_total": dir_total,
        "d1_wide": d1_w, "d1_body": d1_b, "d1_centre": d1_c,
        "d2_wide": d2_w, "d2_body": d2_b, "d2_centre": d2_c,
        # per-court direction
        "dc1_wide": dc1_w, "dc1_body": dc1_b, "dc1_centre": dc1_c,
        "dc2_wide": dc2_w, "dc2_body": dc2_b, "dc2_centre": dc2_c,
        "ac1_wide": ac1_w, "ac1_body": ac1_b, "ac1_centre": ac1_c,
        "ac2_wide": ac2_w, "ac2_body": ac2_b, "ac2_centre": ac2_c,
        # rally count
        "rc_avail": int(rc_avail),
        "rc_srv_sum": float(srv_rc.sum()),   "rc_srv_n": int(len(srv_rc)),
        "rc_srv1_sum": float(srv_rc1.sum()), "rc_srv1_n": int(len(srv_rc1)),
        "rc_srv2_sum": float(srv_rc2.sum()), "rc_srv2_n": int(len(srv_rc2)),
        "rc_ret_sum": float(ret_rc.sum()),   "rc_ret_n": int(len(ret_rc)),
        "rc_ret1_sum": float(ret_rc1.sum()), "rc_ret1_n": int(len(ret_rc1)),
        "rc_ret2_sum": float(ret_rc2.sum()), "rc_ret2_n": int(len(ret_rc2)),
        # attack
        "winners": winners, "unf_err": unf_err, "net_pts": net_pts, "net_won": net_won,
        # pressure
        "bp_created": bp_created, "bp_converted": bp_converted, "opp_sg": opp_sg,
        "bp_faced": bp_faced, "bp_saved": bp_saved,
        # totals
        "pts_won": pts_won, "pts_total": pts_total,
        # streaks / clean
        "streaks": streaks,
        "srv_clean": srv_games_clean, "srv_games": srv_games_total,
        "ret_clean": ret_games_clean, "ret_games_won": ret_games_total,
        # duration / distance (raw — may be None)
        "_duration_mins": duration_mins,
        "_distance_km":   distance_km,
    }


def _court_dir(agg, prefix, dir_ok):
    """Build deuce/ad direction dict from aggregated counts."""
    if not dir_ok:
        return {"available": False, "first_serve": None, "second_serve": None}
    def _pcts(w, b, c):
        tot = w + b + c
        if not tot: return None
        return {"wide_pct": pct(w, tot), "body_pct": pct(b, tot), "centre_pct": pct(c, tot)}
    srv1 = _pcts(agg[f"{prefix}1_wide"], agg[f"{prefix}1_body"], agg[f"{prefix}1_centre"])
    srv2 = _pcts(agg[f"{prefix}2_wide"], agg[f"{prefix}2_body"], agg[f"{prefix}2_centre"])
    return {"available": srv1 is not None, "first_serve": srv1, "second_serve": srv2}


# ── duration / distance helpers ────────────────────────────────────────────

def _agg_duration(match_results):
    vals = [d["_duration_mins"] for d in match_results if d.get("_duration_mins") is not None]
    if not vals:
        return {"available": False, "avg_mins": None, "total_mins": None, "matches_tracked": 0}
    avg_mins = round(sum(vals) / len(vals), 0)
    return {
        "available":       True,
        "avg_mins":        int(avg_mins),
        "total_mins":      int(sum(vals)),
        "matches_tracked": len(vals),
    }


def _agg_distance(match_results):
    vals = [d["_distance_km"] for d in match_results if d.get("_distance_km") is not None]
    if not vals:
        return {"available": False, "avg_km_per_match": None, "total_km": None,
                "matches_tracked": 0, "matches_total": len(match_results)}
    avg_km = round(sum(vals) / len(vals), 2)
    return {
        "available":        True,
        "avg_km_per_match": avg_km,
        "total_km":         round(sum(vals), 2),
        "matches_tracked":  len(vals),
        "matches_total":    len(match_results),
    }


# ── aggregation ────────────────────────────────────────────────────────────

def aggregate_matches(match_results: list, year: int) -> dict:
    agg = {}
    for d in match_results:
        for k, v in d.items():
            agg[k] = agg.get(k, 0) + (v or 0)

    srv   = agg["srv_total"]
    s1in  = agg["srv_1st_in"]
    s2in  = agg["srv_2nd_in"]
    s2tot = agg["srv_2nd_total"]

    def mean_sd(s, sq, n):
        if n < 2: return (r(s/n, 1) if n else None, None)
        mean = s / n
        var  = max(0, sq/n - mean**2)
        return (r(mean, 1), r(math.sqrt(var), 1))

    spd1_mean, spd1_sd = mean_sd(agg["spd1_sum"], agg["spd1_sumsq"], agg["spd1_n"])
    spd2_mean, spd2_sd = mean_sd(agg["spd2_sum"], agg["spd2_sumsq"], agg["spd2_n"])

    dir_ok  = agg["dir_avail"] > 0
    dir_tot = agg["dir_total"]
    d1_tot  = agg["d1_wide"] + agg["d1_body"] + agg["d1_centre"]
    d2_tot  = agg["d2_wide"] + agg["d2_body"] + agg["d2_centre"]

    rc_ok = agg["rc_avail"] > 0

    agg_index = agg["winners"] + agg["unf_err"]

    return {
        "matches_played": len(match_results),
        "serve": {
            "total_pts":      srv,
            "first_in_pct":   pct(s1in, srv),
            "first_won_pct":  pct(agg["srv_1st_won"], s1in),
            "second_in_pct":  pct(s2in, s2tot) if s2tot > 0 else None,
            "second_won_pct": pct(agg["srv_2nd_won"], s2in) if s2in > 0 else None,
            "ace_pct":        pct(agg["aces"], srv),
            "df_pct":         pct(agg["dfs"], srv),
            "aces_total":     int(agg["aces"]),
            "dfs_total":      int(agg["dfs"]),
        },
        "serve_speed": {
            "available":          bool(agg["spd1_n"] > 0),
            "first_avg_kmh":      spd1_mean, "first_sd_kmh":  spd1_sd,
            "second_avg_kmh":     spd2_mean, "second_sd_kmh": spd2_sd,
            "first_avg_mph":      r(spd1_mean * 0.621371, 1) if spd1_mean else None,
            "second_avg_mph":     r(spd2_mean * 0.621371, 1) if spd2_mean else None,
            # zero-speed radar misses omitted (double faults excluded — ServeNumber==0)
            "omitted_zero_count": int(agg["spd_omitted"]),
            "matches_with_speed": int(agg["spd_match_has_speed"]),
        },
        "serve_direction": {
            "available":  dir_ok,
            "source":     "ServeWidth" if dir_ok else None,
            "wide_pct":   pct(agg["dir_wide"],   dir_tot) if dir_ok else None,
            "body_pct":   pct(agg["dir_body"],   dir_tot) if dir_ok else None,
            "centre_pct": pct(agg["dir_centre"], dir_tot) if dir_ok else None,
            "counts": {
                "wide": agg["dir_wide"], "body": agg["dir_body"], "centre": agg["dir_centre"]
            } if dir_ok else None,
            "first_serve": {
                "wide_pct":   pct(agg["d1_wide"],   d1_tot),
                "body_pct":   pct(agg["d1_body"],   d1_tot),
                "centre_pct": pct(agg["d1_centre"], d1_tot),
            } if dir_ok and d1_tot > 0 else None,
            "second_serve": {
                "wide_pct":   pct(agg["d2_wide"],   d2_tot),
                "body_pct":   pct(agg["d2_body"],   d2_tot),
                "centre_pct": pct(agg["d2_centre"], d2_tot),
            } if dir_ok and d2_tot > 0 else None,
            "deuce": _court_dir(agg, "dc", dir_ok),
        "ad":    _court_dir(agg, "ac", dir_ok),
        },
        "rally_shots": {
            "available":    rc_ok,
            "srv_all_avg":  r(agg["rc_srv_sum"]  / agg["rc_srv_n"],  1) if rc_ok and agg["rc_srv_n"]  else None,
            "srv_1st_avg":  r(agg["rc_srv1_sum"] / agg["rc_srv1_n"], 1) if rc_ok and agg["rc_srv1_n"] else None,
            "srv_2nd_avg":  r(agg["rc_srv2_sum"] / agg["rc_srv2_n"], 1) if rc_ok and agg["rc_srv2_n"] else None,
            "ret_all_avg":  r(agg["rc_ret_sum"]  / agg["rc_ret_n"],  1) if rc_ok and agg["rc_ret_n"]  else None,
            "ret_1st_avg":  r(agg["rc_ret1_sum"] / agg["rc_ret1_n"], 1) if rc_ok and agg["rc_ret1_n"] else None,
            "ret_2nd_avg":  r(agg["rc_ret2_sum"] / agg["rc_ret2_n"], 1) if rc_ok and agg["rc_ret2_n"] else None,
        },
        "pressure": {
            "bp_created":            int(agg["bp_created"]),
            "bp_converted":          int(agg["bp_converted"]),
            "bp_conv_pct":           pct(agg["bp_converted"], agg["bp_created"]),
            "bp_created_per_opp_sg": r(agg["bp_created"] / agg["opp_sg"], 2) if agg["opp_sg"] else None,
            "bp_faced":              int(agg["bp_faced"]),
            "bp_saved":              int(agg["bp_saved"]),
            "bp_saved_pct":          pct(agg["bp_saved"], agg["bp_faced"]),
        },
        "net": {
            "net_pts":     int(agg["net_pts"]),
            "net_won":     int(agg["net_won"]),
            "net_won_pct": pct(agg["net_won"], agg["net_pts"]),
        },
        "aggression": {
            "winners":          int(agg["winners"]),
            "unf_err":          int(agg["unf_err"]),
            "aggression_index": r(agg["winners"] / agg_index * 100, 1) if agg_index else None,
        },
        "points": {
            "total_won":    int(agg["pts_won"]),
            "total_played": int(agg["pts_total"]),
            "win_pct":      pct(agg["pts_won"], agg["pts_total"]),
        },
        "clean_games": {
            "srv_clean":     int(agg["srv_clean"]),
            "srv_games":     int(agg["srv_games"]),
            "srv_clean_pct": pct(agg["srv_clean"], agg["srv_games"]),
            "ret_clean":     int(agg["ret_clean"]),
            "ret_games_won": int(agg["ret_games_won"]),
            "ret_clean_pct": pct(agg["ret_clean"], agg["ret_games_won"]),
        },
        "streaks": {
            "total_streaks":     int(agg["streaks"]),
            "streaks_per_match": r(agg["streaks"] / len(match_results), 1),
        },
        "match_duration": _agg_duration(match_results),
        "distance":        _agg_distance(match_results),
    }


# ── bad-break detection ────────────────────────────────────────────────────

def bad_break_check(pts, mat, men_ids):
    result = {}
    men_pts = pts[pts["match_id"].isin(men_ids)]
    for mid, grp in men_pts.groupby("match_id"):
        last = grp.iloc[-1]
        p1_pts = int(last["P1PointsWon"])
        p2_pts = int(last["P2PointsWon"])
        match_row = mat[mat["match_id"] == mid]
        if match_row.empty: continue
        p1_sets = grp.groupby("SetNo")["SetWinner"].last()
        p1_sets_won = (p1_sets == 1).sum()
        p2_sets_won = (p1_sets == 2).sum()
        if p1_sets_won > p2_sets_won:
            winner_pts, loser_pts = p1_pts, p2_pts
            winner_name = match_row.iloc[0]["player1"]
            loser_name  = match_row.iloc[0]["player2"]
        elif p2_sets_won > p1_sets_won:
            winner_pts, loser_pts = p2_pts, p1_pts
            winner_name = match_row.iloc[0]["player2"]
            loser_name  = match_row.iloc[0]["player1"]
        else:
            continue
        result[mid] = {
            "winner": winner_name, "loser": loser_name,
            "winner_pts": winner_pts, "loser_pts": loser_pts,
            "bad_break": winner_pts < loser_pts,
            "margin": winner_pts - loser_pts,
        }
    return result


# ── build one year ─────────────────────────────────────────────────────────

def build_year(year: int, out_dir: Path):
    print(f"\n=== {year} ===")
    pts, mat = load_year(year)
    men_ids  = men_match_ids(mat)
    men_pts  = pts[pts["match_id"].isin(men_ids)].copy()
    players  = player_list(mat, men_ids)
    bad_breaks = bad_break_check(pts, mat, men_ids)

    print(f"  Men's matches: {len(men_ids)}  Points: {len(men_pts):,}  Players: {len(players)}")

    profiles = {}
    for player_name, info in players.items():
        match_results  = []
        match_summaries = []
        for mid in info["matches"]:
            pn = info["side_in_match"][mid]
            mp = men_pts[men_pts["match_id"] == mid]
            if len(mp) == 0: continue
            mi = mat[mat["match_id"] == mid].iloc[0]
            res = compute_player_profile(player_name, pn, mp)
            match_results.append(res)

            bb = bad_breaks.get(mid, {})
            opponent = mi["player2"] if pn == 1 else mi["player1"]
            match_summaries.append({
                "match_id": mid,
                "round":    match_round(mid),
                "opponent": opponent,
                "points_won":    res["pts_won"],
                "points_played": res["pts_total"],
                "duration_mins": res["_duration_mins"],
                "distance_km":   res["_distance_km"],
                "bad_break_match": bb.get("bad_break", False),
                "bad_break_detail": bb if bb.get("bad_break") else None,
            })

        if not match_results: continue

        agg = aggregate_matches(match_results, year)
        agg["player"] = player_name
        agg["year"]   = year
        agg["match_summaries"] = match_summaries
        profiles[player_name] = agg

    # ── tournament averages ───────────────────────────────────────────────
    def avg(vals):
        v = [x for x in vals if x is not None]
        return r(sum(v) / len(v), 1) if v else None

    tourn = {
        "year": year, "n_players": len(profiles),
        "serve": {
            "first_in_pct":   avg([p["serve"]["first_in_pct"]   for p in profiles.values()]),
            "first_won_pct":  avg([p["serve"]["first_won_pct"]  for p in profiles.values()]),
            "second_won_pct": avg([p["serve"]["second_won_pct"] for p in profiles.values()]),
            "ace_pct":        avg([p["serve"]["ace_pct"]        for p in profiles.values()]),
            "df_pct":         avg([p["serve"]["df_pct"]         for p in profiles.values()]),
        },
        "serve_speed": {
            "first_avg_kmh":  avg([p["serve_speed"]["first_avg_kmh"]  for p in profiles.values()]),
            "second_avg_kmh": avg([p["serve_speed"]["second_avg_kmh"] for p in profiles.values()]),
        },
        "serve_direction": {
            "wide_pct":   avg([p["serve_direction"]["wide_pct"]   for p in profiles.values()]),
            "body_pct":   avg([p["serve_direction"]["body_pct"]   for p in profiles.values()]),
            "centre_pct": avg([p["serve_direction"]["centre_pct"] for p in profiles.values()]),
        },
        "rally_shots": {
            "srv_all_avg": avg([p["rally_shots"]["srv_all_avg"] for p in profiles.values()]),
            "srv_1st_avg": avg([p["rally_shots"]["srv_1st_avg"] for p in profiles.values()]),
            "srv_2nd_avg": avg([p["rally_shots"]["srv_2nd_avg"] for p in profiles.values()]),
            "ret_all_avg": avg([p["rally_shots"]["ret_all_avg"] for p in profiles.values()]),
            "ret_1st_avg": avg([p["rally_shots"]["ret_1st_avg"] for p in profiles.values()]),
            "ret_2nd_avg": avg([p["rally_shots"]["ret_2nd_avg"] for p in profiles.values()]),
        },
        "pressure": {
            "bp_created_per_opp_sg": avg([p["pressure"]["bp_created_per_opp_sg"] for p in profiles.values()]),
            "bp_saved_pct":          avg([p["pressure"]["bp_saved_pct"]           for p in profiles.values()]),
            "bp_conv_pct":           avg([p["pressure"]["bp_conv_pct"]            for p in profiles.values()]),
        },
        "aggression": {
            "aggression_index": avg([p["aggression"]["aggression_index"] for p in profiles.values()]),
        },
        "clean_games": {
            "srv_clean_pct": avg([p["clean_games"]["srv_clean_pct"] for p in profiles.values()]),
            "ret_clean_pct": avg([p["clean_games"]["ret_clean_pct"] for p in profiles.values()]),
        },
        "streaks": {
            "streaks_per_match": avg([p["streaks"]["streaks_per_match"] for p in profiles.values()]),
        },
        "bad_breaks": [v for v in bad_breaks.values() if v["bad_break"]],
        "match_duration": {
            "avg_mins": avg([p["match_duration"]["avg_mins"] for p in profiles.values()
                             if p["match_duration"]["available"]]),
        },
        "distance": {
            "avg_km_per_match": avg([p["distance"]["avg_km_per_match"] for p in profiles.values()
                                     if p["distance"]["available"]]),
        },
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{year}_men_profiles.json").write_text(
        json.dumps(profiles, indent=2, default=str))
    (out_dir / f"{year}_men_tournament.json").write_text(
        json.dumps(tourn, indent=2, default=str))
    print(f"  → {out_dir}/{year}_men_profiles.json  ({len(profiles)} players)")
    return profiles, tourn


# ── entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", default="2017",
                        help="Year to process, or 'all' for 2017+2018+2019")
    args = parser.parse_args()
    years = SUPPORTED_YEARS if args.year == "all" else [int(args.year)]
    for y in years:
        try:
            build_year(y, OUT_DIR)
        except FileNotFoundError as e:
            print(f"  Skipping {y}: {e}")

if __name__ == "__main__":
    main()
