/**
 * compare.js — Five-axis player comparison engine (browser-side).
 *
 * Port of src/comparison.py. Depends on eraStats object loaded from
 * data/era_stats.json and fingerprint objects from *_fingerprints.json.
 *
 * All axes return values in [−1, +1].
 * Positive = A advantage. Negative = B advantage.
 *
 * Surface weights (grass) — empirically rebalanced 2026-05:
 *   Serve/Return   0.40  (↑ from 0.35 — dominant predictor, AUC-validated)
 *   Rally Shape    0.28  (↑ from 0.20 — grass win% is 2nd-strongest signal)
 *   Break Pressure 0.15  (↓ from 0.20 — BP conversion low-signal in sparse data)
 *   Pressure       0.12  (↓ from 0.20 — SPCI/clutch carry meaning but dataset-limited)
 *   Durability     0.05  (unchanged  — grass matches are short; endurance minimal)
 */

// ── helpers ────────────────────────────────────────────────────────────────

function _confWeight(confidence) {
  if (confidence === "UNRELIABLE") return 0;
  if (confidence === "LOW")        return 0.5;
  return 1;
}

function _clamp(v, lo = -1, hi = 1) {
  return Math.max(lo, Math.min(hi, v));
}

function _zScore(value, metric, year, eraStats) {
  if (value == null) return null;
  const stats = (eraStats[String(year)] || {})[metric];
  if (!stats) return null;
  return (value - stats.mean) / stats.sd;
}

function _t1(fp, key) {
  const v = (fp.tier1 || {})[key];
  return (v && v.value != null) ? v.value : null;
}

function _t2(fp, ...keys) {
  let node = fp.tier2 || {};
  for (const k of keys) {
    if (!node || typeof node !== "object") return null;
    node = node[k];
  }
  if (node == null) return null;
  if (typeof node === "object" && node.available === false) return null;
  return (typeof node === "object") ? (node.value ?? null) : node;
}

function _t2conf(fp, ...keys) {
  let node = fp.tier2 || {};
  for (const k of keys) {
    if (!node || typeof node !== "object") return null;
    node = node[k];
  }
  return (node && typeof node === "object") ? (node.confidence ?? null) : null;
}

// ── p_serve ────────────────────────────────────────────────────────────────

function pServe(fp) {
  const fsp  = _t1(fp, "fsp_pct");
  const fspw = _t1(fp, "fspw_pct");
  const sspw = _t1(fp, "sspw_pct");
  if (fsp == null || fspw == null || sspw == null) {
    const sgw = _t1(fp, "sgw_pct");
    if (sgw != null) return Math.max(0.50, Math.min(0.80, sgw / 100 * 0.93 + 0.05));
    return 0.63;
  }
  return (fsp/100)*(fspw/100) + (1 - fsp/100)*(sspw/100);
}

// ── Axis 1: Serve vs Return ────────────────────────────────────────────────

function axisServeReturn(fpA, fpB, year, eraStats) {
  const z = (v, m) => _zScore(v, m, year, eraStats);

  const fspwA = z(_t1(fpA, "fspw_pct"), "fspw_pct");
  const fspwB = z(_t1(fpB, "fspw_pct"), "fspw_pct");
  const sspwA = z(_t1(fpA, "sspw_pct"), "sspw_pct");
  const sspwB = z(_t1(fpB, "sspw_pct"), "sspw_pct");
  const rpwA  = z(_t1(fpA, "rpw_pct"),  "rpw_pct");
  const rpwB  = z(_t1(fpB, "rpw_pct"),  "rpw_pct");

  let raw = 0, wSum = 0;
  if (fspwA != null && fspwB != null) { raw += 0.32 * (fspwA - fspwB); wSum += 0.32; }
  if (sspwA != null && sspwB != null) { raw += 0.20 * (sspwA - sspwB); wSum += 0.20; }
  if (rpwA  != null && rpwB  != null) { raw += 0.16 * (rpwA  - rpwB);  wSum += 0.16; }

  // Serve direction entropy (existing)
  const entA = _t2(fpA, "serve_entropy", "pct_of_max");
  const entB = _t2(fpB, "serve_entropy", "pct_of_max");
  if (entA != null && entB != null) {
    const zeA = z(entA, "serve_entropy_pct");
    const zeB = z(entB, "serve_entropy_pct");
    if (zeA != null && zeB != null) { raw += 0.10 * (zeA - zeB); wSum += 0.10; }
  }

  // NEW: Mean serve speed (sourced from serve_speed_courage.overall_speed_kmh)
  const spdA = _t2(fpA, "serve_speed_courage", "overall_speed_kmh");
  const spdB = _t2(fpB, "serve_speed_courage", "overall_speed_kmh");
  if (spdA != null && spdB != null) {
    // Simple normalised difference: 10 km/h gap → 0.1 edge unit
    raw += 0.12 * _clamp((spdA - spdB) / 30.0); wSum += 0.12;
  }

  // NEW: Serve speed differential (1st minus 2nd mean speed)
  const ssdA = _t2(fpA, "serve_speed_differential", "value");
  const ssdB = _t2(fpB, "serve_speed_differential", "value");
  if (ssdA != null && ssdB != null) {
    // Higher gap = bigger boom-or-bust risk; on grass the 2nd serve is attackable.
    // Opponent who can threaten the 2nd serve more (lower differential) has edge.
    raw += 0.06 * _clamp((ssdB - ssdA) / 20.0); wSum += 0.06;  // reversed: lower diff = better
  }

  // NEW: Serve depth entropy (CTL/NCTL — unpredictability of depth placement)
  const sdepA = _t2(fpA, "serve_depth_entropy", "pct_of_max");
  const sdepB = _t2(fpB, "serve_depth_entropy", "pct_of_max");
  if (sdepA != null && sdepB != null) { raw += 0.04 * _clamp((sdepA - sdepB) / 30.0); wSum += 0.04; }

  // Rescale to declared axis weight if some components missing
  return wSum > 0 ? _clamp(raw * (1.0 / wSum)) : 0;
}

