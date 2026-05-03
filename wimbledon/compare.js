/**
 * compare.js — Five-axis player comparison engine (browser-side).
 *
 * Port of src/comparison.py. Depends on eraStats object loaded from
 * data/era_stats.json and fingerprint objects from *_fingerprints.json.
 *
 * All axes return values in [−1, +1].
 * Positive = A advantage. Negative = B advantage.
 *
 * Surface weights (grass):
 *   Serve/Return   0.35
 *   Rally Shape    0.15
 *   Pressure       0.25
 *   Durability     0.10
 *   Break Pressure 0.15
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

  let raw = 0;
  if (fspwA != null && fspwB != null) raw += 0.40 * (fspwA - fspwB);
  if (sspwA != null && sspwB != null) raw += 0.25 * (sspwA - sspwB);
  if (rpwA  != null && rpwB  != null) raw += 0.20 * (rpwA  - rpwB);

  const entA = _t2(fpA, "serve_entropy", "pct_of_max");
  const entB = _t2(fpB, "serve_entropy", "pct_of_max");
  if (entA != null && entB != null) {
    const zeA = z(entA, "serve_entropy_pct");
    const zeB = z(entB, "serve_entropy_pct");
    if (zeA != null && zeB != null) raw += 0.15 * (zeA - zeB);
  }

  return _clamp(raw);
}

// ── Axis 2: Rally Shape ────────────────────────────────────────────────────

// Kept in sync with GRASS_PRIOR in mc_worker.js
const GRASS_RALLY_W = { "1_3": 0.55, "4_6": 0.30, "7_9": 0.10, "10+": 0.05 };

function axisRallyShape(fpA, fpB) {
  const rwcA = (fpA.tier2 || {}).rally_win_curve || {};
  const rwcB = (fpB.tier2 || {}).rally_win_curve || {};
  if (!Object.keys(rwcA).length || !Object.keys(rwcB).length) return 0;

  let raw = 0, used = 0;
  for (const [band, w] of Object.entries(GRASS_RALLY_W)) {
    const wA = rwcA[band] && rwcA[band].win_pct;
    const wB = rwcB[band] && rwcB[band].win_pct;
    if (wA == null || wB == null) continue;
    raw += w * (wA - wB) / 10.0;
    used++;
  }
  return used ? _clamp(raw) : 0;
}

// ── Axis 3: Pressure Resilience ────────────────────────────────────────────

function axisPressure(fpA, fpB) {
  const spciA = (fpA.tier2 || {}).spci || {};
  const spciB = (fpB.tier2 || {}).spci || {};

  let spciScore = 0;
  if (spciA.value != null && spciB.value != null) {
    const wa = _confWeight(spciA.confidence);
    const wb = _confWeight(spciB.confidence);
    if (wa > 0 || wb > 0) {
      const gd = (spciA.value * wa) - (spciB.value * wb);
      spciScore = 0.50 * _clamp(gd / 0.30);
    }
  }

  const clutchA = _t2(fpA, "clutch_differential", "value") != null
    ? (fpA.tier2.clutch_differential) : null;
  const clutchB = _t2(fpB, "clutch_differential", "value") != null
    ? (fpB.tier2.clutch_differential) : null;

  let clutchScore = 0;
  if (clutchA && clutchA.value != null && clutchB && clutchB.value != null) {
    const wa = _confWeight(clutchA.confidence);
    const wb = _confWeight(clutchB.confidence);
    const gd = (clutchA.value * wa) - (clutchB.value * wb);
    clutchScore = 0.35 * _clamp(gd / 12.0);
  }

  const dfA = (fpA.tier2 || {}).df_pressure_delta;
  const dfB = (fpB.tier2 || {}).df_pressure_delta;
  let dfScore = 0;
  if (dfA && dfA.value != null && dfB && dfB.value != null) {
    const wa = _confWeight(dfA.confidence);
    const wb = _confWeight(dfB.confidence);
    const gd = (dfB.value * wb) - (dfA.value * wa);  // lower is better → reversed
    dfScore = 0.15 * _clamp(gd / 10.0);
  }

  return _clamp(spciScore + clutchScore + dfScore);
}

// ── Axis 4: Durability ─────────────────────────────────────────────────────

function axisDurability(fpA, fpB) {
  const dA = fpA.distance || {};
  const dB = fpB.distance || {};
  const kmA = dA.avg_km_per_match;
  const kmB = dB.avg_km_per_match;
  if (kmA == null || kmB == null) return 0;
  return _clamp((kmA - kmB) / 2.0, -0.5, 0.5);
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

// Axis weights rebalanced for grass:
//  • Serve/Return remains dominant — grass is won on serve.
//  • Rally Shape elevated — short-ball dominance defines Wimbledon.
//  • Break Pressure elevated — BPs are rare; converting them is decisive.
//  • Pressure equalised with break pressure — clutch hold/return matter equally.
//  • Durability trimmed — grass matches are shorter; endurance matters less.
const AXIS_META = [
  { key: "serveReturn",   label: "Serve / Return",   weight: 0.35 },
  { key: "rallyShape",    label: "Rally Shape",       weight: 0.20 },
  { key: "breakPressure", label: "Break Pressure",    weight: 0.20 },
  { key: "pressure",      label: "Pressure",          weight: 0.20 },
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

  const edge = 0.35*ax1 + 0.20*ax2 + 0.20*ax3 + 0.05*ax4 + 0.20*ax5;

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
