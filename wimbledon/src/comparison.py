"""
Comparison model — Phase 3.

Bridges two player fingerprints to the Markov engine by computing:
  1. Era-normalised (z-scored) metric values per year
  2. Confidence-gated five-axis edge values
  3. A MatchupOutput dataclass consumed by monte_carlo.py

Five axes (Wimbledon grass weights — 908-match logistic regression, v9):
  Axis 1 — Serve / Return      0.37  (paired 1st/2nd serve vs return)
  Axis 2 — Rally Shape          0.06  (low marginal value after serve/return)
  Axis 3 — Pressure Resilience  0.13  (spci, clutch, tiebreak)
  Axis 4 — Physical Durability  0.05  (grass matches are short)
  Axis 5 — Break Pressure       0.39  (rgw_pct is #1 correlated metric)

Usage:
    from comparison import compare, load_fingerprint
    fp_a = load_fingerprint("Roger Federer", 2019)
    fp_b = load_fingerprint("Novak Djokovic", 2019)
    out  = compare(fp_a, fp_b, year=2019)
    print(out)

Confidence gating rules (session decision 4):
  UNRELIABLE (n_eff < 30)  → excluded entirely from axis computation
  LOW (n_eff 30-60)        → included but edge contribution halved
  RELIABLE (n_eff > 60)    → full weight

Axis weights are surface-specific and stored in SURFACE_WEIGHTS.
Do not hardcode these values inside axis functions.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ── paths ─────────────────────────────────────────────────────────────────

DATA_DIR = Path(__file__).parent.parent / "data"

# ── surface-specific axis weights ─────────────────────────────────────────

SURFACE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "grass": {
        "axis_serve_return":  0.37,
        "axis_rally_shape":   0.06,
        "axis_pressure":      0.13,
        "axis_durability":    0.05,
        "axis_break_pressure":0.39,
    },
    "hard": {
        "axis_serve_return":  0.28,
        "axis_rally_shape":   0.22,
        "axis_pressure":      0.22,
        "axis_durability":    0.12,
        "axis_break_pressure":0.16,
    },
    "clay": {
        "axis_serve_return":  0.20,
        "axis_rally_shape":   0.30,
        "axis_pressure":      0.22,
        "axis_durability":    0.14,
        "axis_break_pressure":0.14,
    },
}

# Rally-length weights on grass (shorter rallies dominate)
GRASS_RALLY_WEIGHTS = {"1_3": 0.50, "4_6": 0.30, "7_9": 0.12, "10+": 0.08}

# RLUEP thresholds (fraction of points that are UFEs in a rally band)
RLUEP_HIGH_THRESHOLD = 0.25   # UFE rate above this in a band = fragility flag

# Axis normalization clamp — raw scores clamped here before returning
AXIS_CLAMP = 1.0


# ── confidence gating ─────────────────────────────────────────────────────

def _conf_weight(confidence: Optional[str]) -> float:
    """Return weight multiplier based on confidence flag."""
    if confidence == "UNRELIABLE":
        return 0.0   # excluded
    if confidence == "LOW":
        return 0.5   # halved
    return 1.0       # RELIABLE or missing (treat as reliable)


def _gate(value: Optional[float], confidence: Optional[str]) -> Optional[float]:
    """Return gated value, or None if excluded."""
    if value is None:
        return None
    w = _conf_weight(confidence)
    if w == 0.0:
        return None   # UNRELIABLE — excluded
    return value * w


# ── era deflator ─────────────────────────────────────────────────────────

class EraDeflator:
    """
    Computes per-metric tour means and standard deviations per year
    from the full fingerprint dataset.

    Deflated value: z = (player_value - tour_mean) / tour_sd

    A positive z means the player is above the tour mean for that metric.
    """

    # Metrics extracted from tier1
    TIER1_METRICS = ["fsp_pct", "fspw_pct", "sspw_pct", "rpw_pct",
                     "rpw_vs_1st_pct", "rpw_vs_2nd_pct",
                     "sgw_pct", "rgw_pct"]

    # Tier2 scalar metrics (key → path in tier2 dict)
    TIER2_SCALAR = {
        "serve_entropy_pct": ("serve_entropy", "pct_of_max"),
        "clutch_diff":       ("clutch_differential", "value"),
        "df_pressure_delta": ("df_pressure_delta", "value"),
        "bp_per_ret_game":   ("bp_creation_profile", "bp_per_return_game"),
        "bp_conversion":     ("bp_creation_profile", "bp_conversion"),
        "streak_init":       ("momentum_profile", "streak_initiation_rate"),
        "streak_surv":       ("momentum_profile", "streak_survival_rate"),
        "streak_rec":        ("momentum_profile", "streak_recovery_rate"),
    }

    def __init__(self, data_dir: Path = DATA_DIR):
        self._stats: Dict[int, Dict[str, Tuple[float, float]]] = {}
        self._build(data_dir)

    def _build(self, data_dir: Path) -> None:
        for fp_file in sorted(data_dir.glob("*_fingerprints.json")):
            year = int(fp_file.stem.split("_")[0])
            fps  = json.loads(fp_file.read_text())
            self._stats[year] = self._year_stats(fps)

    def _year_stats(self, fps: dict) -> Dict[str, Tuple[float, float]]:
        buckets: Dict[str, list] = {}
        for fp in fps.values():
            # Tier 1
            for m in self.TIER1_METRICS:
                v = fp.get("tier1", {}).get(m, {})
                if isinstance(v, dict) and v.get("value") is not None:
                    buckets.setdefault(m, []).append(v["value"])
            # Tier 2 scalars
            t2 = fp.get("tier2", {})
            for label, (key, sub) in self.TIER2_SCALAR.items():
                node = t2.get(key)
                if isinstance(node, dict):
                    val = node.get(sub)
                    if val is not None:
                        buckets.setdefault(label, []).append(val)
        result: Dict[str, Tuple[float, float]] = {}
        for metric, vals in buckets.items():
            if len(vals) >= 5:
                result[metric] = (float(np.mean(vals)),
                                  float(np.std(vals)) or 1.0)
        return result

    def z(self, year: int, metric: str,
          value: Optional[float]) -> Optional[float]:
        """Return z-score of value relative to year's tour distribution."""
        if value is None:
            return None
        stats = self._stats.get(year, {}).get(metric)
        if stats is None:
            return None
        mean, sd = stats
        return (value - mean) / (sd or 1.0)

    def available_years(self) -> List[int]:
        return sorted(self._stats.keys())