// ── Axis 2: Rally Shape ────────────────────────────────────────────────────

// Kept in sync with GRASS_PRIOR in mc_worker.js
const GRASS_RALLY_W = { "1_3": 0.55, "4_6": 0.30, "7_9": 0.10, "10+": 0.05 };

function axisRallyShape(fpA, fpB) {
  const rwcA = (fpA.tier2 || {}).rally_win_curve || {};
  const rwcB = (fpB.tier2 || {}).rally_win_curve || {};

  let raw = 0, wSum = 0;

  // Rally win curve — scaled to 0.75 of axis weight
  if (Object.keys(rwcA).length && Object.keys(rwcB).length) {
    for (const [band, w] of Object.entries(GRASS_RALLY_W)) {
      const wA = rwcA[band] && rwcA[band].win_pct;
      const wB = rwcB[band] && rwcB[band].win_pct;
      if (wA == null || wB == null) continue;
      raw  += w * 0.75 * (wA - wB) / 10.0;
      wSum += w * 0.75;
    }
  }

  // NEW: Rally volatility (std dev of rally lengths on won points)
  // High volatility = adaptable all-court player (advantage on grass)
  const rvA = _t2(fpA, "rally_volatility", "value");
  const rvB = _t2(fpB, "rally_volatility", "value");
  if (rvA != null && rvB != null) {
    const waA = _confWeight(_t2conf(fpA, "rally_volatility"));
    const waB = _confWeight(_t2conf(fpB, "rally_volatility"));
    const gd  = (rvA * waA) - (rvB * waB);
    raw  += 0.25 * _clamp(gd / 2.0);
    wSum += 0.25;
  }

  return wSum > 0 ? _clamp(raw * (1.0 / wSum)) : 0;
}

// ── Axis 3: Pressure Resilience ────────────────────────────────────────────

