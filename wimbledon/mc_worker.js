/**
 * mc_worker.js — Phased Monte Carlo point simulation (Web Worker).
 *
 * Simulates full best-of-5 matches point-by-point using a three-phase
 * point model that mirrors how real grass-court tennis points unfold:
 *
 *   Phase 1  SERVE   — 1st serve in/out (fsp_pct), DF check, court side
 *   Phase 2  RALLY   — Length sampled by serve type, outcome from
 *                       serve-type baseline (fspw/sspw) + rally curve
 *   Phase 3  MODIFY  — Pressure, momentum, entropy, attrition, etc.
 *
 * Momentum "catch fire" mechanic:
 *   Momentum builds from the FIRST consecutive point won — not after
 *   an arbitrary threshold. A player's natural streakiness determines
 *   how quickly the fire builds:
 *     • streak_initiation_rate — how often they enter momentum mode
 *     • streak_survival_rate  — how long the fire burns once lit
 *   The opponent's streak_recovery_rate acts as resistance.
 *   A streaky player against a mentally fragile opponent can generate
 *   boosts of +8pp at 5 points in a row; a steady player against a
 *   resilient opponent might only reach +3pp.
 *
 * Inputs (via postMessage):
 *   { fpA, fpB, year, nSims }
 *
 * Output:
 *   { pWinA, pWinB, scoreDist, ciLow, ciHigh,
 *     axisContrib, dominantAxis, nSims }
 */

"use strict";

// ── Helpers ────────────────────────────────────────────────────────────────

function clamp(v, lo = 0.05, hi = 0.95) {
  return Math.max(lo, Math.min(hi, v));
}

function confWeight(conf) {
  if (!conf || conf === "UNRELIABLE") return 0;
  if (conf === "LOW")                 return 0.40;
  if (conf === "MODERATE")            return 0.75;
  return 1;  // RELIABLE / HIGH
}

function deepClone(obj) { return JSON.parse(JSON.stringify(obj)); }

// ── Constants ─────────────────────────────────────────────────────────────

const GRASS_RPW_AVG      = 35.0;   // blended; used as fallback / display
// Phased return baselines — grass-court tour averages by serve type.
// Measured on the 2024 fingerprint+grass-profile dataset (273 players):
//   ~25% RPW vs 1st serve (returner under maximum pressure)
//   ~40% RPW vs 2nd serve (returner attacks; tends to win rallies)
// The matchup adjustment uses the right baseline for each serve regime.
const GRASS_RPW_VS_1ST_AVG = 25.0;
const GRASS_RPW_VS_2ND_AVG = 40.0;
const GRASS_AVG_DF_RATE  = 0.035;  // ~3.5% of service points are DFs
const GRASS_AVG_FSP      = 0.63;
const GRASS_AVG_FSPW     = 0.72;
const GRASS_AVG_SSPW     = 0.56;
const GRASS_AVG_RGW      = 16.0;  // return games won %, grass-court 2014–2024 mean

// Rally length bands and grass-court prior
const RALLY_BANDS = ["1_3", "4_6", "7_9", "10+"];
const GRASS_PRIOR = { "1_3": 0.55, "4_6": 0.30, "7_9": 0.10, "10+": 0.05 };

// Serve type rally length adjustments:
// 1st serves produce shorter rallies (aces, service winners)
// 2nd serves produce longer rallies (returner gets into the point)
const FIRST_SERVE_RALLY_WEIGHTS  = { "1_3": 1.15, "4_6": 1.00, "7_9": 0.80, "10+": 0.70 };
const SECOND_SERVE_RALLY_WEIGHTS = { "1_3": 0.85, "4_6": 1.05, "7_9": 1.15, "10+": 1.20 };

// NOTE: The momentum "catch-fire" streak mechanic was removed in v3 after
// 915-match ablation testing showed it cost 0.44pp accuracy.  Likely cause:
// real momentum decays between service games but our model fired the boost
// on every consecutive point during the same game.  Concept may return in
// a future revision applied across service games rather than within them.


// ── Platt calibration ──────────────────────────────────────────────────────
// Grid-searched + leave-one-year-out CV on 908 matches (2014–2024).
// ELO blend (up to 50%), steeper Platt (A=1.5–5.0), and wider bounds
// (0.10–0.90) all tested — none beat this config on the full dataset.
// Bottleneck is raw MC discrimination, not post-hoc calibration.
const PLATT_A    = 0.35;
const PROB_FLOOR = 0.20;
const PROB_CEIL  = 0.80;

function plattCalibrate(p) {
  const EPS = 1e-6;
  p = Math.max(EPS, Math.min(1 - EPS, p));
  const logit = Math.log(p / (1 - p));
  const cal = 1 / (1 + Math.exp(-PLATT_A * logit));
  return Math.max(PROB_FLOOR, Math.min(PROB_CEIL, cal));
}


// ── v11 production stack (mirrors monte_carlo_phased.py) ──────────────────
//
// Pressure-state baselines: per-state fspw/sspw/rpw_vs_1st/rpw_vs_2nd
//   fitted from raw PBP with archetype-prior shrinkage.  Applied via a
//   per-match reliability gate ('stale_only': fires when EITHER player's
//   prior fingerprint has its most-recent career edition >1yr before the
//   predicted year — the COVID-gap case).
// Tiebreak baselines: separate fspw/sspw/rpw fits for tiebreak vs regular
//   game points.  Applied when state.isTiebreak and both players have data.
//
// Validated on 908-match LOYO backtest:
//   pre-v11 baseline       acc 67.7%  brier 0.2098
//   v11 production stack   acc 68.5%  brier 0.2091
const USE_PRESSURE_STATES   = true;
const USE_TIEBREAK_BASELINES = true;
const PRESSURE_GATE_MODE    = "stale_only";

function isStaleFp(fp, currentYear) {
  const eds = fp?.career_editions_used;
  if (!Array.isArray(eds) || eds.length === 0 || currentYear == null) return false;
  let mostRecent = -Infinity;
  for (const y of eds) {
    const n = parseInt(y, 10);
    if (!Number.isNaN(n) && n > mostRecent) mostRecent = n;
  }
  if (mostRecent === -Infinity) return false;
  return (currentYear - mostRecent) > 1;
}

function isSparseFp(fp, currentYear) {
  const eds = fp?.career_editions_used;
  const len = Array.isArray(eds) ? eds.length : 0;
  if (len < 3) return true;
  return isStaleFp(fp, currentYear);
}

function shouldUsePressure(fpA, fpB, currentYear) {
  if (!USE_PRESSURE_STATES) return false;
  if (PRESSURE_GATE_MODE === "stale_only") {
    return isStaleFp(fpA, currentYear) || isStaleFp(fpB, currentYear);
  }
  // (other modes parity with Python kept for ablation; production = stale_only)
  return isSparseFp(fpA, currentYear) || isSparseFp(fpB, currentYear);
}


// ── v12 matchup-neighbors prior (mirrors src/matchup_neighbors.py) ────────
//
// At match-level finalisation, blend in the win rate of the K nearest
// historical matchups (by metric-vector distance).  Supplementary signal:
// MC engine continues to drive the prediction; neighbour prior nudges it
// at NEIGHBOR_BLEND_WEIGHT.  Validated -0.0013 Brier vs v11 on 908-match
// backtest at w=0.15.
const USE_MATCHUP_NEIGHBORS = true;
const NEIGHBOR_BLEND_WEIGHT = 0.15;
const NEIGHBOR_K            = 30;