# ── helper: safe tier1 value ──────────────────────────────────────────────

def _t1(fp: dict, key: str) -> Optional[float]:
    v = fp.get("tier1", {}).get(key, {})
    return v.get("value") if isinstance(v, dict) else None


def _t2(fp: dict, *keys) -> Optional[float]:
    """Drill into tier2 nested dict by keys, return None if absent."""
    node = fp.get("tier2", {})
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    if node is None or (isinstance(node, dict) and node.get("available") is False):
        return None
    return node if not isinstance(node, dict) else node.get("value")


def _t2_conf(fp: dict, *keys) -> Optional[str]:
    """Return confidence flag for a tier2 metric."""
    node = fp.get("tier2", {})
    for k in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node.get("confidence") if isinstance(node, dict) else None


# ── p_serve computation ───────────────────────────────────────────────────

# Grass-court tour average return points won (~35% across ATP grass events).
# Used as the baseline for matchup adjustment: if the opponent's rpw is above
# this, the server's effective probability decreases and vice versa.
GRASS_RPW_AVG        = 35.0
GRASS_RPW_VS_1ST_AVG = 25.0   # return pts won vs 1st serve, grass tour avg
GRASS_RPW_VS_2ND_AVG = 40.0   # return pts won vs 2nd serve, grass tour avg


def p_serve_from_fp(fp: dict) -> float:
    """
    Compute baseline per-point serve win probability from Tier 1 stats.

    Formula: p = (fsp/100) × (fspw/100) + (1 - fsp/100) × (sspw/100)

    This is the mathematical definition of "fraction of serve points won."
    Falls back to 0.63 (grass tour average) if data unavailable.
    """
    fsp  = _t1(fp, "fsp_pct")
    fspw = _t1(fp, "fspw_pct")
    sspw = _t1(fp, "sspw_pct")
    if any(v is None for v in [fsp, fspw, sspw]):
        # Fallback: use sgw_pct as proxy for serve dominance
        sgw = _t1(fp, "sgw_pct")
        if sgw is not None:
            return max(0.50, min(0.80, sgw / 100 * 0.93 + 0.05))
        return 0.63   # grass tour average
    fsp_f  = fsp  / 100.0
    fspw_f = fspw / 100.0
    sspw_f = sspw / 100.0
    return fsp_f * fspw_f + (1.0 - fsp_f) * sspw_f


def p_serve_matchup(fp_server: dict, fp_returner: dict) -> float:
    """
    Compute matchup-adjusted serve win probability using split return stats.

    Pairs each serve type against its return counterpart:
      p_first  = fspw_server − (rpw_vs_1st_returner − avg) / 100
      p_second = sspw_server − (rpw_vs_2nd_returner − avg) / 100
      p_serve  = fsp × p_first + (1 − fsp) × p_second

    Falls back to blended rpw_pct adjustment if split stats unavailable.
    Clamped to [0.45, 0.85] to prevent degenerate probabilities.
    """
    fsp  = _t1(fp_server, "fsp_pct")
    fspw = _t1(fp_server, "fspw_pct")
    sspw = _t1(fp_server, "sspw_pct")

    if fsp is None or fspw is None or sspw is None:
        # Fallback: no granular serve data → use SGW-based estimate
        p_raw = p_serve_from_fp(fp_server)
        rpw = _t1(fp_returner, "rpw_pct")
        if rpw is not None:
            p_raw -= (rpw - GRASS_RPW_AVG) / 100.0
        return max(0.45, min(0.85, p_raw))

    # Try split return stats first
    rpw_v1 = _t1(fp_returner, "rpw_vs_1st_pct")
    rpw_v2 = _t1(fp_returner, "rpw_vs_2nd_pct")

    if rpw_v1 is not None and rpw_v2 is not None:
        # Paired matchup: each serve type adjusted by the returner's
        # quality against that specific serve type
        p_first  = fspw / 100.0 - (rpw_v1 - GRASS_RPW_VS_1ST_AVG) / 100.0
        p_second = sspw / 100.0 - (rpw_v2 - GRASS_RPW_VS_2ND_AVG) / 100.0
    else:
        # Fallback to blended rpw adjustment
        rpw = _t1(fp_returner, "rpw_pct")
        adj = (rpw - GRASS_RPW_AVG) / 100.0 if rpw is not None else 0.0
        p_first  = fspw / 100.0 - adj
        p_second = sspw / 100.0 - adj

    p_serve = (fsp / 100.0) * p_first + (1 - fsp / 100.0) * p_second
    return max(0.45, min(0.85, p_serve))


# ── Axis 1: Serve vs Return ───────────────────────────────────────────────