function axisPressure(fpA, fpB) {
  let raw = 0, wSum = 0;

  // SPCI (serve pressure composite)
  const spciA = (fpA.tier2 || {}).spci || {};
  const spciB = (fpB.tier2 || {}).spci || {};
  if (spciA.value != null && spciB.value != null) {
    const wa = _confWeight(spciA.confidence);
    const wb = _confWeight(spciB.confidence);
    if (wa > 0 || wb > 0) {
      const gd = (spciA.value * wa) - (spciB.value * wb);
      raw  += 0.22 * _clamp(gd / 0.30);
      wSum += 0.22;
    }
  }

  // Clutch differential
  const clutchA = _t2(fpA, "clutch_differential", "value") != null ? (fpA.tier2.clutch_differential) : null;
  const clutchB = _t2(fpB, "clutch_differential", "value") != null ? (fpB.tier2.clutch_differential) : null;
  if (clutchA && clutchA.value != null && clutchB && clutchB.value != null) {
    const wa = _confWeight(clutchA.confidence);
    const wb = _confWeight(clutchB.confidence);
    const gd = (clutchA.value * wa) - (clutchB.value * wb);
    raw  += 0.16 * _clamp(gd / 12.0);
    wSum += 0.16;
  }

  // DF pressure delta (lower is better → reversed)
  const dfA = (fpA.tier2 || {}).df_pressure_delta;
  const dfB = (fpB.tier2 || {}).df_pressure_delta;
  if (dfA && dfA.value != null && dfB && dfB.value != null) {
    const wa = _confWeight(dfA.confidence);
    const wb = _confWeight(dfB.confidence);
    const gd = (dfB.value * wb) - (dfA.value * wa);
    raw  += 0.16 * _clamp(gd / 10.0);
    wSum += 0.16;
  }

  // NEW: Tiebreak differential (win% in tiebreaks vs baseline)
  const tbA = (fpA.tier2 || {}).tiebreak_differential;
  const tbB = (fpB.tier2 || {}).tiebreak_differential;
  if (tbA && tbA.value != null && tbB && tbB.value != null) {
    const wa = _confWeight(tbA.confidence);
    const wb = _confWeight(tbB.confidence);
    const gd = (tbA.value * wa) - (tbB.value * wb);
    raw  += 0.24 * _clamp(gd / 8.0);
    wSum += 0.24;
  }

  // NEW: Set transition delta (first 2 games of each set win% vs overall)
  const stA = (fpA.tier2 || {}).set_transition_delta;
  const stB = (fpB.tier2 || {}).set_transition_delta;
  if (stA && stA.value != null && stB && stB.value != null) {
    const wa = _confWeight(stA.confidence);
    const wb = _confWeight(stB.confidence);
    const gd = (stA.value * wa) - (stB.value * wb);
    raw  += 0.14 * _clamp(gd / 8.0);
    wSum += 0.14;
  }

  // NEW: 1st serve aggression under pressure (higher = maintains aggression)
  const fspA = (fpA.tier2 || {}).first_serve_pressure;
  const fspB = (fpB.tier2 || {}).first_serve_pressure;
  if (fspA && fspA.value != null && fspB && fspB.value != null) {
    const wa = _confWeight(fspA.confidence);
    const wb = _confWeight(fspB.confidence);
    const gd = (fspA.value * wa) - (fspB.value * wb);
    raw  += 0.08 * _clamp(gd / 10.0);
    wSum += 0.08;
  }

  return wSum > 0 ? _clamp(raw * (1.0 / wSum)) : 0;
}

// ── Axis 4: Durability ─────────────────────────────────────────────────────

function axisDurability(fpA, fpB) {
  let raw = 0, wSum = 0;

  // Raw distance covered per match (km) — higher = more mobile, better fitness
  const dA = fpA.distance || {};
  const dB = fpB.distance || {};
  const kmA = dA.avg_km_per_match;
  const kmB = dB.avg_km_per_match;
  if (kmA != null && kmB != null) {
    raw  += 0.50 * _clamp((kmA - kmB) / 2.0);
    wSum += 0.50;
  }

  // NEW: Distance run efficiency (m per rally-length unit) — lower = more efficient
  // Reversed: a player who runs fewer metres per rally-length unit expends less energy.
  const dreA = _t2(fpA, "distance_run_efficiency", "value");
  const dreB = _t2(fpB, "distance_run_efficiency", "value");
  if (dreA != null && dreB != null) {
    const waA = _confWeight(_t2conf(fpA, "distance_run_efficiency"));
    const waB = _confWeight(_t2conf(fpB, "distance_run_efficiency"));
    const gd  = (dreB * waB) - (dreA * waA);   // reversed: lower = better
    raw  += 0.30 * _clamp(gd / 0.5);
    wSum += 0.30;
  }

  // NEW: Attrition slope (OLS of per-set distance averages) — lower = less tiring
  // Negative slope = running less as match progresses (dominant performance);
  // positive slope = getting ground down into longer rallies in later sets. Reversed.
  const attA = _t2(fpA, "attrition_slope", "value");
  const attB = _t2(fpB, "attrition_slope", "value");
  if (attA != null && attB != null) {
    const gd = attB - attA;   // reversed: lower slope favours the player
    raw  += 0.20 * _clamp(gd / 0.5);
    wSum += 0.20;
  }

  return wSum > 0 ? _clamp(raw * (1.0 / wSum)) : 0;
}

// ── Axis 5: Break Pressure ─────────────────────────────────────────────────

