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
# Phase B: canonical name normalisation — applied to PBP-derived player names
# so fingerprint keys match the same canonical form as the match-level data.
from canonical_names import normalize_name


# ── constants ──────────────────────────────────────────────────────────────

DATA_DIR        = Path(__file__).parent.parent / "data" / "raw"
PROC_DIR        = Path(__file__).parent.parent / "data" / "processed"
OUT_DIR         = Path(__file__).parent.parent / "data"
SUPPORTED_YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025]

# Career-window fingerprint settings
CAREER_WINDOW   = 10   # max matches per fingerprint (across all eligible years)
CAREER_EDITIONS = 3    # look back this many Wimbledon editions (not calendar years)
                       # e.g. for 2024: use 2022, 2023, 2024 — 2020 gap handled naturally

# Actual Wimbledon start dates (Monday of the fortnight)
WIMBLEDON_STARTS: Dict[int, pd.Timestamp] = {
    2011: pd.Timestamp("2011-06-27"),
    2012: pd.Timestamp("2012-06-25"),
    2013: pd.Timestamp("2013-06-24"),
    2014: pd.Timestamp("2014-06-23"),
    2015: pd.Timestamp("2015-06-29"),
    2016: pd.Timestamp("2016-06-27"),
    2017: pd.Timestamp("2017-07-03"),
    2018: pd.Timestamp("2018-07-02"),
    2019: pd.Timestamp("2019-07-01"),
    2021: pd.Timestamp("2021-06-28"),
    2022: pd.Timestamp("2022-06-27"),
    2023: pd.Timestamp("2023-07-03"),
    2024: pd.Timestamp("2024-07-01"),
    2025: pd.Timestamp("2025-06-30"),   # Wimbledon 2025: 30 Jun – 13 Jul
}

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

    if "ServeNumber" in serving.columns:
        sn = serving["ServeNumber"]
    else:
        sn = pd.Series(0, index=serving.index)
    is_1st  = (sn == 1)
    is_2nd  = (sn == 2)
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

    # Return splits by serve type — captures returner quality vs 1st vs 2nd serves.
    # On grass these regimes are very different (~28% vs 1st, ~52% vs 2nd) so
    # collapsing them into a single rpw_pct loses signal.  Used by phased MC.
    if "ServeNumber" in returning.columns:
        ret_sn = returning["ServeNumber"]
    else:
        ret_sn = pd.Series(0, index=returning.index)
    ret_vs_1st_total = int((ret_sn == 1).sum())
    ret_vs_1st_won   = int(((ret_sn == 1) & (returning["PointWinner"] == pn)).sum())
    ret_vs_2nd_total = int((ret_sn == 2).sum())
    ret_vs_2nd_won   = int(((ret_sn == 2) & (returning["PointWinner"] == pn)).sum())

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

    # ── NEW: Rally volatility (variance of rally lengths on won points) ────
    # Tracks sum and sum-of-squares for online variance: Var = E[X²] - E[X]²

    rv_won_sum = rv_won_sq = 0.0
    rv_won_n   = 0
    if rc_avail:
        for _, row in match_pts.iterrows():
            rc_val = row.get(rc_col)
            if pd.isna(rc_val):
                continue
            rc_f = float(rc_val)
            if row["PointWinner"] == pn:
                rv_won_sum += rc_f
                rv_won_sq  += rc_f * rc_f
                rv_won_n   += 1

    # ── NEW: Serve speed differential (mean 1st minus mean 2nd speed) ─────

    spd_1st_sum = spd_1st_n = 0.0
    spd_2nd_sum = spd_2nd_n = 0.0
    if spd_avail:
        for _, row in serving.iterrows():
            spd_val = row.get("Speed_KMH", 0)
            sn_val  = row.get("ServeNumber", 0)
            if pd.isna(spd_val) or spd_val <= 0:
                continue
            if sn_val == 1:
                spd_1st_sum += spd_val
                spd_1st_n   += 1
            elif sn_val == 2:
                spd_2nd_sum += spd_val
                spd_2nd_n   += 1

    # ── NEW: Serve depth entropy (CTL = deep / NCTL = short) ──────────────

    depth_ctl = depth_nctl = 0
    if "ServeDepth" in serving.columns:
        sd_vals    = serving["ServeDepth"].dropna()
        depth_ctl  = int((sd_vals == "CTL").sum())
        depth_nctl = int((sd_vals == "NCTL").sum())

    # ── NEW: Distance run efficiency & attrition slope ────────────────────
    # dist_eff = mean(distance_m / rally_length) per point
    # dist_sN  = per-set distance sums for slope computation

    dist_col_pn  = f"P{pn}DistanceRun"
    dist_m_total = 0.0
    dist_eff_sum = dist_eff_n = 0.0
    # Per-set totals for sets 1..5
    dist_s_sum = [0.0] * 6   # index 1..5 used; 0 unused
    dist_s_n   = [0]   * 6

    if dist_col_pn in match_pts.columns:
        d_vals = pd.to_numeric(match_pts[dist_col_pn], errors="coerce")
        rc_vals = pd.to_numeric(match_pts.get(rc_col, pd.Series(dtype=float)),
                                errors="coerce") if rc_avail else None
        set_vals = pd.to_numeric(match_pts.get("SetNo", pd.Series(dtype=float)),
                                 errors="coerce")
        for i, (d, s) in enumerate(zip(d_vals, set_vals)):
            if pd.isna(d) or d <= 0:
                continue
            dist_m_total += d
            sn_int = int(s) if not pd.isna(s) and 1 <= int(s if not pd.isna(s) else 0) <= 5 else 0
            if sn_int:
                dist_s_sum[sn_int] += d
                dist_s_n[sn_int]   += 1
            if rc_vals is not None:
                rc_v = rc_vals.iloc[i] if i < len(rc_vals) else float("nan")
                if not pd.isna(rc_v) and rc_v > 0:
                    dist_eff_sum += d / float(rc_v)
                    dist_eff_n   += 1

    # ── NEW: Set transition delta (win% in first 2 games of each set) ─────

    st_pts_won = st_pts_total = 0
    # Find the first 2 unique GameNo values per set
    if "SetNo" in match_pts.columns and "GameNo" in match_pts.columns:
        set_nos  = pd.to_numeric(match_pts["SetNo"],  errors="coerce")
        game_nos = pd.to_numeric(match_pts["GameNo"], errors="coerce")
        for sn_val in set_nos.dropna().unique():
            set_mask  = set_nos == sn_val
            set_games = game_nos[set_mask].dropna().unique()
            first_two = sorted(set_games)[:2]
            for gn in first_two:
                g_mask = set_mask & (game_nos == gn)
                st_pts_total += int(g_mask.sum())
                st_pts_won   += int((g_mask & (match_pts["PointWinner"] == pn)).sum())

    # ── NEW: 1st serve aggression under pressure (deuce / advantage) ──────
    # 1st serve rate specifically at deuce/AD points vs overall serve rate

    pres_srv_1st = pres_srv_total = 0
    for _, row in serving.iterrows():
        p1s = str(row.get("P1Score", "")).strip()
        p2s = str(row.get("P2Score", "")).strip()
        at_deuce_ad = (p1s in ("40", "AD") and p2s in ("40", "AD"))
        if at_deuce_ad:
            pres_srv_total += 1
            if row.get("ServeNumber", 0) == 1:
                pres_srv_1st += 1

    # ── NEW: Tiebreak differential (win% in tiebreaks vs overall) ─────────

    tb_won = tb_total = 0
    for _, row in match_pts.iterrows():
        p1s = str(row.get("P1Score", "")).strip()
        try:
            int(p1s)          # tiebreak uses numeric score notation
            tb_total += 1
            if row["PointWinner"] == pn:
                tb_won += 1
        except ValueError:
            pass

    # ── NEW: Hold After Break Rate ─────────────────────────────────────────
    # When the player is broken in a service game, do they hold in their
    # very next service game? Captures mental resilience under adversity.

    habr_won = habr_total = 0
    pn_srv_results: List[bool] = []
    for (sn_g, gn_g), gpts in sorted(
            match_pts.groupby(["SetNo", "GameNo"]),
            key=lambda x: (x[0][0], x[0][1])):
        if "GameWinner" not in gpts.columns:
            continue
        gw = gpts["GameWinner"].iloc[-1]
        if gw == 0:
            continue
        if gpts["PointServer"].iloc[0] == pn:
            pn_srv_results.append(int(gw) == pn)

    for i in range(1, len(pn_srv_results)):
        if not pn_srv_results[i - 1]:   # previous service game = broken
            habr_total += 1
            if pn_srv_results[i]:        # current service game = held
                habr_won += 1

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
        "ret_vs_1st_total": ret_vs_1st_total, "ret_vs_1st_won": ret_vs_1st_won,
        "ret_vs_2nd_total": ret_vs_2nd_total, "ret_vs_2nd_won": ret_vs_2nd_won,
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
        # NEW: rally volatility
        "rv_won_sum": rv_won_sum, "rv_won_sq": rv_won_sq, "rv_won_n": rv_won_n,
        # NEW: serve speed differential
        "spd_1st_sum": spd_1st_sum, "spd_1st_n": spd_1st_n,
        "spd_2nd_sum": spd_2nd_sum, "spd_2nd_n": spd_2nd_n,
        # NEW: serve depth entropy
        "depth_ctl": depth_ctl, "depth_nctl": depth_nctl,
        # NEW: distance run efficiency & total
        "dist_m_total": dist_m_total,
        "dist_eff_sum": dist_eff_sum, "dist_eff_n": dist_eff_n,
        **{f"dist_s{i}_sum": dist_s_sum[i] for i in range(1, 6)},
        **{f"dist_s{i}_n":   dist_s_n[i]   for i in range(1, 6)},
        # NEW: set transition delta
        "st_pts_won": st_pts_won, "st_pts_total": st_pts_total,
        # NEW: 1st serve aggression under pressure
        "pres_srv_1st": pres_srv_1st, "pres_srv_total": pres_srv_total,
        # NEW: tiebreak differential
        "tb_won": tb_won, "tb_total": tb_total,
        # NEW: hold after break rate
        "habr_won": habr_won, "habr_total": habr_total,
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

    # Return splits — RPW vs 1st serve and vs 2nd serve.
    # On grass these are typically ~28% vs 1st (returner under heavy pressure)
    # and ~52% vs 2nd (returner attacks, often dictates the rally).
    # Used by the phased Monte Carlo for serve-type-aware matchup adjustment.
    rpw_vs_1st = pct_metric(ws("ret_vs_1st_won"), ws("ret_vs_1st_total"))
    rpw_vs_2nd = pct_metric(ws("ret_vs_2nd_won"), ws("ret_vs_2nd_total"))

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

    # ── NEW: Rally volatility ──────────────────────────────────────────────
    # Weighted variance of rally-length on player's won points.
    # Var = E[X²] - E[X]²  using weighted sums.

    rv_n_w  = ws("rv_won_n")
    rally_volatility: Optional[dict] = None
    if rv_n_w >= 10:
        rv_mean  = ws("rv_won_sum") / rv_n_w
        rv_var   = max(ws("rv_won_sq") / rv_n_w - rv_mean ** 2, 0.0)
        rv_sd    = math.sqrt(rv_var)
        rv_conf  = confidence_flag(round(rv_n_w))
        rally_volatility = {
            "available":  True,
            "value":      round(rv_sd, 2),
            "mean_rc_won": round(rv_mean, 2),
            "n_eff":      round(rv_n_w),
            "confidence": rv_conf,
        }
    else:
        rally_volatility = {"available": False, "value": None}

    # ── NEW: Serve speed differential ─────────────────────────────────────

    spd_1st_n_w = ws("spd_1st_n")
    spd_2nd_n_w = ws("spd_2nd_n")
    serve_speed_differential: Optional[dict] = None
    if spd_1st_n_w >= 5 and spd_2nd_n_w >= 5:
        spd_1st_mean = round(ws("spd_1st_sum") / spd_1st_n_w, 1)
        spd_2nd_mean = round(ws("spd_2nd_sum") / spd_2nd_n_w, 1)
        ssd_value    = round(spd_1st_mean - spd_2nd_mean, 1)
        serve_speed_differential = {
            "available":      True,
            "value":          ssd_value,   # positive = bigger gap (boom-or-bust)
            "first_avg_kmh":  spd_1st_mean,
            "second_avg_kmh": spd_2nd_mean,
            "n_eff":          round(min(spd_1st_n_w, spd_2nd_n_w)),
            "confidence":     confidence_flag(round(min(spd_1st_n_w, spd_2nd_n_w))),
        }
    else:
        serve_speed_differential = {"available": False, "value": None}

    # ── NEW: Serve depth entropy (CTL vs NCTL) ────────────────────────────

    depth_ctl_w  = ws("depth_ctl")
    depth_nctl_w = ws("depth_nctl")
    serve_depth_entropy: Optional[dict] = None
    depth_total_w = depth_ctl_w + depth_nctl_w
    if depth_total_w >= 10:
        p_ctl  = depth_ctl_w  / depth_total_w
        p_nctl = depth_nctl_w / depth_total_w
        # 2-category Shannon entropy — max = 1.0 bit (50/50 split)
        ent_depth = 0.0
        for p in (p_ctl, p_nctl):
            if p > 0:
                ent_depth -= p * math.log2(p)
        serve_depth_entropy = {
            "available":   True,
            "value":       round(ent_depth, 4),
            "pct_of_max":  round(ent_depth / 1.0 * 100, 1),  # max = 1 bit
            "pct_deep":    round(p_ctl * 100, 1),             # CTL = deep
            "n_eff":       round(depth_total_w),
            "confidence":  confidence_flag(round(depth_total_w)),
        }
    else:
        serve_depth_entropy = {"available": False, "value": None}

    # ── NEW: Distance run efficiency & attrition slope ────────────────────

    dist_eff_n_w    = ws("dist_eff_n")
    dist_m_total_w  = ws("dist_m_total")
    distance_run_efficiency: Optional[dict] = None
    if dist_eff_n_w >= 10:
        eff_mean = round(ws("dist_eff_sum") / dist_eff_n_w, 4)
        distance_run_efficiency = {
            "available":    True,
            "value":        eff_mean,   # metres per rally-length unit (lower = efficient)
            "n_eff":        round(dist_eff_n_w),
            "confidence":   confidence_flag(round(dist_eff_n_w)),
        }
    else:
        distance_run_efficiency = {"available": False, "value": None}

    # Attrition slope: OLS slope of per-set distance avg across sets 1..5
    attrition_slope: Optional[dict] = None
    _set_avgs = []
    _set_ns   = []
    for si in range(1, 6):
        sn_w = ws(f"dist_s{si}_n")
        if sn_w >= 5:
            _set_avgs.append((si, ws(f"dist_s{si}_sum") / sn_w))
            _set_ns.append(sn_w)
    if len(_set_avgs) >= 2:
        xs = [a[0] for a in _set_avgs]
        ys = [a[1] for a in _set_avgs]
        _xm, _ym = sum(xs)/len(xs), sum(ys)/len(ys)
        _num = sum((x-_xm)*(y-_ym) for x, y in zip(xs, ys))
        _den = sum((x-_xm)**2 for x in xs)
        slope_val = round(_num / _den, 4) if _den > 0 else 0.0
        # Confidence based on min per-set point count — slope reliability
        # is limited by the thinnest set bucket.
        _att_n_eff = round(min(_set_ns))
        attrition_slope = {
            "available":  True,
            "value":      slope_val,  # positive = running more per point as match progresses
            "n_sets":     len(_set_avgs),
            "n_eff":      _att_n_eff,
            "confidence": confidence_flag(_att_n_eff),
            "set_avgs_m": {str(a[0]): round(a[1], 3) for a in _set_avgs},
        }
    else:
        attrition_slope = {"available": False, "value": None}

    # Fix distance block: total km from weighted point sum
    dist_km_avg = round(dist_m_total_w / 1000, 2) if dist_m_total_w > 0 else None
    distance_block = {
        "available":        dist_km_avg is not None,
        "avg_km_per_match": dist_km_avg,
    }

    # ── NEW: Set transition delta ──────────────────────────────────────────

    st_won_w  = ws("st_pts_won")
    st_tot_w  = ws("st_pts_total")
    set_transition_delta: Optional[dict] = None
    if st_tot_w >= 10 and pts_total_w > 0:
        st_pct       = round(st_won_w  / st_tot_w  * 100, 1)
        overall_pct  = round(pts_won_w / pts_total_w * 100, 1)
        std_value    = round(st_pct - overall_pct, 1)
        set_transition_delta = {
            "available":         True,
            "value":             std_value,  # positive = stronger set-start
            "first_games_win_pct": st_pct,
            "overall_win_pct":   overall_pct,
            "n_eff":             round(st_tot_w),
            "confidence":        confidence_flag(round(st_tot_w)),
        }
    else:
        set_transition_delta = {"available": False, "value": None}

    # ── NEW: 1st serve aggression under pressure ───────────────────────────

    pres_srv_tot_w  = ws("pres_srv_total")
    pres_srv_1st_w  = ws("pres_srv_1st")
    first_serve_pressure: Optional[dict] = None
    srv_total_w_new = ws("srv_total")
    srv_1st_in_w    = ws("srv_1st_in")
    if pres_srv_tot_w >= 5 and srv_total_w_new > 0:
        fsp_pressure = round(pres_srv_1st_w / pres_srv_tot_w * 100, 1)
        fsp_overall  = round(srv_1st_in_w   / srv_total_w_new * 100, 1)
        fsp_delta    = round(fsp_pressure - fsp_overall, 1)
        first_serve_pressure = {
            "available":          True,
            "value":              fsp_delta,   # negative = becomes more conservative under pressure
            "fsp_at_pressure":    fsp_pressure,
            "fsp_overall":        fsp_overall,
            "n_eff":              round(pres_srv_tot_w),
            "confidence":         confidence_flag(round(pres_srv_tot_w)),
        }
    else:
        first_serve_pressure = {"available": False, "value": None}

    # ── NEW: Tiebreak differential ────────────────────────────────────────

    tb_won_w  = ws("tb_won")
    tb_tot_w  = ws("tb_total")
    tiebreak_differential: Optional[dict] = None
    if tb_tot_w >= 5 and pts_total_w > 0:
        tb_pct      = round(tb_won_w  / tb_tot_w  * 100, 1)
        base_pct_tb = round(pts_won_w / pts_total_w * 100, 1)
        tb_value    = round(tb_pct - base_pct_tb, 1)
        tiebreak_differential = {
            "available":      True,
            "value":          tb_value,    # positive = clutch tiebreak player
            "tb_win_pct":     tb_pct,
            "baseline_win_pct": base_pct_tb,
            "n_eff":          round(tb_tot_w),
            "confidence":     confidence_flag(round(tb_tot_w)),
        }
    else:
        tiebreak_differential = {"available": False, "value": None}

    # ── NEW: Hold After Break Rate ────────────────────────────────────────
    # Hold% in the service game immediately following a break, vs overall SGW%

    habr_won_w = ws("habr_won")
    habr_tot_w = ws("habr_total")
    hold_after_break: Optional[dict] = None
    if habr_tot_w >= 5:
        habr_pct   = round(habr_won_w / habr_tot_w * 100, 1)
        sgw_n      = ws("srv_games")
        sgw_pct_b  = round(ws("srv_games_won") / sgw_n * 100, 1) if sgw_n > 0 else None
        habr_delta = round(habr_pct - sgw_pct_b, 1) if sgw_pct_b is not None else None
        hold_after_break = {
            "available":    True,
            "value":        habr_delta,    # +ve = bounces back above normal hold rate
            "habr_pct":     habr_pct,      # hold% in first game after being broken
            "sgw_pct":      sgw_pct_b,     # overall service-game hold%
            "n_eff":        round(habr_tot_w),
            "confidence":   confidence_flag(round(habr_tot_w)),
        }
    else:
        hold_after_break = {"available": False, "value": None}

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
            "note": ("Partial SPCI: 2/5 components. "
                     "See tiebreak_differential, set_transition_delta, first_serve_pressure "
                     "for additional pressure metrics."),
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
            "fsp_pct":         fsp,
            "fspw_pct":        fspw,
            "sspw_pct":        sspw,
            "rpw_pct":         rpw,
            "rpw_vs_1st_pct":  rpw_vs_1st,
            "rpw_vs_2nd_pct":  rpw_vs_2nd,
            "sgw_pct":         sgw,
            "rgw_pct":         rgw,
        },
        "tier2": {
            # Existing
            "serve_entropy":         serve_entropy,
            "rally_win_curve":       rally_win_curve,
            "clutch_differential":   clutch,
            "df_pressure_delta":     df_pressure,
            "serve_speed_courage":   spd_courage,
            "court_side_asymmetry":  court_asymmetry,
            "momentum_profile":      momentum,
            "bp_creation_profile":   bp_profile,
            "rluep":                 rluep,
            "spci":                  spci,
            # New indexes
            "rally_volatility":         rally_volatility,
            "serve_speed_differential": serve_speed_differential,
            "serve_depth_entropy":      serve_depth_entropy,
            "distance_run_efficiency":  distance_run_efficiency,
            "attrition_slope":          attrition_slope,
            "set_transition_delta":     set_transition_delta,
            "first_serve_pressure":     first_serve_pressure,
            "tiebreak_differential":    tiebreak_differential,
            "hold_after_break":         hold_after_break,
        },
        "distance": distance_block,
    }