// Must mirror MATCHUP_METRICS in src/matchup_neighbors.py exactly.
// Each entry: [display_name, tier1_key | null | "PROFILE", tier2_key | null, value_path | null, fallback]
// "PROFILE" sentinel -> traverse fp.men_profile via dotted path.
const MATCHUP_METRICS = [
  ["fspw",          "fspw_pct",        null,                      null,         72.0],
  ["sspw",          "sspw_pct",        null,                      null,         56.0],
  ["rpw_vs_1st",    "rpw_vs_1st_pct",  null,                      null,         25.0],
  ["rpw_vs_2nd",    "rpw_vs_2nd_pct",  null,                      null,         40.0],
  ["sgw",           "sgw_pct",         null,                      null,         80.0],
  ["rgw",           "rgw_pct",         null,                      null,         16.0],
  ["serve_entropy", null,              "serve_entropy",           "pct_of_max", 75.0],
  ["clutch_diff",   null,              "clutch_differential",     "value",       0.0],
  ["tiebreak_diff", null,              "tiebreak_differential",   "value",       0.0],
  ["attrition",     null,              "attrition_slope",         "value",       0.1],
  ["rally_volat",   null,              "rally_volatility",        "value",       3.0],
  // Display-layer additions from men_profile (added v12.1)
  ["net_won_pct",     "PROFILE",       null, "net.net_won_pct",                60.0],
  ["aggression_idx",  "PROFILE",       null, "aggression.aggression_index",    55.0],
  ["rally_srv_1st",   "PROFILE",       null, "rally_shots.srv_1st_avg",         3.5],
  ["rally_srv_2nd",   "PROFILE",       null, "rally_shots.srv_2nd_avg",         4.0],
  ["serve_dir_wide",  "PROFILE",       null, "serve_direction.wide_pct",       35.0],
  ["srv_clean_pct",   "PROFILE",       null, "clean_games.srv_clean_pct",      65.0],
  ["match_mins",      "PROFILE",       null, "match_duration.avg_mins",       150.0],
  ["distance_km",     "PROFILE",       null, "distance.avg_km_per_match",       3.2],
];

function fpMetricValue(fp, t1Key, t2Key, path, fallback) {
  // v12.1: "PROFILE" -> dotted-path lookup in fp.men_profile
  if (t1Key === "PROFILE") {
    let node = fp.men_profile;
    if (!node) return fallback;
    for (const k of (path || "").split(".")) {
      if (!node || typeof node !== "object") return fallback;
      node = node[k];
    }
    if (node == null || typeof node === "object") return fallback;
    const n = Number(node);
    return Number.isFinite(n) ? n : fallback;
  }
  if (t1Key) {
    const node = fp.tier1?.[t1Key];
    return (node && node.value != null) ? node.value : fallback;
  }
  const node = fp.tier2?.[t2Key];
  if (!node || typeof node !== "object") return fallback;
  if (node.available === false) return fallback;
  const v = node[path];
  return v != null ? v : fallback;
}

function matchupFeatures(fpA, fpB) {
  const feats = new Array(MATCHUP_METRICS.length * 2);
  let i = 0;
  for (const [, t1, t2, path, fb] of MATCHUP_METRICS) {
    const a = fpMetricValue(fpA, t1, t2, path, fb);
    const b = fpMetricValue(fpB, t1, t2, path, fb);
    feats[i++] = a - b;          // differential
    feats[i++] = (a + b) / 2;    // level
  }
  return feats;
}

function neighborLookup(fpA, fpB, corpus, excludeYear, k = NEIGHBOR_K) {
  if (!corpus || !corpus.entries || !corpus.mean || !corpus.std) return null;
  const raw = matchupFeatures(fpA, fpB);
  // z-score with corpus normalisation
  const q = new Array(raw.length);
  for (let i = 0; i < raw.length; i++) q[i] = (raw[i] - corpus.mean[i]) / corpus.std[i];

  const distances = [];
  for (const e of corpus.entries) {
    if (excludeYear != null && e.year === excludeYear) continue;
    const f = e.features;
    let d2 = 0;
    for (let i = 0; i < q.length; i++) {
      const diff = q[i] - f[i];
      d2 += diff * diff;
    }
    distances.push([d2, e.won_left]);
  }
  if (distances.length < k) return null;
  distances.sort((a, b) => a[0] - b[0]);
  let weightedWon = 0, totalW = 0;
  for (let i = 0; i < k; i++) {
    const [d2, won] = distances[i];
    const w = 1 / (Math.sqrt(d2) + 0.5);
    weightedWon += w * won;
    totalW += w;
  }
  return weightedWon / totalW;
}


// ── Pre-extracted modifier cache ──────────────────────────────────────────
// Extracted once per matchup to avoid repeated deep property access
// during the hot simulation loop.

function extractModifiers(fp) {
  const t1 = fp.tier1 || {};
  const t2 = fp.tier2 || {};

  // Base DF rate (fraction of service points)
  let baseDFRate = GRASS_AVG_DF_RATE;
  if (t2.df_pressure_delta?.baseline_df_rate != null) {
    baseDFRate = t2.df_pressure_delta.baseline_df_rate / 100;
  }

  // Default Tier-1 base metrics
  const fspwPct   = (t1.fspw_pct?.value       != null) ? t1.fspw_pct.value       : GRASS_AVG_FSPW * 100;
  const sspwPct   = (t1.sspw_pct?.value       != null) ? t1.sspw_pct.value       : GRASS_AVG_SSPW * 100;
  const rpwV1Pct  = (t1.rpw_vs_1st_pct?.value != null) ? t1.rpw_vs_1st_pct.value : GRASS_RPW_VS_1ST_AVG;
  const rpwV2Pct  = (t1.rpw_vs_2nd_pct?.value != null) ? t1.rpw_vs_2nd_pct.value : GRASS_RPW_VS_2ND_AVG;

  // v11: per-state baselines (used when reliability gate fires for this match).
  // Default fallback to the overall Tier-1 baseline so any missing data
  // gracefully degrades.
  let fspwN = fspwPct, fspwP = fspwPct;
  let sspwN = sspwPct, sspwP = sspwPct;
  let rpwV1N = rpwV1Pct, rpwV1P = rpwV1Pct;
  let rpwV2N = rpwV2Pct, rpwV2P = rpwV2Pct;
  const ps = fp.pressure_states;
  if (ps) {
    fspwN  = ps.fspw_neutral_pct       ?? fspwN;
    fspwP  = ps.fspw_pressure_pct      ?? fspwP;
    sspwN  = ps.sspw_neutral_pct       ?? sspwN;
    sspwP  = ps.sspw_pressure_pct      ?? sspwP;
    rpwV1N = ps.rpw_vs_1st_neutral_pct ?? rpwV1N;
    rpwV1P = ps.rpw_vs_1st_pressure_pct ?? rpwV1P;
    rpwV2N = ps.rpw_vs_2nd_neutral_pct ?? rpwV2N;
    rpwV2P = ps.rpw_vs_2nd_pressure_pct ?? rpwV2P;
  }

  // v11: tiebreak-specific baselines (used when state.isTiebreak and both
  // players have tiebreak data).  Stored in pct units; converted to
  // fractions for fspw/sspw downstream.
  let fspwTb = null, sspwTb = null, rpwV1Tb = null, rpwV2Tb = null;
  const tb = fp.tiebreak_baselines;
  if (tb) {
    if (tb.fspw_tiebreak_pct       != null) fspwTb  = tb.fspw_tiebreak_pct;
    if (tb.sspw_tiebreak_pct       != null) sspwTb  = tb.sspw_tiebreak_pct;
    if (tb.rpw_vs_1st_tiebreak_pct != null) rpwV1Tb = tb.rpw_vs_1st_tiebreak_pct;
    if (tb.rpw_vs_2nd_tiebreak_pct != null) rpwV2Tb = tb.rpw_vs_2nd_tiebreak_pct;
  }

  return {
    // Tier 1 serve / return stats — backbone of the model
    fsp:  (t1.fsp_pct?.value  != null) ? t1.fsp_pct.value  / 100 : GRASS_AVG_FSP,
    fspw:     fspwPct / 100,
    sspw:     sspwPct / 100,
    rpw:      (t1.rpw_pct?.value         != null) ? t1.rpw_pct.value         : GRASS_RPW_AVG,
    rpwVs1st: rpwV1Pct,
    rpwVs2nd: rpwV2Pct,
    rgw:      (t1.rgw_pct?.value         != null) ? t1.rgw_pct.value         : GRASS_AVG_RGW,

    // v11: state-conditional baselines.  fspw* in fractions, rpw* in pct.
    fspwNeutral:      fspwN  / 100,
    fspwPressure:     fspwP  / 100,
    sspwNeutral:      sspwN  / 100,
    sspwPressure:     sspwP  / 100,
    rpwVs1stNeutral:  rpwV1N,
    rpwVs1stPressure: rpwV1P,
    rpwVs2ndNeutral:  rpwV2N,
    rpwVs2ndPressure: rpwV2P,
    // v11: tiebreak baselines (null when fp lacks tiebreak data).
    fspwTiebreak:     fspwTb != null ? fspwTb / 100 : null,
    sspwTiebreak:     sspwTb != null ? sspwTb / 100 : null,
    rpwVs1stTiebreak: rpwV1Tb,
    rpwVs2ndTiebreak: rpwV2Tb,

    // Phase 1: serve
    baseDFRate,
    dfPressureDelta:     t2.df_pressure_delta   || null,

    // Phase 2: rally distribution sampling (per-band sample sizes)
    rallyCurve:          t2.rally_win_curve || null,

    // Phase 3: keepers (proven net-positive in ablation)
    serveEntropy:        (t2.serve_entropy?.pct_of_max != null) ? t2.serve_entropy.pct_of_max : null,
    spci:                t2.spci                  || null,
    clutch:              t2.clutch_differential   || null,
    tiebreak:            t2.tiebreak_differential || null,
    holdAfterBreak:      t2.hold_after_break      || null,
    attrition:           t2.attrition_slope       || null,
    rallyVolatility:     t2.rally_volatility      || null,

    // v11: per-match reliability-gate decision stamped here by runMonteCarlo
    _usePressure:        false,
  };
}


