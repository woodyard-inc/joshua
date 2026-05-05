"""
monte_carlo_phased.py — Phased point Monte Carlo (Python port of mc_worker.js).

Mirrors the JavaScript browser engine 1:1 so that backtest accuracy
reflects what users see in the app.  Three-phase point model:

  Phase 1 SERVE   1st serve in/out, court side modifier, DF check
  Phase 2 RALLY   serve-type baseline + matchup adj + rally curve delta
  Phase 3 MODIFY  pressure, momentum (catch-fire), entropy, attrition…

Continuous momentum mechanic builds from the first consecutive point won.

Public API mirrors monte_carlo.py so the backtest can swap engines:

    result = simulate_match_phased(fp_a, fp_b, n=1000, seed=42)
    print(result.p_win_a)
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# ── constants ──────────────────────────────────────────────────────────────
PLATT_A = 0.42

GRASS_RPW_AVG     = 35.0
GRASS_AVG_DF_RATE = 0.035
GRASS_AVG_FSP     = 0.63
GRASS_AVG_FSPW    = 0.72
GRASS_AVG_SSPW    = 0.56

RALLY_BANDS = ["1_3", "4_6", "7_9", "10+"]
GRASS_PRIOR = {"1_3": 0.55, "4_6": 0.30, "7_9": 0.10, "10+": 0.05}

FIRST_SERVE_RALLY_WEIGHTS  = {"1_3": 1.15, "4_6": 1.00, "7_9": 0.80, "10+": 0.70}
SECOND_SERVE_RALLY_WEIGHTS = {"1_3": 0.85, "4_6": 1.05, "7_9": 1.15, "10+": 1.20}

STREAK_BOOST_PER_POINT = 0.004
STREAK_GROWTH_RATE     = 0.35
STREAK_MAX_BOOST       = 0.06
AVG_STREAK_INIT        = 5.5
AVG_STREAK_SURV        = 0.431
AVG_STREAK_REC         = 0.416

SETS_TO_WIN = 3


# ── ablation harness ──────────────────────────────────────────────────────
# Set via set_ablation(); each name corresponds to a modifier block in
# simulate_point.  Disabling = the modifier contributes 0pp to p.
ABLATABLE = {
    "courtSideServe",     # Phase 1 court-side fsp shift
    "firstServePressure", # Phase 1 fsp drop at BPs
    "dfPressure",         # Phase 1 DF rate modifier at BPs
    "rallyCurve",         # Phase 2 rally curve differential
    "courtSideRally",     # Phase 2 court-side rally outcome shift
    "serveEntropy",       # Phase 3 entropy
    "spci",               # Phase 3 SPCI under pressure
    "clutch",             # Phase 3 returner clutch differential
    "bpConversion",       # Phase 3 returner BP conversion
    "momentum",           # Phase 3 catch-fire streak boost
    "tiebreak",           # Phase 3 tiebreak differential
    "setTransition",      # Phase 3 set-opener edge
    "holdAfterBreak",     # Phase 3 post-break hold edge
    "attrition",          # Phase 3 fatigue per set
    "rallyVolatility",    # Phase 3 volatility direction kick
}

_ABLATED: set = set()

def set_ablation(modifiers: set) -> None:
    """Disable specific modifier blocks for the next simulation runs."""
    global _ABLATED
    invalid = modifiers - ABLATABLE
    if invalid:
        raise ValueError(f"Unknown ablation flags: {invalid}. Valid: {sorted(ABLATABLE)}")
    _ABLATED = set(modifiers)
    print(f"[monte_carlo_phased] Ablating: {sorted(_ABLATED)}")


# ── helpers ────────────────────────────────────────────────────────────────

def _clamp(v, lo=0.05, hi=0.95):
    return max(lo, min(hi, v))


def _conf_weight(conf: Optional[str]) -> float:
    if not conf or conf == "UNRELIABLE":
        return 0.0
    if conf == "LOW":
        return 0.40
    if conf == "MODERATE":
        return 0.75
    return 1.0


def _platt(p: float) -> float:
    if p <= 1e-9 or p >= 1 - 1e-9:
        return p
    logit = math.log(p / (1 - p))
    return 1.0 / (1.0 + math.exp(-PLATT_A * logit))


def _t1(fp: dict, key: str, default=None):
    v = (fp.get("tier1") or {}).get(key)
    if isinstance(v, dict) and v.get("value") is not None:
        return v["value"]
    return default


# ── modifier extraction (once per matchup) ─────────────────────────────────

def extract_modifiers(fp: dict) -> dict:
    t2 = fp.get("tier2") or {}

    base_df = GRASS_AVG_DF_RATE
    df_node = t2.get("df_pressure_delta")
    if isinstance(df_node, dict) and df_node.get("baseline_df_rate") is not None:
        base_df = df_node["baseline_df_rate"] / 100

    rally_curve = t2.get("rally_win_curve")

    # Pre-compute rally curve average ONCE per player (not per point).
    # Some bands may have win_pct=None (sparse data) — substitute 50.
    rally_curve_avg = None
    rally_curve_flat = None
    if rally_curve:
        vals = []
        flat = {}
        for b in RALLY_BANDS:
            wp = (rally_curve.get(b) or {}).get("win_pct")
            flat[b] = wp                     # None preserved for downstream check
            vals.append(50.0 if wp is None else wp)
        rally_curve_avg = sum(vals) / 4
        rally_curve_flat = flat

    csa = t2.get("court_side_asymmetry")
    csa_overall = csa_deuce = csa_ad = None
    if csa and csa.get("available"):
        csa_deuce = csa.get("deuce_win_pct")
        csa_ad    = csa.get("ad_win_pct")
        if csa_deuce is not None and csa_ad is not None:
            csa_overall = (csa_deuce + csa_ad) / 2
        else:
            csa_deuce = csa_ad = None

    return {
        "fsp":  (_t1(fp, "fsp_pct",  GRASS_AVG_FSP * 100)) / 100,
        "fspw": (_t1(fp, "fspw_pct", GRASS_AVG_FSPW * 100)) / 100,
        "sspw": (_t1(fp, "sspw_pct", GRASS_AVG_SSPW * 100)) / 100,
        "rpw":  _t1(fp, "rpw_pct",  GRASS_RPW_AVG),
        "baseDFRate":         base_df,
        "dfPressureDelta":    df_node if isinstance(df_node, dict) else None,
        "firstServePressure": t2.get("first_serve_pressure"),
        "csaDeuce":           csa_deuce,
        "csaAd":              csa_ad,
        "csaOverall":         csa_overall,
        "rallyCurve":         rally_curve_flat,
        "rallyCurveAvg":      rally_curve_avg,
        "serveEntropy":       (t2.get("serve_entropy") or {}).get("pct_of_max"),
        "spci":               t2.get("spci"),
        "clutch":             t2.get("clutch_differential"),
        "bpConversion":       (t2.get("bp_creation_profile") or {}).get("bp_conversion"),
        "momentum":           t2.get("momentum_profile"),
        "tiebreak":           t2.get("tiebreak_differential"),
        "setTransition":      t2.get("set_transition_delta"),
        "holdAfterBreak":     t2.get("hold_after_break"),
        "attrition":          t2.get("attrition_slope"),
        "rallyVolatility":    t2.get("rally_volatility"),
        "rluep":              t2.get("rluep"),
    }


# ── rally distribution ────────────────────────────────────────────────────

def _build_rally_dist(srv_n_dict: dict, ret_n_dict: dict) -> dict:
    """Build rally length distribution. n_dicts are pre-extracted band → n maps."""
    sn = sum(srv_n_dict.values()) if srv_n_dict else 0
    rn = sum(ret_n_dict.values()) if ret_n_dict else 0

    dist = {}
    total = 0.0
    for b in RALLY_BANDS:
        prior = GRASS_PRIOR[b]
        ss = (srv_n_dict.get(b, 0) / sn) if sn >= 30 else prior
        rs = (ret_n_dict.get(b, 0) / rn) if rn >= 30 else prior
        dist[b] = 0.45 * prior + 0.35 * ss + 0.20 * rs
        total += dist[b]
    for b in RALLY_BANDS:
        dist[b] /= total
    return dist


def _matchup_rally_dists(srv_mods: dict, ret_mods: dict, fp_srv: dict, fp_ret: dict):
    """
    Pre-compute the 1st-serve and 2nd-serve rally distributions for one
    serving direction so that _reweight isn't called on every point.
    Returns (first_serve_dist, second_serve_dist).
    """
    srv_curve = (fp_srv.get("tier2") or {}).get("rally_win_curve") or {}
    ret_curve = (fp_ret.get("tier2") or {}).get("rally_win_curve") or {}
    srv_n = {b: (srv_curve.get(b) or {}).get("n", 0) for b in RALLY_BANDS}
    ret_n = {b: (ret_curve.get(b) or {}).get("n", 0) for b in RALLY_BANDS}
    base = _build_rally_dist(srv_n, ret_n)
    return _reweight(base, FIRST_SERVE_RALLY_WEIGHTS), _reweight(base, SECOND_SERVE_RALLY_WEIGHTS)


def _reweight(dist: dict, weights: dict) -> dict:
    out = {}
    total = 0.0
    for b in RALLY_BANDS:
        out[b] = dist[b] * weights.get(b, 1.0)
        total += out[b]
    for b in RALLY_BANDS:
        out[b] /= total
    return out


def _sample_band(dist: dict, rng) -> str:
    r = rng.random()
    cum = 0.0
    for b in RALLY_BANDS:
        cum += dist[b]
        if r < cum:
            return b
    return "10+"


# ── PHASED POINT SIMULATION ───────────────────────────────────────────────

def simulate_point(srv: dict, ret: dict, state: dict, rng) -> bool:
    """Returns True if server wins the point.
    state must contain: courtSide, isBreakPoint, isDeuce, isTiebreak,
    setIndex, isSetOpener, isPostBreak, streakCount,
    rallyDist1 (1st-serve dist), rallyDist2 (2nd-serve dist)."""

    rand = rng.random
    is_deuce_court = state["courtSide"] == "deuce"

    # ──────────────── PHASE 1: SERVE ────────────────
    fsp = srv["fsp"]

    if "courtSideServe" not in _ABLATED and srv["csaOverall"] is not None:
        side = srv["csaDeuce"] if is_deuce_court else srv["csaAd"]
        fsp += ((side - srv["csaOverall"]) / 100) * 0.30

    if "firstServePressure" not in _ABLATED and state["isBreakPoint"]:
        fsp_press = srv["firstServePressure"]
        if fsp_press and fsp_press.get("available") and fsp_press.get("value") is not None:
            fsp += (fsp_press["value"] / 100) * 0.40

    if fsp < 0.30: fsp = 0.30
    elif fsp > 0.90: fsp = 0.90

    is_second = rand() >= fsp

    if is_second:
        fsp_overall = srv["fsp"]
        denom = 1 - fsp_overall
        if denom < 0.20: denom = 0.20
        df_rate = srv["baseDFRate"] / denom

        if "dfPressure" not in _ABLATED and state["isBreakPoint"]:
            dfp = srv["dfPressureDelta"]
            if dfp and dfp.get("modifier_delta") is not None:
                w = _conf_weight(dfp.get("confidence"))
                df_rate += w * (dfp["modifier_delta"] / denom)

        if df_rate < 0.01: df_rate = 0.01
        elif df_rate > 0.20: df_rate = 0.20
        if rand() < df_rate:
            return False  # double fault

    # ──────────────── PHASE 2: RALLY ────────────────
    rd = state["rallyDist2"] if is_second else state["rallyDist1"]
    band = _sample_band(rd, rng)

    p = srv["sspw"] if is_second else srv["fspw"]
    p -= (ret["rpw"] - GRASS_RPW_AVG) / 100

    if "rallyCurve" not in _ABLATED:
        src = srv["rallyCurve"]
        rcc = ret["rallyCurve"]
        if src and rcc:
            s_win = src.get(band)
            r_win = rcc.get(band)
            if s_win is not None and r_win is not None:
                band_p = 0.5 * (s_win / 100) + 0.5 * (1 - r_win / 100)
                avg_p  = 0.5 * (srv["rallyCurveAvg"] / 100) + 0.5 * (1 - ret["rallyCurveAvg"] / 100)
                p += (band_p - avg_p) * 0.8

    if "courtSideRally" not in _ABLATED:
        if srv["csaOverall"] is not None:
            side = srv["csaDeuce"] if is_deuce_court else srv["csaAd"]
            p += ((side - srv["csaOverall"]) / 100) * 0.60

        if ret["csaOverall"] is not None:
            sideR = ret["csaDeuce"] if is_deuce_court else ret["csaAd"]
            p -= ((sideR - ret["csaOverall"]) / 100) * 0.30

    # ──────────────── PHASE 3: MODIFIERS ────────────────
    if "serveEntropy" not in _ABLATED and srv.get("serveEntropy") is not None:
        p += 0.025 * ((srv["serveEntropy"] / 100) - 0.75)

    if state["isBreakPoint"] or state["isDeuce"]:
        if "spci" not in _ABLATED:
            spci = srv.get("spci")
            if spci and spci.get("modifier_delta") is not None:
                p += _conf_weight(spci.get("confidence")) * spci["modifier_delta"] * 0.50

        if "clutch" not in _ABLATED:
            clutch = ret.get("clutch")
            if clutch and clutch.get("modifier_delta") is not None:
                p -= _conf_weight(clutch.get("confidence")) * (clutch["modifier_delta"] / 100) * 0.60

        if "bpConversion" not in _ABLATED and state["isBreakPoint"] and ret.get("bpConversion") is not None:
            p -= (ret["bpConversion"] - 0.45) * 0.15

    # Continuous momentum (catch fire from point 1)
    streak = state.get("streakCount", 0)
    if "momentum" not in _ABLATED and streak != 0:
        abs_s = abs(streak)
        is_srv_streak = streak > 0
        streaker = srv if is_srv_streak else ret
        resister = ret if is_srv_streak else srv

        mom_s = streaker.get("momentum") or {}
        mom_r = resister.get("momentum") or {}

        init = mom_s.get("streak_initiation_rate", AVG_STREAK_INIT)
        surv = mom_s.get("streak_survival_rate",   AVG_STREAK_SURV)
        rec  = mom_r.get("streak_recovery_rate",   AVG_STREAK_REC)

        init_n = math.sqrt(init / AVG_STREAK_INIT)
        surv_n = surv / AVG_STREAK_SURV
        rec_n  = max(0.5, rec / AVG_STREAK_REC)

        streakiness = min(max((init_n * 0.35 + surv_n * 0.65) / rec_n, 0.5), 1.8)
        raw = STREAK_BOOST_PER_POINT * abs_s * (1 + (abs_s - 1) * STREAK_GROWTH_RATE)
        boost = min(raw * streakiness, STREAK_MAX_BOOST)

        p += boost if is_srv_streak else -boost

    if "tiebreak" not in _ABLATED and state["isTiebreak"]:
        tb = srv.get("tiebreak")
        tbR = ret.get("tiebreak")
        if tb and tb.get("available") and tb.get("value") is not None:
            p += _conf_weight(tb.get("confidence")) * (tb["value"] / 100) * 0.60
        if tbR and tbR.get("available") and tbR.get("value") is not None:
            p -= _conf_weight(tbR.get("confidence")) * (tbR["value"] / 100) * 0.60

    if "setTransition" not in _ABLATED and state["isSetOpener"]:
        st = srv.get("setTransition")
        stR = ret.get("setTransition")
        if st and st.get("available") and st.get("value") is not None:
            p += _conf_weight(st.get("confidence")) * (st["value"] / 100) * 0.50
        if stR and stR.get("available") and stR.get("value") is not None:
            p -= _conf_weight(stR.get("confidence")) * (stR["value"] / 100) * 0.50

    if "holdAfterBreak" not in _ABLATED and state["isPostBreak"]:
        habr = srv.get("holdAfterBreak")
        if habr and habr.get("available") and habr.get("value") is not None:
            p += _conf_weight(habr.get("confidence")) * (habr["value"] / 100) * 0.50

    if "attrition" not in _ABLATED and state["setIndex"] is not None and state["setIndex"] > 0:
        att = srv.get("attrition")
        attR = ret.get("attrition")
        if att and att.get("available") and att.get("value") is not None:
            p += _conf_weight(att.get("confidence")) * (-(att["value"] / 2.0) * state["setIndex"] * 0.02)
        if attR and attR.get("available") and attR.get("value") is not None:
            p -= _conf_weight(attR.get("confidence")) * (-(attR["value"] / 2.0) * state["setIndex"] * 0.02)

    if "rallyVolatility" not in _ABLATED:
        rvS = srv.get("rallyVolatility")
        rvR = ret.get("rallyVolatility")
        if rvS and rvS.get("available") and rvR and rvR.get("available"):
            rv_diff = rvS["value"] - rvR["value"]
            direction = -1 if p > 0.5 else 1
            p += direction * (rv_diff / 5.0) * 0.012

    return rng.random() < _clamp(p)


# ── game / tiebreak / set / match ─────────────────────────────────────────

def _simulate_game(srv, ret, rd1, rd2, opts, rng) -> bool:
    s = r = 0
    total_pts = 0
    streak = 0
    set_index   = opts.get("setIndex", 0)
    is_opener   = opts.get("isSetOpener", False)
    is_post_brk = opts.get("isPostBreak", False)
    state = {
        "isTiebreak":  False,
        "setIndex":    set_index,
        "isSetOpener": is_opener,
        "isPostBreak": is_post_brk,
        "rallyDist1":  rd1,
        "rallyDist2":  rd2,
    }
    while True:
        at_ad_srv = s > r and s >= 4
        at_ad_ret = r > s and r >= 4
        at_deuce  = s >= 3 and r >= 3 and s == r
        is_bp     = at_ad_ret or (r == 3 and s < 3)
        state["isBreakPoint"] = is_bp
        state["isDeuce"]      = at_deuce or at_ad_srv or at_ad_ret
        state["courtSide"]    = "deuce" if (total_pts % 2 == 0) else "ad"
        state["streakCount"]  = streak

        won = simulate_point(srv, ret, state, rng)

        streak = (streak + 1) if (won and streak > 0) else (1 if won else (streak - 1) if streak < 0 else -1)
        total_pts += 1

        if at_ad_srv:
            if won: return True
            s, r = 3, 3
            continue
        if at_ad_ret:
            if not won: return False
            s, r = 3, 3
            continue
        if won: s += 1
        else:   r += 1
        if s >= 4 and s - r >= 2: return True
        if r >= 4 and r - s >= 2: return False
        if s == r and s >= 3: s, r = 3, 3


def _simulate_tiebreak(srv_a, srv_b, ab_dists, ba_dists, set_index, rng) -> bool:
    pA = pB = 0
    pts = 0
    streak = 0  # from A's perspective

    def a_serves(n):
        if n == 0: return True
        return ((n - 1) // 2) % 2 == 1

    state = {
        "isTiebreak":  True,
        "setIndex":    set_index,
        "isSetOpener": False,
        "isPostBreak": False,
    }
    while True:
        is_a_serving = a_serves(pts)
        if is_a_serving:
            srv, ret = srv_a, srv_b
            state["rallyDist1"], state["rallyDist2"] = ab_dists
        else:
            srv, ret = srv_b, srv_a
            state["rallyDist1"], state["rallyDist2"] = ba_dists

        at_deuce = pA >= 6 and pB >= 6 and pA == pB
        adA = pA > pB and pA >= 7
        adB = pB > pA and pB >= 7

        state["courtSide"]    = "deuce" if (pts % 2 == 0) else "ad"
        state["isBreakPoint"] = adB
        state["isDeuce"]      = at_deuce or adA or adB
        state["streakCount"]  = streak if is_a_serving else -streak

        s_won = simulate_point(srv, ret, state, rng)

        a_won = s_won if is_a_serving else (not s_won)
        streak = (streak + 1) if (a_won and streak > 0) else (1 if a_won else (streak - 1) if streak < 0 else -1)

        if adA:
            if a_won: return True
            pA, pB = 6, 6
        elif adB:
            if not a_won: return False
            pA, pB = 6, 6
        else:
            if a_won: pA += 1
            else:     pB += 1

        if pA >= 7 and pA - pB >= 2: return True
        if pB >= 7 and pB - pA >= 2: return False
        pts += 1


def _simulate_set(srv_a, srv_b, ab_dists, ba_dists, a_serves_first, set_index, rng):
    gA = gB = 0
    a_serves = a_serves_first
    game_in_set = 0
    last_break = False

    while True:
        if a_serves:
            srv, ret = srv_a, srv_b
            rd1, rd2 = ab_dists
        else:
            srv, ret = srv_b, srv_a
            rd1, rd2 = ba_dists

        held = _simulate_game(srv, ret, rd1, rd2, {
            "isSetOpener": game_in_set < 2,
            "isPostBreak": last_break,
            "setIndex":    set_index,
        }, rng)

        last_break = not held
        game_in_set += 1

        if a_serves:
            if held: gA += 1
            else:    gB += 1
        else:
            if held: gB += 1
            else:    gA += 1

        if gA == 6 and gB == 6:
            return _simulate_tiebreak(srv_a, srv_b, ab_dists, ba_dists, set_index, rng), 7, 6
        if gA >= 6 and gA - gB >= 2: return True, gA, gB
        if gB >= 6 and gB - gA >= 2: return False, gA, gB
        if gA == 7: return True, 7, 5
        if gB == 7: return False, 5, 7
        a_serves = not a_serves


def _simulate_one_match(srv_a, srv_b, ab_dists, ba_dists, rng):
    sA = sB = 0
    a_serves_set = True
    set_idx = 0
    while sA < SETS_TO_WIN and sB < SETS_TO_WIN:
        a_won, _, _ = _simulate_set(srv_a, srv_b, ab_dists, ba_dists, a_serves_set, set_idx, rng)
        if a_won: sA += 1
        else:     sB += 1
        a_serves_set = not a_serves_set
        set_idx += 1
    return sA, sB


# ── public API ────────────────────────────────────────────────────────────

@dataclass
class PhasedResult:
    player_a: str
    player_b: str
    n_simulations: int
    p_win_a: float
    p_win_b: float
    score_dist: Dict[str, float] = field(default_factory=dict)
    p_win_a_raw: float = 0.0


def simulate_match_phased(fp_a: dict, fp_b: dict,
                          n: int = 10_000,
                          seed: Optional[int] = None) -> PhasedResult:
    rng = random.Random(seed)
    mods_a = extract_modifiers(fp_a)
    mods_b = extract_modifiers(fp_b)

    # Pre-compute reweighted rally distributions (1st/2nd serve) for each
    # serving direction.  Constant for the whole match.
    ab_dists = _matchup_rally_dists(mods_a, mods_b, fp_a, fp_b)  # A serves
    ba_dists = _matchup_rally_dists(mods_b, mods_a, fp_b, fp_a)  # B serves

    score_count = {}
    wins_a = 0
    for _ in range(n):
        sA, sB = _simulate_one_match(mods_a, mods_b, ab_dists, ba_dists, rng)
        key = f"{sA}-{sB}"
        score_count[key] = score_count.get(key, 0) + 1
        if sA > sB:
            wins_a += 1

    p_raw = wins_a / n
    p_cal = _platt(p_raw)

    return PhasedResult(
        player_a      = fp_a.get("player", "A"),
        player_b      = fp_b.get("player", "B"),
        n_simulations = n,
        p_win_a       = p_cal,
        p_win_b       = 1 - p_cal,
        score_dist    = {k: v / n for k, v in score_count.items()},
        p_win_a_raw   = p_raw,
    )
