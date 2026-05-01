"""
Monte Carlo match simulation — Phase 3.

Runs N simulations of a tennis match point-by-point, applying
state-dependent probability modifiers from the MatchupOutput.

State-dependent modifiers (δ(s)):
  Normal points:     p(s) = p_base
  BP/Deuce states:   p(s) = p_base × (1 + spci_delta)
  Tiebreak points:   p(s) = p_base × (1 + axis_pressure)
  Sets 4-5 points:   p(s) = p_base × (1 + axis_durability)

Score distribution output format:
  {
    "3-0": 0.23, "3-1": 0.31, "3-2": 0.18,  # A wins
    "0-3": 0.06, "1-3": 0.12, "2-3": 0.10,  # B wins
  }

Usage:
    from monte_carlo import simulate_match
    from comparison import compare, load_fingerprint

    fp_a = load_fingerprint("Roger Federer",  2019)
    fp_b = load_fingerprint("Novak Djokovic", 2019)
    out  = compare(fp_a, fp_b, year=2019)
    result = simulate_match(out, n=10000)
    print(result.summary())
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from markov import TennisMarkov


# ── simulation parameters ─────────────────────────────────────────────────

DEFAULT_N      = 10_000
BEST_OF        = 5          # Grand Slam men's draw
SETS_TO_WIN    = 3

# Tiebreak format by set number (1-indexed)
# Wimbledon 2022+: 10-pt match tiebreak in Set 5
# Pre-2022: advantage set in Set 5 (played until 2-game lead)
# For simplicity, model all years with 7-pt tiebreak at 6-6; flag set 5 handling
TB_POINTS_TO_WIN = {1: 7, 2: 7, 3: 7, 4: 7, 5: 7}  # 10 for 2022+ Set 5

# Score state categories — determines which δ modifier to apply
class ScoreState:
    NORMAL        = "normal"
    BREAK_POINT   = "break_point"    # BP against server
    DEUCE         = "deuce"
    TIEBREAK      = "tiebreak"
    LATE_MATCH    = "late_match"     # Set 4 or 5


# ── state detection ───────────────────────────────────────────────────────

def _score_state(gs: int, gr: int, in_tiebreak: bool,
                 current_set: int) -> str:
    """Classify current game score state for modifier selection."""
    if in_tiebreak:
        return ScoreState.TIEBREAK
    if current_set >= 4:
        return ScoreState.LATE_MATCH
    # Game scores: 0=0, 1=15, 2=30, 3=40+, with BP/Deuce at 3-3+
    if gs == 3 and gr >= 3:
        return ScoreState.DEUCE
    if gr == 3 and gs < 3:
        return ScoreState.BREAK_POINT   # returner at match point (BP against server)
    return ScoreState.NORMAL


# ── per-point probability with modifier ───────────────────────────────────

def _p_point(base: float, state: str,
             spci_delta: float,
             axis_pressure: float,
             axis_durability: float) -> float:
    """
    Apply score-state modifier δ(s) to base probability.

    p(s) = p_base × (1 + δ(s))

    clamped to [0.05, 0.95] to prevent degenerate probabilities.
    """
    if state == ScoreState.BREAK_POINT or state == ScoreState.DEUCE:
        delta = spci_delta          # SPCI modifier (negative = worse under pressure)
    elif state == ScoreState.TIEBREAK:
        delta = axis_pressure       # pressure resilience edge
    elif state == ScoreState.LATE_MATCH:
        delta = axis_durability     # physical durability edge
    else:
        delta = 0.0

    modified = base * (1.0 + delta)
    return max(0.05, min(0.95, modified))


# ── tiebreak simulation ───────────────────────────────────────────────────

def _play_tiebreak(p_serve_a: float, p_serve_b: float,
                   axis_pressure: float, axis_durability: float,
                   current_set: int, rng) -> bool:
    """
    Simulate a single tiebreak. Returns True if player A wins.

    Service alternates: A serves first, then every 2 points.
    In late sets (4-5), durability modifier also applies.
    """
    ta, tb = 0, 0
    tb_pts = TB_POINTS_TO_WIN.get(current_set, 7)

    def _a_serves(n_played: int) -> bool:
        if n_played == 0:
            return True
        return ((n_played - 1) // 2) % 2 == 1

    n = 0
    while True:
        a_serves  = _a_serves(n)
        base      = p_serve_a if a_serves else (1.0 - p_serve_b)
        # Tiebreak points get both tiebreak modifier AND late-match if applicable
        state     = ScoreState.TIEBREAK
        if current_set >= 4:
            state = ScoreState.LATE_MATCH  # durability takes precedence in late sets
        spci_here = axis_pressure if state == ScoreState.TIEBREAK else axis_durability
        p = _p_point(base, state, spci_here, axis_pressure, axis_durability)

        if rng.random() < p:
            ta += 1
        else:
            tb += 1
        n += 1

        if ta >= tb_pts and ta - tb >= 2:
            return True
        if tb >= tb_pts and tb - ta >= 2:
            return False


# ── game simulation ───────────────────────────────────────────────────────

def _play_game(p_serve: float, a_is_server: bool,
               spci_a: float, spci_b: float,
               axis_pressure: float, axis_durability: float,
               current_set: int, rng) -> bool:
    """
    Simulate one game (non-tiebreak). Returns True if player A wins.

    p_serve: baseline probability the *server* wins a point.
    a_is_server: whether player A is serving this game.

    SPCI modifier applied at BP and Deuce states:
    - If A is server, A's SPCI applies (negative = A less reliable under pressure)
    - If B is server, B's SPCI applies (B's weakness = A's opportunity, so -spci_b)
    """
    gs, gr = 0, 0   # server's score, returner's score

    # Which player's SPCI to use at pressure states
    # Server's SPCI modifies the server's performance at BP/Deuce
    # If A serves: positive SPCI_A = A holds better under pressure (good for A)
    # If B serves: positive SPCI_B = B holds better under pressure (bad for A)
    if a_is_server:
        pressure_delta = spci_a    # A serving: A's SPCI
    else:
        pressure_delta = -spci_b   # B serving: B's SPCI (negated → A's perspective)

    while True:
        state = _score_state(gs, gr, False, current_set)
        p = _p_point(p_serve, state, pressure_delta, axis_pressure, axis_durability)

        if rng.random() < p:
            gs += 1
        else:
            gr += 1

        # Game-winning conditions
        if gs >= 4 and gs - gr >= 2:
            return a_is_server    # server wins game
        if gr >= 4 and gr - gs >= 2:
            return not a_is_server  # returner wins game


# ── set simulation ────────────────────────────────────────────────────────

def _play_set(p_serve_a: float, p_serve_b: float,
              a_serves_first: bool,
              spci_a: float, spci_b: float,
              axis_pressure: float, axis_durability: float,
              current_set: int, rng) -> Tuple[bool, int, int]:
    """
    Simulate one set. Returns (A_won, games_A, games_B).
    """
    ga, gb = 0, 0
    a_serves = a_serves_first

    while True:
        # Tiebreak at 6-6
        if ga == 6 and gb == 6:
            a_wins_tb = _play_tiebreak(
                p_serve_a, p_serve_b,
                axis_pressure, axis_durability, current_set, rng)
            if a_wins_tb:
                ga += 1
            else:
                gb += 1
            return (ga > gb), ga, gb

        # Determine serving probability
        if a_serves:
            p_serve = p_serve_a
        else:
            p_serve = p_serve_b

        a_wins_game = _play_game(
            p_serve, a_serves,
            spci_a, spci_b,
            axis_pressure, axis_durability,
            current_set, rng)

        if a_wins_game:
            ga += 1
        else:
            gb += 1

        # Set-winning conditions
        if ga >= 6 and ga - gb >= 2:
            return True, ga, gb
        if gb >= 6 and gb - ga >= 2:
            return False, ga, gb

        a_serves = not a_serves


# ── match simulation ──────────────────────────────────────────────────────

def _simulate_one_match(p_serve_a: float, p_serve_b: float,
                        spci_a: float, spci_b: float,
                        axis_pressure: float, axis_durability: float,
                        rng) -> Tuple[int, int]:
    """
    Simulate one full match. Returns (sets_a, sets_b).
    """
    sa, sb = 0, 0
    a_serves_set = True   # A serves first game of Set 1

    while sa < SETS_TO_WIN and sb < SETS_TO_WIN:
        current_set = sa + sb + 1
        a_won_set, _, _ = _play_set(
            p_serve_a, p_serve_b, a_serves_set,
            spci_a, spci_b,
            axis_pressure, axis_durability,
            current_set, rng)

        if a_won_set:
            sa += 1
        else:
            sb += 1

        # Service alternates each set
        a_serves_set = not a_serves_set

    return sa, sb


# ── SimulationResult ──────────────────────────────────────────────────────

@dataclass
class SimulationResult:
    """Full Monte Carlo output for one matchup."""

    player_a:      str
    player_b:      str
    n_simulations: int

    # Win probability
    p_win_a:       float
    p_win_b:       float

    # Score distribution: {"3-0": 0.22, "3-1": 0.31, ...}
    score_dist:    Dict[str, float] = field(default_factory=dict)

    # Expected set count
    expected_sets: float = 0.0

    # Analytical baseline (no state modifiers) for comparison
    analytical_p_win_a: float = 0.0

    # Markov baseline summary
    markov_summary: dict = field(default_factory=dict)

    def summary(self) -> dict:
        dist_str = {k: f"{v*100:.1f}%" for k, v in sorted(self.score_dist.items())}
        return {
            "matchup":             f"{self.player_a} vs {self.player_b}",
            "n_simulations":       self.n_simulations,
            "p_win_A":             f"{self.p_win_a*100:.1f}%",
            "p_win_B":             f"{self.p_win_b*100:.1f}%",
            "analytical_p_win_A":  f"{self.analytical_p_win_a*100:.1f}%",
            "expected_sets":       f"{self.expected_sets:.2f}",
            "score_distribution":  dist_str,
        }

    def __repr__(self) -> str:
        return (f"SimulationResult({self.player_a} vs {self.player_b}: "
                f"{self.p_win_a*100:.1f}% A, n={self.n_simulations})")


# ── public API ────────────────────────────────────────────────────────────

def simulate_match(matchup,
                   n: int = DEFAULT_N,
                   seed: Optional[int] = None) -> SimulationResult:
    """
    Run N Monte Carlo match simulations from a MatchupOutput.

    Parameters
    ----------
    matchup : MatchupOutput (from comparison.compare())
    n       : number of simulations (default 10,000)
    seed    : optional RNG seed for reproducibility

    Returns
    -------
    SimulationResult with win probabilities, score distribution,
    and comparison against the analytical Markov baseline.
    """
    rng = random.Random(seed)

    # Markov analytical baseline (no state modifiers)
    markov = TennisMarkov(matchup.p_serve_a, matchup.p_serve_b)

    # Monte Carlo state-modifier inputs
    spci_a        = matchup.spci_a         # A's pressure modifier
    spci_b        = matchup.spci_b         # B's pressure modifier
    axis_pressure  = matchup.axis_pressure  # tiebreak modifier (edge)
    axis_durability= matchup.axis_durability

    # Scale SPCI to a sensible δ range
    # SPCI ∈ [-0.3, +0.3] roughly; we want δ ∈ [-0.15, +0.15]
    spci_scale = 0.5
    spci_a_delta = spci_a * spci_scale
    spci_b_delta = spci_b * spci_scale

    # Pressure and durability edges are already in [-1, +1]; scale down
    pressure_delta  = axis_pressure   * 0.08
    durability_delta = axis_durability * 0.05

    # Run simulations
    score_counts: Dict[Tuple[int, int], int] = {}
    wins_a = 0

    for _ in range(n):
        sa, sb = _simulate_one_match(
            matchup.p_serve_a, matchup.p_serve_b,
            spci_a_delta, spci_b_delta,
            pressure_delta, durability_delta,
            rng)
        score_counts[(sa, sb)] = score_counts.get((sa, sb), 0) + 1
        if sa > sb:
            wins_a += 1

    # Build result
    score_dist = {f"{sa}-{sb}": count / n
                  for (sa, sb), count in sorted(score_counts.items())}
    expected_sets = sum(
        (sa + sb) * (count / n)
        for (sa, sb), count in score_counts.items())

    return SimulationResult(
        player_a           = matchup.player_a,
        player_b           = matchup.player_b,
        n_simulations      = n,
        p_win_a            = wins_a / n,
        p_win_b            = 1.0 - wins_a / n,
        score_dist         = score_dist,
        expected_sets      = round(expected_sets, 2),
        analytical_p_win_a = markov.p_match(),
        markov_summary     = markov.summary(),
    )