// ── Rally length distribution ─────────────────────────────────────────────

/**
 * Build a rally-length probability distribution for a server/returner pair.
 * Blends the grass prior with each player's empirical distribution.
 */
function buildRallyDist(srvMods, retMods) {
  const srvCurve = srvMods.rallyCurve || {};
  const retCurve = retMods.rallyCurve || {};
  const srvN = RALLY_BANDS.reduce((s, b) => s + (srvCurve[b]?.n || 0), 0);
  const retN = RALLY_BANDS.reduce((s, b) => s + (retCurve[b]?.n || 0), 0);

  const dist = {};
  let total = 0;
  for (const b of RALLY_BANDS) {
    const prior    = GRASS_PRIOR[b];
    const srvShare = srvN >= 30 ? (srvCurve[b]?.n || 0) / srvN : prior;
    const retShare = retN >= 30 ? (retCurve[b]?.n || 0) / retN : prior;
    // Server drives pace on grass
    dist[b] = 0.45 * prior + 0.35 * srvShare + 0.20 * retShare;
    total  += dist[b];
  }
  for (const b of RALLY_BANDS) dist[b] /= total;
  return dist;
}

/**
 * Reweight a rally distribution by per-band multipliers and renormalise.
 */
function reweightDist(dist, weights) {
  const out = {};
  let total = 0;
  for (const b of RALLY_BANDS) {
    out[b] = dist[b] * (weights[b] || 1.0);
    total += out[b];
  }
  for (const b of RALLY_BANDS) out[b] /= total;
  return out;
}

function sampleBand(dist) {
  let r = Math.random(), cum = 0;
  for (const b of RALLY_BANDS) {
    cum += dist[b];
    if (r < cum) return b;
  }
  return "10+";
}


// ══════════════════════════════════════════════════════════════════════════
//  PHASED POINT SIMULATION
// ══════════════════════════════════════════════════════════════════════════

/**
 * Simulate a single point using a three-phase model.
 *
 * Phase 1 — SERVE:  Determine 1st/2nd serve in, check for double fault.
 * Phase 2 — RALLY:  Sample rally length, compute outcome probability
 *                    from serve-type-specific baseline + rally curve.
 * Phase 3 — MODIFY: Apply contextual modifiers (pressure, momentum,
 *                    entropy, court side, attrition, etc.)
 *
 * @param {object} srvMods  - Pre-extracted server modifiers
 * @param {object} retMods  - Pre-extracted returner modifiers
 * @param {object} state    - Game state context
 * @returns {boolean} true if server wins the point
 */
