/**
 * mc_worker.js — Fingerprint-driven Monte Carlo simulation (Web Worker).
 *
 * Simulates full best-of-5 matches point by point using Tier 2 fingerprint
 * data as primary inputs. Tier 1 serve stats are a fallback only when
 * Tier 2 rally curve data is insufficient.
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

// ── Rally length ───────────────────────────────────────────────────────────

const RALLY_BANDS   = ["1_3", "4_6", "7_9", "10+"];
// Grass prior updated to better reflect Wimbledon reality:
// ~55% of points end within 1–3 shots; long (10+) rallies are rare on fast grass.
const GRASS_PRIOR   = { "1_3": 0.55, "4_6": 0.30, "7_9": 0.10, "10+": 0.05 };

/**
 * Build a rally-length probability distribution for a server/returner pair.
 * Blends the Wimbledon grass prior with each player's empirical distribution.
 * Server's preference weighted more heavily (they control the pace on grass).
 */
function buildRallyDist(server, returner) {
  const srvCurve = (server.tier2  || {}).rally_win_curve || {};
  const retCurve = (returner.tier2 || {}).rally_win_curve || {};
  const srvN = RALLY_BANDS.reduce((s, b) => s + (srvCurve[b]?.n || 0), 0);
  const retN = RALLY_BANDS.reduce((s, b) => s + (retCurve[b]?.n || 0), 0);

  const dist = {};
  let total = 0;
  for (const b of RALLY_BANDS) {
    const prior    = GRASS_PRIOR[b];
    const srvShare = srvN >= 30 ? (srvCurve[b]?.n || 0) / srvN : prior;
    const retShare = retN >= 30 ? (retCurve[b]?.n || 0) / retN : prior;
    // Server drives the pace on grass — weight their preference higher
    dist[b] = 0.45 * prior + 0.35 * srvShare + 0.20 * retShare;
    total  += dist[b];
  }
  for (const b of RALLY_BANDS) dist[b] /= total;
  return dist;
}

function sampleBand(dist) {
  let r = Math.random(), cum = 0;
  for (const b of RALLY_BANDS) {
    cum += dist[b];
    if (r < cum) return b;
  }
  return "10+";
}

// ── Tier-1 baseline serve probability ─────────────────────────────────────

const GRASS_RPW_AVG = 35.0;  // tour average return points won on grass

function tier1ServeProb(fp) {
  const t = fp.tier1 || {};
  const fsp  = t.fsp_pct?.value;
  const fspw = t.fspw_pct?.value;
  const sspw = t.sspw_pct?.value;
  if (fsp != null && fspw != null && sspw != null) {
    return (fsp / 100) * (fspw / 100) + (1 - fsp / 100) * (sspw / 100);
  }
  const sgw = t.sgw_pct?.value;
  if (sgw != null) return clamp(sgw / 100 * 0.93 + 0.05, 0.50, 0.85);
  return 0.63;
}

/**
 * Matchup-adjusted serve probability.
 * Adjusts the server's isolated SPW for the returner's quality relative
 * to the grass tour average.  A strong returner (rpw > 35%) reduces the
 * server's effective probability, a weak one increases it.
 */
function matchupServeProb(server, returner) {
  const pRaw  = tier1ServeProb(server);
  const rpwR  = returner.tier1?.rpw_pct?.value;
  if (rpwR == null) return pRaw;
  const adj   = (rpwR - GRASS_RPW_AVG) / 100;
  return clamp(pRaw - adj, 0.45, 0.85);
}

// ── Core point win probability ─────────────────────────────────────────────

/**
 * Estimate P(server wins point) for a given rally band and game state.
 *
 * Architecture (updated):
 *   1. Start with MATCHUP-ADJUSTED Tier 1 serve probability
 *      (server's SPW corrected for returner's return quality).
 *   2. Apply rally-curve MODIFIER if both players have Tier 2 data —
 *      the rally curve tells us whether the matchup is relatively better
 *      or worse in this particular rally band vs the player's baseline.
 *   3. Apply pressure/momentum/state modifiers.
 *
 * The rally curve modifier is DIFFERENTIAL (how much this band deviates
 * from the baseline), not absolute — this preserves the correct magnitude
 * of the serve advantage on grass while still capturing matchup-specific
 * rally-length effects.
 */
