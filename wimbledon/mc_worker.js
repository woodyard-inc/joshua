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
const GRASS_PRIOR   = { "1_3": 0.50, "4_6": 0.30, "7_9": 0.12, "10+": 0.08 };

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

// ── Core point win probability ─────────────────────────────────────────────

/**
 * Estimate P(server wins point) for a given rally band and game state.
 *
 * Primary source: rally_win_curve (Tier 2).
 * The blend  0.5 * server.win_pct + 0.5 * (1 - returner.win_pct)
 * gives a symmetric estimate that cancels when both players are equal.
 *
 * Pressure modifiers use SPCI (server side) and clutch_differential
 * (returner side), gated by confidence weights so unreliable data
 * contributes nothing.
 */
function pointWinProb(server, returner, band, isBreakPoint, isDeuce, opts = {}) {
  const srvCurve = (server.tier2  || {}).rally_win_curve || {};
  const retCurve = (returner.tier2 || {}).rally_win_curve || {};
  const sWin = srvCurve[band]?.win_pct;
  const rWin = retCurve[band]?.win_pct;

  let p;
  if (sWin != null && rWin != null) {
    // Fingerprint primary path — blend both players' rally profiles
    p = 0.5 * (sWin / 100) + 0.5 * (1 - rWin / 100);
  } else if (sWin != null) {
    p = sWin / 100;
  } else if (rWin != null) {
    p = 1 - rWin / 100;
  } else {
    // Tier-1 fallback only when no rally curve data at all
    p = tier1ServeProb(server);
  }

  // ── Serve entropy (unpredictability of direction) ──────────────────────
  const entropy = server.tier2?.serve_entropy?.pct_of_max;
  if (entropy != null) {
    // Max effect ±1.5 pp; centred at 75% of maximum entropy
    p += 0.015 * ((entropy / 100) - 0.75);
  }

  // ── Pressure adjustments ───────────────────────────────────────────────
  if (isBreakPoint || isDeuce) {
    // Server SPCI (service pressure clutch index)
    const spci = server.tier2?.spci;
    if (spci?.modifier_delta != null) {
      const w = confWeight(spci.confidence);
      // modifier_delta is already a probability fraction; scale down to avoid overweighting
      p += w * spci.modifier_delta * 0.40;
    }

    // Returner clutch differential — positive value = returner lifts under pressure
    const clutch = returner.tier2?.clutch_differential;
    if (clutch?.modifier_delta != null) {
      const w = confWeight(clutch.confidence);
      // modifier_delta in pp units (e.g. 0.2 pp difference) → convert to fraction
      p -= w * (clutch.modifier_delta / 100) * 0.50;
    }

    // Server double fault risk — negative modifier_delta = fewer DFs under pressure
    const dfDelta = server.tier2?.df_pressure_delta;
    if (dfDelta?.modifier_delta != null && isBreakPoint) {
      const w = confWeight(dfDelta.confidence);
      // modifier_delta is a percentage-point change in DF rate (e.g. -2.1 pp)
      p += w * (dfDelta.modifier_delta / 100);
    }
  }

  // ── Momentum carry-over (previous point result) ────────────────────────
  if (opts.prevWonByServer != null) {
    const mom = server.tier2?.momentum_profile;
    if (mom) {
      const rate = opts.prevWonByServer
        ? mom.streak_survival_rate   // continuing a run
        : mom.streak_recovery_rate;  // recovering after a loss
      p += (rate - 0.5) * 0.04;
    }
  }

  return clamp(p);
}

// ── Game simulation ────────────────────────────────────────────────────────

/**
 * Simulate a single service game.
 * Returns true if the server holds, false if the returner breaks.
 */
function simulateGame(server, returner, rallyDist) {
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
      server, returner, band, isBreakPoint, isDeuce, { prevWonByServer }
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
      server, returner, band, adB /* break point from server's pov */, atDeuce, { prevWonByServer: prevWon }
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

function simulateSet(fpA, fpB, aServesFirst) {
  let gA = 0, gB = 0;
  let aServes = aServesFirst;

  // Pre-build rally distributions (server perspective changes each game)
  for (;;) {
    const server   = aServes ? fpA : fpB;
    const returner = aServes ? fpB : fpA;
    const dist     = buildRallyDist(server, returner);
    const held     = simulateGame(server, returner, dist);

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

  const setScores = [];

  while (sA < 3 && sB < 3) {
    const { aWins, gA, gB } = simulateSet(fpA, fpB, aServesSet);
    setScores.push(aWins ? `${gA}-${gB}` : `${gB}-${gA}`);
    if (aWins) sA++; else sB++;
    aServesSet = !aServesSet;  // alternate who serves first each set
  }

  return { aWins: sA === 3, sA, sB };
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

  const pWinA = winsA / nSims;
  const pWinB = 1 - pWinA;

  // Wilson confidence interval
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

  return { pWinA, pWinB, scoreDist, ciLow, ciHigh, nSims };
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
  // BP creation/conversion currently informs axis display but doesn't
  // directly modify the point simulation — skip for now.
  return [deepClone(fpA), deepClone(fpB)];
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