function simulatePoint(srvMods, retMods, state) {
  // state = {
  //   courtSide:        "deuce" | "ad"
  //   isBreakPoint:     bool
  //   isDeuce:          bool
  //   isTiebreak:       bool
  //   setIndex:         number (0-based)
  //   isSetOpener:      bool
  //   isPostBreak:      bool
  //   streakCount:      number (consecutive points won by server, negative = lost)
  //   rallyDist:        object (base rally distribution)
  // }

  // ────────────────────────────────────────────────────────────────────────
  // PHASE 1: SERVE
  // ────────────────────────────────────────────────────────────────────────

  // courtSideServe and firstServePressure modifiers were dropped in v3
  // (ablation results: each cost +0.33pp accuracy when ON).
  const fsp = srvMods.fsp;

  // 1c. Draw: is the first serve in?
  const firstServeIn = Math.random() < fsp;
  let isSecondServe = false;

  if (!firstServeIn) {
    // 1d. Second serve: check for double fault
    //     P(DF | 1st out) = baseDFRate / (1 - fsp_overall)
    //     For Sinner: 0.026 / 0.40 = 6.5% conditional DF rate
    const fspOverall = srvMods.fsp;  // use unmodified base FSP for denominator
    let dfRate = srvMods.baseDFRate / Math.max(0.20, 1 - fspOverall);

    // DF pressure delta: modifier at break points
    if (state.isBreakPoint) {
      const dfp = srvMods.dfPressureDelta;
      if (dfp?.modifier_delta != null) {
        const w = confWeight(dfp.confidence);
        // modifier_delta is negative when DF rate drops under pressure
        dfRate += w * (dfp.modifier_delta / Math.max(0.20, 1 - fspOverall));
      }
    }

    dfRate = Math.max(0.01, Math.min(0.20, dfRate));  // guard rails

    if (Math.random() < dfRate) {
      return false;  // Double fault — server loses point immediately
    }

    isSecondServe = true;
  }

  // ────────────────────────────────────────────────────────────────────────
  // PHASE 2: RALLY
  // ────────────────────────────────────────────────────────────────────────

  // 2a. Adjust rally distribution for serve type
  const rallyWeights = isSecondServe ? SECOND_SERVE_RALLY_WEIGHTS : FIRST_SERVE_RALLY_WEIGHTS;
  const rallyDist = reweightDist(state.rallyDist, rallyWeights);
  const band = sampleBand(rallyDist);

  // 2b. Serve-type-specific baseline + serve-type-specific matchup adjustment.
  //     v11 priority order in Phase 2: tiebreak > pressure-state > Tier 1.
  //     - Tiebreak baseline used when state.isTiebreak and BOTH players have
  //       tiebreak data (avoids asymmetric Tier-1-vs-tiebreak matchups).
  //     - Pressure-state baseline used when the per-match reliability gate
  //       fired and the point is BP-against or deuce/AD.  Skips spci/clutch
  //       later to avoid double-counting.
  const usePressurePt = !!srvMods._usePressure;
  const isPressurePt  = !!(state.isBreakPoint || state.isDeuce);
  const useTiebreakPt = (
    USE_TIEBREAK_BASELINES && state.isTiebreak &&
    (isSecondServe ? srvMods.sspwTiebreak     : srvMods.fspwTiebreak)     != null &&
    (isSecondServe ? retMods.rpwVs2ndTiebreak : retMods.rpwVs1stTiebreak) != null
  );

  let p, adj;
  if (useTiebreakPt) {
    if (isSecondServe) {
      p   = srvMods.sspwTiebreak;
      adj = (retMods.rpwVs2ndTiebreak - GRASS_RPW_VS_2ND_AVG) / 100;
    } else {
      p   = srvMods.fspwTiebreak;
      adj = (retMods.rpwVs1stTiebreak - GRASS_RPW_VS_1ST_AVG) / 100;
    }
  } else if (usePressurePt) {
    if (isSecondServe) {
      p   = isPressurePt ? srvMods.sspwPressure : srvMods.sspwNeutral;
      adj = ((isPressurePt ? retMods.rpwVs2ndPressure : retMods.rpwVs2ndNeutral)
             - GRASS_RPW_VS_2ND_AVG) / 100;
    } else {
      p   = isPressurePt ? srvMods.fspwPressure : srvMods.fspwNeutral;
      adj = ((isPressurePt ? retMods.rpwVs1stPressure : retMods.rpwVs1stNeutral)
             - GRASS_RPW_VS_1ST_AVG) / 100;
    }
  } else {
    if (isSecondServe) {
      p   = srvMods.sspw;
      adj = (retMods.rpwVs2nd - GRASS_RPW_VS_2ND_AVG) / 100;
    } else {
      p   = srvMods.fspw;
      adj = (retMods.rpwVs1st - GRASS_RPW_VS_1ST_AVG) / 100;
    }
  }
  p -= adj;

  // rallyCurve differential, courtSideRally, courtSideServe modifiers were
  // dropped in v3 (ablation: rallyCurve cost +1.53pp accuracy alone — the
  // single biggest drag on the model.  Per-band win% is too noisy on small
  // samples and duplicates signal already in the fspw/sspw baselines).


  // ────────────────────────────────────────────────────────────────────────
  // PHASE 3: CONTEXTUAL MODIFIERS
  // ────────────────────────────────────────────────────────────────────────

  // 3a. Serve entropy (unpredictability of direction)
  //     Max effect ±2.5pp; centred at 75% of max entropy.
  if (srvMods.serveEntropy != null) {
    p += 0.025 * ((srvMods.serveEntropy / 100) - 0.75);
  }

  // 3b. Pressure adjustments (break point / deuce states)
  //     v11: skip spci/clutch for THIS point when per-state pressure baseline
  //     was used in Phase 2 (their effect is absorbed into that baseline).
  if (state.isBreakPoint || state.isDeuce) {
    if (!usePressurePt) {
      // Server SPCI — holding serve under pressure
      const spci = srvMods.spci;
      if (spci?.modifier_delta != null) {
        const w = confWeight(spci.confidence);
        p += w * spci.modifier_delta * 0.50;
      }

      // Returner clutch differential — lifting their game at BPs
      const clutch = retMods.clutch;
      if (clutch?.modifier_delta != null) {
        const w = confWeight(clutch.confidence);
        p -= w * (clutch.modifier_delta / 100) * 0.60;
      }
    }

    // bpConversion modifier dropped in v3 (cost +0.44pp accuracy ablated).

    // Return-game conversion burst at break points (rgw_pct).
    // The "closing" component — stronger at the decisive moment.
    if (state.isBreakPoint) {
      const rgwExcess = (retMods.rgw - GRASS_AVG_RGW) / 100;
      p -= rgwExcess * 0.20;
    }
  }

  // 3b-iv. Return-game pressure (rgw_pct) — ALL POINTS.
  //   rgw_pct (r=0.314 with match outcome) captures sustained return
  //   quality that rpw doesn't explain (r²=0.74, 26% unique variance).
  //   A player who wins more return games brings relentless pressure
  //   across all points — not just at break points.
  //   Split: 0.08 baseline (all points) + 0.20 burst (break points above).
  {
    const rgwExcess = (retMods.rgw - GRASS_AVG_RGW) / 100;
    p -= rgwExcess * 0.08;
  }

  // momentum catch-fire mechanic dropped in v3 (cost +0.44pp accuracy).
  // The mechanism is plausible (real momentum exists) but the within-game
  // application was too aggressive — real momentum decays between service
  // games, not point-to-point during the same hold.

  // 3e. Tiebreak differential modifier
  //     v11: skip when per-state tiebreak baseline was used in Phase 2
  //     for THIS point (absorbed into baseline; double-counting otherwise).
  if (state.isTiebreak && !useTiebreakPt) {
    const tb  = srvMods.tiebreak;
    const tbR = retMods.tiebreak;
    if (tb?.available && tb.value != null)
      p += confWeight(tb.confidence) * (tb.value / 100) * 0.60;
    if (tbR?.available && tbR.value != null)
      p -= confWeight(tbR.confidence) * (tbR.value / 100) * 0.60;
  }

  // 3f. Rally volatility as direct advantage (r=+0.233 with outcome).
  // Higher volatility = more aggressive rally play = wins more on grass.
  // Replaces direction-based version and setTransition (both ablated out).
  {
    const rvS = srvMods.rallyVolatility;
    const rvR = retMods.rallyVolatility;
    if (rvS?.available && rvS.value != null && rvR?.available && rvR.value != null) {
      const rvDiff = rvS.value - rvR.value;
      p += (rvDiff / 5.0) * 0.015;
    }
  }

  // 3g. Hold after break (server was broken in their last service game)
  if (state.isPostBreak) {
    const habr = srvMods.holdAfterBreak;
    if (habr?.available && habr.value != null)
      p += confWeight(habr.confidence) * (habr.value / 100) * 0.50;
  }

  // 3h. Attrition slope (per-set physical decay)
  //     Positive slope = player runs more per point as match goes on (fatigue).
  //     Effect grows linearly with set number.
  if (state.setIndex != null && state.setIndex > 0) {
    const att  = srvMods.attrition;
    const attR = retMods.attrition;
    if (att?.available && att.value != null)
      p += confWeight(att.confidence) * (-(att.value / 2.0) * state.setIndex * 0.02);
    if (attR?.available && attR.value != null)
      p -= confWeight(attR.confidence) * (-(attR.value / 2.0) * state.setIndex * 0.02);
  }

  // rallyVolatility direction-based modifier dropped in v3; replaced by
  // rallyVolDirect (block 3f above).  setTransition also dropped (slight
  // Brier drag, 0.2103→0.2100 without it).

  // 3j. RLUEP — unforced error rate per rally band (future use, currently null)
  if (srvMods.rluep && srvMods.rluep[band]) {
    p -= srvMods.rluep[band] * 0.01;
  }

  return Math.random() < clamp(p);
}


// ══════════════════════════════════════════════════════════════════════════
//  GAME / TIEBREAK / SET / MATCH SIMULATION
// ══════════════════════════════════════════════════════════════════════════

/**
 * Simulate a single service game.
 * Returns true if the server holds.
 *
 * Court side alternates each point:
 *   Even total points → deuce court, odd → ad court.
 *
 * Streak tracking: positive = server consecutive wins,
 *                  negative = returner consecutive wins.
 */