function pointWinProb(server, returner, band, isBreakPoint, isDeuce, opts = {}) {
  // 1. Matchup-adjusted Tier 1 baseline
  let p = matchupServeProb(server, returner);

  // 2. Rally-curve modifier: if both players have Tier 2 rally data,
  //    shift p based on how this specific rally band compares to the
  //    baseline. This captures matchup-specific effects (e.g., "Alcaraz
  //    dominates short rallies but struggles in 7-9") without erasing
  //    the serve advantage baked into the Tier 1 baseline.
  const srvCurve = (server.tier2  || {}).rally_win_curve || {};
  const retCurve = (returner.tier2 || {}).rally_win_curve || {};
  const sWin = srvCurve[band]?.win_pct;
  const rWin = retCurve[band]?.win_pct;

  if (sWin != null && rWin != null) {
    // Rally curve blend for THIS band
    const bandP = 0.5 * (sWin / 100) + 0.5 * (1 - rWin / 100);
    // Overall rally curve blend (average across bands) as the neutral reference
    const srvAll = RALLY_BANDS.reduce((s, b) => s + (srvCurve[b]?.win_pct || 50), 0) / RALLY_BANDS.length;
    const retAll = RALLY_BANDS.reduce((s, b) => s + (retCurve[b]?.win_pct || 50), 0) / RALLY_BANDS.length;
    const avgP   = 0.5 * (srvAll / 100) + 0.5 * (1 - retAll / 100);
    // Deviation of this band from the overall rally curve baseline
    const delta  = bandP - avgP;
    // Apply the band-specific deviation to the Tier 1 baseline
    // Scale factor 0.8: rally curve data is noisier than Tier 1 aggregates
    p += delta * 0.8;
  }

  // ── Serve entropy (unpredictability of direction) ──────────────────────
  const entropy = server.tier2?.serve_entropy?.pct_of_max;
  if (entropy != null) {
    // Max effect ±2.5 pp; centred at 75% of maximum entropy.
    // Increased from 0.015 — serve variety is more decisive on fast grass.
    p += 0.025 * ((entropy / 100) - 0.75);
  }

  // ── Pressure adjustments ───────────────────────────────────────────────
  if (isBreakPoint || isDeuce) {
    // Server SPCI — raised from 0.40: holding serve under pressure is the
    // defining skill on grass where breaks are rare and costly.
    const spci = server.tier2?.spci;
    if (spci?.modifier_delta != null) {
      const w = confWeight(spci.confidence);
      p += w * spci.modifier_delta * 0.50;
    }

    // Returner clutch differential — raised from 0.50: on grass, a returner
    // who lifts their game at break point has an outsized impact.
    const clutch = returner.tier2?.clutch_differential;
    if (clutch?.modifier_delta != null) {
      const w = confWeight(clutch.confidence);
      p -= w * (clutch.modifier_delta / 100) * 0.60;
    }

    // Server double fault risk under pressure
    const dfDelta = server.tier2?.df_pressure_delta;
    if (dfDelta?.modifier_delta != null && isBreakPoint) {
      const w = confWeight(dfDelta.confidence);
      p += w * (dfDelta.modifier_delta / 100);
    }

    // Returner BP conversion ability — how efficiently a player converts
    // break-point opportunities. Average ATP conversion ~45%.
    // A 55% converter vs 35% creates a meaningful edge at this moment.
    if (isBreakPoint) {
      const bpc = returner.tier2?.bp_creation_profile;
      if (bpc?.bp_conversion != null) {
        const avgConversion = 0.45;
        p -= (bpc.bp_conversion - avgConversion) * 0.15;
      }
    }
  }

  // ── Momentum carry-over (previous point result) ────────────────────────
  if (opts.prevWonByServer != null) {
    const mom = server.tier2?.momentum_profile;
    if (mom) {
      const rate = opts.prevWonByServer
        ? mom.streak_survival_rate   // continuing a run
        : mom.streak_recovery_rate;  // recovering after a loss
      // Raised from 0.04 — momentum streaks are more persistent on grass
      // (serve dominance means once a player finds rhythm it compounds).
      p += (rate - 0.5) * 0.06;
    }
  }

  // ── Tiebreak differential ─────────────────────────────────────────────
  // Players with a positive tiebreak_differential win more points in
  // tiebreaks than their overall baseline — wire that into tiebreak points.
  if (opts.isTiebreak) {
    const tb  = server.tier2?.tiebreak_differential;
    const tbR = returner.tier2?.tiebreak_differential;
    if (tb?.available && tb.value != null)
      p += confWeight(tb.confidence) * (tb.value / 100) * 0.60;
    if (tbR?.available && tbR.value != null)
      p -= confWeight(tbR.confidence) * (tbR.value / 100) * 0.60;
  }

  // ── Set transition delta ──────────────────────────────────────────────
  // Players with a positive set_transition_delta start sets stronger than
  // their average — apply that edge to the opening games of each set.
  if (opts.isSetOpener) {
    const st  = server.tier2?.set_transition_delta;
    const stR = returner.tier2?.set_transition_delta;
    if (st?.available && st.value != null)
      p += confWeight(st.confidence) * (st.value / 100) * 0.50;
    if (stR?.available && stR.value != null)
      p -= confWeight(stR.confidence) * (stR.value / 100) * 0.50;
  }

  // ── Hold after break ──────────────────────────────────────────────────
  // When a server was broken in their previous service game, their
  // hold_after_break rate captures whether they respond well or poorly.
  if (opts.isPostBreak) {
    const habr = server.tier2?.hold_after_break;
    if (habr?.available && habr.value != null)
      p += confWeight(habr.confidence) * (habr.value / 100) * 0.50;
  }

  // ── Attrition slope (per-set physical decay) ──────────────────────────
  // A positive attrition_slope means the player runs more per point as
  // the match progresses — a proxy for fatigue. Negative is efficient.
  // Effect grows linearly with set number (setIndex 0 = first set, no effect).
  // confWeight gates out low-n slope estimates to prevent outlier values
  // (max observed: 9.5 m/set → −38pp unchecked) from breaking predictions.
  if (opts.setIndex != null && opts.setIndex > 0) {
    const att  = server.tier2?.attrition_slope;
    const attR = returner.tier2?.attrition_slope;
    if (att?.available && att.value != null)
      p += confWeight(att.confidence) * (-(att.value / 2.0) * opts.setIndex * 0.02);
    if (attR?.available && attR.value != null)
      p -= confWeight(attR.confidence) * (-(attR.value / 2.0) * opts.setIndex * 0.02);
  }

  return clamp(p);
}