def _axis_serve_return(fp_a: dict, fp_b: dict,
                       year: int, defl: EraDeflator) -> Tuple[float, dict]:
    """
    Edge = A's serve+return package minus B's (paired by serve type).

    Empirically derived from 908-match logistic regression (2014–2024).
    Return stats are roughly as predictive as serve stats (52% vs 48%),
    so the axis pairs each serve metric against its return counterpart:

      1st-serve regime (0.52 total):
        0.26 × z(fspw_pct) edge       — 1st serve quality
        0.26 × z(rpw_vs_1st_pct) edge — return quality vs 1st serve

      2nd-serve regime (0.24 total):
        0.12 × z(sspw_pct) edge       — 2nd serve quality
        0.12 × z(rpw_vs_2nd_pct) edge — return quality vs 2nd serve

      Serve style modifiers (0.24 total):
        0.08 × z(entropy) edge         — serve direction unpredictability
        0.08 × speed edge              — mean serve speed (km/h)
        0.04 × speed differential      — 1st-2nd speed gap (reversed)
        0.04 × depth entropy edge      — serve depth placement mix
    """
    components: dict = {}
    raw = 0.0
    w_sum = 0.0

    # ── 1st-serve regime ─────────────────────────────────────────────
    # Server's 1st serve quality
    fspw_a = defl.z(year, "fspw_pct", _t1(fp_a, "fspw_pct"))
    fspw_b = defl.z(year, "fspw_pct", _t1(fp_b, "fspw_pct"))
    if fspw_a is not None and fspw_b is not None:
        raw   += 0.26 * (fspw_a - fspw_b)
        w_sum += 0.26
        components["fspw_z_A"] = round(fspw_a, 3)
        components["fspw_z_B"] = round(fspw_b, 3)
        components["fspw_edge"] = round(fspw_a - fspw_b, 3)

    # Returner's quality vs 1st serve
    rpw1_a = defl.z(year, "rpw_vs_1st_pct", _t1(fp_a, "rpw_vs_1st_pct"))
    rpw1_b = defl.z(year, "rpw_vs_1st_pct", _t1(fp_b, "rpw_vs_1st_pct"))
    if rpw1_a is not None and rpw1_b is not None:
        raw   += 0.26 * (rpw1_a - rpw1_b)
        w_sum += 0.26
        components["rpw_vs_1st_z_A"] = round(rpw1_a, 3)
        components["rpw_vs_1st_z_B"] = round(rpw1_b, 3)
        components["rpw_vs_1st_edge"] = round(rpw1_a - rpw1_b, 3)

    # ── 2nd-serve regime ─────────────────────────────────────────────
    # Server's 2nd serve quality
    sspw_a = defl.z(year, "sspw_pct", _t1(fp_a, "sspw_pct"))
    sspw_b = defl.z(year, "sspw_pct", _t1(fp_b, "sspw_pct"))
    if sspw_a is not None and sspw_b is not None:
        raw   += 0.12 * (sspw_a - sspw_b)
        w_sum += 0.12
        components["sspw_edge"] = round(sspw_a - sspw_b, 3)

    # Returner's quality vs 2nd serve
    rpw2_a = defl.z(year, "rpw_vs_2nd_pct", _t1(fp_a, "rpw_vs_2nd_pct"))
    rpw2_b = defl.z(year, "rpw_vs_2nd_pct", _t1(fp_b, "rpw_vs_2nd_pct"))
    if rpw2_a is not None and rpw2_b is not None:
        raw   += 0.12 * (rpw2_a - rpw2_b)
        w_sum += 0.12
        components["rpw_vs_2nd_edge"] = round(rpw2_a - rpw2_b, 3)

    # ── Serve style modifiers ────────────────────────────────────────
    # Entropy (direction unpredictability, z-scored)
    ent_a_raw = _t2(fp_a, "serve_entropy", "pct_of_max")
    ent_b_raw = _t2(fp_b, "serve_entropy", "pct_of_max")
    if ent_a_raw is not None and ent_b_raw is not None:
        ent_a = defl.z(year, "serve_entropy_pct", ent_a_raw)
        ent_b = defl.z(year, "serve_entropy_pct", ent_b_raw)
        if ent_a is not None and ent_b is not None:
            raw   += 0.08 * (ent_a - ent_b)
            w_sum += 0.08
            components["entropy_edge_z"] = round(ent_a - ent_b, 3)

    # Mean serve speed (overall_speed_kmh)
    spd_a = _t2(fp_a, "serve_speed_courage", "overall_speed_kmh")
    spd_b = _t2(fp_b, "serve_speed_courage", "overall_speed_kmh")
    if spd_a is not None and spd_b is not None:
        raw   += 0.08 * float(np.clip((spd_a - spd_b) / 30.0, -1, 1))
        w_sum += 0.08
        components["speed_edge_kmh"] = round(spd_a - spd_b, 1)

    # Serve speed differential (1st − 2nd mean speed) — lower = better (reversed)
    ssd_a = _t2(fp_a, "serve_speed_differential", "value")
    ssd_b = _t2(fp_b, "serve_speed_differential", "value")
    if ssd_a is not None and ssd_b is not None:
        raw   += 0.04 * float(np.clip((ssd_b - ssd_a) / 20.0, -1, 1))
        w_sum += 0.04
        components["speed_diff_A"] = round(ssd_a, 1)
        components["speed_diff_B"] = round(ssd_b, 1)

    # Serve depth entropy (CTL/NCTL mix — unpredictability of depth)
    sdep_a = _t2(fp_a, "serve_depth_entropy", "pct_of_max")
    sdep_b = _t2(fp_b, "serve_depth_entropy", "pct_of_max")
    if sdep_a is not None and sdep_b is not None:
        raw   += 0.04 * float(np.clip((sdep_a - sdep_b) / 30.0, -1, 1))
        w_sum += 0.04
        components["depth_entropy_edge"] = round(sdep_a - sdep_b, 2)

    # Rescale to declared weight if some components missing
    scaled = (raw / w_sum) if w_sum > 0 else 0.0
    edge = float(np.clip(scaled, -AXIS_CLAMP, AXIS_CLAMP))
    components["raw"] = round(raw, 4)
    return edge, components


# ── Axis 2: Rally Shape ───────────────────────────────────────────────────