function simulateGame(srvMods, retMods, rallyDist, gameOpts = {}) {
  let s = 0, r = 0;
  let totalPoints = 0;
  let streakCount = 0;  // +N = server won last N, -N = returner won last N

  for (;;) {
    const atAdSrv = s > r && s >= 4;   // advantage server
    const atAdRet = r > s && r >= 4;   // advantage returner
    const atDeuce = s >= 3 && r >= 3 && s === r;

    const isBreakPoint = atAdRet || (r === 3 && s < 3);
    const isDeuce      = atDeuce || atAdSrv || atAdRet;
    const courtSide    = (totalPoints % 2 === 0) ? "deuce" : "ad";

    const won = simulatePoint(srvMods, retMods, {
      courtSide,
      isBreakPoint,
      isDeuce,
      isTiebreak:   false,
      setIndex:     gameOpts.setIndex || 0,
      isSetOpener:  gameOpts.isSetOpener || false,
      isPostBreak:  gameOpts.isPostBreak || false,
      streakCount,
      rallyDist,
    });

    // Update streak
    if (won) {
      streakCount = (streakCount > 0) ? streakCount + 1 : 1;
    } else {
      streakCount = (streakCount < 0) ? streakCount - 1 : -1;
    }

    totalPoints++;

    // Score progression
    if (atAdSrv) { if (won) return true;  else { s = 3; r = 3; } continue; }
    if (atAdRet) { if (!won) return false; else { s = 3; r = 3; } continue; }

    if (won) s++; else r++;

    if (s >= 4 && s - r >= 2) return true;
    if (r >= 4 && r - s >= 2) return false;
    if (s === r && s >= 3) { s = 3; r = 3; }  // normalise deuce
  }
}


/**
 * Simulate a tiebreak. Returns true if player A wins.
 *
 * Service alternates: A serves 1st point, then 2 each.
 * Court side: even points → deuce, odd → ad.
 * Streak carries through the tiebreak.
 */
function simulateTiebreak(modsA, modsB, setIndex = 0) {
  let pA = 0, pB = 0, pointsPlayed = 0;
  let streakCount = 0;  // from A's perspective: +N = A streak, -N = B streak

  function aServes(n) {
    if (n === 0) return true;
    return Math.floor((n - 1) / 2) % 2 === 1;
  }

  for (;;) {
    const isAServing = aServes(pointsPlayed);
    const srvMods = isAServing ? modsA : modsB;
    const retMods = isAServing ? modsB : modsA;

    const courtSide = (pointsPlayed % 2 === 0) ? "deuce" : "ad";

    // In a tiebreak, pressure states
    const atDeuce = pA >= 6 && pB >= 6 && pA === pB;
    const adA = pA > pB && pA >= 7;
    const adB = pB > pA && pB >= 7;
    const isPressure = (pA >= 5 && pB >= 5) || atDeuce || adA || adB;

    // Streak from server's perspective for the point simulation
    let srvStreak;
    if (isAServing) {
      srvStreak = streakCount;  // A's streak = server's streak
    } else {
      srvStreak = -streakCount;  // B serving, A's streak is returner's streak
    }

    const rallyDist = buildRallyDist(srvMods, retMods);

    const sWon = simulatePoint(srvMods, retMods, {
      courtSide,
      isBreakPoint: adB,  // "break point" from server's perspective
      isDeuce:      atDeuce || adA || adB,
      isTiebreak:   true,
      setIndex,
      isSetOpener:  false,
      isPostBreak:  false,
      streakCount:  srvStreak,
      rallyDist,
    });

    // Convert server-won to A-won
    const aWon = isAServing ? sWon : !sWon;

    // Update A's streak
    if (aWon) {
      streakCount = (streakCount > 0) ? streakCount + 1 : 1;
    } else {
      streakCount = (streakCount < 0) ? streakCount - 1 : -1;
    }

    // Score update
    if (adA) {
      if (aWon) return true;
      else { pA = 6; pB = 6; }
    } else if (adB) {
      if (!aWon) return false;
      else { pA = 6; pB = 6; }
    } else {
      if (aWon) pA++; else pB++;
    }

    if (pA >= 7 && pA - pB >= 2) return true;
    if (pB >= 7 && pB - pA >= 2) return false;

    pointsPlayed++;
  }
}


/**
 * Simulate one set. Returns { aWins, gA, gB }.
 * setIndex: 0 = first set, used for attrition decay.
 */
function simulateSet(modsA, modsB, aServesFirst, setIndex = 0) {
  let gA = 0, gB = 0;
  let aServes = aServesFirst;
  let gameInSet = 0;
  let lastWasBreak = false;

  for (;;) {
    const srvMods = aServes ? modsA : modsB;
    const retMods = aServes ? modsB : modsA;
    const rallyDist = buildRallyDist(srvMods, retMods);

    const gameOpts = {
      isSetOpener: gameInSet < 2,
      isPostBreak: lastWasBreak,
      setIndex,
    };

    const held = simulateGame(srvMods, retMods, rallyDist, gameOpts);

    lastWasBreak = !held;
    gameInSet++;

    if (aServes) { if (held) gA++; else gB++; }
    else          { if (held) gB++; else gA++; }

    if (gA === 6 && gB === 6) {
      return { aWins: simulateTiebreak(modsA, modsB, setIndex), gA: 7, gB: 6 };
    }
    if (gA >= 6 && gA - gB >= 2) return { aWins: true,  gA, gB };
    if (gB >= 6 && gB - gA >= 2) return { aWins: false, gA, gB };
    if (gA === 7) return { aWins: true,  gA: 7, gB: 5 };
    if (gB === 7) return { aWins: false, gA: 5, gB: 7 };

    aServes = !aServes;
  }
}


/**
 * Simulate one full best-of-5 match. Returns { aWins, sA, sB }.
 */
function simulateMatch(modsA, modsB) {
  let sA = 0, sB = 0;
  let aServesSet = true;
  let setIndex = 0;

  while (sA < 3 && sB < 3) {
    const { aWins } = simulateSet(modsA, modsB, aServesSet, setIndex);
    if (aWins) sA++; else sB++;
    aServesSet = !aServesSet;
    setIndex++;
  }

  return { aWins: sA === 3, sA, sB };
}


// ══════════════════════════════════════════════════════════════════════════
//  MONTE CARLO RUNNER
// ══════════════════════════════════════════════════════════════════════════

function runMonteCarlo(fpA, fpB, nSims, onProgress, currentYear = null, matchupCorpus = null) {
  // Pre-extract modifiers once for the entire matchup
  const modsA = extractModifiers(fpA);
  const modsB = extractModifiers(fpB);

  // v11: per-match reliability gate.  Pressure-state baselines are used
  // only when at least one fingerprint is stale (max career_editions_used
  // year > 1yr before currentYear, e.g., 2019 fp predicting 2021).
  // Otherwise the standard Tier-1 baseline + Phase-3 modifier pipeline
  // runs unchanged for this match.
  const useMatchPressure = shouldUsePressure(fpA, fpB, currentYear);
  modsA._usePressure = useMatchPressure;
  modsB._usePressure = useMatchPressure;

  let winsA = 0;
  const scoreCount = {};
  const reportEvery = Math.max(250, Math.floor(nSims / 20));

  for (let i = 0; i < nSims; i++) {
    const { aWins, sA, sB } = simulateMatch(modsA, modsB);
    const key = `${sA}-${sB}`;
    scoreCount[key] = (scoreCount[key] || 0) + 1;
    if (aWins) winsA++;
    if (onProgress && (i + 1) % reportEvery === 0) {
      const pct = Math.round(5 + ((i + 1) / nSims) * 73);
      onProgress(i + 1, pct);
    }
  }

  // Raw MC → Platt-calibrated probability
  const pWinA_raw = winsA / nSims;
  let pWinA = plattCalibrate(pWinA_raw);

  // v12: blend in K-nearest matchup-neighbors prior at NEIGHBOR_BLEND_WEIGHT.
  // Lookup is leakage-safe via currentYear filter.  Supplementary signal —
  // skipped silently if corpus unavailable or too few neighbours.
  if (USE_MATCHUP_NEIGHBORS && matchupCorpus) {
    try {
      const pNeighbor = neighborLookup(fpA, fpB, matchupCorpus, currentYear, NEIGHBOR_K);
      if (pNeighbor != null) {
        pWinA = (1 - NEIGHBOR_BLEND_WEIGHT) * pWinA + NEIGHBOR_BLEND_WEIGHT * pNeighbor;
      }
    } catch (e) { /* swallow — neighbour signal is supplementary */ }
  }
  const pWinB = 1 - pWinA;

  // Wilson confidence interval
  const z = 1.96;
  const n = nSims;
  const pp = pWinA;
  const denom = 1 + z * z / n;
  const centre = (pp + z * z / (2 * n)) / denom;
  const margin = z * Math.sqrt(pp * (1 - pp) / n + z * z / (4 * n * n)) / denom;
  const ciLow  = Math.max(0, centre - margin);
  const ciHigh = Math.min(1, centre + margin);

  // Normalise score distribution
  const scoreDist = {};
  for (const [k, c] of Object.entries(scoreCount)) scoreDist[k] = c / nSims;

  return { pWinA, pWinB, scoreDist, ciLow, ciHigh, nSims, pWinA_raw };
}


