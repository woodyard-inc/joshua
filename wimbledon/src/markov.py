"""
Tennis Markov chain engine.

Provides analytical point → game → set → match win probabilities,
plus tiebreak modelling with correct alternating service.

Usage:
    from markov import TennisMarkov
    m = TennisMarkov(p_serve_a=0.66, p_serve_b=0.63)
    print(m.p_match())            # P(A wins match)
    print(m.full_score_dist())    # {(sa, sb): prob, ...}

Design note
-----------
The Markov chain computes *expected* outcomes using constant per-point
probabilities.  State-dependent modifiers (SPCI deltas at pressure states,
durability edge in late sets) are applied in monte_carlo.py on a
point-by-point basis inside each simulation.  Keeping the two concerns
separate makes both easier to reason about.

Conventions
-----------
• "A" is always the player whose perspective we track.
• p_serve_a  = P(A wins a point when A is serving)
• p_serve_b  = P(B wins a point when B is serving)
  → P(A wins a point when B is serving) = 1 - p_serve_b
• Best-of-5 by default (Wimbledon men's draw).
• Tiebreak at 6-6 in every set (Wimbledon uses 10-pt match tiebreak
  in Set 5 from 2022; that nuance is handled in monte_carlo.py).
"""

from __future__ import annotations

import math
from functools import lru_cache
from typing import Dict, Tuple


# ── point → game ──────────────────────────────────────────────────────────

def p_game_win(p: float) -> float:
    """
    P(server wins game) given per-point probability p.

    Analytical formula derived from the Markov chain solution.
    Deuce is modelled as an absorbing geometric series: at 3-3 (score),
    P(server wins from deuce) = p² / (p² + q²).
    """
    p = float(p)
    q = 1.0 - p
    # Paths that end before deuce (server wins 4-0, 4-1, 4-2)
    no_deuce = p**4 * (1.0 + 4*q + 10*q**2)
    # Probability of reaching deuce (both players at 3 points)
    reach_deuce = math.comb(6, 3) * p**3 * q**3
    p_win_deuce = p**2 / (p**2 + q**2)
    return no_deuce + reach_deuce * p_win_deuce


# ── tiebreak service sequence ──────────────────────────────────────────────