def _axis_rally_shape(fp_a: dict, fp_b: dict) -> Tuple[float, dict, list]:
    """
    Edge = A's win-rate advantage integrated across rally-length bands,
    weighted by grass-surface expected rally frequency, plus rally volatility.

    Components:
      0.75 × rally_win_curve (integrated across GRASS_RALLY_WEIGHTS bands)
      0.25 × rally_volatility (std dev of rally lengths on won points)

    Rescaled if data missing. Trap zone detection requires RLUEP (currently null).
    Session Decision 12: RLUEP only adds value in 7-9 and 10+ bands.
    Returns: (edge, components, trap_zone)
    """
    rwc_a = fp_a.get("tier2", {}).get("rally_win_curve") or {}
    rwc_b = fp_b.get("tier2", {}).get("rally_win_curve") or {}

    components: dict = {"available": True}
    raw = 0.0
    w_sum = 0.0

    # --- Rally win curve — scaled to 0.75 of axis weight ---
    bands_used = 0
    for band, weight in GRASS_RALLY_WEIGHTS.items():
        win_a = rwc_a.get(band, {}).get("win_pct")
        win_b = rwc_b.get(band, {}).get("win_pct")
        if win_a is None or win_b is None:
            continue
        # Normalise: 10pp difference → 1.0 contribution at weight × 0.75
        band_edge = weight * 0.75 * (win_a - win_b) / 10.0
        raw   += band_edge
        w_sum += weight * 0.75
        components[f"edge_{band}"] = round(win_a - win_b, 2)
        bands_used += 1

    # --- NEW: Rally volatility (higher = more adaptable; advantage on grass) ---
    rv_a = _t2(fp_a, "rally_volatility", "value")
    rv_b = _t2(fp_b, "rally_volatility", "value")
    if rv_a is not None and rv_b is not None:
        wa = _conf_weight(_t2_conf(fp_a, "rally_volatility"))
        wb = _conf_weight(_t2_conf(fp_b, "rally_volatility"))
        gated_diff = (rv_a * wa) - (rv_b * wb)
        raw   += 0.25 * float(np.clip(gated_diff / 2.0, -1, 1))
        w_sum += 0.25
        components["rally_vol_A"] = round(rv_a, 3)
        components["rally_vol_B"] = round(rv_b, 3)

    if w_sum == 0:
        return 0.0, {"available": False}, []

    # Rescale to declared weight if some components missing
    scaled = raw / w_sum
    edge = float(np.clip(scaled, -AXIS_CLAMP, AXIS_CLAMP))

    # --- Trap zone detection (requires RLUEP — currently null) ---
    rluep_a = fp_a.get("tier2", {}).get("rluep")  # None until charting data wired
    trap_zone: list = []
    if isinstance(rluep_a, dict):
        for band in ["7_9", "10+"]:  # only relevant in long-rally bands
            ufe_rate = rluep_a.get(band, {}).get("ufe_rate") if rluep_a.get(band) else None
            b_win    = rwc_b.get(band, {}).get("win_pct")
            b_avg    = np.mean([v["win_pct"] for v in rwc_b.values()
                                if isinstance(v, dict) and "win_pct" in v] or [50.0])
            if (ufe_rate is not None and ufe_rate > RLUEP_HIGH_THRESHOLD
                    and b_win is not None and b_win > b_avg):
                trap_zone.append(band)

    components["trap_zone"] = trap_zone
    return edge, components, trap_zone


# ── Axis 3: Pressure Resilience ───────────────────────────────────────────

def _axis_pressure(fp_a: dict, fp_b: dict,
                   year: int, defl: EraDeflator) -> Tuple[float, dict, float, float]:
    """
    Edge = A's pressure-handling advantage over B.

    Components (rebalanced 2026-05, rescaled if data missing):
      0.22 × SPCI edge                    — confidence-gated
      0.16 × Clutch Differential edge     — confidence-gated
      0.16 × DF Pressure Delta (reversed) — confidence-gated
      0.24 × Tiebreak Differential        — win% above baseline in tiebreaks
      0.14 × Set Transition Delta         — first-2-games win% vs overall
      0.08 × 1st Serve % under Pressure   — maintains aggression on big points

    Session Decision 9: SPCI = Σ φ_k × (M_pressure - M_baseline)/M_baseline
    Returns: (edge, components, spci_a, spci_b) for Markov modifier use.
    """
    components: dict = {}
    raw = 0.0
    w_sum = 0.0

    # --- SPCI ---
    spci_a_node = fp_a.get("tier2", {}).get("spci") or {}
    spci_b_node = fp_b.get("tier2", {}).get("spci") or {}
    spci_a = spci_a_node.get("value") if isinstance(spci_a_node, dict) else None
    spci_b = spci_b_node.get("value") if isinstance(spci_b_node, dict) else None
    spci_a_conf = spci_a_node.get("confidence") if isinstance(spci_a_node, dict) else None
    spci_b_conf = spci_b_node.get("confidence") if isinstance(spci_b_node, dict) else None

    if spci_a is not None and spci_b is not None:
        wa = _conf_weight(spci_a_conf)
        wb = _conf_weight(spci_b_conf)
        if wa > 0 or wb > 0:
            gated_diff = (spci_a * wa) - (spci_b * wb)
            raw   += 0.22 * float(np.clip(gated_diff / 0.30, -1, 1))
            w_sum += 0.22
            components["spci_A"] = round(spci_a, 4)
            components["spci_B"] = round(spci_b, 4)
            components["spci_edge_gated"] = round(gated_diff, 4)

    # --- Clutch Differential ---
    clutch_a = _t2(fp_a, "clutch_differential", "value")
    clutch_b = _t2(fp_b, "clutch_differential", "value")
    clutch_a_conf = _t2_conf(fp_a, "clutch_differential")
    clutch_b_conf = _t2_conf(fp_b, "clutch_differential")

    if clutch_a is not None and clutch_b is not None:
        wa = _conf_weight(clutch_a_conf)
        wb = _conf_weight(clutch_b_conf)
        gated_diff = (clutch_a * wa) - (clutch_b * wb)
        raw   += 0.16 * float(np.clip(gated_diff / 12.0, -1, 1))
        w_sum += 0.16
        components["clutch_A"] = round(clutch_a, 2)
        components["clutch_B"] = round(clutch_b, 2)
        components["clutch_edge_gated"] = round(gated_diff, 3)
    else:
        components["clutch"] = "unavailable"

    # --- DF Pressure Delta (lower = better; A lower than B = A advantage) ---
    df_a = _t2(fp_a, "df_pressure_delta", "value")
    df_b = _t2(fp_b, "df_pressure_delta", "value")
    df_a_conf = _t2_conf(fp_a, "df_pressure_delta")
    df_b_conf = _t2_conf(fp_b, "df_pressure_delta")

    if df_a is not None and df_b is not None:
        wa = _conf_weight(df_a_conf)
        wb = _conf_weight(df_b_conf)
        gated_diff = (df_b * wb) - (df_a * wa)  # reversed: lower is better
        raw   += 0.16 * float(np.clip(gated_diff / 10.0, -1, 1))
        w_sum += 0.16
        components["df_delta_A"] = round(df_a, 2)
        components["df_delta_B"] = round(df_b, 2)

    # --- NEW: Tiebreak Differential (win% above baseline in tiebreaks) ---
    tb_a = fp_a.get("tier2", {}).get("tiebreak_differential")
    tb_b = fp_b.get("tier2", {}).get("tiebreak_differential")
    if isinstance(tb_a, dict) and isinstance(tb_b, dict):
        if tb_a.get("value") is not None and tb_b.get("value") is not None:
            wa = _conf_weight(tb_a.get("confidence"))
            wb = _conf_weight(tb_b.get("confidence"))
            gated_diff = (tb_a["value"] * wa) - (tb_b["value"] * wb)
            raw   += 0.24 * float(np.clip(gated_diff / 8.0, -1, 1))
            w_sum += 0.24
            components["tiebreak_A"] = round(tb_a["value"], 2)
            components["tiebreak_B"] = round(tb_b["value"], 2)

    # --- NEW: Set Transition Delta (first-2-games win% vs overall) ---
    st_a = fp_a.get("tier2", {}).get("set_transition_delta")
    st_b = fp_b.get("tier2", {}).get("set_transition_delta")
    if isinstance(st_a, dict) and isinstance(st_b, dict):
        if st_a.get("value") is not None and st_b.get("value") is not None:
            wa = _conf_weight(st_a.get("confidence"))
            wb = _conf_weight(st_b.get("confidence"))
            gated_diff = (st_a["value"] * wa) - (st_b["value"] * wb)
            raw   += 0.14 * float(np.clip(gated_diff / 8.0, -1, 1))
            w_sum += 0.14
            components["set_trans_A"] = round(st_a["value"], 2)
            components["set_trans_B"] = round(st_b["value"], 2)

    # --- NEW: 1st Serve % under Pressure (higher = maintains aggression) ---
    fsp_a = fp_a.get("tier2", {}).get("first_serve_pressure")
    fsp_b = fp_b.get("tier2", {}).get("first_serve_pressure")
    if isinstance(fsp_a, dict) and isinstance(fsp_b, dict):
        if fsp_a.get("value") is not None and fsp_b.get("value") is not None:
            wa = _conf_weight(fsp_a.get("confidence"))
            wb = _conf_weight(fsp_b.get("confidence"))
            gated_diff = (fsp_a["value"] * wa) - (fsp_b["value"] * wb)
            raw   += 0.08 * float(np.clip(gated_diff / 10.0, -1, 1))
            w_sum += 0.08
            components["fsp_pressure_A"] = round(fsp_a["value"], 2)
            components["fsp_pressure_B"] = round(fsp_b["value"], 2)

    # Rescale to declared weight if some components missing
    scaled = (raw / w_sum) if w_sum > 0 else 0.0
    edge = float(np.clip(scaled, -AXIS_CLAMP, AXIS_CLAMP))
    components["raw"] = round(raw, 4)

    return (edge, components,
            spci_a if spci_a is not None else 0.0,
            spci_b if spci_b is not None else 0.0)