// ══════════════════════════════════════════════════════════════════════════
//  AXIS CONTRIBUTION ANALYSIS
// ══════════════════════════════════════════════════════════════════════════

/**
 * Estimate each fingerprint axis's contribution by running "neutralised"
 * simulations — replace one axis's data with the tour average and measure
 * the change in pWinA.
 *
 * v9 UNIFIED AXES (5 axes matching the display):
 *   serveReturn   — fsp, fspw, sspw, rpw_vs_1st, rpw_vs_2nd, entropy
 *   rallyShape    — rally_win_curve distribution
 *   pressure      — spci, clutch, tiebreak, setTransition, holdAfterBreak
 *   durability    — attrition_slope
 *   breakPressure — rgw_pct
 *
 * Axis weights (empirically derived, 908-match logistic regression):
 *   serveReturn 0.37, breakPressure 0.39, pressure 0.13,
 *   rallyShape 0.06, durability 0.05
 */
function measureAxisContrib(fpA, fpB, basePWinA, nSims = 2000) {
  const axes = {
    serveReturn:   () => neutraliseServeReturn(fpA, fpB),
    rallyShape:    () => neutraliseRally(fpA, fpB),
    pressure:      () => neutralisePressure(fpA, fpB),
    durability:    () => neutraliseDurability(fpA, fpB),
    breakPressure: () => neutraliseBP(fpA, fpB),
  };

  const contrib = {};
  for (const [name, neutralFn] of Object.entries(axes)) {
    const [nA, nB] = neutralFn();
    const { pWinA } = runMonteCarlo(nA, nB, nSims);
    contrib[name] = basePWinA - pWinA;
  }

  const dominant = Object.entries(contrib).reduce(
    (best, [k, v]) => Math.abs(v) > Math.abs(best[1]) ? [k, v] : best,
    ["none", 0]
  );

  return { contrib, dominantAxis: dominant[0] };
}

// ── Axis labels for self-contained display ────────────────────────────────
const AXIS_LABELS = {
  serveReturn:   "Serve / Return",
  rallyShape:    "Rally Shape",
  pressure:      "Pressure",
  durability:    "Durability",
  breakPressure: "Break Pressure",
};

// Axis order + empirical weights for display
const AXIS_ORDER = ["serveReturn", "rallyShape", "pressure", "durability", "breakPressure"];
const AXIS_WEIGHTS = { serveReturn: 0.37, rallyShape: 0.06, pressure: 0.13, durability: 0.05, breakPressure: 0.39 };

// NOTE: rallyCurve, court-side, momentum, bp-conversion, rally-volatility,
// first-serve-pressure modifiers were removed from the model in v3.
// The neutralisation functions below only modify fields the model actually
// reads; touching dropped fields would be a no-op.

/**
 * Neutralise SERVE/RETURN axis.
 * Sets both players' core serve & return stats to tour average so the MC
 * runs as if they have identical serve/return profiles. Includes entropy
 * (part of the serve package) and DF rate.
 */
function neutraliseServeReturn(fpA, fpB) {
  const nA = deepClone(fpA), nB = deepClone(fpB);
  for (const fp of [nA, nB]) {
    // Tier 1: serve stats → tour average
    if (fp.tier1?.fsp_pct)        fp.tier1.fsp_pct.value        = GRASS_AVG_FSP * 100;
    if (fp.tier1?.fspw_pct)       fp.tier1.fspw_pct.value       = GRASS_AVG_FSPW * 100;
    if (fp.tier1?.sspw_pct)       fp.tier1.sspw_pct.value       = GRASS_AVG_SSPW * 100;
    // Tier 1: return stats → tour average
    if (fp.tier1?.rpw_pct)        fp.tier1.rpw_pct.value        = GRASS_RPW_AVG;
    if (fp.tier1?.rpw_vs_1st_pct) fp.tier1.rpw_vs_1st_pct.value = GRASS_RPW_VS_1ST_AVG;
    if (fp.tier1?.rpw_vs_2nd_pct) fp.tier1.rpw_vs_2nd_pct.value = GRASS_RPW_VS_2ND_AVG;
    // Tier 2: serve entropy → tour centre (75% of max)
    if (fp.tier2?.serve_entropy)  fp.tier2.serve_entropy.pct_of_max = 75;
    // Tier 2: DF pressure → neutral
    if (fp.tier2?.df_pressure_delta) fp.tier2.df_pressure_delta.baseline_df_rate = GRASS_AVG_DF_RATE * 100;
  }
  return [nA, nB];
}

function neutraliseRally(fpA, fpB) {
  // The rally-shape "axis" no longer drives p in the model — only the
  // rally-length sampling distribution uses rally_win_curve sample sizes.
  // We leave the data unchanged here; the contribution will be ~0.
  return [deepClone(fpA), deepClone(fpB)];
}

function neutralisePressure(fpA, fpB) {
  const nA = deepClone(fpA), nB = deepClone(fpB);
  for (const fp of [nA, nB]) {
    if (fp.tier2?.spci)                  fp.tier2.spci.modifier_delta = 0;
    if (fp.tier2?.clutch_differential)   fp.tier2.clutch_differential.modifier_delta = 0;
    if (fp.tier2?.tiebreak_differential) fp.tier2.tiebreak_differential.value = 0;
    if (fp.tier2?.set_transition_delta)  fp.tier2.set_transition_delta.value = 0;
    if (fp.tier2?.hold_after_break)      fp.tier2.hold_after_break.value = 0;
  }
  return [nA, nB];
}

/**
 * Neutralise DURABILITY axis.
 * Sets attrition_slope to 0 (no per-set decay) for both players.
 */
function neutraliseDurability(fpA, fpB) {
  const nA = deepClone(fpA), nB = deepClone(fpB);
  for (const fp of [nA, nB]) {
    if (fp.tier2?.attrition_slope) fp.tier2.attrition_slope.value = 0;
  }
  return [nA, nB];
}

function neutraliseBP(fpA, fpB) {
  // Neutralise return-game conversion: set rgw_pct to tour average for
  // both players so the break-point modifier fires with zero edge.
  const nA = deepClone(fpA), nB = deepClone(fpB);
  for (const fp of [nA, nB]) {
    if (fp.tier1?.rgw_pct) fp.tier1.rgw_pct.value = GRASS_AVG_RGW;
  }
  return [nA, nB];
}


// ══════════════════════════════════════════════════════════════════════════
//  STRUCTURAL AXIS EDGES (for bar display)
// ══════════════════════════════════════════════════════════════════════════

/**
 * Compute structural player-vs-player edges for the axis bar display.
 * Returns values in [-1, +1] where positive = A advantage.
 *
 * These measure WHO IS BETTER per axis (intuitive for bars), as opposed
 * to MC neutralisation which measures HOW MUCH EACH AXIS MATTERS for the
 * match outcome.  Both are needed: structural for display, neutralisation
 * for the "decisive axis" analytical tag.
 *
 * Uses normalised metric differences: (valA - valB) / typical_SD.
 * The SDs are stable grass-court averages (2014–2024), so we don't need
 * eraStats in the worker.  For same-year comparisons the z-score difference
 * is mathematically identical: zA - zB = (valA - valB) / SD.
 */