def _tb_a_serves(total_points: int) -> bool:
    """
    Returns True if player A serves the next tiebreak point.

    Service sequence (assuming A serves the first point):
      Point 1  : A serves
      Points 2–3: B serves
      Points 4–5: A serves
      Points 6–7: B serves  … and so on every 2 points.
    """
    if total_points == 0:
        return True
    # After the first point, groups of 2 alternate starting with B
    return ((total_points - 1) // 2) % 2 == 1  # A serves in odd-numbered groups


def p_tiebreak_win(p_serve_a: float, p_serve_b: float) -> float:
    """
    P(player A wins 7-point tiebreak) with correct alternating service.

    Uses memoised DP over (ta, tb, total_points) states.
    First to 7 with 2-point lead; at 6-6 uses closed-form deuce formula
    to avoid infinite recursion.

    Closed-form at deuce (ta == tb >= 6):
      pa = p_serve_a,  pb = 1 - p_serve_b
      P(A wins) = pa*pb / (pa*pb + (1-pa)*(1-pb))

    This holds because the alternating pair structure (A,B) and (B,A)
    produces symmetric equations that collapse to this single expression.
    """
    pa = p_serve_a
    pb = 1.0 - p_serve_b
    denom = pa * pb + (1.0 - pa) * (1.0 - pb)
    p_deuce_win = (pa * pb / denom) if denom > 0 else 0.5

    @lru_cache(maxsize=None)
    def dp(ta: int, tb: int, n: int) -> float:
        """P(A wins tiebreak) from score (ta, tb), n points already played."""
        if ta >= 7 and ta - tb >= 2:
            return 1.0
        if tb >= 7 and tb - ta >= 2:
            return 0.0
        # Deuce extension: closed-form to avoid infinite recursion
        if ta == tb and ta >= 6:
            return p_deuce_win
        p = pa if _tb_a_serves(n) else pb
        return p * dp(ta + 1, tb, n + 1) + (1.0 - p) * dp(ta, tb + 1, n + 1)

    result = dp(0, 0, 0)
    dp.cache_clear()
    return result


# ── game → set ────────────────────────────────────────────────────────────

def p_set_win(p_serve_a: float, p_serve_b: float,
              a_serves_first: bool = True) -> float:
    """
    P(player A wins a set), with correct alternating service per game.

    p_serve_a: P(A wins a point when A serves)
    p_serve_b: P(B wins a point when B serves)
    a_serves_first: whether A serves the first game of the set.
    """
    p_hold_a  = p_game_win(p_serve_a)
    p_hold_b  = p_game_win(p_serve_b)
    p_break_a = 1.0 - p_hold_b   # P(A wins game when B serves)
    p_tb      = p_tiebreak_win(p_serve_a, p_serve_b)

    @lru_cache(maxsize=None)
    def dp(ga: int, gb: int, a_serves: bool) -> float:
        """P(A wins set from game score (ga, gb))."""
        # Set won
        if ga >= 6 and ga - gb >= 2:
            return 1.0
        if gb >= 6 and gb - ga >= 2:
            return 0.0
        # Tiebreak at 6-6 (A serves first tiebreak point = whoever served last)
        if ga == 6 and gb == 6:
            return p_tb
        # Next game
        if a_serves:
            p_win = p_hold_a
        else:
            p_win = p_break_a
        return (p_win   * dp(ga + 1, gb,     not a_serves) +
                (1-p_win) * dp(ga,     gb + 1, not a_serves))

    result = dp(0, 0, a_serves_first)
    dp.cache_clear()
    return result


# ── set → match ───────────────────────────────────────────────────────────

def p_match_win(p_serve_a: float, p_serve_b: float,
                best_of: int = 5) -> float:
    """
    P(player A wins match), best-of-5 or best-of-3.

    Service in each set alternates starting server.  Assumes A serves
    the first game of Set 1; sets thereafter alternate the opening server.
    """
    sets_to_win = (best_of + 1) // 2

    @lru_cache(maxsize=None)
    def dp(sa: int, sb: int, a_serves_set: bool) -> float:
        """P(A wins match from set score (sa, sb))."""
        if sa == sets_to_win:
            return 1.0
        if sb == sets_to_win:
            return 0.0
        # Service flips each set
        p_set = p_set_win(p_serve_a, p_serve_b, a_serves_first=a_serves_set)
        return (p_set       * dp(sa + 1, sb,     not a_serves_set) +
                (1 - p_set) * dp(sa,     sb + 1, not a_serves_set))

    result = dp(0, 0, True)
    dp.cache_clear()
    return result


def full_score_distribution(p_serve_a: float, p_serve_b: float,
                             best_of: int = 5) -> Dict[Tuple[int, int], float]:
    """
    Returns the probability of every possible match scoreline.

    Result keys are (sets_a, sets_b) tuples, values are probabilities.
    Probabilities sum to 1.0.
    """
    sets_to_win = (best_of + 1) // 2
    dist: Dict[Tuple[int, int], float] = {}

    @lru_cache(maxsize=None)
    def dp(sa: int, sb: int, a_serves_set: bool) -> Dict[Tuple[int, int], float]:
        if sa == sets_to_win:
            return {(sa, sb): 1.0}
        if sb == sets_to_win:
            return {(sa, sb): 1.0}
        p_set = p_set_win(p_serve_a, p_serve_b, a_serves_first=a_serves_set)
        d: Dict[Tuple[int, int], float] = {}
        for scoreline, prob in dp(sa + 1, sb, not a_serves_set).items():
            d[scoreline] = d.get(scoreline, 0.0) + p_set * prob
        for scoreline, prob in dp(sa, sb + 1, not a_serves_set).items():
            d[scoreline] = d.get(scoreline, 0.0) + (1 - p_set) * prob
        return d

    result = dict(dp(0, 0, True))
    dp.cache_clear()
    return result


# ── top-level convenience class ───────────────────────────────────────────

class TennisMarkov:
    """
    Convenience wrapper holding a fixed (p_serve_a, p_serve_b) pair.

    All analytical outputs are cached after first computation.
    """

    def __init__(self, p_serve_a: float, p_serve_b: float, best_of: int = 5):
        self.p_serve_a = float(p_serve_a)
        self.p_serve_b = float(p_serve_b)
        self.best_of   = best_of
        self._p_match: float | None = None
        self._dist:    Dict | None  = None

    # ── convenience ────────────────────────────────────────────────────

    def p_hold_a(self) -> float:
        return p_game_win(self.p_serve_a)

    def p_hold_b(self) -> float:
        return p_game_win(self.p_serve_b)

    def p_set(self, a_serves_first: bool = True) -> float:
        return p_set_win(self.p_serve_a, self.p_serve_b, a_serves_first)

    def p_match(self) -> float:
        if self._p_match is None:
            self._p_match = p_match_win(self.p_serve_a, self.p_serve_b, self.best_of)
        return self._p_match

    def score_distribution(self) -> Dict[Tuple[int, int], float]:
        if self._dist is None:
            self._dist = full_score_distribution(
                self.p_serve_a, self.p_serve_b, self.best_of)
        return self._dist

    def summary(self) -> dict:
        dist = self.score_distribution()
        p_win = self.p_match()
        return {
            "p_match_win_A":   round(p_win, 4),
            "p_hold_A":        round(self.p_hold_a(), 4),
            "p_hold_B":        round(self.p_hold_b(), 4),
            "p_set":           round(self.p_set(), 4),
            "score_dist":      {f"{sa}-{sb}": round(p, 4)
                                for (sa, sb), p in sorted(dist.items())},
        }

    def __repr__(self) -> str:
        return (f"TennisMarkov(p_serve_a={self.p_serve_a:.3f}, "
                f"p_serve_b={self.p_serve_b:.3f}, "
                f"p_match={self.p_match():.3f})")