# ── Axis 4: Physical Durability ───────────────────────────────────────────

def _axis_durability(fp_a: dict, fp_b: dict) -> Tuple[float, dict]:
    """
    Physical durability edge.

    Components (rescaled if data missing):
      0.50 × avg_km_per_match         — raw distance covered (mobility)
      0.30 × distance_run_efficiency  — m per rally-length unit (lower = better, reversed)
      0.20 × attrition_slope          — OLS slope of per-set distances (lower = better, reversed)

    Note: this axis weight increases automatically in 4th/5th set Monte
    Carlo states (see monte_carlo.py).
    """
    components: dict = {"note": "distance + efficiency + attrition proxy"}
    raw = 0.0
    w_sum = 0.0

    # --- Raw distance covered per match (km) — higher = more mobile ---
    dist_a = fp_a.get("distance", {}) or {}
    dist_b = fp_b.get("distance", {}) or {}
    km_a = dist_a.get("avg_km_per_match") if isinstance(dist_a, dict) else None
    km_b = dist_b.get("avg_km_per_match") if isinstance(dist_b, dict) else None
    if km_a is not None and km_b is not None:
        raw   += 0.50 * float(np.clip((km_a - km_b) / 2.0, -1, 1))
        w_sum += 0.50
        components["km_A"] = round(km_a, 2)
        components["km_B"] = round(km_b, 2)

    # --- NEW: Distance run efficiency (m per rally-length unit) — lower = more efficient ---
    dre_a = _t2(fp_a, "distance_run_efficiency", "value")
    dre_b = _t2(fp_b, "distance_run_efficiency", "value")
    if dre_a is not None and dre_b is not None:
        wa = _conf_weight(_t2_conf(fp_a, "distance_run_efficiency"))
        wb = _conf_weight(_t2_conf(fp_b, "distance_run_efficiency"))
        gated_diff = (dre_b * wb) - (dre_a * wa)   # reversed: lower is better for A
        raw   += 0.30 * float(np.clip(gated_diff / 0.5, -1, 1))
        w_sum += 0.30
        components["dre_A"] = round(dre_a, 3)
        components["dre_B"] = round(dre_b, 3)

    # --- NEW: Attrition slope — lower = less tiring in later sets (reversed) ---
    att_a = _t2(fp_a, "attrition_slope", "value")
    att_b = _t2(fp_b, "attrition_slope", "value")
    if att_a is not None and att_b is not None:
        gated_diff = att_b - att_a   # reversed: lower slope is better for A
        raw   += 0.20 * float(np.clip(gated_diff / 0.5, -1, 1))
        w_sum += 0.20
        components["attrition_A"] = round(att_a, 4)
        components["attrition_B"] = round(att_b, 4)

    # Rescale to declared weight if some components missing
    scaled = (raw / w_sum) if w_sum > 0 else 0.0
    edge = float(np.clip(scaled, -AXIS_CLAMP, AXIS_CLAMP))
    components["raw"] = round(raw, 4)
    return edge, components


