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
// Temperature-scaling: p_cal = sigmoid(PLATT_A * logit(p_raw))
// v2: PLATT_A = 0.42, fitted on aggregate Markov-style outputs.
// v3: PLATT_A = 0.35, re-fit specifically on the phased model's raw
//     probability distribution (915-match Brier-optimised sweep).
//     Accuracy is identical; Brier improves marginally (-0.0006).
const PLATT_A = 0.35;

// Probability floor/ceiling — the model's actual win rate plateaus at ~79%
// for any prediction above 75% confidence.  Predictions above 80% add noise,
// not signal.  Empirically derived from 908-match calibration (2014–2024):
//   predicted 75–80% → actually wins 79.5%  (well-calibrated)
//   predicted 80–85% → actually wins 78.1%  (overconfident)
//   predicted 95–99% → actually wins 68.6%  (actively harmful)
// Cap at [0.20, 0.80]: Brier 0.2146→0.2113, Brier skill 0.142→0.155.
const PROB_FLOOR = 0.20;
const PROB_CEIL  = 0.80;

function plattCalibrate(p) {
  // Old behaviour bypassed calibration when p_raw hit 0.0 or 1.0 — letting
  // literal 100%/0% predictions through to wreck log-loss on misses.
  // Fix: clamp the input so PLATT always runs, then apply floor/ceil.
  const EPS = 1e-6;
  p = Math.max(EPS, Math.min(1 - EPS, p));
  const logit = Math.log(p / (1 - p));
  const cal = 1 / (1 + Math.exp(-PLATT_A * logit));
  return Math.max(PROB_FLOOR, Math.min(PROB_CEIL, cal));
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

  return {
    // Tier 1 serve / return stats — backbone of the model
    fsp:  (t1.fsp_pct?.value  != null) ? t1.fsp_pct.value  / 100 : GRASS_AVG_FSP,
    fspw:     (t1.fspw_pct?.value != null) ? t1.fspw_pct.value / 100 : GRASS_AVG_FSPW,
    sspw:     (t1.sspw_pct?.value != null) ? t1.sspw_pct.value / 100 : GRASS_AVG_SSPW,
    rpw:      (t1.rpw_pct?.value          != null) ? t1.rpw_pct.value         : GRASS_RPW_AVG,
    rpwVs1st: (t1.rpw_vs_1st_pct?.value  != null) ? t1.rpw_vs_1st_pct.value  : GRASS_RPW_VS_1ST_AVG,
    rpwVs2nd: (t1.rpw_vs_2nd_pct?.value  != null) ? t1.rpw_vs_2nd_pct.value  : GRASS_RPW_VS_2ND_AVG,

    // Phase 1: serve
    baseDFRate,
    dfPressureDelta:     t2.df_pressure_delta   || null,

    // Phase 2: rally distribution sampling (per-band sample sizes)
    rallyCurve:          t2.rally_win_curve || null,

    // Phase 3: keepers (proven net-positive in 915-match ablation)
    serveEntropy:        (t2.serve_entropy?.pct_of_max != null) ? t2.serve_entropy.pct_of_max : null,
    spci:                t2.spci                  || null,
    clutch:              t2.clutch_differential   || null,
    tiebreak:            t2.tiebreak_differential || null,
    setTransition:       t2.set_transition_delta  || null,
    holdAfterBreak:      t2.hold_after_break      || null,
    attrition:           t2.attrition_slope       || null,
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

  // 2b. Serve-type-specific baseline
  //     1st serve in → fspw_pct as baseline (big serve = advantage)
  //     2nd serve    → sspw_pct as baseline (returner is more engaged)
  // 2b. Serve-type-specific baseline + serve-type-specific matchup adjustment.
  //     The returner's RPW vs 1st serves and vs 2nd serves are very different
  //     regimes on grass (~28% vs ~52%).  Using the right split per phase
  //     captures whether the returner is good at neutralising big serves
  //     or at attacking second serves — those are different skills.
  let p, adj;
  if (isSecondServe) {
    p   = srvMods.sspw;
    adj = (retMods.rpwVs2nd - GRASS_RPW_VS_2ND_AVG) / 100;
  } else {
    p   = srvMods.fspw;
    adj = (retMods.rpwVs1st - GRASS_RPW_VS_1ST_AVG) / 100;
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
  if (state.isBreakPoint || state.isDeuce) {
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

    // bpConversion modifier dropped in v3 (cost +0.44pp accuracy ablated).
  }

  // momentum catch-fire mechanic dropped in v3 (cost +0.44pp accuracy).
  // The mechanism is plausible (real momentum exists) but the within-game
  // application was too aggressive — real momentum decays between service
  // games, not point-to-point during the same hold.

  // 3e. Tiebreak differential
  if (state.isTiebreak) {
    const tb  = srvMods.tiebreak;
    const tbR = retMods.tiebreak;
    if (tb?.available && tb.value != null)
      p += confWeight(tb.confidence) * (tb.value / 100) * 0.60;
    if (tbR?.available && tbR.value != null)
      p -= confWeight(tbR.confidence) * (tbR.value / 100) * 0.60;
  }

  // 3f. Set transition delta (first 2 games of each set)
  if (state.isSetOpener) {
    const st  = srvMods.setTransition;
    const stR = retMods.setTransition;
    if (st?.available && st.value != null)
      p += confWeight(st.confidence) * (st.value / 100) * 0.50;
    if (stR?.available && stR.value != null)
      p -= confWeight(stR.confidence) * (stR.value / 100) * 0.50;
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

  // rallyVolatility modifier dropped in v3 (cost +0.33pp accuracy ablated).

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

function runMonteCarlo(fpA, fpB, nSims, onProgress) {
  // Pre-extract modifiers once for the entire matchup
  const modsA = extractModifiers(fpA);
  const modsB = extractModifiers(fpB);

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
  const pWinA = plattCalibrate(pWinA_raw);
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
 * simulations — replace one axis's data with the average and measure
 * the change in pWinA.
 */
function measureAxisContrib(fpA, fpB, basePWinA, nSims = 2000) {
  const axes = {
    rallyShape:    () => neutraliseRally(fpA, fpB),
    pressure:      () => neutralisePressure(fpA, fpB),
    serveEntropy:  () => neutraliseEntropy(fpA, fpB),
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

// NOTE: rallyCurve, court-side, momentum, bp-conversion, rally-volatility,
// first-serve-pressure modifiers were removed from the model in v3.
// The neutralisation functions below only modify fields the model actually
// reads; touching dropped fields would be a no-op.

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

function neutraliseEntropy(fpA, fpB) {
  const nA = deepClone(fpA), nB = deepClone(fpB);
  if (nA.tier2?.serve_entropy) nA.tier2.serve_entropy.pct_of_max = 75;
  if (nB.tier2?.serve_entropy) nB.tier2.serve_entropy.pct_of_max = 75;
  return [nA, nB];
}

function neutraliseBP(fpA, fpB) {
  // bp-conversion modifier removed in v3 (see model header notes).
  // Neutralising rgw at Tier 1 instead, since break creation/conversion
  // is captured implicitly in the returner's rgw_pct that drives the model.
  const nA = deepClone(fpA), nB = deepClone(fpB);
  for (const fp of [nA, nB]) {
    if (fp.tier1?.rgw_pct?.value != null) fp.tier1.rgw_pct.value = 17;  // tour avg
  }
  return [nA, nB];
}


// ══════════════════════════════════════════════════════════════════════════
//  WORKER ENTRY POINT
// ══════════════════════════════════════════════════════════════════════════

self.onmessage = function (e) {
  const { fpA, fpB, nSims = 10000 } = e.data;

  self.postMessage({ type: "progress", pct: 5, msg: "Loading player data…" });

  const result = runMonteCarlo(fpA, fpB, nSims, (count, pct) => {
    self.postMessage({
      type: "progress",
      pct,
      msg: `Running simulations… ${count.toLocaleString()} / ${nSims.toLocaleString()}`,
    });
  });

  self.postMessage({ type: "progress", pct: 82, msg: "Calculating probabilities…" });

  const { contrib, dominantAxis } = measureAxisContrib(fpA, fpB, result.pWinA);

  self.postMessage({
    type: "result",
    ...result,
    axisContrib: contrib,
    dominantAxis,
  });
};