// Typical grass-court SDs for key metrics (2014–2024 average)
const SD_FSPW    = 6.3;
const SD_SSPW    = 6.3;
const SD_RPW_V1  = 5.2;
const SD_RPW_V2  = 5.4;
const SD_RGW     = 7.1;

function _t1v(fp, key) { return fp?.tier1?.[key]?.value ?? null; }

function _t2v(fp, ...keys) {
  let node = fp?.tier2;
  for (const k of keys) {
    if (!node || typeof node !== "object") return null;
    node = node[k];
  }
  if (node == null) return null;
  if (typeof node === "object" && node.available === false) return null;
  return (typeof node === "object") ? (node.value ?? null) : node;
}

function _cw(fp, ...keys) {
  let node = fp?.tier2;
  for (const k of keys) { if (!node) return 0; node = node[k]; }
  if (!node || typeof node !== "object") return 0;
  const c = node.confidence;
  if (!c || c === "UNRELIABLE") return 0;
  if (c === "LOW") return 0.5;
  return 1;
}

function _cl(v) { return Math.max(-1, Math.min(1, v)); }

function computeStructuralAxes(fpA, fpB) {
  // ── Axis 1: Serve / Return ──────────────────────────────────────
  // Paired structure: 1st-serve regime (0.52), 2nd-serve regime (0.24),
  // serve style modifiers (0.24).  Matches compare.js axisServeReturn.
  let sr = 0, srW = 0;

  // 1st-serve regime
  const fspwA = _t1v(fpA, "fspw_pct"), fspwB = _t1v(fpB, "fspw_pct");
  if (fspwA != null && fspwB != null) {
    sr += 0.26 * _cl((fspwA - fspwB) / (2 * SD_FSPW)); srW += 0.26;
  }
  const rpw1A = _t1v(fpA, "rpw_vs_1st_pct"), rpw1B = _t1v(fpB, "rpw_vs_1st_pct");
  if (rpw1A != null && rpw1B != null) {
    sr += 0.26 * _cl((rpw1A - rpw1B) / (2 * SD_RPW_V1)); srW += 0.26;
  }

  // 2nd-serve regime
  const sspwA = _t1v(fpA, "sspw_pct"), sspwB = _t1v(fpB, "sspw_pct");
  if (sspwA != null && sspwB != null) {
    sr += 0.12 * _cl((sspwA - sspwB) / (2 * SD_SSPW)); srW += 0.12;
  }
  const rpw2A = _t1v(fpA, "rpw_vs_2nd_pct"), rpw2B = _t1v(fpB, "rpw_vs_2nd_pct");
  if (rpw2A != null && rpw2B != null) {
    sr += 0.12 * _cl((rpw2A - rpw2B) / (2 * SD_RPW_V2)); srW += 0.12;
  }

  // Serve style modifiers
  const entA = _t2v(fpA, "serve_entropy", "pct_of_max");
  const entB = _t2v(fpB, "serve_entropy", "pct_of_max");
  if (entA != null && entB != null) {
    sr += 0.08 * _cl((entA - entB) / 30); srW += 0.08;
  }
  const spdA = _t2v(fpA, "serve_speed_courage", "overall_speed_kmh");
  const spdB = _t2v(fpB, "serve_speed_courage", "overall_speed_kmh");
  if (spdA != null && spdB != null) {
    sr += 0.08 * _cl((spdA - spdB) / 30); srW += 0.08;
  }
  const ssdA = _t2v(fpA, "serve_speed_differential", "value");
  const ssdB = _t2v(fpB, "serve_speed_differential", "value");
  if (ssdA != null && ssdB != null) {
    sr += 0.04 * _cl((ssdB - ssdA) / 20); srW += 0.04;  // reversed: lower diff = better
  }
  const sdepA = _t2v(fpA, "serve_depth_entropy", "pct_of_max");
  const sdepB = _t2v(fpB, "serve_depth_entropy", "pct_of_max");
  if (sdepA != null && sdepB != null) {
    sr += 0.04 * _cl((sdepA - sdepB) / 30); srW += 0.04;
  }

  const axServeReturn = srW > 0 ? _cl(sr / srW) : 0;

  // ── Axis 2: Rally Shape ─────────────────────────────────────────
  let rs = 0, rsW = 0;
  const rwcA = fpA?.tier2?.rally_win_curve || {};
  const rwcB = fpB?.tier2?.rally_win_curve || {};
  const RALLY_W = { "1_3": 0.55, "4_6": 0.30, "7_9": 0.10, "10+": 0.05 };
  if (Object.keys(rwcA).length && Object.keys(rwcB).length) {
    for (const [band, w] of Object.entries(RALLY_W)) {
      const wA = rwcA[band]?.win_pct, wB = rwcB[band]?.win_pct;
      if (wA != null && wB != null) {
        rs += w * 0.75 * (wA - wB) / 10; rsW += w * 0.75;
      }
    }
  }
  const rvA = _t2v(fpA, "rally_volatility", "value");
  const rvB = _t2v(fpB, "rally_volatility", "value");
  if (rvA != null && rvB != null) {
    const wA = _cw(fpA, "rally_volatility") || 1;
    const wB = _cw(fpB, "rally_volatility") || 1;
    rs += 0.25 * _cl((rvA * wA - rvB * wB) / 2); rsW += 0.25;
  }
  const axRallyShape = rsW > 0 ? _cl(rs / rsW) : 0;

  // ── Axis 3: Pressure ────────────────────────────────────────────
  let pr = 0, prW = 0;

  // SPCI
  const spciA = fpA?.tier2?.spci, spciB = fpB?.tier2?.spci;
  if (spciA?.value != null && spciB?.value != null) {
    const wa = _cw(fpA, "spci") || 1, wb = _cw(fpB, "spci") || 1;
    pr += 0.22 * _cl((spciA.value * wa - spciB.value * wb) / 0.30); prW += 0.22;
  }

  // Clutch differential
  const clA = fpA?.tier2?.clutch_differential, clB = fpB?.tier2?.clutch_differential;
  if (clA?.value != null && clB?.value != null) {
    const wa = _cw(fpA, "clutch_differential") || 1, wb = _cw(fpB, "clutch_differential") || 1;
    pr += 0.16 * _cl((clA.value * wa - clB.value * wb) / 12); prW += 0.16;
  }

  // DF pressure delta (lower = better → reversed)
  const dfpA = fpA?.tier2?.df_pressure_delta, dfpB = fpB?.tier2?.df_pressure_delta;
  if (dfpA?.value != null && dfpB?.value != null) {
    const wa = _cw(fpA, "df_pressure_delta") || 1, wb = _cw(fpB, "df_pressure_delta") || 1;
    pr += 0.16 * _cl((dfpB.value * wb - dfpA.value * wa) / 10); prW += 0.16;
  }

  // Tiebreak differential
  const tbA = fpA?.tier2?.tiebreak_differential, tbB = fpB?.tier2?.tiebreak_differential;
  if (tbA?.value != null && tbB?.value != null) {
    const wa = _cw(fpA, "tiebreak_differential") || 1, wb = _cw(fpB, "tiebreak_differential") || 1;
    pr += 0.24 * _cl((tbA.value * wa - tbB.value * wb) / 8); prW += 0.24;
  }

  // Set transition delta
  const stA = fpA?.tier2?.set_transition_delta, stB = fpB?.tier2?.set_transition_delta;
  if (stA?.value != null && stB?.value != null) {
    const wa = _cw(fpA, "set_transition_delta") || 1, wb = _cw(fpB, "set_transition_delta") || 1;
    pr += 0.14 * _cl((stA.value * wa - stB.value * wb) / 8); prW += 0.14;
  }

  // 1st serve aggression under pressure
  const fspA2 = fpA?.tier2?.first_serve_pressure, fspB2 = fpB?.tier2?.first_serve_pressure;
  if (fspA2?.value != null && fspB2?.value != null) {
    const wa = _cw(fpA, "first_serve_pressure") || 1, wb = _cw(fpB, "first_serve_pressure") || 1;
    pr += 0.08 * _cl((fspA2.value * wa - fspB2.value * wb) / 10); prW += 0.08;
  }

  const axPressure = prW > 0 ? _cl(pr / prW) : 0;

  // ── Axis 4: Durability ──────────────────────────────────────────
  let du = 0, duW = 0;

  const kmA = fpA?.distance?.avg_km_per_match, kmB = fpB?.distance?.avg_km_per_match;
  if (kmA != null && kmB != null) {
    du += 0.50 * _cl((kmA - kmB) / 2); duW += 0.50;
  }
  const dreA = _t2v(fpA, "distance_run_efficiency", "value");
  const dreB = _t2v(fpB, "distance_run_efficiency", "value");
  if (dreA != null && dreB != null) {
    du += 0.30 * _cl((dreB - dreA) / 0.5); duW += 0.30;  // reversed: lower = better
  }
  const attA = _t2v(fpA, "attrition_slope", "value");
  const attB = _t2v(fpB, "attrition_slope", "value");
  if (attA != null && attB != null) {
    du += 0.20 * _cl((attB - attA) / 0.5); duW += 0.20;  // reversed: lower = better
  }

  const axDurability = duW > 0 ? _cl(du / duW) : 0;

  // ── Axis 5: Break Pressure ──────────────────────────────────────
  let bp = 0;

  const rgwA = _t1v(fpA, "rgw_pct"), rgwB = _t1v(fpB, "rgw_pct");
  if (rgwA != null && rgwB != null) {
    bp += 0.40 * _cl((rgwA - rgwB) / (2 * SD_RGW));
  }
  const bpcA = fpA?.tier2?.bp_creation_profile || {};
  const bpcB = fpB?.tier2?.bp_creation_profile || {};
  if (bpcA.bp_per_return_game != null && bpcB.bp_per_return_game != null) {
    bp += 0.35 * _cl((bpcA.bp_per_return_game - bpcB.bp_per_return_game) / 0.30);
  }
  if (bpcA.bp_conversion != null && bpcB.bp_conversion != null) {
    bp += 0.25 * _cl((bpcA.bp_conversion - bpcB.bp_conversion) / 0.20);
  }

  const axBreakPressure = _cl(bp);

  return {
    serveReturn:   axServeReturn,
    rallyShape:    axRallyShape,
    pressure:      axPressure,
    durability:    axDurability,
    breakPressure: axBreakPressure,
  };
}