// ── Game simulation ────────────────────────────────────────────────────────

/**
 * Simulate a single service game.
 * Returns true if the server holds, false if the returner breaks.
 * gameOpts are forwarded into pointWinProb: { isSetOpener, isPostBreak, setIndex }.
 */
function simulateGame(server, returner, rallyDist, gameOpts = {}) {
  let s = 0, r = 0;
  let prevWonByServer = null;

  for (;;) {
    const atAdSrv = s > r && s >= 4;   // advantage server
    const atAdRet = r > s && r >= 4;   // advantage returner
    const atDeuce  = s >= 3 && r >= 3 && s === r;

    const isBreakPoint = atAdRet || (r === 3 && s < 3);
    const isDeuce      = atDeuce || atAdSrv || atAdRet;

    const band = sampleBand(rallyDist);
    const won  = Math.random() < pointWinProb(
      server, returner, band, isBreakPoint, isDeuce,
      { prevWonByServer, ...gameOpts }
    );

    prevWonByServer = won;

    if (atAdSrv) { if (won) return true;  else { s = 3; r = 3; } continue; }
    if (atAdRet) { if (!won) return false; else { s = 3; r = 3; } continue; }

    if (won) s++; else r++;

    if (s >= 4 && s - r >= 2) return true;
    if (r >= 4 && r - s >= 2) return false;
    if (s === r && s >= 3) { s = 3; r = 3; }  // normalise deuce
  }
}

// ── Tiebreak simulation ────────────────────────────────────────────────────