# ── per-year pipeline ──────────────────────────────────────────────────────

def _build_year_index(year: int) -> tuple:
    """
    Load one year's PBP data and return
    (pts_df, men_ids, opp_lookup, player_info, T_start)
    where player_info maps player_name → {match_id: player_num}.
    """
    pts, mat = load_year(year)
    men_ids  = men_match_ids(mat)
    T_start  = WIMBLEDON_STARTS.get(year, pd.Timestamp(f"{year}-07-01"))

    opp_lookup: Dict[tuple, str] = {}
    player_info: Dict[str, Dict[str, int]] = {}   # name → {match_id: pnum}

    men_rows = mat[mat["match_id"].isin(men_ids)]
    for _, row in men_rows.iterrows():
        mid = row["match_id"]
        # Phase B: normalise PBP player names to the canonical form so
        # fingerprint keys match the spelling in all other pipeline outputs.
        p1 = normalize_name(str(row["player1"]))
        p2 = normalize_name(str(row["player2"]))
        opp_lookup[(mid, p1)] = p2
        opp_lookup[(mid, p2)] = p1
        for name, pnum in ((p1, 1), (p2, 2)):
            if name not in player_info:
                player_info[name] = {}
            player_info[name][mid] = pnum

    return pts, men_ids, opp_lookup, player_info, T_start