# ── Axis 5: Break Pressure ────────────────────────────────────────────────

def _axis_break_pressure(fp_a: dict, fp_b: dict,
                         year: int, defl: EraDeflator) -> Tuple[float, dict, tuple, tuple]:
    """
    Edge = A's break-pressure advantage.

    Session Decision — Break Point Creation Profile must remain 2D:
    (creation_rate, conversion_rate) is the insight, not a single number.
    A high-creator/low-converter is structurally different from a
    low-creator/high-converter.

    Components:
      0.40 × rgw% z-score edge        — return game dominance
      0.35 × BP creation rate edge    — how often A creates BPs
      0.25 × BP conversion rate edge  — how often A converts them

    Returns: (edge, components, profile_a, profile_b)
    profile_x = (creation_rate, conversion_rate) 2D coordinate
    """
    components: dict = {}

    # --- Return game win % ---
    rgw_a_z = defl.z(year, "rgw_pct", _t1(fp_a, "rgw_pct"))
    rgw_b_z = defl.z(year, "rgw_pct", _t1(fp_b, "rgw_pct"))
    rgw_score = 0.0
    if rgw_a_z is not None and rgw_b_z is not None:
        rgw_score = 0.40 * float(np.clip(rgw_a_z - rgw_b_z, -3, 3) / 3)
        components["rgw_z_edge"] = round(rgw_a_z - rgw_b_z, 3)

    # --- BP Creation (per return game) ---
    bpc_a = fp_a.get("tier2", {}).get("bp_creation_profile") or {}
    bpc_b = fp_b.get("tier2", {}).get("bp_creation_profile") or {}

    cr_a   = bpc_a.get("bp_per_return_game")  if isinstance(bpc_a, dict) else None
    cr_b   = bpc_b.get("bp_per_return_game")  if isinstance(bpc_b, dict) else None
    cvr_a  = bpc_a.get("bp_conversion")       if isinstance(bpc_a, dict) else None
    cvr_b  = bpc_b.get("bp_conversion")       if isinstance(bpc_b, dict) else None

    creation_score = 0.0
    if cr_a is not None and cr_b is not None:
        # BP/game typically 0.3–0.8 on grass; normalise by 0.3 range
        creation_score = 0.35 * float(np.clip((cr_a - cr_b) / 0.3, -1, 1))
        components["bp_creation_A"] = round(cr_a, 3)
        components["bp_creation_B"] = round(cr_b, 3)

    conversion_score = 0.0
    if cvr_a is not None and cvr_b is not None:
        conversion_score = 0.25 * float(np.clip((cvr_a - cvr_b) / 0.2, -1, 1))
        components["bp_conversion_A"] = round(cvr_a, 3)
        components["bp_conversion_B"] = round(cvr_b, 3)

    raw = rgw_score + creation_score + conversion_score
    edge = float(np.clip(raw, -AXIS_CLAMP, AXIS_CLAMP))
    components["raw"] = round(raw, 4)

    # 2D profiles (must not be collapsed to a single number)
    profile_a = (round(cr_a, 3)  if cr_a  is not None else None,
                 round(cvr_a, 3) if cvr_a is not None else None)
    profile_b = (round(cr_b, 3)  if cr_b  is not None else None,
                 round(cvr_b, 3) if cvr_b is not None else None)

    return edge, components, profile_a, profile_b


# ── MatchupOutput ─────────────────────────────────────────────────────────

@dataclass
class MatchupOutput:
    """
    Structured output of the comparison engine.

    Consumed by monte_carlo.py (Markov inputs) and the display layer
    (verdicts, annotations, narrative).
    """
    player_a:    str
    player_b:    str
    year:        int
    surface:     str

    # ── Markov engine inputs ─────────────────────────────────────────
    p_serve_a:   float   # P(A wins point when A serves)
    p_serve_b:   float   # P(B wins point when B serves)
    spci_a:      float   # SPCI modifier for A's service games at pressure
    spci_b:      float   # SPCI modifier for B's service games at pressure

    # ── Axis edges [-1, +1], positive = A advantage ──────────────────
    axis_serve_return:   float
    axis_rally_shape:    float
    axis_pressure:       float
    axis_durability:     float
    axis_break_pressure: float
    weighted_edge:       float   # Σ weight_i × axis_i

    # ── Tactical annotations ─────────────────────────────────────────
    trap_zone:             list            # rally bands to target
    break_point_profile_a: tuple           # (creation_rate, conversion_rate)
    break_point_profile_b: tuple

    # ── Confidence & verdicts ────────────────────────────────────────
    verdicts:         dict = field(default_factory=dict)
    confidence_flags: dict = field(default_factory=dict)

    # ── Axis component breakdowns (for display) ───────────────────────
    axis_components: dict = field(default_factory=dict)

    # ── Summary ──────────────────────────────────────────────────────
    dominant_axis:  str = ""
    edge_magnitude: float = 0.0
    edge_narrative: str = ""

    def __post_init__(self) -> None:
        # Determine dominant axis
        axis_vals = {
            "Serve vs Return":     abs(self.axis_serve_return),
            "Rally Shape":         abs(self.axis_rally_shape),
            "Pressure Resilience": abs(self.axis_pressure),
            "Physical Durability": abs(self.axis_durability),
            "Break Pressure":      abs(self.axis_break_pressure),
        }
        self.dominant_axis  = max(axis_vals, key=axis_vals.get)
        self.edge_magnitude = abs(self.weighted_edge)
        self.edge_narrative = self._narrative()

    def _narrative(self) -> str:
        direction = self.player_a if self.weighted_edge >= 0 else self.player_b
        magnitude = abs(self.weighted_edge)
        if magnitude < 0.08:
            strength = "is marginally ahead of"
        elif magnitude < 0.20:
            strength = "holds a structural edge over"
        elif magnitude < 0.40:
            strength = "has a clear advantage over"
        else:
            strength = "is strongly favoured over"
        return (f"{direction} {strength} "
                f"{'their opponent' if direction == self.player_a else self.player_a} "
                f"on {self.dominant_axis.lower()} (edge {self.weighted_edge:+.2f}).")

    def summary(self) -> dict:
        return {
            "matchup":        f"{self.player_a} vs {self.player_b}",
            "year":           self.year,
            "p_serve_A":      round(self.p_serve_a, 4),
            "p_serve_B":      round(self.p_serve_b, 4),
            "axes": {
                "serve_return":   round(self.axis_serve_return,   3),
                "rally_shape":    round(self.axis_rally_shape,    3),
                "pressure":       round(self.axis_pressure,       3),
                "durability":     round(self.axis_durability,     3),
                "break_pressure": round(self.axis_break_pressure, 3),
            },
            "weighted_edge":  round(self.weighted_edge, 3),
            "dominant_axis":  self.dominant_axis,
            "narrative":      self.edge_narrative,
            "trap_zone":      self.trap_zone,
            "bp_profile_A":   self.break_point_profile_a,
            "bp_profile_B":   self.break_point_profile_b,
        }