// ══════════════════════════════════════════════════════════════════════════
//  pServeMatchup — matchup-aware serve win probability
// ══════════════════════════════════════════════════════════════════════════

/**
 * Compute P(server wins service point) factoring in the returner's quality.
 * Splits by serve type: 1st serve vs 1st return, 2nd serve vs 2nd return.
 *
 * This is the SAME formula used by the Python monte_carlo_phased engine
 * (see src/monte_carlo_phased.py lines 346-356), ensuring browser and
 * backtest produce identical probabilities.
 */
function pServeMatchup(fpServer, fpReturner) {
  const t1s = fpServer.tier1   || {};
  const t1r = fpReturner.tier1 || {};

  const fsp  = (t1s.fsp_pct?.value  != null) ? t1s.fsp_pct.value / 100  : GRASS_AVG_FSP;
  const fspw = (t1s.fspw_pct?.value != null) ? t1s.fspw_pct.value / 100 : GRASS_AVG_FSPW;
  const sspw = (t1s.sspw_pct?.value != null) ? t1s.sspw_pct.value / 100 : GRASS_AVG_SSPW;

  const rpwV1 = (t1r.rpw_vs_1st_pct?.value != null) ? t1r.rpw_vs_1st_pct.value : GRASS_RPW_VS_1ST_AVG;
  const rpwV2 = (t1r.rpw_vs_2nd_pct?.value != null) ? t1r.rpw_vs_2nd_pct.value : GRASS_RPW_VS_2ND_AVG;

  // 1st-serve regime: server fspw adjusted by returner's rpw vs 1st
  const pFirst  = fspw - (rpwV1 - GRASS_RPW_VS_1ST_AVG) / 100;
  // 2nd-serve regime: server sspw adjusted by returner's rpw vs 2nd
  const pSecond = sspw - (rpwV2 - GRASS_RPW_VS_2ND_AVG) / 100;

  // Blend by 1st-serve percentage
  const pServe = fsp * clamp(pFirst) + (1 - fsp) * clamp(pSecond);
  return clamp(pServe, 0.45, 0.85);   // guard rails
}


// ══════════════════════════════════════════════════════════════════════════
//  edgeNarrative — self-contained narrative builder
// ══════════════════════════════════════════════════════════════════════════

function edgeNarrative(axisContrib, nameA, nameB, pWinA) {
  // Dominant axis = largest absolute MC contribution
  let domKey = "serveReturn", domAbs = 0;
  for (const k of AXIS_ORDER) {
    const v = Math.abs(axisContrib[k] || 0);
    if (v > domAbs) { domKey = k; domAbs = v; }
  }

  const gap = Math.abs(pWinA - 0.5);
  const favours = pWinA >= 0.5 ? nameA : nameB;

  const confidence = gap < 0.03 ? "too close to call"
                   : gap < 0.08 ? "has a slight advantage"
                   : gap < 0.18 ? "is the likelier winner"
                   : "is the clear favourite";

  if (gap < 0.03) {
    return `Too close to call — the decisive axis will be ${AXIS_LABELS[domKey].toLowerCase()}.`;
  }
  return `${favours} ${confidence} — the decisive axis is ${AXIS_LABELS[domKey].toLowerCase()}.`;
}


// ══════════════════════════════════════════════════════════════════════════
//  WORKER ENTRY POINT
// ══════════════════════════════════════════════════════════════════════════

self.onmessage = function (e) {
  try {
  const { fpA, fpB, nSims = 10000, currentYear = null, matchupCorpus = null } = e.data;

  self.postMessage({ type: "progress", pct: 5, msg: "Loading player data…" });

  const result = runMonteCarlo(fpA, fpB, nSims, (count, pct) => {
    self.postMessage({
      type: "progress",
      pct,
      msg: `Running simulations… ${count.toLocaleString()} / ${nSims.toLocaleString()}`,
    });
  }, currentYear, matchupCorpus);

  self.postMessage({ type: "progress", pct: 82, msg: "Calculating axis contributions…" });

  // 5-axis neutralisation-based decomposition (for "decisive axis" analysis)
  const { contrib, dominantAxis } = measureAxisContrib(fpA, fpB, result.pWinA);

  // Structural axes (for bar display — who is better per axis)
  const structuralAxes = computeStructuralAxes(fpA, fpB);

  // Matchup-aware serve win probabilities (same formula as Python engine)
  const pServeA = pServeMatchup(fpA, fpB);
  const pServeB = pServeMatchup(fpB, fpA);

  // Self-contained narrative
  const nameA = fpA.player || "Player A";
  const nameB = fpB.player || "Player B";
  const narrative = edgeNarrative(contrib, nameA, nameB, result.pWinA);

  self.postMessage({
    type: "result",
    ...result,
    pServeA,
    pServeB,
    axes: structuralAxes,       // structural edges for bar display [-1, +1]
    axisContrib: contrib,       // MC neutralisation deltas for analysis
    dominantAxis,
    axisLabels: AXIS_LABELS,
    axisOrder: AXIS_ORDER,
    narrative,
  });
  } catch (err) {
    self.postMessage({ type: "error", message: err.message, stack: err.stack });
  }
};
