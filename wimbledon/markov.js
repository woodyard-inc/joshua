/**
 * markov.js — Analytical tennis Markov chain (browser-side).
 *
 * Port of src/markov.py. All functions are pure and stateless.
 *
 * Conventions:
 *   pA = P(A wins a point when A serves)
 *   pB = P(B wins a point when B serves)
 *   → P(A wins a point when B serves) = 1 − pB
 */

// ── point → game ──────────────────────────────────────────────────────────

function pGameWin(p) {
  const q = 1 - p;
  const noDeuce   = p**4 * (1 + 4*q + 10*q**2);
  const reachDeuce = 20 * p**3 * q**3;           // C(6,3) = 20
  const pWinDeuce  = p**2 / (p**2 + q**2);
  return noDeuce + reachDeuce * pWinDeuce;
}

// ── tiebreak ───────────────────────────────────────────────────────────────

function _tbAServes(n) {
  if (n === 0) return true;
  return Math.floor((n - 1) / 2) % 2 === 1;
}

function pTiebreakWin(pA, pB) {
  const pa = pA;
  const pb = 1 - pB;   // P(A wins a point when B serves)
  const denom = pa * pb + (1 - pa) * (1 - pb);
  const pDeuceWin = denom > 0 ? pa * pb / denom : 0.5;

  const memo = {};
  function dp(ta, tb, n) {
    if (ta >= 7 && ta - tb >= 2) return 1;
    if (tb >= 7 && tb - ta >= 2) return 0;
    if (ta === tb && ta >= 6) return pDeuceWin;
    const key = `${ta},${tb},${n}`;
    if (key in memo) return memo[key];
    const p = _tbAServes(n) ? pa : pb;
    memo[key] = p * dp(ta+1, tb, n+1) + (1-p) * dp(ta, tb+1, n+1);
    return memo[key];
  }
  return dp(0, 0, 0);
}

// ── game → set ─────────────────────────────────────────────────────────────

function pSetWin(pA, pB, aServesFirst = true) {
  const pHoldA  = pGameWin(pA);
  const pBreakA = 1 - pGameWin(pB);   // P(A wins game when B serves)
  const pTB     = pTiebreakWin(pA, pB);

  const memo = {};
  function dp(ga, gb, aServes) {
    if (ga >= 6 && ga - gb >= 2) return 1;
    if (gb >= 6 && gb - ga >= 2) return 0;
    if (ga === 6 && gb === 6) return pTB;
    const key = `${ga},${gb},${+aServes}`;
    if (key in memo) return memo[key];
    const p = aServes ? pHoldA : pBreakA;
    memo[key] = p * dp(ga+1, gb, !aServes) + (1-p) * dp(ga, gb+1, !aServes);
    return memo[key];
  }
  return dp(0, 0, aServesFirst);
}

// ── set → match ────────────────────────────────────────────────────────────

function pMatchWin(pA, pB) {
  const S = 3; // sets to win (best of 5)

  const memo = {};
  function dp(sa, sb, aServesSet) {
    if (sa === S) return 1;
    if (sb === S) return 0;
    const key = `${sa},${sb},${+aServesSet}`;
    if (key in memo) return memo[key];
    const pSet = pSetWin(pA, pB, aServesSet);
    memo[key] = pSet * dp(sa+1, sb, !aServesSet) +
                (1-pSet) * dp(sa, sb+1, !aServesSet);
    return memo[key];
  }
  return dp(0, 0, true);
}

// ── full scoreline distribution ─────────────────────────────────────────────

function fullScoreDist(pA, pB) {
  const S = 3;

  const memo = {};
  function dp(sa, sb, aServesSet) {
    const key = `${sa},${sb},${+aServesSet}`;
    if (key in memo) return memo[key];
    if (sa === S || sb === S) {
      memo[key] = { [`${sa}-${sb}`]: 1 };
      return memo[key];
    }
    const pSet = pSetWin(pA, pB, aServesSet);
    const dA = dp(sa+1, sb,  !aServesSet);
    const dB = dp(sa,   sb+1, !aServesSet);
    const result = {};
    for (const [k, p] of Object.entries(dA)) result[k] = (result[k] || 0) + pSet   * p;
    for (const [k, p] of Object.entries(dB)) result[k] = (result[k] || 0) + (1-pSet) * p;
    memo[key] = result;
    return result;
  }
  return dp(0, 0, true);
}