function simulateTiebreak(fpA, fpB) {
  let pA = 0, pB = 0, pointsPlayed = 0;
  let prevWon = null;

  // Alternate serve: A serves 1st, then B serves 2 each, etc.
  function aServes(n) {
    if (n === 0) return true;
    return Math.floor((n - 1) / 2) % 2 === 1;
  }

  for (;;) {
    const atDeuce = pA >= 6 && pB >= 6 && pA === pB;
    const adA = pA > pB && pA >= 7;
    const adB = pB > pA && pB >= 7;

    const server  = aServes(pointsPlayed) ? fpA : fpB;
    const returner = aServes(pointsPlayed) ? fpB : fpA;

    // In a tiebreak any point at 6-6 is pressure
    const isPressure = (pA >= 5 && pB >= 5) || atDeuce || adA || adB;
    const band = sampleBand(buildRallyDist(server, returner));
    const sWon = Math.random() < pointWinProb(
      server, returner, band, adB /* break point from server's pov */, atDeuce,
      { prevWonByServer: prevWon, isTiebreak: true }
    );

    if (adA) { if (sWon === (server === fpA)) return true;  else { pA = 6; pB = 6; } }
    else if (adB) { if (sWon === (server === fpB)) return false; else { pA = 6; pB = 6; } }
    else {
      if (sWon) { if (server === fpA) pA++; else pB++; }
      else       { if (server === fpB) pA++; else pB++; }
    }

    if (pA >= 7 && pA - pB >= 2) return true;
    if (pB >= 7 && pB - pA >= 2) return false;

    prevWon = sWon === (server === fpA) ? true : false;  // from A's perspective
    pointsPlayed++;
  }
}

// ── Set simulation ─────────────────────────────────────────────────────────

/**
 * Simulate a single set.
 * setIndex: 0 = first set, 1 = second, etc. Used for attrition_slope decay.
 * Tracks gameInSet for set_transition_delta (first 2 games of each set)
 * and lastWasBreak for hold_after_break (server broken in previous game).
 */
function simulateSet(fpA, fpB, aServesFirst, setIndex = 0) {
  let gA = 0, gB = 0;
  let aServes = aServesFirst;
  let gameInSet = 0;
  let lastWasBreak = false;  // was the server broken in their last service game?

  for (;;) {
    const server   = aServes ? fpA : fpB;
    const returner = aServes ? fpB : fpA;
    const dist     = buildRallyDist(server, returner);

    const gameOpts = {
      isSetOpener: gameInSet < 2,   // first two games of each set
      isPostBreak: lastWasBreak,    // server was broken in their last service game
      setIndex,                     // for attrition_slope decay
    };

    const held = simulateGame(server, returner, dist, gameOpts);

    lastWasBreak = !held;  // update for next time this player serves
    gameInSet++;

    if (aServes) { if (held) gA++; else gB++; }
    else          { if (held) gB++; else gA++; }

    if (gA === 6 && gB === 6) {
      return { aWins: simulateTiebreak(fpA, fpB), gA: 7, gB: 6 };
    }
    if (gA >= 6 && gA - gB >= 2) return { aWins: true,  gA, gB };
    if (gB >= 6 && gB - gA >= 2) return { aWins: false, gA, gB };
    if (gA === 7) return { aWins: true,  gA: 7, gB: 5 };  // 7-5
    if (gB === 7) return { aWins: false, gA: 5, gB: 7 };  // 5-7

    aServes = !aServes;
  }
}

// ── Match simulation ───────────────────────────────────────────────────────

function simulateMatch(fpA, fpB) {
  let sA = 0, sB = 0;
  let aServesSet = true;
  let setIndex = 0;

  const setScores = [];

  while (sA < 3 && sB < 3) {
    const { aWins, gA, gB } = simulateSet(fpA, fpB, aServesSet, setIndex);
    setScores.push(aWins ? `${gA}-${gB}` : `${gB}-${gA}`);
    if (aWins) sA++; else sB++;
    aServesSet = !aServesSet;  // alternate who serves first each set
    setIndex++;
  }

  return { aWins: sA === 3, sA, sB };
}

// ── Platt calibration ──────────────────────────────────────────────────────
// Temperature-scaling form: p_cal = sigmoid(PLATT_A × logit(p_raw))
//
// v1: PLATT_A = 0.33 — heavy compression needed before matchup adjustment.
// v2: PLATT_A = 0.42 — matchup-adjusted serve probabilities reduce
//     overconfidence substantially; A=0.42 fitted on 915 matches.
const PLATT_A = 0.42;