def build_year_fingerprints(year: int,
                             elo: Optional[GrassElo] = None,
                             grass_df: Optional[pd.DataFrame] = None,
                             career_window: int = CAREER_WINDOW,
                             career_editions: int = CAREER_EDITIONS) -> dict:
    """
    Build fingerprints for every player in the Wimbledon draw for `year`.

    Uses the player's last `career_window` matches across the most recent
    `career_editions` Wimbledon editions (≤ `year`).  No temporal weighting
    is applied — all matches in the window are weighted equally (w_t = 1.0).
    Opponent Elo quality weights (w_q) still apply.

    Returns a dict: {player_name: fingerprint_dict}
    """
    print(f"\n── Fingerprints {year}  "
          f"(window={career_window} matches / {career_editions} editions) "
          f"──────────────────────────────────────")

    # ── Determine which editions to look back through ──────────────────────
    # Take the last `career_editions` Wimbledons from SUPPORTED_YEARS ≤ year.
    # This naturally skips 2020 (no tournament) without special-casing.
    eligible_years = [y for y in SUPPORTED_YEARS if y <= year][-career_editions:]

    # ── Load all eligible years ────────────────────────────────────────────
    year_index: Dict[int, tuple] = {}
    for y in eligible_years:
        try:
            year_index[y] = _build_year_index(y)
        except FileNotFoundError as exc:
            print(f"  Warning: skipping {y} — {exc}")

    if year not in year_index:
        raise RuntimeError(f"No data found for target year {year}")

    pts_cur, men_ids_cur, _, player_info_cur, _ = year_index[year]

    # ── Elo setup (year-aware) ─────────────────────────────────────────────
    # Use the snapshot captured just before THIS year's Wimbledon so opponent
    # quality weights don't peek forward.  Falls back to the all-time mean
    # if no per-year snapshot is available (e.g. for years outside the
    # SNAPSHOT_DATES table).
    if elo is not None:
        mean_r = elo.mean_active_rating_at(year)
        # Sanity-log so it's obvious in the build output whether the
        # year-snapshot is present.
        has_year_snap = year in elo._year_snapshots
        if not has_year_snap:
            print(f"  Note: no year-snapshot for {year} — falling back to all-time Elo")
    else:
        mean_r = None

    # ── Round → approximate day offset within a tournament ─────────────────
    RND_ORDER = {"R128": 1, "R64": 2, "R32": 3, "R16": 4, "QF": 5, "SF": 6, "F": 7}

    # ── Build one fingerprint per player in the current year's draw ────────
    all_fingerprints: Dict[str, dict] = {}

    for player_name in player_info_cur:
        # Collect every match from every eligible year for this player,
        # tagged with an approximate date for sorting purposes.
        candidates: List[dict] = []

        for y, (pts_y, men_ids_y, opp_lkp_y, pinfo_y, T_y) in year_index.items():
            if player_name not in pinfo_y:
                continue
            for match_id, pnum in pinfo_y[player_name].items():
                if match_id not in men_ids_y:
                    continue
                rnd   = match_round(match_id)
                # Later rounds happen ~2 days after earlier rounds.
                day_n = (RND_ORDER.get(rnd, 1) - 1) * 2
                match_date = T_y + pd.Timedelta(days=day_n)
                candidates.append({
                    "match_date": match_date,
                    "year":       y,
                    "match_id":   match_id,
                    "player_num": pnum,
                    "pts":        pts_y,
                    "opp_name":   opp_lkp_y.get((match_id, player_name), "Unknown"),
                    "round":      rnd,
                })

        if not candidates:
            continue

        # Most-recent first; take up to career_window matches
        candidates.sort(key=lambda c: c["match_date"], reverse=True)
        selected = candidates[:career_window]

        # ── Compute features for each selected match ───────────────────────
        match_features_list: List[dict] = []
        match_meta_list:     List[dict] = []
        weights_raw:         List[float] = []

        for rec in selected:
            match_pts = rec["pts"][rec["pts"]["match_id"] == rec["match_id"]].copy()
            if match_pts.empty:
                continue

            # No temporal weighting — all matches in the window treated equally.
            # Opponent quality weight still applied so finals/QF matches count more.
            # The opponent's Elo is looked up AS-OF the year that match was
            # played (rec["year"]), not the prediction year.  This avoids
            # weighting a 2014 Hurkacz match by his 2024 form.
            w_t = 1.0
            if elo is not None:
                match_year = rec["year"]
                w_q     = elo.quality_weight_at_by_name(rec["opp_name"], match_year)
                snap    = elo.snapshot_at_by_name(rec["opp_name"], match_year)
                opp_elo = snap["R_adjusted"] if snap else None
            else:
                w_q     = 1.0
                opp_elo = None

            feats = compute_match_features(player_name, rec["player_num"], match_pts)
            match_features_list.append(feats)
            match_meta_list.append({
                "match_id":     rec["match_id"],
                "year":         rec["year"],
                "round":        rec["round"],
                "opponent":     rec["opp_name"],
                "opponent_elo": opp_elo,
                "w_t":          w_t,
                "w_q":          round(w_q, 4),
            })
            weights_raw.append(w_t * w_q)

        if not match_features_list:
            continue

        fingerprint = build_fingerprint(
            player_name, year,
            match_features_list,
            match_meta_list,
            weights_raw,
        )

        # Attach Elo snapshot (display only) — use the year-aware snapshot
        # so 2017 fingerprints show a player's pre-Wimbledon-2017 rating
        # rather than the all-time-final value.
        if elo is not None:
            fingerprint["elo_snapshot"] = (
                elo.snapshot_at_by_name(player_name, year)
                or elo.get_snapshot_by_name(player_name)
            )

        # Record which years contributed data
        years_used = sorted({rec["year"] for rec in selected[:len(match_features_list)]})
        fingerprint["career_editions_used"] = years_used
        fingerprint["n_matches_career"]     = len(match_features_list)

        all_fingerprints[player_name] = fingerprint
        yrs_str = ", ".join(str(y) for y in years_used)
        print(f"  {player_name:<30}  {len(match_features_list):2d} matches  "
              f"(editions: {yrs_str})")

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
