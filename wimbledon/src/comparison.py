"""
Comparison model — Phase 3.

Bridges two player fingerprints to the Markov engine by computing:
  1. Era-normalised (z-scored) metric values per year
  2. Confidence-gated five-axis edge values
  3. A MatchupOutput dataclass consumed by monte_carlo.py

Five axes (Wimbledon grass weights):
  Axis 1 — Serve vs Return     0.35
  Axis 2 — Rally Shape         0.15
  Axis 3 — Pressure Resilience 0.25
  Axis 4 — Physical Durability 0.10  (partial — RDI/DCR not yet computed)
  Axis 5 — Break Pressure      0.15

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
        "axis_serve_return":  0.35,
        "axis_rally_shape":   0.15,
        "axis_pressure":      0.25,
        "axis_durability":    0.10,
        "axis_break_pressure":0.15,
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
            # Service game win % → approximate point win % on serve
            # sgw = (p_serve)^4 * (1 + 4q + 10q^2) + 20p^3q^3 * p^2/(p^2+q^2)
            # Numerically invert: rough approximation
            return max(0.50, min(0.80, sgw / 100 * 0.93 + 0.05))
        return 0.63   # grass tour average
    fsp_f  = fsp  / 100.0
    fspw_f = fspw / 100.0
    sspw_f = sspw / 100.0
    return fsp_f * fspw_f + (1.0 - fsp_f) * sspw_f


# ── Axis 1: Serve vs Return ───────────────────────────────────────────────

def _axis_serve_return(fp_a: dict, fp_b: dict,
                       year: int, defl: EraDeflator) -> Tuple[float, dict]:
    """
    Edge = A's serve+return package minus B's (fully differential).

    All components are (A - B) differences in z-scores, ensuring the
    axis is symmetric: compare(A,B) == -compare(B,A), and identical
    fingerprints produce zero edge.

    Components (grass weights sum to 1.0):
      0.40 × (z(fspw_pct_A) - z(fspw_pct_B))   — 1st serve quality edge
      0.25 × (z(sspw_pct_A) - z(sspw_pct_B))   — 2nd serve quality edge
      0.20 × (z(rpw_pct_A)  - z(rpw_pct_B))    — return quality edge
      0.15 × (z(entropy_A)  - z(entropy_B))     — serve unpredictability edge

    Session Decision 7: entropy is directional (W/B/T) only.
    Session Decision 8: SSCI is NOT applied here (it's an SPCI component).
    """
    components: dict = {}

    # --- 1st serve quality differential ---
    fspw_a = defl.z(year, "fspw_pct", _t1(fp_a, "fspw_pct"))
    fspw_b = defl.z(year, "fspw_pct", _t1(fp_b, "fspw_pct"))
    serve1_score = 0.0
    if fspw_a is not None and fspw_b is not None:
        serve1_score = 0.40 * (fspw_a - fspw_b)
        components["fspw_z_A"] = round(fspw_a, 3)
        components["fspw_z_B"] = round(fspw_b, 3)
        components["fspw_edge"] = round(fspw_a - fspw_b, 3)

    # --- 2nd serve quality differential ---
    sspw_a = defl.z(year, "sspw_pct", _t1(fp_a, "sspw_pct"))
    sspw_b = defl.z(year, "sspw_pct", _t1(fp_b, "sspw_pct"))
    serve2_score = 0.0
    if sspw_a is not None and sspw_b is not None:
        serve2_score = 0.25 * (sspw_a - sspw_b)
        components["sspw_edge"] = round(sspw_a - sspw_b, 3)

    # --- Return quality differential ---
    rpw_a = defl.z(year, "rpw_pct", _t1(fp_a, "rpw_pct"))
    rpw_b = defl.z(year, "rpw_pct", _t1(fp_b, "rpw_pct"))
    return_score = 0.0
    if rpw_a is not None and rpw_b is not None:
        return_score = 0.20 * (rpw_a - rpw_b)
        components["rpw_edge"] = round(rpw_a - rpw_b, 3)

    # --- Entropy (direction unpredictability) ---
    ent_a_raw = _t2(fp_a, "serve_entropy", "pct_of_max")
    ent_b_raw = _t2(fp_b, "serve_entropy", "pct_of_max")
    entropy_score = 0.0
    if ent_a_raw is not None and ent_b_raw is not None:
        ent_a = defl.z(year, "serve_entropy_pct", ent_a_raw)
        ent_b = defl.z(year, "serve_entropy_pct", ent_b_raw)
        if ent_a is not None and ent_b is not None:
            entropy_score = 0.15 * (ent_a - ent_b)
            components["entropy_edge_z"] = round(ent_a - ent_b, 3)

    raw = serve1_score + serve2_score + return_score + entropy_score
    edge = float(np.clip(raw, -AXIS_CLAMP, AXIS_CLAMP))
    components["raw"] = round(raw, 4)
    return edge, components


# ── Axis 2: Rally Shape ───────────────────────────────────────────────────

def _axis_rally_shape(fp_a: dict, fp_b: dict) -> Tuple[float, dict, list]:
    """
    Edge = A's win-rate advantage integrated across rally-length bands,
    weighted by grass-surface expected rally frequency.

    Also identifies tactical trap zones: bands where A's RLUEP spike
    coincides with B's win-rate peak (if RLUEP is available).

    Session Decision 12: RLUEP only adds value in 7-9 and 10+ bands.
    Returns: (edge, components, trap_zone)
    """
    rwc_a = fp_a.get("tier2", {}).get("rally_win_curve") or {}
    rwc_b = fp_b.get("tier2", {}).get("rally_win_curve") or {}

    if not rwc_a or not rwc_b:
        return 0.0, {"available": False}, []

    components: dict = {"available": True}
    raw = 0.0
    bands_used = 0

    for band, weight in GRASS_RALLY_WEIGHTS.items():
        win_a = rwc_a.get(band, {}).get("win_pct")
        win_b = rwc_b.get(band, {}).get("win_pct")
        if win_a is None or win_b is None:
            continue
        # Normalise: 10pp difference → 1.0 contribution at weight 1.0
        band_edge = weight * (win_a - win_b) / 10.0
        raw += band_edge
        components[f"edge_{band}"] = round(win_a - win_b, 2)
        bands_used += 1

    if bands_used == 0:
        return 0.0, {"available": False}, []

    edge = float(np.clip(raw, -AXIS_CLAMP, AXIS_CLAMP))

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

    Components:
      0.50 × SPCI edge (A.spci - B.spci)          — confidence-gated
      0.35 × Clutch Differential edge              — confidence-gated
      0.15 × DF Pressure Delta edge (lower better) — confidence-gated

    Session Decision 9: SPCI = Σ φ_k × (M_pressure - M_baseline)/M_baseline
    Session Decision 8: SSCI only meaningful vs opponent RDAS (deferred).

    Returns: (edge, components, spci_a, spci_b) for Markov modifier use.
    """
    components: dict = {}

    # --- SPCI ---
    spci_a_node = fp_a.get("tier2", {}).get("spci") or {}
    spci_b_node = fp_b.get("tier2", {}).get("spci") or {}
    spci_a = spci_a_node.get("value") if isinstance(spci_a_node, dict) else None
    spci_b = spci_b_node.get("value") if isinstance(spci_b_node, dict) else None
    spci_a_conf = spci_a_node.get("confidence") if isinstance(spci_a_node, dict) else None
    spci_b_conf = spci_b_node.get("confidence") if isinstance(spci_b_node, dict) else None

    spci_score = 0.0
    if spci_a is not None and spci_b is not None:
        wa = _conf_weight(spci_a_conf)
        wb = _conf_weight(spci_b_conf)
        if wa > 0 or wb > 0:
            # SPCI range roughly -0.3 to +0.3 → normalise to [-1,+1]
            gated_diff = (spci_a * wa) - (spci_b * wb)
            spci_score = 0.50 * np.clip(gated_diff / 0.30, -1, 1)
            components["spci_A"] = round(spci_a, 4)
            components["spci_B"] = round(spci_b, 4)
            components["spci_edge_gated"] = round(gated_diff, 4)

    # --- Clutch Differential ---
    clutch_a = _t2(fp_a, "clutch_differential", "value")
    clutch_b = _t2(fp_b, "clutch_differential", "value")
    clutch_a_conf = _t2_conf(fp_a, "clutch_differential")
    clutch_b_conf = _t2_conf(fp_b, "clutch_differential")

    clutch_score = 0.0
    if clutch_a is not None and clutch_b is not None:
        wa = _conf_weight(clutch_a_conf)
        wb = _conf_weight(clutch_b_conf)
        gated_diff = (clutch_a * wa) - (clutch_b * wb)
        # Clutch diff range roughly -15 to +15pp → normalise
        clutch_score = 0.35 * float(np.clip(gated_diff / 12.0, -1, 1))
        components["clutch_A"] = round(clutch_a, 2)
        components["clutch_B"] = round(clutch_b, 2)
        components["clutch_edge_gated"] = round(gated_diff, 3)
    elif clutch_a is None and clutch_b is None:
        components["clutch"] = "unavailable"

    # --- DF Pressure Delta (lower = better; A lower than B = A advantage) ---
    df_a = _t2(fp_a, "df_pressure_delta", "value")
    df_b = _t2(fp_b, "df_pressure_delta", "value")
    df_a_conf = _t2_conf(fp_a, "df_pressure_delta")
    df_b_conf = _t2_conf(fp_b, "df_pressure_delta")

    df_score = 0.0
    if df_a is not None and df_b is not None:
        wa = _conf_weight(df_a_conf)
        wb = _conf_weight(df_b_conf)
        # A lower DF delta = more pressure-resistant = positive edge for A
        gated_diff = (df_b * wb) - (df_a * wa)  # reversed: lower is better
        df_score = 0.15 * float(np.clip(gated_diff / 10.0, -1, 1))
        components["df_delta_A"] = round(df_a, 2)
        components["df_delta_B"] = round(df_b, 2)

    raw = spci_score + clutch_score + df_score
    edge = float(np.clip(raw, -AXIS_CLAMP, AXIS_CLAMP))
    components["raw"] = round(raw, 4)

    return (edge, components,
            spci_a if spci_a is not None else 0.0,
            spci_b if spci_b is not None else 0.0)


# ── Axis 4: Physical Durability ───────────────────────────────────────────

def _axis_durability(fp_a: dict, fp_b: dict) -> Tuple[float, dict]:
    """
    Physical durability edge.

    Full implementation requires RDI (Recovery Decrement Index) and
    DCR (Defensive Conversion Rate) — not yet computed in fingerprints.

    Current proxy: distance run per match (higher = more physical demand
    absorbed, indicating durability).  Returns partial score flagged as such.

    Note: this axis weight increases automatically in 4th/5th set Monte
    Carlo states (see monte_carlo.py).
    """
    components: dict = {"partial": True,
                        "missing": ["RDI", "DCR"],
                        "note": "Full implementation requires per-point distance data"}

    # Distance run proxy
    dist_a = fp_a.get("distance", {})
    dist_b = fp_b.get("distance", {})

    if isinstance(dist_a, dict) and isinstance(dist_b, dict):
        km_a = dist_a.get("avg_km_per_match")
        km_b = dist_b.get("avg_km_per_match")
        if km_a is not None and km_b is not None:
            # Higher distance = more physical = proxy for durability
            edge = float(np.clip((km_a - km_b) / 2.0, -0.5, 0.5))
            components["km_A"] = round(km_a, 2)
            components["km_B"] = round(km_b, 2)
            return edge, components

    return 0.0, components


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

    # ── baseline serve probabilities ─────────────────────────────────
    p_serve_a = p_serve_from_fp(fp_a)
    p_serve_b = p_serve_from_fp(fp_b)

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


# ── fingerprint loader ────────────────────────────────────────────────────

def load_fingerprint(player: str, year: int,
                     data_dir: Path = DATA_DIR) -> Optional[dict]:
    """Load a single player fingerprint from the docs/data JSON files."""
    fp_file = data_dir / f"{year}_fingerprints.json"
    if not fp_file.exists():
        raise FileNotFoundError(f"No fingerprint file for {year}: {fp_file}")
    fps = json.loads(fp_file.read_text())
    # Case-insensitive name lookup
    for name, fp in fps.items():
        if name.lower() == player.lower():
            return fp
    # Partial match fallback
    matches = [fp for name, fp in fps.items()
               if player.lower() in name.lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        names = [fp["player"] for fp in matches]
        raise ValueError(f"Ambiguous player '{player}'. Matches: {names}")
    raise ValueError(f"Player '{player}' not found in {year} fingerprints. "
                     f"Available: {sorted(fps.keys())[:10]}...")
