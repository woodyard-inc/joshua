"""
Player fingerprint builder.

Builds a weighted player fingerprint for each player × year combination
in the Wimbledon draw, using:

  Recency decay:   w_t = exp(-DECAY_LAMBDA * days_before_tournament / 365)
  Quality weight:  w_q = R_adj_opponent / mean(R_active_grass)
  Combined:        w   = w_t * w_q  (normalised to sum=1 across training matches)

Tier 1 metrics (FSPW%, SSPW%, RPW%, SGW%, RGW%) are computed with
90% credible intervals from a Beta-Binomial model (prior Beta(2,2)).

Tier 2 metrics (serve entropy, rally win curve, clutch differential,
break-point creation profile, momentum profile, DF pressure delta,
court-side asymmetry) are derived from the weighted point-by-point data.

Input:
    data/raw/{year}-wimbledon-points.csv      ← Sackmann slam pbp data
    data/raw/{year}-wimbledon-matches.csv
    data/processed/all_grass_matches.csv      ← for Elo (from data_prep.py)
    data/processed/elo_ratings.json           ← pre-built (optional fast path)

Output:
    data/processed/{year}_fingerprints.json
    docs/data/{year}_fingerprints.json

Usage:
    python src/fingerprint.py --year 2017
    python src/fingerprint.py --year all
    python src/fingerprint.py --year 2019 --no_elo   # skip Elo weighting
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
from scipy import stats

# Local imports — run from project root
sys.path.insert(0, str(Path(__file__).parent))
from elo import GrassElo, build_from_csv as build_elo_from_csv
from build_player_profiles import (
    load_year, men_match_ids, player_list,
    classify_serve_width, parse_elapsed_mins, match_round,
    col_available, pct, safe,
)


# ── constants ──────────────────────────────────────────────────────────────

DATA_DIR        = Path(__file__).parent.parent / "data" / "raw"
PROC_DIR        = Path(__file__).parent.parent / "data" / "processed"
OUT_DIR         = Path(__file__).parent.parent / "data"
SUPPORTED_YEARS = [2017, 2018, 2019]

DECAY_LAMBDA      = 0.5    # recency half-life ≈ 1.4 years
CI_LEVEL          = 0.90   # credible interval coverage
BETA_PRIOR_A      = 2.0    # Beta(2,2) prior — slightly centred, weak
BETA_PRIOR_B      = 2.0
RALLY_BANDS       = [(1, 3), (4, 6), (7, 9), (10, 999)]
RALLY_LABELS      = ["1_3", "4_6", "7_9", "10+"]

# Session decision 4: point-level n_eff thresholds
CONF_UNRELIABLE   = 30     # n_eff below this → UNRELIABLE, exclude from matchup
CONF_LOW          = 60     # n_eff 30–60 → LOW CONFIDENCE, widen interval

# Session decision 3: match-count thresholds
MIN_MATCHES_TIER2 = 5      # below this, Tier 1 only
FULL_MATCHES      = 8      # 8–10 = full fingerprint viable

# Session decision 10: verdict thresholds (raw values, not percentages)
VERDICT_THRESHOLDS = {
    "clutch_diff":       (-4.0,  "lt"),   # Clutch Diff < -4pp → FAIL
    "serve_speed_delta": (-8.0,  "lt"),   # Speed delta < -8 km/h → FAIL
    "df_rate_doubles":   (2.0,   "gt"),   # DF rate ratio > 2× → FAIL
    "spci":              (-0.06, "lt"),   # SPCI < -0.06 → FAIL
}

# Session decision 9: SPCI component weights
SPCI_WEIGHTS = {
    "serve_speed_delta": 0.30,
    "df_rate_delta":     0.20,
    # rally_length_at_pressure: 0.20 — needs per-point tracking (future)
    # distance_per_shot_pressure: 0.15 — needs GPS data (future)
    # serve_dir_variety_pressure: 0.15 — needs per-point entropy (future)
}


# ── credible intervals ─────────────────────────────────────────────────────

def beta_ci(wins: float, total: float,
            level: float = CI_LEVEL,
            prior_a: float = BETA_PRIOR_A,
            prior_b: float = BETA_PRIOR_B) -> tuple[float, float]:
    """
    Beta-Binomial 90% credible interval.
    Accepts weighted pseudo-counts (non-integer wins/total are fine).
    """
    a = prior_a + max(wins, 0.0)
    b = prior_b + max(total - wins, 0.0)
    lo_q = (1.0 - level) / 2.0
    hi_q = 1.0 - lo_q
    lo = round(stats.beta.ppf(lo_q, a, b) * 100, 1)
    hi = round(stats.beta.ppf(hi_q, a, b) * 100, 1)
    return (lo, hi)


def metric(value: Optional[float],
           ci_lo: Optional[float] = None,
           ci_hi: Optional[float] = None) -> dict:
    return {"value": safe(value), "ci_90_lo": safe(ci_lo), "ci_90_hi": safe(ci_hi)}


def pct_metric(wins_w: float, total_w: float) -> dict:
    """Weighted percentage + CI, or null metric if no data."""
    if not total_w:
        return metric(None)
    val = round(wins_w / total_w * 100, 1)
    lo, hi = beta_ci(wins_w, total_w)
    return metric(val, lo, hi)


# ── Session decision 4: n_eff + confidence ─────────────────────────────────

def kish_n_eff(weights: List[float]) -> float:
    """
    Kish (1965) effective sample size for weighted estimates.
    n_eff = (Σwᵢ)² / Σwᵢ²   (applied to raw, unnormalised weights)
    Represents how many equally-weighted samples the weighted set is worth.
    """
    sw  = sum(weights)
    sw2 = sum(w * w for w in weights)
    return round((sw ** 2) / sw2, 1) if sw2 > 0 else 0.0


def confidence_flag(n_eff: float) -> str:
    """
    Three-tier per-metric confidence flag (session decision 4).
      n_eff < 30  → UNRELIABLE  (exclude from matchup)
      n_eff 30–60 → LOW         (widen interval, FAIL → MARGINAL)
      n_eff > 60  → RELIABLE
    n_eff here is the raw or pseudo-count of points underpinning the metric,
    not the match-level Kish n_eff.
    """
    if n_eff < CONF_UNRELIABLE:
        return "UNRELIABLE"
    if n_eff < CONF_LOW:
        return "LOW"
    return "RELIABLE"


def pressure_verdict(value: Optional[float], key: str, conf: str) -> str:
    """
    Threshold-based PASS/FAIL label (session decision 10).
    Returns MARGINAL instead of FAIL when confidence is LOW.
    Returns UNRELIABLE when confidence is UNRELIABLE.
    """
    if value is None or conf == "UNRELIABLE":
        return "UNRELIABLE"
    rule = VERDICT_THRESHOLDS.get(key)
    if rule is None:
        return "N/A"
    thresh, op = rule
    fails = (value < thresh) if op == "lt" else (value > thresh)
    if fails:
        return "MARGINAL" if conf == "LOW" else "FAIL"
    return "PASS"


# ── Tier 2 helpers ─────────────────────────────────────────────────────────

def shannon_entropy(counts: dict) -> Optional[float]:
    """Shannon entropy (bits) for a direction distribution."""
    total = sum(counts.values())
    if not total:
        return None
    probs = [v / total for v in counts.values() if v > 0]
    return round(-sum(p * math.log2(p) for p in probs), 4)


def is_high_leverage(row: pd.Series, player_num: int) -> bool:
    """
    True when this point is high-leverage for the player:
      - Player is returning and opponent is at a break point
      - Score is deuce or advantage
    """
    try:
        opp = 3 - player_num
        # Break point situation for the player (they are returning)
        is_returner = (row["PointServer"] == opp)
        bp_flag = bool(row.get(f"P{player_num}BreakPoint", 0))

        # Deuce / advantage detection
        s1, s2 = str(row.get("P1Score", "")), str(row.get("P2Score", ""))
        at_deuce = (s1 == "40" and s2 == "40") or s1 == "AD" or s2 == "AD"

        return (is_returner and bp_flag) or at_deuce
    except Exception:
        return False


# ── per-match feature computation ──────────────────────────────────────────

def compute_match_features(player_name: str, player_num: int,
                            match_pts: pd.DataFrame) -> dict:
    """
    Compute all Tier 1 + Tier 2 raw counts from a single match's
    point-by-point data. Returns a flat dict of numeric accumulators
    suitable for weighted aggregation.
    """
    pn  = player_num
    opp = 3 - pn

    # Deuce/Ad court derivation (even index within game = Deuce)
    match_pts = match_pts.copy()
    match_pts["_court"] = match_pts.groupby(["SetNo", "GameNo"]).cumcount() % 2

    serving   = match_pts[match_pts["PointServer"] == pn]
    returning = match_pts[match_pts["PointServer"] == opp]

    sn     = serving.get("ServeNumber", pd.Series(dtype=float))
    is_1st = (sn == 1)
    is_2nd = (sn == 2)
    srv_1st = serving[is_1st]
    srv_2nd = serving[is_2nd]

    # ── Tier 1 raw counts ──────────────────────────────────────────────────

    srv_total   = len(serving)
    aces        = int(serving.get(f"P{pn}Ace", pd.Series(0)).sum())
    dfs         = int(serving.get(f"P{pn}DoubleFault", pd.Series(0)).sum())

    srv_1st_in  = int(is_1st.sum())
    srv_1st_won = int((is_1st & (serving["PointWinner"] == pn)).sum())
    srv_2nd_in  = int(is_2nd.sum())
    srv_2nd_won = int((is_2nd & (serving["PointWinner"] == pn)).sum())
    srv_2nd_tot = srv_2nd_in + dfs

    ret_total   = len(returning)
    ret_won     = int((returning["PointWinner"] == pn).sum())

    # Service / return games
    srv_games = ret_games = srv_games_won = ret_games_won = 0
    for (_, _), gpts in match_pts.groupby(["SetNo", "GameNo"]):
        gw = gpts["GameWinner"].iloc[-1] if "GameWinner" in gpts.columns else 0
        if gw == 0:
            continue
        server = gpts["PointServer"].iloc[0]
        won    = (gw == pn)
        if server == pn:
            srv_games += 1
            if won:
                srv_games_won += 1
        else:
            ret_games += 1
            if won:
                ret_games_won += 1

    # ── Tier 2: serve entropy ──────────────────────────────────────────────

    dir_avail = col_available(serving, "ServeWidth")
    if dir_avail:
        w, b, c, _ = classify_serve_width(serving.get("ServeWidth", pd.Series(dtype=str)))
        entropy = shannon_entropy({"wide": w, "body": b, "centre": c}) or 0.0
    else:
        w = b = c = 0
        entropy = 0.0

    # ── Tier 2: rally win curve ────────────────────────────────────────────

    rc_col   = "RallyCount"
    rc_avail = col_available(match_pts, rc_col)
    rally_wins  = {label: 0 for label in RALLY_LABELS}
    rally_total = {label: 0 for label in RALLY_LABELS}

    if rc_avail:
        for _, row in match_pts.iterrows():
            rc  = row.get(rc_col)
            win = (row["PointWinner"] == pn)
            if pd.isna(rc):
                continue
            rc = int(rc)
            for (lo, hi), label in zip(RALLY_BANDS, RALLY_LABELS):
                if lo <= rc <= hi:
                    rally_total[label] += 1
                    if win:
                        rally_wins[label] += 1
                    break

    # ── Tier 2: RLUEP (Rally-Length UFE Profile) ──────────────────────────
    # Decision 12: UFE rate per rally band; primary value in 7–9 and 10+ bands.
    # Denominator reuses rally_total (points per band) from rally win curve above.

    ufe_col   = f"P{pn}UnforcedError"
    ufe_avail = col_available(match_pts, ufe_col) and rc_avail
    rluep_ufe = {label: 0 for label in RALLY_LABELS}

    if ufe_avail:
        for _, row in match_pts.iterrows():
            rc  = row.get(rc_col)
            ufe = bool(row.get(ufe_col, 0))
            if pd.isna(rc) or not ufe:
                continue
            rc = int(rc)
            for (lo, hi), label in zip(RALLY_BANDS, RALLY_LABELS):
                if lo <= rc <= hi:
                    rluep_ufe[label] += 1
                    break

    # ── Tier 2: clutch differential ───────────────────────────────────────

    hl_total = hl_won = 0
    for _, row in match_pts.iterrows():
        win = (row["PointWinner"] == pn)
        if is_high_leverage(row, pn):
            hl_total += 1
            if win:
                hl_won += 1

    # ── Tier 2: DF pressure delta ──────────────────────────────────────────
    # Compare DF rate when player faces a break point (server) vs overall

    bp_serve_total = bp_serve_df = 0
    for _, row in serving.iterrows():
        is_bp_against = bool(row.get(f"P{opp}BreakPoint", 0))
        is_df         = bool(row.get(f"P{pn}DoubleFault", 0))
        if is_bp_against:
            bp_serve_total += 1
            if is_df:
                bp_serve_df += 1

    # ── Tier 2: serve speed courage index ─────────────────────────────────
    # Mean speed on break-point-against serve vs overall mean serve speed

    spd_avail   = col_available(serving, "Speed_KMH")
    spd_all_sum = spd_all_n = 0
    spd_bp_sum  = spd_bp_n  = 0

    if spd_avail:
        has_speed = bool((match_pts["Speed_KMH"] > 0).any())
        if has_speed:
            for _, row in serving.iterrows():
                spd = row.get("Speed_KMH", 0)
                if pd.isna(spd) or spd <= 0:
                    continue
                spd_all_sum += spd
                spd_all_n   += 1
                if bool(row.get(f"P{opp}BreakPoint", 0)):
                    spd_bp_sum += spd
                    spd_bp_n   += 1

    # ── Tier 2: court-side asymmetry ──────────────────────────────────────

    is_deuce = match_pts["_court"] == 0
    is_ad    = match_pts["_court"] == 1
    dc_total = int(is_deuce.sum())
    dc_won   = int((is_deuce & (match_pts["PointWinner"] == pn)).sum())
    ac_total = int(is_ad.sum())
    ac_won   = int((is_ad & (match_pts["PointWinner"] == pn)).sum())

    # ── Tier 2: momentum profile ──────────────────────────────────────────
    # Computed per-game; resets at game boundary.

    streak_init_3    = 0   # player 3+ streaks started
    streak_surv_4    = 0   # of those, how many reached 4
    opp_streak_total = 0   # opponent 3+ streaks that ended (recovery opportunity)
    opp_streak_won   = 0   # player won the point immediately after

    for (_, _), gpts in match_pts.groupby(["SetNo", "GameNo"]):
        p_run = 0
        o_run = 0
        prev_opp_ended = False

        for _, row in gpts.iterrows():
            win = (row["PointWinner"] == pn)

            # Recovery opportunity: point right after opponent's 3+ ended
            if prev_opp_ended:
                opp_streak_total += 1
                if win:
                    opp_streak_won += 1
                prev_opp_ended = False

            if win:
                p_run += 1
                if p_run == 3:
                    streak_init_3 += 1
                if p_run == 4:
                    streak_surv_4 += 1
                # opponent streak just reset
                if o_run >= 3:
                    prev_opp_ended = True
                o_run = 0
            else:
                o_run += 1
                if o_run == 3:
                    pass   # could track opponent initiation here
                # player streak just reset
                p_run = 0

    # ── Tier 2: break point creation profile ─────────────────────────────

    bp_created   = int(match_pts.get(f"P{pn}BreakPoint", pd.Series(0)).sum())
    bp_converted = int(
        match_pts[
            (match_pts.get(f"P{pn}BreakPoint", 0) == 1) &
            (match_pts["PointWinner"] == pn)
        ].shape[0]
    )
    bp_faced  = int(match_pts.get(f"P{opp}BreakPoint", pd.Series(0)).sum())
    bp_saved  = int(
        match_pts[
            (match_pts.get(f"P{opp}BreakPoint", 0) == 1) &
            (match_pts["PointWinner"] == pn)
        ].shape[0]
    )

    # ── totals ─────────────────────────────────────────────────────────────

    pts_won   = int(match_pts[f"P{pn}PointsWon"].iloc[-1]) if len(match_pts) else 0
    pts_total = int(match_pts["P1PointsWon"].iloc[-1] +
                    match_pts["P2PointsWon"].iloc[-1]) if len(match_pts) else 0

    return {
        # Tier 1 serve
        "srv_total":    srv_total,
        "srv_1st_in":   srv_1st_in,   "srv_1st_won":  srv_1st_won,
        "srv_2nd_in":   srv_2nd_in,   "srv_2nd_won":  srv_2nd_won,
        "srv_2nd_tot":  srv_2nd_tot,
        "aces": aces, "dfs": dfs,
        # Tier 1 return / games
        "ret_total": ret_total, "ret_won": ret_won,
        "srv_games": srv_games, "srv_games_won": srv_games_won,
        "ret_games": ret_games, "ret_games_won": ret_games_won,
        # Tier 2 serve direction entropy
        "dir_avail": int(dir_avail),
        "dir_wide": w, "dir_body": b, "dir_centre": c,
        "serve_entropy": entropy,
        # Tier 2 rally win curve
        "rc_avail": int(rc_avail),
        **{f"rw_{lbl}":   rally_wins[lbl]  for lbl in RALLY_LABELS},
        **{f"rt_{lbl}":   rally_total[lbl] for lbl in RALLY_LABELS},
        # Tier 2 RLUEP (UFE per rally band)
        "ufe_avail": int(ufe_avail),
        **{f"rufe_{lbl}": rluep_ufe[lbl]  for lbl in RALLY_LABELS},
        # Tier 2 clutch
        "hl_total": hl_total, "hl_won": hl_won,
        # Tier 2 DF pressure delta
        "bp_srv_total": bp_serve_total, "bp_srv_df": bp_serve_df,
        # Tier 2 serve speed courage
        "spd_avail": int(spd_avail),
        "spd_all_sum": spd_all_sum, "spd_all_n": spd_all_n,
        "spd_bp_sum":  spd_bp_sum,  "spd_bp_n":  spd_bp_n,
        # Tier 2 court-side
        "dc_total": dc_total, "dc_won": dc_won,
        "ac_total": ac_total, "ac_won": ac_won,
        # Tier 2 momentum
        "streak_init_3":    streak_init_3,
        "streak_surv_4":    streak_surv_4,
        "opp_streak_total": opp_streak_total,
        "opp_streak_won":   opp_streak_won,
        # Tier 2 break point creation
        "bp_created": bp_created, "bp_converted": bp_converted,
        "bp_faced":   bp_faced,   "bp_saved":     bp_saved,
        # totals
        "pts_won": pts_won, "pts_total": pts_total,
    }


# ── weighted aggregation ───────────────────────────────────────────────────

def weighted_sum(features: List[dict], weights: List[float], key: str) -> float:
    """Sum of feature[key] * weight across all matches."""
    return sum(f.get(key, 0) * w for f, w in zip(features, weights))


def build_fingerprint(player_name: str, year: int,
                      match_features: List[dict],
                      match_meta: List[dict],
                      weights_raw: List[float]) -> dict:
    """
    Aggregate per-match feature dicts into a single player fingerprint
    using normalised weights.

    match_meta entries: {match_id, round, opponent_name, opponent_elo,
                         w_t, w_q, date}
    """
    n = len(match_features)
    if n == 0:
        return {}

    # ── Session decision 4: Kish n_eff (match-level) ───────────────────────
    n_eff_matches = kish_n_eff(weights_raw)

    # Normalise weights
    total_w = sum(weights_raw)
    weights  = [w / total_w for w in weights_raw] if total_w > 0 else [1/n] * n

    def ws(key): return weighted_sum(match_features, weights, key)

    # ── Session decision 3: sample-size tier ───────────────────────────────
    tier2_enabled = n >= MIN_MATCHES_TIER2
    tier2_wide    = MIN_MATCHES_TIER2 <= n < FULL_MATCHES   # wide intervals flag

    # Total weighted points (pseudo-count)
    n_points = round(ws("pts_total"))

    # ── Tier 1 ──────────────────────────────────────────────────────────────

    # FSPW%
    fspw = pct_metric(ws("srv_1st_won"), ws("srv_1st_in"))

    # SSPW%
    sspw = pct_metric(ws("srv_2nd_won"), ws("srv_2nd_in"))

    # RPW%
    rpw = pct_metric(ws("ret_won"), ws("ret_total"))

    # SGW%
    sgw = pct_metric(ws("srv_games_won"), ws("srv_games"))

    # RGW%
    rgw = pct_metric(ws("ret_games_won"), ws("ret_games"))

    # First serve %
    fsp = pct_metric(ws("srv_1st_in"), ws("srv_total"))

    # ── Tier 2 ──────────────────────────────────────────────────────────────
    # Note: Tier 2 is always computed for the display layer regardless of
    # match count. The confidence flags (UNRELIABLE/LOW/RELIABLE) and n_eff
    # values communicate data quality. The matchup engine should gate on
    # n_eff thresholds itself; the visualisation page shows everything.

    # Serve entropy
    dir_avail = ws("dir_avail") > 0
    if dir_avail:
        ent = round(sum(
            (f.get("serve_entropy", 0) * w)
            for f, w in zip(match_features, weights)
        ), 4)
        max_ent = round(-3 * (1/3) * math.log2(1/3), 4)
        serve_entropy = {"available": True, "value": ent, "max_bits": max_ent,
                         "pct_of_max": round(ent / max_ent * 100, 1) if max_ent else None}
    else:
        serve_entropy = {"available": False, "value": None}

    # Rally win curve
    rc_avail = ws("rc_avail") > 0
    if rc_avail:
        rally_win_curve = {}
        for lbl in RALLY_LABELS:
            rw = ws(f"rw_{lbl}")
            rt = ws(f"rt_{lbl}")
            if rt > 0:
                rally_win_curve[lbl] = {
                    "win_pct": round(rw / rt * 100, 1),
                    "n": round(rt),
                }
            else:
                rally_win_curve[lbl] = {"win_pct": None, "n": 0}
    else:
        rally_win_curve = None

    # Clutch differential
    hl_total_w = ws("hl_total")
    hl_won_w   = ws("hl_won")
    pts_total_w = ws("pts_total")
    pts_won_w   = ws("pts_won")
    if hl_total_w > 0 and pts_total_w > 0:
        hl_pct       = round(hl_won_w / hl_total_w * 100, 1)
        baseline_pct = round(pts_won_w / pts_total_w * 100, 1)
        clutch_diff  = round(hl_pct - baseline_pct, 1)
        clutch = {
            "available":          True,
            "value":              clutch_diff,
            "high_lev_win_pct":   hl_pct,
            "baseline_win_pct":   baseline_pct,
        }
    else:
        clutch = {"available": False, "value": None}

    # DF pressure delta
    bp_srv_total_w = ws("bp_srv_total")
    bp_srv_df_w    = ws("bp_srv_df")
    srv_total_w    = ws("srv_total")
    dfs_w          = ws("dfs")
    if bp_srv_total_w > 0 and srv_total_w > 0:
        bp_df_rate   = round(bp_srv_df_w / bp_srv_total_w * 100, 1)
        base_df_rate = round(dfs_w / srv_total_w * 100, 1)
        df_pressure = {
            "available":      True,
            "value":          round(bp_df_rate - base_df_rate, 1),
            "bp_df_rate":     bp_df_rate,
            "baseline_df_rate": base_df_rate,
        }
    else:
        df_pressure = {"available": False, "value": None}

    # Serve speed courage index
    spd_all_n_w = ws("spd_all_n")
    spd_bp_n_w  = ws("spd_bp_n")
    if spd_all_n_w > 0 and spd_bp_n_w > 0:
        spd_overall = round(ws("spd_all_sum") / spd_all_n_w, 1)
        spd_bp      = round(ws("spd_bp_sum") / spd_bp_n_w, 1)
        spd_courage = {
            "available":         True,
            "value":             round(spd_bp - spd_overall, 1),
            "bp_speed_kmh":      spd_bp,
            "overall_speed_kmh": spd_overall,
        }
    else:
        spd_courage = {"available": False, "value": None}

    # Court-side asymmetry
    dc_total_w = ws("dc_total")
    dc_won_w   = ws("dc_won")
    ac_total_w = ws("ac_total")
    ac_won_w   = ws("ac_won")
    if dc_total_w > 0 and ac_total_w > 0:
        deuce_pct = round(dc_won_w / dc_total_w * 100, 1)
        ad_pct    = round(ac_won_w / ac_total_w * 100, 1)
        court_asymmetry = {
            "available":       True,
            "deuce_win_pct":   deuce_pct,
            "ad_win_pct":      ad_pct,
            "asymmetry":       round(abs(deuce_pct - ad_pct), 1),
            "stronger_side":   "deuce" if deuce_pct >= ad_pct else "ad",
        }
    else:
        court_asymmetry = {"available": False}

    # Momentum profile
    si3_w  = ws("streak_init_3")
    ss4_w  = ws("streak_surv_4")
    ost_w  = ws("opp_streak_total")
    osw_w  = ws("opp_streak_won")
    momentum = {
        "streak_initiation_rate": round(si3_w / n, 2) if n > 0 else None,
        "streak_survival_rate":   round(ss4_w / si3_w, 3) if si3_w > 0 else None,
        "streak_recovery_rate":   round(osw_w / ost_w, 3) if ost_w > 0 else None,
    }

    # Break point creation profile
    bp_c_w  = ws("bp_created")
    bp_cv_w = ws("bp_converted")
    ret_g_w = ws("ret_games")
    bp_profile = {
        "bp_per_return_game": round(bp_c_w / ret_g_w, 3) if ret_g_w > 0 else None,
        "bp_conversion":      round(bp_cv_w / bp_c_w, 3) if bp_c_w > 0 else None,
        "bp_created":         round(bp_c_w, 1),
        "bp_converted":       round(bp_cv_w, 1),
    }

    # ── RLUEP (Rally-Length UFE Profile) ───────────────────────────────────
    # Session decision 12: UFE rate per rally band; 10+ band flagged if thin.

    ufe_data_avail = ws("ufe_avail") > 0 and rc_avail
    rluep: Optional[dict] = None
    if ufe_data_avail:
        rluep = {}
        for lbl in RALLY_LABELS:
            ufe_w = ws(f"rufe_{lbl}")
            tot_w = ws(f"rt_{lbl}")
            n_eff_band = round(tot_w)
            conf_band  = confidence_flag(n_eff_band)
            if tot_w > 0:
                rluep[lbl] = {
                    "ufe_rate":   round(ufe_w / tot_w * 100, 1),
                    "n":          n_eff_band,
                    "confidence": conf_band,
                }
            else:
                rluep[lbl] = {"ufe_rate": None, "n": 0, "confidence": "UNRELIABLE"}

    # ── Per-metric confidence flags (point n_eff) ───────────────────────────
    # Use weighted pseudo-counts as proxy for effective point count per metric.

    clutch_n   = round(hl_total_w)
    clutch_conf = confidence_flag(clutch_n)

    bp_srv_n   = round(bp_srv_total_w)
    df_conf    = confidence_flag(bp_srv_n)

    spd_bp_n   = round(ws("spd_bp_n"))
    spd_conf   = confidence_flag(spd_bp_n)

    # ── Enrich clutch with verdict + n_eff ──────────────────────────────────
    if clutch.get("available"):
        clutch.update({
            "n_eff":      clutch_n,
            "confidence": clutch_conf,
            "verdict":    pressure_verdict(clutch_diff, "clutch_diff", clutch_conf),
            "modifier_delta": round(clutch_diff / 100, 4),
        })

    # ── Enrich DF pressure with verdict + n_eff ─────────────────────────────
    if df_pressure.get("available"):
        base_df = df_pressure["baseline_df_rate"]
        bp_df   = df_pressure["bp_df_rate"]
        # Ratio for "doubles" verdict check (bp_df / base_df)
        df_ratio = round(bp_df / base_df, 2) if base_df > 0 else None
        df_pressure.update({
            "n_eff":      bp_srv_n,
            "confidence": df_conf,
            "verdict":    pressure_verdict(df_ratio, "df_rate_doubles", df_conf),
            "modifier_delta": round((bp_df - base_df) / 100, 4),
        })

    # ── Enrich serve speed courage with verdict + n_eff ─────────────────────
    if spd_courage.get("available"):
        speed_delta = spd_courage["value"]
        spd_courage.update({
            "n_eff":      spd_bp_n,
            "confidence": spd_conf,
            "verdict":    pressure_verdict(speed_delta, "serve_speed_delta", spd_conf),
            "modifier_delta": round(speed_delta / 100, 4),
            "note": ("Component of SPCI — cross with opponent RDAS before verdict. "
                     "See session decision 8." if tier2_wide else None),
        })

    # ── Partial SPCI (session decision 9) ────────────────────────────────────
    # Two of five components available now; the others need per-point data.
    spci: Optional[dict] = None
    spci_components: dict = {}

    if spd_courage.get("available") and spd_courage["value"] is not None:
        # Serve Speed Delta component: (speed_bp - speed_overall) / speed_overall
        base_spd = spd_courage["overall_speed_kmh"]
        if base_spd and base_spd > 0:
            delta_pct = (spd_courage["bp_speed_kmh"] - base_spd) / base_spd
            spci_components["serve_speed_delta"] = {
                "delta_frac": round(delta_pct, 4),
                "weight":     SPCI_WEIGHTS["serve_speed_delta"],
                "contribution": round(SPCI_WEIGHTS["serve_speed_delta"] * delta_pct, 4),
            }

    if df_pressure.get("available") and df_pressure["value"] is not None:
        # DF Rate Delta component: (bp_df_rate - base_df_rate) / base_df_rate
        base_df  = df_pressure["baseline_df_rate"]
        delta_df = df_pressure["value"]  # already bp - base in pp
        if base_df and base_df > 0:
            delta_pct_df = delta_df / base_df  # fractional change
            spci_components["df_rate_delta"] = {
                "delta_frac": round(delta_pct_df, 4),
                "weight":     SPCI_WEIGHTS["df_rate_delta"],
                "contribution": round(SPCI_WEIGHTS["df_rate_delta"] * delta_pct_df, 4),
            }

    if spci_components:
        spci_value = round(sum(c["contribution"] for c in spci_components.values()), 4)
        spci_conf  = clutch_conf if clutch_conf != "UNRELIABLE" else df_conf
        spci = {
            "available":      True,
            "value":          spci_value,
            "components":     spci_components,
            "components_used": len(spci_components),
            "components_total": 5,
            "confidence":     spci_conf,
            "verdict":        pressure_verdict(spci_value, "spci", spci_conf),
            "modifier_delta": spci_value,
            "note": ("Partial SPCI: 2/5 components. Missing: rally_length@pressure, "
                     "distance_per_shot@pressure, serve_dir_variety@pressure."),
        }

    # ── Overall fingerprint confidence (n_eff-based) ────────────────────────
    # Use total points as proxy for point-level n_eff
    overall_conf = confidence_flag(n_points)

    return {
        "player":        player_name,
        "surface":       "grass",
        "year":          year,
        "n_matches":     n,
        "n_points":      n_points,
        "n_eff_matches": n_eff_matches,
        "confidence":    overall_conf,
        "tier2_available":      True,
        "tier2_wide_intervals": tier2_wide,   # True when n < FULL_MATCHES (informational only)
        "training_matches": match_meta,
        "tier1": {
            "fsp_pct":  fsp,
            "fspw_pct": fspw,
            "sspw_pct": sspw,
            "rpw_pct":  rpw,
            "sgw_pct":  sgw,
            "rgw_pct":  rgw,
        },
        "tier2": {
            "serve_entropy":       serve_entropy,
            "rally_win_curve":     rally_win_curve,
            "clutch_differential": clutch,
            "df_pressure_delta":   df_pressure,
            "serve_speed_courage": spd_courage,
            "court_side_asymmetry": court_asymmetry,
            "momentum_profile":    momentum,
            "bp_creation_profile": bp_profile,
            "rluep":               rluep,
            "spci":                spci,
        },
    }


# ── per-year pipeline ──────────────────────────────────────────────────────

def build_year_fingerprints(year: int,
                             elo: Optional[GrassElo] = None,
                             grass_df: Optional[pd.DataFrame] = None) -> dict:
    """
    Build fingerprints for every player in the Wimbledon draw for `year`.

    Returns a dict: {player_name: fingerprint_dict}
    """
    print(f"\n── Fingerprints {year} ──────────────────────────────────────")
    pts, mat = load_year(year)
    men_ids  = men_match_ids(mat)
    players  = player_list(mat, men_ids)

    # Match date lookup (use first point timestamp or tournament start)
    # Wimbledon tournament reference dates for recency calculation
    tourney_starts = {2017: pd.Timestamp("2017-07-03"),
                      2018: pd.Timestamp("2018-07-02"),
                      2019: pd.Timestamp("2019-07-01")}
    T_ref = tourney_starts.get(year, pd.Timestamp(f"{year}-07-03"))

    # Build Elo quality weights if available
    mean_r = elo.mean_active_rating() if elo else None

    # Build match → opponent name lookup from matches CSV
    opp_lookup = {}
    for _, row in mat[mat["match_id"].isin(men_ids)].iterrows():
        mid = row["match_id"]
        opp_lookup[(mid, str(row["player1"]))] = str(row["player2"])
        opp_lookup[(mid, str(row["player2"]))] = str(row["player1"])

    all_fingerprints = {}

    for player_name, info in players.items():
        match_features_list = []
        match_meta_list     = []
        weights_raw         = []

        for match_id in info["matches"]:
            if match_id not in men_ids:
                continue

            pn       = info["side_in_match"][match_id]
            match_pts = pts[pts["match_id"] == match_id].copy()
            if match_pts.empty:
                continue

            # Opponent identity
            opp_name = opp_lookup.get((match_id, player_name), "Unknown")

            # ── Recency weight ──────────────────────────────────────────────
            # All Wimbledon matches are close in time; scale by round depth
            # (later rounds are slightly more recent within the tournament)
            rnd_label = match_round(match_id)
            rnd_order = {"R128":1,"R64":2,"R32":3,"R16":4,"QF":5,"SF":6,"F":7}
            days_offset = -(rnd_order.get(rnd_label, 1) - 1) * 2  # 2 days per round
            match_date  = T_ref + pd.Timedelta(days=days_offset)
            days_before = (T_ref - match_date).days   # 0 for finals-week
            w_t = math.exp(-DECAY_LAMBDA * max(days_before, 0) / 365.0)

            # ── Quality weight (date-capped — no look-ahead bias) ───────────
            # Use the opponent's Elo as it stood at match_date, not their
            # final career rating.  Falls back to neutral (1.0) if no
            # pre-match snapshot exists yet (e.g. player's debut).
            if elo is not None:
                w_q     = elo.quality_weight_at_by_name(
                              opp_name, match_date, mean_active=mean_r)
                r_at    = elo.adjusted_rating_at_by_name(opp_name, match_date)
                opp_elo = round(r_at, 1) if r_at is not None else None
            else:
                w_q     = 1.0
                opp_elo = None

            # ── Per-match features ──────────────────────────────────────────
            feats = compute_match_features(player_name, pn, match_pts)
            match_features_list.append(feats)

            meta = {
                "match_id":     match_id,
                "round":        rnd_label,
                "opponent":     opp_name,
                "opponent_elo": round(opp_elo, 1) if opp_elo else None,
                "w_t":          round(w_t, 4),
                "w_q":          round(w_q, 4),
            }
            match_meta_list.append(meta)
            weights_raw.append(w_t * w_q)

        if not match_features_list:
            continue

        fingerprint = build_fingerprint(
            player_name, year,
            match_features_list,
            match_meta_list,
            weights_raw,
        )

        # Attach final Elo snapshot for the player themselves (display only —
        # quality weights already used date-capped ratings above)
        if elo is not None:
            fingerprint["elo_snapshot"] = elo.get_snapshot_by_name(player_name)

        all_fingerprints[player_name] = fingerprint
        print(f"  {player_name:<30}  {len(match_features_list)} matches")

    return all_fingerprints


# ── output ──────────────────────────────────────────────────────────────────

def save_fingerprints(fingerprints: dict, year: int) -> None:
    PROC_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fname = f"{year}_fingerprints.json"
    for dest in [PROC_DIR / fname, OUT_DIR / fname]:
        with open(dest, "w") as f:
            json.dump(fingerprints, f, indent=2)
        print(f"Saved → {dest}")


# ── CLI ──────────────────────────────────────────────────────────────────────

def load_elo(no_elo: bool = False) -> Optional[GrassElo]:
    if no_elo:
        print("Skipping Elo — uniform quality weights will be used.")
        return None

    cached = PROC_DIR / "elo_ratings.json"
    grass_csv = PROC_DIR / "all_grass_matches.csv"

    if cached.exists():
        try:
            return GrassElo.load(cached)
        except Exception as e:
            print(f"Warning: could not load cached Elo ({e}). Rebuilding…")

    if grass_csv.exists():
        elo = build_elo_from_csv(grass_csv)
        elo.save(cached)
        return elo

    print(
        "Warning: neither elo_ratings.json nor all_grass_matches.csv found.\n"
        "  Run:  python src/data_prep.py --download\n"
        "  Then: python src/elo.py\n"
        "Continuing with uniform quality weights."
    )
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Build weighted Wimbledon player fingerprints"
    )
    parser.add_argument(
        "--year", default="all",
        help="Year to process (2017, 2018, 2019, or 'all')"
    )
    parser.add_argument(
        "--no_elo", action="store_true",
        help="Skip Elo weighting (use uniform quality weights)"
    )
    args = parser.parse_args()

    years = SUPPORTED_YEARS if args.year == "all" else [int(args.year)]

    elo = load_elo(no_elo=args.no_elo)

    for year in years:
        fingerprints = build_year_fingerprints(year, elo=elo)
        save_fingerprints(fingerprints, year)
        print(f"  → {len(fingerprints)} player fingerprints built for {year}")

    print("\nFingerprint build complete.")


if __name__ == "__main__":
    main()
