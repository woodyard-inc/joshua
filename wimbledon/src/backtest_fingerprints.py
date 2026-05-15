"""
Fingerprint MC backtest.

For each available test year, loads the PRIOR edition's fingerprints
(no data leakage) and runs the Monte Carlo simulation for every men's
singles match where both players have a fingerprint.

Metrics reported:
  accuracy   — % of matches where predicted winner (p_win > 0.5) was correct
  brier      — mean squared error vs actual outcome (0.25 = naive baseline)
  coverage   — % of draw matches where both players had prior fingerprints
  log_loss   — binary cross-entropy

Usage:
    python src/backtest_fingerprints.py
    python src/backtest_fingerprints.py --n_sims 2000 --out data/model_metrics.json
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

# ── path setup ────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from build_player_profiles import load_year, men_match_ids
from comparison import compare, load_all_fingerprints, DATA_DIR as COMP_DATA_DIR
from monte_carlo import simulate_match
from monte_carlo_phased import simulate_match_phased

# ── constants ─────────────────────────────────────────────────────────────────
SUPPORTED_YEARS = [2011, 2012, 2013, 2014, 2015, 2016,
                   2017, 2018, 2019, 2021, 2022, 2023, 2024]

# First year with full 128-player draws + reasonable PBP coverage
MIN_TEST_YEAR   = 2014
N_SIMS_DEFAULT  = 1000   # fast but still stable estimates

EPSILON = 1e-9  # for log-loss clipping


# ── helpers ───────────────────────────────────────────────────────────────────

def prior_edition(year: int) -> Optional[int]:
    """Return the most recent Wimbledon edition before `year`, or None."""
    past = [y for y in SUPPORTED_YEARS if y < year]
    return past[-1] if past else None


def load_fp_file(year: int, merge_grass: bool = True,
                 attach_pressure: bool = False,
                 attach_momentum: bool = False) -> Dict[str, dict]:
    """Load all fingerprints for a given year, merged with grass profiles.

    If attach_pressure=True, also load {year}_pressure_states.json and attach
    state-conditional baselines to fp["pressure_states"] for the engine to
    use under USE_PRESSURE_STATES + reliability gate.

    If attach_momentum=True, also load {year}_momentum_hmm.json and attach
    within-game momentum (hot/neutral) baselines to fp["momentum_hmm"].
    """
    data_dir = ROOT / "data"
    fps = load_all_fingerprints(year, data_dir=data_dir, merge_grass=merge_grass)
    if attach_pressure:
        ps_path = data_dir / f"{year}_pressure_states.json"
        if ps_path.exists():
            states = json.loads(ps_path.read_text())
            attached = 0
            for player, fp in fps.items():
                ps = states.get(player)
                if ps is not None:
                    fp["pressure_states"] = ps
                    attached += 1
            print(f"  [{year}] attached pressure states for {attached}/{len(fps)} players")
        else:
            print(f"  [{year}] WARNING: --use_pressure_states set but {ps_path.name} not found")
    if attach_momentum:
        mh_path = data_dir / f"{year}_momentum_hmm.json"
        if mh_path.exists():
            mh = json.loads(mh_path.read_text())
            attached = 0
            for player, fp in fps.items():
                rec = mh.get(player)
                if rec is not None:
                    fp["momentum_hmm"] = rec
                    attached += 1
            print(f"  [{year}] attached momentum HMM for {attached}/{len(fps)} players")
        else:
            print(f"  [{year}] WARNING: --use_momentum_hmm set but {mh_path.name} not found")
    return fps


def match_winner_from_points(pts: pd.DataFrame, match_id: str) -> Optional[int]:
    """
    Derive match winner (1 or 2) from the last point's PointWinner column.
    Returns None if no points found.
    """
    mpts = pts[pts["match_id"] == match_id]
    if mpts.empty:
        return None
    last = mpts.iloc[-1]
    pw = last.get("PointWinner")
    try:
        return int(pw)
    except (TypeError, ValueError):
        return None


def safe_log(p: float) -> float:
    return math.log(max(p, EPSILON))


# ── per-year evaluation ───────────────────────────────────────────────────────

def evaluate_year(year: int, prior_fps: Dict[str, dict],
                  n_sims: int = N_SIMS_DEFAULT,
                  engine: str = "aggregate") -> List[dict]:
    """
    Run MC predictions for every men's singles match in `year`
    using fingerprints from the prior edition.
    Returns a list of result dicts (one per match attempted).
    """
    try:
        pts, mat = load_year(year)
    except FileNotFoundError:
        print(f"  [{year}] No data — skipping.")
        return []

    men_ids = men_match_ids(mat)
    men_rows = mat[mat["match_id"].isin(men_ids)]

    results = []
    skipped_no_fp = 0
    skipped_no_pts = 0

    for _, row in men_rows.iterrows():
        mid   = row["match_id"]
        p1    = str(row["player1"])
        p2    = str(row["player2"])

        # Derive actual winner from points data
        winner = match_winner_from_points(pts, mid)
        if winner is None:
            skipped_no_pts += 1
            continue

        # Look up both players in the PRIOR year's fingerprints
        fp1 = prior_fps.get(p1)
        fp2 = prior_fps.get(p2)
        if fp1 is None or fp2 is None:
            skipped_no_fp += 1
            continue

        # Run MC
        try:
            if engine == "phased":
                sim = simulate_match_phased(fp1, fp2, n=n_sims, seed=42,
                                            current_year=year)
                p_win_1_raw = sim.p_win_a_raw
            else:
                matchup = compare(fp1, fp2, year=year)
                sim     = simulate_match(matchup, n=n_sims, seed=42)
                p_win_1_raw = None
            p_win_1 = sim.p_win_a   # p(player1 wins)
        except Exception as exc:
            print(f"  [{year}] Error on {p1} vs {p2}: {exc}")
            continue

        actual_1_wins = 1 if winner == 1 else 0
        correct       = (p_win_1 > 0.5) == (actual_1_wins == 1)
        brier         = (p_win_1 - actual_1_wins) ** 2
        log_l         = -(actual_1_wins * safe_log(p_win_1) +
                          (1 - actual_1_wins) * safe_log(1 - p_win_1))

        results.append({
            "year":        year,
            "match_id":    mid,
            "player1":     p1,
            "player2":     p2,
            "p_win_1":     round(p_win_1, 4),
            "p_win_1_raw": round(p_win_1_raw, 4) if p_win_1_raw is not None else None,
            "actual_1_wins": actual_1_wins,
            "correct":     correct,
            "brier":       round(brier, 6),
            "log_loss":    round(log_l, 6),
        })

    rnd_label = f"  [{year}]"
    total_matches = len(men_rows)
    print(f"{rnd_label}  {len(results):3d}/{total_matches} matches evaluated  "
          f"(no_fp={skipped_no_fp}, no_pts={skipped_no_pts})")
    return results


# ── calibration ───────────────────────────────────────────────────────────────

def calibration_table(results: List[dict], n_bins: int = 5) -> List[dict]:
    """Bin predictions into quantile buckets and compare to actual rates."""
    if not results:
        return []

    preds   = [r["p_win_1"]      for r in results]
    actuals = [r["actual_1_wins"] for r in results]

    # Sort by predicted probability
    paired = sorted(zip(preds, actuals), key=lambda x: x[0])
    bin_size = max(1, len(paired) // n_bins)

    table = []
    for i in range(0, len(paired), bin_size):
        chunk = paired[i: i + bin_size]
        if not chunk:
            continue
        ps  = [c[0] for c in chunk]
        acs = [c[1] for c in chunk]
        table.append({
            "pred_range":  f"{min(ps):.2f}–{max(ps):.2f}",
            "pred_mean":   round(sum(ps) / len(ps), 3),
            "actual_rate": round(sum(acs) / len(acs), 3),
            "n":           len(chunk),
        })
    return table


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="MC fingerprint backtest")
    parser.add_argument("--n_sims", type=int, default=N_SIMS_DEFAULT,
                        help=f"Simulations per match (default {N_SIMS_DEFAULT})")
    parser.add_argument("--out", default=str(ROOT / "data" / "model_metrics.json"),
                        help="Output JSON path to update")
    parser.add_argument("--engine", choices=["aggregate", "phased"], default="aggregate",
                        help="MC engine: 'aggregate' (Markov-style) or 'phased' (per-phase point sim)")
    parser.add_argument("--save_per_match", default=None,
                        help="If set, write per-match results JSON to this path "
                             "(includes p_win_1_raw for offline PLATT sweeping)")
    parser.add_argument("--ablate", default=None,
                        help="(phased only) Comma-separated modifier names to disable, e.g. "
                             "'momentum,courtSide,setTransition'. See monte_carlo_phased.ABLATABLE.")
    parser.add_argument("--use_pressure_states", action="store_true",
                        help="(phased only) Use per-state baselines from {year}_pressure_states.json. "
                             "Per-match reliability gate decides which matches actually use them.")
    parser.add_argument("--pressure_gate_mode",
                        choices=["any", "both", "stale_only", "stale_or_gap"],
                        default="stale_only",
                        help="(phased only) Which matches fire the pressure-state gate. "
                             "any=either player sparse/stale, both=both must be, "
                             "stale_only=only fire on year-staleness >1 (COVID-style).")
    parser.add_argument("--use_momentum_hmm", action="store_true",
                        help="(phased only) Apply per-player within-game momentum delta from "
                             "{year}_momentum_hmm.json (Klaassen-Magnus 2014 backed). "
                             "Resets at game boundary; +/-MOMENTUM_MAX_DELTA_PCT cap.")
    args = parser.parse_args()

    # Apply ablation flags before importing happens (they're read at runtime)
    if args.ablate:
        import monte_carlo_phased as mcp
        mcp.set_ablation(set(args.ablate.split(",")))

    if args.use_pressure_states:
        import monte_carlo_phased as mcp
        mcp.set_use_pressure_states(True)
        mcp.set_pressure_gate_mode(args.pressure_gate_mode)

    if args.use_momentum_hmm:
        import monte_carlo_phased as mcp
        mcp.set_use_momentum_hmm(True)

    test_years = [y for y in SUPPORTED_YEARS if y >= MIN_TEST_YEAR]
    all_results: List[dict] = []

    print(f"\nMC Fingerprint Backtest  (n_sims={args.n_sims}, engine={args.engine})")
    print(f"Strategy: prior-edition fingerprints → predict each match")
    print("=" * 60)

    per_year_stats = {}

    for year in test_years:
        prior = prior_edition(year)
        if prior is None:
            print(f"  [{year}] No prior edition — skipping.")
            continue

        prior_fps = load_fp_file(prior,
                                 attach_pressure=args.use_pressure_states,
                                 attach_momentum=args.use_momentum_hmm)
        if not prior_fps:
            print(f"  [{year}] No fingerprints for prior edition {prior} — skipping.")
            continue

        print(f"\n  Predicting {year}  ←  {prior} fingerprints "
              f"({len(prior_fps)} players available)")

        year_results = evaluate_year(year, prior_fps, n_sims=args.n_sims, engine=args.engine)
        all_results.extend(year_results)

        if year_results:
            acc   = sum(r["correct"] for r in year_results) / len(year_results)
            brier = sum(r["brier"]   for r in year_results) / len(year_results)
            ll    = sum(r["log_loss"]for r in year_results) / len(year_results)
            per_year_stats[str(year)] = {
                "n_matches":  len(year_results),
                "accuracy":   round(acc, 3),
                "brier":      round(brier, 3),
                "log_loss":   round(ll, 3),
            }
            print(f"         accuracy={acc:.1%}  brier={brier:.3f}  "
                  f"log_loss={ll:.3f}  n={len(year_results)}")

    # ── overall metrics ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    if not all_results:
        print("No results. Check that fingerprint files exist.")
        return

    n_total   = len(all_results)
    accuracy  = sum(r["correct"] for r in all_results) / n_total
    brier     = sum(r["brier"]   for r in all_results) / n_total
    log_loss  = sum(r["log_loss"]for r in all_results) / n_total
    naive_brier = 0.25   # always predicting 0.5
    brier_skill = round(1 - brier / naive_brier, 3)  # Brier Skill Score

    print(f"\nOverall  (n={n_total} matches, years {test_years[0]}–{test_years[-1]})")
    print(f"  Accuracy   : {accuracy:.1%}")
    print(f"  Brier score: {brier:.4f}  (naive=0.25, skill={brier_skill:+.3f})")
    print(f"  Log loss   : {log_loss:.4f}")

    cal = calibration_table(all_results)
    print("\nCalibration (predicted → actual win rate):")
    for row in cal:
        bar = "█" * round(row["actual_rate"] * 20)
        print(f"  {row['pred_range']:>12}  pred={row['pred_mean']:.2f}  "
              f"actual={row['actual_rate']:.2f}  n={row['n']:4d}  {bar}")

    # ── update model_metrics.json ─────────────────────────────────────────────
    out_path = Path(args.out)
    existing = {}
    if out_path.exists():
        try:
            existing = json.loads(out_path.read_text())
        except Exception:
            pass

    existing["mc_fingerprint_backtest"] = {
        "method":        "Prior-edition fingerprints → MC simulation → actual outcomes",
        "leakage_note":  "Fingerprints built from editions BEFORE the predicted year. "
                         "Career window (last 3 editions) never includes the test year.",
        "n_sims":        args.n_sims,
        "test_years":    [y for y in test_years if str(y) in per_year_stats],
        "min_test_year": MIN_TEST_YEAR,
        "n_total":       n_total,
        "accuracy":      round(accuracy, 3),
        "brier":         round(brier, 4),
        "brier_skill":   brier_skill,
        "log_loss":      round(log_loss, 4),
        "naive_brier":   naive_brier,
        "per_year":      per_year_stats,
        "calibration":   cal,
        "notes": (
            "Coverage ~50–70% per year — first-time Wimbledon players have no prior "
            "fingerprint and are excluded. These tend to be lower-seeded players, so "
            "excluded matches are systematically easier to predict, meaning true "
            "accuracy on uncovered matches may be lower."
        ),
    }

    out_path.write_text(json.dumps(existing, indent=2))
    print(f"\nResults written → {out_path}")

    if args.save_per_match:
        from pathlib import Path as _P
        per_match_path = _P(args.save_per_match)
        per_match_path.write_text(json.dumps(all_results, indent=2))
        print(f"Per-match results written → {per_match_path}")


if __name__ == "__main__":
    main()