function plattCalibrate(p) {
  if (p <= 1e-9 || p >= 1 - 1e-9) return p;
  const logit = Math.log(p / (1 - p));
  return 1 / (1 + Math.exp(-PLATT_A * logit));
}

// ── Monte Carlo run ────────────────────────────────────────────────────────

function runMonteCarlo(fpA, fpB, nSims, onProgress) {
  let winsA = 0;
  const scoreCount = {};
  const reportEvery = Math.max(250, Math.floor(nSims / 20));  // ~20 updates

  for (let i = 0; i < nSims; i++) {
    const { aWins, sA, sB } = simulateMatch(fpA, fpB);
    // Always store from A's perspective: "sA-sB" so "3-x" = A wins, "x-3" = B wins
    const key = `${sA}-${sB}`;
    scoreCount[key] = (scoreCount[key] || 0) + 1;
    if (aWins) winsA++;
    if (onProgress && (i + 1) % reportEvery === 0) {
      // Scale to 5–78% range (leave headroom for axis analysis)
      const pct = Math.round(5 + ((i + 1) / nSims) * 73);
      onProgress(i + 1, pct);
    }
  }

  // Raw MC output → Platt-calibrated probability
  const pWinA_raw = winsA / nSims;
  const pWinA = plattCalibrate(pWinA_raw);
  const pWinB = 1 - pWinA;

  // Wilson confidence interval (computed on calibrated probability)
  const z  = 1.96;
  const n  = nSims;
  const p  = pWinA;
  const denom = 1 + z * z / n;
  const centre = (p + z * z / (2 * n)) / denom;
  const margin = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom;
  const ciLow  = Math.max(0, centre - margin);
  const ciHigh = Math.min(1, centre + margin);

  // Normalise score distribution
  const scoreDist = {};
  for (const [k, c] of Object.entries(scoreCount)) scoreDist[k] = c / nSims;

  return { pWinA, pWinB, scoreDist, ciLow, ciHigh, nSims, pWinA_raw };
}

// ── Axis contribution analysis ─────────────────────────────────────────────

/**
 * Estimate each fingerprint axis's contribution by running a short
 * "neutralised" simulation — replace one axis's data with the average
 * of both players and measure the change in pWinA.
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
    contrib[name] = basePWinA - pWinA;  // positive = this axis helps A
  }

  const dominant = Object.entries(contrib).reduce(
    (best, [k, v]) => Math.abs(v) > Math.abs(best[1]) ? [k, v] : best,
    ["none", 0]
  );

  return { contrib, dominantAxis: dominant[0] };
}

function deepClone(obj) { return JSON.parse(JSON.stringify(obj)); }

function neutraliseRally(fpA, fpB) {
  const nA = deepClone(fpA), nB = deepClone(fpB);
  // Average the two rally curves
  for (const b of ["1_3","4_6","7_9","10+"]) {
    const wa = nA.tier2?.rally_win_curve?.[b]?.win_pct;
    const wb = nB.tier2?.rally_win_curve?.[b]?.win_pct;
    if (wa != null && wb != null) {
      const avg = (wa + wb) / 2;
      nA.tier2.rally_win_curve[b].win_pct = avg;
      nB.tier2.rally_win_curve[b].win_pct = avg;
    }
  }
  return [nA, nB];
}

function neutralisePressure(fpA, fpB) {
  const nA = deepClone(fpA), nB = deepClone(fpB);
  for (const fp of [nA, nB]) {
    if (fp.tier2?.spci)                  fp.tier2.spci.modifier_delta = 0;
    if (fp.tier2?.clutch_differential)  fp.tier2.clutch_differential.modifier_delta = 0;
    if (fp.tier2?.df_pressure_delta)    fp.tier2.df_pressure_delta.modifier_delta = 0;
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
  // Neutralise break-point conversion advantage by setting both players to
  // the average ATP grass conversion rate. This makes the axis contribution
  // meaningful rather than a no-op.
  const avgConv = 0.45;
  const nA = deepClone(fpA), nB = deepClone(fpB);
  for (const fp of [nA, nB]) {
    if (fp.tier2?.bp_creation_profile) {
      fp.tier2.bp_creation_profile.bp_conversion = avgConv;
    }
  }
  return [nA, nB];
}

// ── Worker entry point ─────────────────────────────────────────────────────

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