# ── top-level compare function ────────────────────────────────────────────

# Singleton deflator — built once per process
_deflator: Optional[EraDeflator] = None


def _get_deflator() -> EraDeflator:
    global _deflator
    if _deflator is None:
        _deflator = EraDeflator(DATA_DIR)
    return _deflator


def compare(fp_a: dict, fp_b: dict,
            year: int, surface: str = "grass") -> MatchupOutput:
    """
    Compare two player fingerprints and return a MatchupOutput.

    Parameters
    ----------
    fp_a, fp_b : dicts loaded from *_fingerprints.json
    year       : Wimbledon year (for era normalisation)
    surface    : "grass" | "hard" | "clay" (affects axis weights)
    """
    defl    = _get_deflator()
    weights = SURFACE_WEIGHTS.get(surface, SURFACE_WEIGHTS["grass"])

    # ── baseline serve probabilities (matchup-adjusted) ─────────────
    # A's serve rate is adjusted for B's return quality, and vice versa.
    p_serve_a = p_serve_matchup(fp_a, fp_b)
    p_serve_b = p_serve_matchup(fp_b, fp_a)

    # ── five axes ────────────────────────────────────────────────────
    e1, c1 = _axis_serve_return(fp_a, fp_b, year, defl)
    e2, c2, trap_zone = _axis_rally_shape(fp_a, fp_b)
    e3, c3, spci_a, spci_b = _axis_pressure(fp_a, fp_b, year, defl)
    e4, c4 = _axis_durability(fp_a, fp_b)
    e5, c5, prof_a, prof_b = _axis_break_pressure(fp_a, fp_b, year, defl)

    # ── weighted edge ─────────────────────────────────────────────────
    weighted = (weights["axis_serve_return"]   * e1 +
                weights["axis_rally_shape"]    * e2 +
                weights["axis_pressure"]       * e3 +
                weights["axis_durability"]     * e4 +
                weights["axis_break_pressure"] * e5)

    # ── verdicts from fingerprints ────────────────────────────────────
    def _pick_verdicts(fp: dict) -> dict:
        verdicts = {}
        t2 = fp.get("tier2", {})
        for key in ["clutch_differential", "df_pressure_delta",
                    "serve_speed_courage", "spci"]:
            node = t2.get(key)
            if isinstance(node, dict) and "verdict" in node:
                verdicts[key] = node["verdict"]
        return verdicts

    def _pick_conf(fp: dict) -> dict:
        conf = {}
        conf["overall"] = fp.get("confidence", "UNKNOWN")
        t2 = fp.get("tier2", {})
        for key in ["clutch_differential", "df_pressure_delta",
                    "serve_speed_courage", "spci"]:
            node = t2.get(key)
            if isinstance(node, dict) and "confidence" in node:
                conf[key] = node["confidence"]
        return conf

    return MatchupOutput(
        player_a   = fp_a.get("player", "Player A"),
        player_b   = fp_b.get("player", "Player B"),
        year       = year,
        surface    = surface,
        p_serve_a  = round(p_serve_a, 4),
        p_serve_b  = round(p_serve_b, 4),
        spci_a     = round(spci_a, 4),
        spci_b     = round(spci_b, 4),
        axis_serve_return   = round(e1, 4),
        axis_rally_shape    = round(e2, 4),
        axis_pressure       = round(e3, 4),
        axis_durability     = round(e4, 4),
        axis_break_pressure = round(e5, 4),
        weighted_edge       = round(weighted, 4),
        trap_zone           = trap_zone,
        break_point_profile_a = prof_a,
        break_point_profile_b = prof_b,
        verdicts         = {"A": _pick_verdicts(fp_a), "B": _pick_verdicts(fp_b)},
        confidence_flags = {"A": _pick_conf(fp_a),     "B": _pick_conf(fp_b)},
        axis_components  = {"1_serve_return":   c1, "2_rally_shape": c2,
                            "3_pressure":       c3, "4_durability":  c4,
                            "5_break_pressure": c5},
    )


# ── grass profile merging ─────────────────────────────────────────────────

# Relevance multiplier: Wimbledon PBP data is from the actual venue so
# each observation carries more weight than a Queen's / Halle match.
WIMBLEDON_RELEVANCE = 1.5

_TIER1_KEYS = ["fsp_pct", "fspw_pct", "sspw_pct", "rpw_pct",
               "rpw_vs_1st_pct", "rpw_vs_2nd_pct",
               "sgw_pct", "rgw_pct"]


def _load_grass_profiles(year: int, data_dir: Path = DATA_DIR) -> Dict[str, dict]:
    """Load the grass profile file for `year`, returning {} if missing."""
    for gp_path in [data_dir / "processed" / f"{year}_grass_profiles.json",
                    data_dir / f"{year}_grass_profiles.json"]:
        if gp_path.exists():
            return json.loads(gp_path.read_text())
    return {}


