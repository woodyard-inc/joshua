"""
form_noise_demo.py — Side-by-side demonstration of form-noise probability
calibration vs current Platt+clamp.

For each matchup:
  * Current engine: Platt sigmoid (A=0.35) + clamp to [0.20, 0.80].
                    Compresses raw MC probabilities aggressively.
  * Form-noise variant: per simulated match, sample each player's
                        fingerprint (fspw, sspw, rpw_vs_1st, rpw_vs_2nd)
                        from a Gaussian centered on their season values.
                        Sigma derived from empirical year-to-year
                        stability (with 75% weighting to filter true
                        skill drift from form variation).
                        No Platt, no clamp.

Result: form-noise can produce probabilities at the tails (90%+) for
blowouts that the Platt+clamp pipeline literally cannot output.
"""

from __future__ import annotations

import copy
import sys
import random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from comparison import load_all_fingerprints
import monte_carlo_phased as mcp

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

# Form-noise sigmas (pp) — derived from empirical year-to-year delta stdev
# scaled by 0.75 to separate form variation from skill drift.
SIGMA_FSPW       = 3.93 * 0.75
SIGMA_SSPW       = 5.03 * 0.75
SIGMA_RPW_VS_1ST = 3.81 * 0.75
SIGMA_RPW_VS_2ND = 4.96 * 0.75

N_SIMS = 2000


def _perturb_fp(fp: dict, rng: random.Random) -> dict:
    """Return a copy of fp with Tier 1 metrics perturbed by Gaussian noise.
    Clips each metric to a physically plausible range so a tail draw
    doesn't produce absurd values."""
    out = copy.deepcopy(fp)
    t1 = out.setdefault("tier1", {})

    def _shift(key, sigma, lo, hi):
        node = t1.get(key) or {}
        if "value" not in node:
            return
        v = node["value"] + rng.gauss(0.0, sigma)
        node["value"] = max(lo, min(hi, v))
        t1[key] = node

    _shift("fspw_pct",       SIGMA_FSPW,       40.0, 90.0)
    _shift("sspw_pct",       SIGMA_SSPW,       30.0, 75.0)
    _shift("rpw_vs_1st_pct", SIGMA_RPW_VS_1ST, 10.0, 45.0)
    _shift("rpw_vs_2nd_pct", SIGMA_RPW_VS_2ND, 25.0, 65.0)
    return out


def run_form_noise(fp_a: dict, fp_b: dict, n: int = N_SIMS,
                   seed: int = 42) -> dict:
    """Run MC with one fingerprint perturbation per simulated match.
    No Platt squash, no clamp.  Returns raw match win rate."""
    rng = random.Random(seed)
    sub = random.Random(seed + 1)
    wins_a = 0
    p_distribution = []
    for _ in range(n):
        fp_a_form = _perturb_fp(fp_a, rng)
        fp_b_form = _perturb_fp(fp_b, rng)
        # Run a single match-level simulation
        mods_a = mcp.extract_modifiers(fp_a_form)
        mods_b = mcp.extract_modifiers(fp_b_form)
        mods_a["_usePressure"] = False
        mods_b["_usePressure"] = False
        ab = mcp._matchup_rally_dists(mods_a, mods_b, fp_a_form, fp_b_form)
        ba = mcp._matchup_rally_dists(mods_b, mods_a, fp_b_form, fp_a_form)
        sA, sB = mcp._simulate_one_match(mods_a, mods_b, ab, ba, sub)
        p_distribution.append(1 if sA > sB else 0)
        if sA > sB:
            wins_a += 1
    return {
        "p_raw":  wins_a / n,
        "n_sims": n,
    }


def show_matchup(label: str, fps: dict, name_a: str, name_b: str,
                 year_pred: int):
    fp_a, fp_b = fps[name_a], fps[name_b]

    # Current engine: Platt + clamp
    cur = mcp.simulate_match_phased(fp_a, fp_b, n=N_SIMS, seed=42,
                                    current_year=year_pred)
    # Form-noise variant
    fn  = run_form_noise(fp_a, fp_b, n=N_SIMS, seed=42)

    print(f"\n  {label}")
    print(f"  {name_a} vs {name_b}  (FPs from {fp_a.get('year')})")
    print(f"    current engine:  p_raw={cur.p_win_a_raw:.3f}  "
          f"p_calibrated={cur.p_win_a:.3f}   "
          f"(clamped {'YES' if cur.p_win_a in (mcp.PROB_FLOOR, mcp.PROB_CEIL) else 'no'})")
    print(f"    form-noise:      p_raw={fn['p_raw']:.3f}  "
          f"(no Platt, no clamp)")
    diff_from_clamp_floor = abs(cur.p_win_a - mcp.PROB_FLOOR)
    diff_from_clamp_ceil  = abs(cur.p_win_a - mcp.PROB_CEIL)
    if min(diff_from_clamp_floor, diff_from_clamp_ceil) < 0.005:
        print(f"    ↳ Platt+clamp output is AT the boundary; form-noise reveals true edge.")


def main():
    # Load 2023 fingerprints with grass merge — what would predict 2024.
    fps = load_all_fingerprints(2023, data_dir=DATA, merge_grass=True)
    fps_2022 = load_all_fingerprints(2022, data_dir=DATA, merge_grass=True)

    print("FORM-NOISE DEMO — current engine (Platt+clamp) vs form-noise variant")
    print(f"Sigma calibration (empirical, 75% of year-to-year delta stdev):")
    print(f"  fspw={SIGMA_FSPW:.2f}pp  sspw={SIGMA_SSPW:.2f}pp  "
          f"rpw_v1={SIGMA_RPW_VS_1ST:.2f}pp  rpw_v2={SIGMA_RPW_VS_2ND:.2f}pp")
    print(f"  PROB clamp range: [{mcp.PROB_FLOOR}, {mcp.PROB_CEIL}]")
    print(f"  N sims: {N_SIMS}")
    print("=" * 72)

    # 1. BIG MISMATCH: dominant top player vs a journeyman
    show_matchup(
        "1. BLOWOUT — top seed vs lower-ranked opponent",
        fps, "Carlos Alcaraz", "Yannick Hanfmann", year_pred=2024)

    # 2. TOP-VS-TOP — two world-class players
    show_matchup(
        "2. TOP-VS-TOP — two world #1s on grass",
        fps, "Carlos Alcaraz", "Novak Djokovic", year_pred=2024)

    # 3. MID-PACK — both established top-30 ATP players, real edge
    show_matchup(
        "3. MID-FAVORITE — established veteran vs younger rival",
        fps, "Daniil Medvedev", "Hubert Hurkacz", year_pred=2024)

    # 4. COIN FLIP — two similar mid-pack players
    show_matchup(
        "4. COIN FLIP — similar-quality mid-pack opponents",
        fps_2022, "Roberto Bautista Agut", "Jordan Thompson", year_pred=2023)


if __name__ == "__main__":
    main()
