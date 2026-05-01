"""
Match Engine — Phase 3 top-level orchestrator.

Chains: fingerprint loading → comparison model → Monte Carlo simulation
into a single callable.

Usage (CLI):
    python src/match_engine.py "Roger Federer" "Novak Djokovic" --year 2019
    python src/match_engine.py "Federer" "Djokovic" --year 2019 --n 50000

Usage (Python):
    from match_engine import analyse_matchup
    result = analyse_matchup("Roger Federer", "Novak Djokovic", year=2019)
    print(result["narrative"])
    print(result["simulation"].summary())
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

# Run from project root
sys.path.insert(0, str(Path(__file__).parent))

from comparison import compare, load_fingerprint, MatchupOutput
from monte_carlo import simulate_match, SimulationResult, DEFAULT_N
from markov import TennisMarkov


def analyse_matchup(
    player_a: str,
    player_b: str,
    year: int,
    surface: str = "grass",
    n_simulations: int = DEFAULT_N,
    seed: Optional[int] = None,
) -> dict:
    """
    Full Phase 3 analysis for a player matchup.

    Returns a structured dict containing:
      - "matchup":    MatchupOutput (comparison model output)
      - "simulation": SimulationResult (Monte Carlo)
      - "narrative":  One-sentence edge summary
      - "axes":       Axis-by-axis breakdown
      - "verdicts":   Pressure verdict per player
    """
    # 1. Load fingerprints
    fp_a = load_fingerprint(player_a, year)
    fp_b = load_fingerprint(player_b, year)

    print(f"\nLoaded: {fp_a['player']} ({fp_a['n_matches']} matches) "
          f"vs {fp_b['player']} ({fp_b['n_matches']} matches) · {year}")

    # 2. Comparison model
    matchup: MatchupOutput = compare(fp_a, fp_b, year=year, surface=surface)

    print(f"\n── Comparison ─────────────────────────────────────────────────")
    print(f"  p_serve_A     = {matchup.p_serve_a:.4f}")
    print(f"  p_serve_B     = {matchup.p_serve_b:.4f}")
    print(f"  Axis 1 Serve  = {matchup.axis_serve_return:+.3f}")
    print(f"  Axis 2 Rally  = {matchup.axis_rally_shape:+.3f}")
    print(f"  Axis 3 Press  = {matchup.axis_pressure:+.3f}")
    print(f"  Axis 4 Durab  = {matchup.axis_durability:+.3f}")
    print(f"  Axis 5 Break  = {matchup.axis_break_pressure:+.3f}")
    print(f"  Weighted edge = {matchup.weighted_edge:+.4f}")
    print(f"  → {matchup.edge_narrative}")

    if matchup.trap_zone:
        print(f"  Trap zone: {matchup.trap_zone} (target rally length for A)")

    # 3. Analytical Markov baseline
    markov = TennisMarkov(matchup.p_serve_a, matchup.p_serve_b)
    markov_summary = markov.summary()

    print(f"\n── Markov Baseline (analytical, no state modifiers) ──────────")
    print(f"  P(A wins match) = {markov_summary['p_match_win_A']*100:.1f}%")
    print(f"  P(A holds)      = {markov_summary['p_hold_A']*100:.1f}%")
    print(f"  P(B holds)      = {markov_summary['p_hold_B']*100:.1f}%")
    for scoreline, p in sorted(markov_summary["score_dist"].items()):
        print(f"    {scoreline}: {p*100:.1f}%")

    # 4. Monte Carlo
    print(f"\n── Monte Carlo ({n_simulations:,} simulations) ──────────────────────────")
    print(f"  (State-dependent modifiers: SPCI={matchup.spci_a:+.4f}/{matchup.spci_b:+.4f}, "
          f"pressure={matchup.axis_pressure:+.3f}, "
          f"durability={matchup.axis_durability:+.3f})")

    simulation: SimulationResult = simulate_match(matchup, n=n_simulations, seed=seed)

    print(f"  P(A wins) = {simulation.p_win_a*100:.1f}%  "
          f"[analytical: {simulation.analytical_p_win_a*100:.1f}%]")
    print(f"  Expected sets = {simulation.expected_sets:.2f}")
    print(f"  Score distribution:")
    for scoreline, p in sorted(simulation.score_dist.items()):
        bar = "█" * int(p * 40)
        print(f"    {scoreline}  {bar}  {p*100:.1f}%")

    # 5. Verdicts
    verdicts_a = matchup.verdicts.get("A", {})
    verdicts_b = matchup.verdicts.get("B", {})
    if verdicts_a or verdicts_b:
        print(f"\n── Pressure Verdicts ──────────────────────────────────────────")
        for k, v in verdicts_a.items():
            print(f"  {fp_a['player']:>25} · {k}: {v}")
        for k, v in verdicts_b.items():
            print(f"  {fp_b['player']:>25} · {k}: {v}")

    # BP profiles (2D — not collapsed)
    if matchup.break_point_profile_a[0] is not None:
        cr_a, cvr_a = matchup.break_point_profile_a
        cr_b, cvr_b = matchup.break_point_profile_b
        print(f"\n── BP Creation Profile (creation_rate × conversion_rate) ──────")
        print(f"  {fp_a['player']}: ({cr_a}, {cvr_a})")
        print(f"  {fp_b['player']}: ({cr_b}, {cvr_b})")

    return {
        "matchup":    matchup,
        "simulation": simulation,
        "markov":     markov_summary,
        "narrative":  matchup.edge_narrative,
        "axes": {
            "serve_return":   matchup.axis_serve_return,
            "rally_shape":    matchup.axis_rally_shape,
            "pressure":       matchup.axis_pressure,
            "durability":     matchup.axis_durability,
            "break_pressure": matchup.axis_break_pressure,
            "weighted_edge":  matchup.weighted_edge,
        },
        "verdicts": {"A": verdicts_a, "B": verdicts_b},
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wimbledon match analysis — Phase 3 (Comparison + Monte Carlo)"
    )
    parser.add_argument("player_a", help="Player A name (partial match OK)")
    parser.add_argument("player_b", help="Player B name (partial match OK)")
    parser.add_argument("--year", type=int, default=2019,
                        help="Wimbledon year (default: 2019)")
    parser.add_argument("--surface", default="grass",
                        choices=["grass", "hard", "clay"],
                        help="Surface (affects axis weights)")
    parser.add_argument("--n", type=int, default=DEFAULT_N,
                        help=f"Monte Carlo simulations (default: {DEFAULT_N})")
    parser.add_argument("--seed", type=int, default=None,
                        help="RNG seed for reproducibility")
    parser.add_argument("--json", action="store_true",
                        help="Output full result as JSON")
    args = parser.parse_args()

    result = analyse_matchup(
        args.player_a, args.player_b,
        year=args.year, surface=args.surface,
        n_simulations=args.n, seed=args.seed,
    )

    if args.json:
        matchup = result["matchup"]
        sim     = result["simulation"]
        output  = {
            "matchup":   matchup.summary(),
            "simulation": sim.summary(),
            "markov":    result["markov"],
        }
        print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