function axisBreakPressure(fpA, fpB, year, eraStats) {
  const z = (v, m) => _zScore(v, m, year, eraStats);

  const rgwAz = z(_t1(fpA, "rgw_pct"), "rgw_pct");
  const rgwBz = z(_t1(fpB, "rgw_pct"), "rgw_pct");
  let rgwScore = 0;
  if (rgwAz != null && rgwBz != null) {
    rgwScore = 0.40 * _clamp((rgwAz - rgwBz) / 3, -1, 1);
  }

  const bpcA = (fpA.tier2 || {}).bp_creation_profile || {};
  const bpcB = (fpB.tier2 || {}).bp_creation_profile || {};
  const crA  = bpcA.bp_per_return_game;
  const crB  = bpcB.bp_per_return_game;
  const cvrA = bpcA.bp_conversion;
  const cvrB = bpcB.bp_conversion;

  // On grass, opportunities are scarce — converting them (cvrScore) matters
  // more than the volume of chances created (crScore). Weights flipped.
  let crScore  = 0;
  let cvrScore = 0;
  if (crA  != null && crB  != null) crScore  = 0.20 * _clamp((crA  - crB)  / 0.30);
  if (cvrA != null && cvrB != null) cvrScore = 0.40 * _clamp((cvrA - cvrB) / 0.20);

  return _clamp(rgwScore + crScore + cvrScore);
}

// ── weighted edge narrative ────────────────────────────────────────────────

// Axis weights rebalanced for grass (empirical regression, 2026-05):
//  • Serve/Return raised — rank + SGW% + 1stWon% are far the strongest predictors.
//  • Rally Shape raised — grass-specific win rate is the clear #2 signal.
//  • Break Pressure trimmed — converting rare BPs matters, but feature signal is sparse.
//  • Pressure trimmed — SPCI/clutch are meaningful but low-n; dataset caps the signal.
//  • Durability unchanged — grass matches are short; physical attrition minimal.
const AXIS_META = [
  { key: "serveReturn",   label: "Serve / Return",   weight: 0.40 },
  { key: "rallyShape",    label: "Rally Shape",       weight: 0.28 },
  { key: "breakPressure", label: "Break Pressure",    weight: 0.15 },
  { key: "pressure",      label: "Pressure",          weight: 0.12 },
  { key: "durability",    label: "Durability",        weight: 0.05 },
];

function edgeNarrative(axes, nameA, nameB, pWinA) {
  // Dominant axis = largest absolute structural differential
  const dominant = AXIS_META.reduce((best, ax) =>
    Math.abs(axes[ax.key]) > Math.abs(axes[best.key]) ? ax : best
  , AXIS_META[0]);

  // Always agree with the Markov winner so narrative never contradicts headline %
  const gap = Math.abs(pWinA - 0.5);
  const favours = pWinA >= 0.5 ? nameA : nameB;

  const confidence = gap < 0.03 ? "too close to call"
                   : gap < 0.08 ? "has a slight advantage"
                   : gap < 0.18 ? "is the likelier winner"
                   : "is the clear favourite";

  if (gap < 0.03) {
    return `Too close to call — the decisive axis will be ${dominant.label.toLowerCase()}.`;
  }
  return `${favours} ${confidence} — the decisive axis is ${dominant.label.toLowerCase()}.`;
}

// ── main compare function ──────────────────────────────────────────────────

function compareEngine(fpA, fpB, year, eraStats) {
  const ax1 = axisServeReturn(fpA, fpB, year, eraStats);
  const ax2 = axisRallyShape(fpA, fpB);
  const ax3 = axisPressure(fpA, fpB);
  const ax4 = axisDurability(fpA, fpB);
  const ax5 = axisBreakPressure(fpA, fpB, year, eraStats);

  const axes = {
    serveReturn:   ax1,
    rallyShape:    ax2,
    pressure:      ax3,
    durability:    ax4,
    breakPressure: ax5,
  };

  const edge = 0.40*ax1 + 0.28*ax2 + 0.12*ax3 + 0.05*ax4 + 0.15*ax5;

  const pA = pServe(fpA);
  const pB = pServe(fpB);

  const pWinA   = pMatchWin(pA, pB);
  const scoreDist = fullScoreDist(pA, pB);

  const nameA = fpA.player || "Player A";
  const nameB = fpB.player || "Player B";

  return {
    nameA, nameB, year,
    pServeA: pA, pServeB: pB,
    axes, edge,
    pWinA, pWinB: 1 - pWinA,
    scoreDist,
    narrative: edgeNarrative(axes, nameA, nameB, pWinA),
  };
}