def _lookup_name(pool: dict, name: str) -> Optional[dict]:
    """Case-insensitive + partial-match lookup in a name→dict pool."""
    # Exact (case-insensitive)
    for k, v in pool.items():
        if k.lower() == name.lower():
            return v
    # Unique partial match
    matches = [v for k, v in pool.items() if name.lower() in k.lower()]
    return matches[0] if len(matches) == 1 else None


def merge_grass_tier1(fp: dict, grass: dict) -> dict:
    """
    Blend grass-circuit Tier 1 into a Wimbledon fingerprint by sample size.

    Both sources measure the same metrics (fsp_pct, etc.) on grass courts.
    Weighting by n_eff with a relevance multiplier for Wimbledon data
    is a principled Bayesian pooling of two independent samples.
    """
    import copy
    merged = copy.deepcopy(fp)

    # Estimate fingerprint-wide n_eff from n_points (total PBP points observed)
    fp_n_total = fp.get("n_points") or fp.get("n_srv_points") or 0

    for key in _TIER1_KEYS:
        fp_metric  = fp.get("tier1", {}).get(key, {}) or {}
        gp_metric  = grass.get("tier1", {}).get(key, {}) or {}

        fp_val = fp_metric.get("value") if isinstance(fp_metric, dict) else None
        gp_val = gp_metric.get("value") if isinstance(gp_metric, dict) else None

        if fp_val is None and gp_val is None:
            continue

        # Per-metric n_eff: use metric-level if available, else top-level proxy
        fp_n = (fp_metric.get("n_eff") or fp_n_total) * WIMBLEDON_RELEVANCE
        gp_n = gp_metric.get("n_eff") or grass.get("n_srv_points") or 0

        if fp_val is None:
            # No Wimbledon data for this metric — use grass profile entirely
            merged.setdefault("tier1", {})[key] = gp_metric
        elif gp_val is None:
            pass  # keep existing fingerprint value
        else:
            # Both available — n_eff-weighted blend
            total_n = fp_n + gp_n
            if total_n > 0:
                blended = (fp_val * fp_n + gp_val * gp_n) / total_n
            else:
                blended = (fp_val + gp_val) / 2
            # Merge CI bounds if the fingerprint has them
            new_metric = {
                "value": round(blended, 1),
                "n_eff": round(total_n),
                "confidence": "RELIABLE" if total_n >= 60 else
                              "LOW"      if total_n >= 30 else "UNRELIABLE",
            }
            if "ci_90_lo" in fp_metric:
                new_metric["ci_90_lo"] = fp_metric["ci_90_lo"]
                new_metric["ci_90_hi"] = fp_metric["ci_90_hi"]
            merged["tier1"][key] = new_metric

    merged["grass_enriched"]        = True
    merged["grass_n_matches"]       = grass.get("n_matches", 0)
    merged["grass_n_srv_points"]    = grass.get("n_srv_points", 0)
    return merged


def grass_profile_as_fingerprint(grass: dict) -> dict:
    """
    Wrap a grass-only profile as a fingerprint-compatible dict.

    Used for players without Wimbledon PBP data so the comparison
    engine can still compute Tier 1 axes (serve/return/break pressure).
    Tier 2 is empty — the MC engine gracefully ignores missing Tier 2.
    """
    return {
        "player":           grass.get("player", ""),
        "year":             grass.get("year"),
        "surface":          "grass",
        "source":           "grass_atp_only",
        "confidence":       grass.get("confidence", "LOW"),
        "n_points":         grass.get("n_srv_points", 0),
        "n_matches":        grass.get("n_matches", 0),
        "tier2_available":  False,
        "tier1":            grass.get("tier1", {}),
        "tier2":            {},
        "elo_snapshot":     grass.get("elo_snapshot"),
        "grass_enriched":   True,
        "grass_n_matches":  grass.get("n_matches", 0),
        "grass_n_srv_points": grass.get("n_srv_points", 0),
    }


# ── fingerprint loader ────────────────────────────────────────────────────

def load_fingerprint(player: str, year: int,
                     data_dir: Path = DATA_DIR,
                     merge_grass: bool = True) -> Optional[dict]:
    """
    Load a player fingerprint, optionally enriched with grass ATP profile.

    Priority:
      1. Wimbledon PBP fingerprint + grass profile blend (both exist)
      2. Wimbledon PBP fingerprint alone (no grass data)
      3. Grass-only synthetic fingerprint (no Wimbledon PBP)
      4. None (player not found anywhere)
    """
    # Load fingerprint pool
    fp = None
    fp_file = data_dir / f"{year}_fingerprints.json"
    if fp_file.exists():
        fps = json.loads(fp_file.read_text())
        fp = _lookup_name(fps, player)

    # Load grass profile
    grass = None
    if merge_grass:
        gps = _load_grass_profiles(year, data_dir)
        grass = _lookup_name(gps, player)

    # Merge or fall back
    if fp is not None and grass is not None:
        return merge_grass_tier1(fp, grass)
    elif fp is not None:
        return fp
    elif grass is not None:
        return grass_profile_as_fingerprint(grass)
    else:
        return None


def load_all_fingerprints(year: int, data_dir: Path = DATA_DIR,
                          merge_grass: bool = True) -> Dict[str, dict]:
    """
    Load all fingerprints for a year, merged with grass profiles.

    Returns dict keyed by player name.
    Used by the backtest for bulk loading.
    """
    result: Dict[str, dict] = {}

    # Load Wimbledon PBP fingerprints
    fp_file = data_dir / f"{year}_fingerprints.json"
    fps = json.loads(fp_file.read_text()) if fp_file.exists() else {}

    # Load grass profiles
    gps = _load_grass_profiles(year, data_dir) if merge_grass else {}

    # All players from both sources
    all_names = set(fps.keys()) | set(gps.keys())

    for name in all_names:
        fp    = fps.get(name)
        grass = gps.get(name)

        if fp is not None and grass is not None:
            result[name] = merge_grass_tier1(fp, grass)
        elif fp is not None:
            result[name] = fp
        elif grass is not None:
            result[name] = grass_profile_as_fingerprint(grass)

    return result
