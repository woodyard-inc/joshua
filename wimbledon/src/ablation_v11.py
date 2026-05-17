"""
ablation_v11.py — Re-validate the PRODUCTION_ABLATED set with bootstrap CIs,
under the v11 production stack (pressure_states + tiebreak baselines).

For each currently-disabled modifier M, run a backtest with M re-enabled
(PRODUCTION_ABLATED - {M}) under the v11 stack and save per-match output.
Then compute bootstrap CI on Brier delta vs the v11 baseline.

Some modifiers may have been dropped pre-v11 on noise (CI overlaps 0) or
may now be neutral under v11 (state-conditional baselines absorbed their
signal).  This separates real harm from sample noise.

Usage:
    python3 src/ablation_v11.py --n_sims 1000
"""

from __future__ import annotations

import argparse
import json
import math
import random
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

import monte_carlo_phased as mcp   # noqa: E402

BOOTSTRAP_DRAWS = 2000


def _run_backtest(per_match_path: Path, ablate_set: set, n_sims: int) -> None:
    """Invoke backtest_fingerprints.py with the given ablation set."""
    args = [
        "python3", "src/backtest_fingerprints.py",
        "--engine", "phased",
        "--n_sims", str(n_sims),
        "--save_per_match", str(per_match_path),
        "--out", "/tmp/_ablation_tmp_metrics.json",
    ]
    if ablate_set:
        args += ["--ablate", ",".join(sorted(ablate_set))]
    print(f"  cmd: {' '.join(args)}")
    t0 = time.time()
    subprocess.run(args, cwd=ROOT, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"  done in {time.time() - t0:.1f}s -> {per_match_path.name}")


def _bootstrap_ci(deltas: List[float], n_draws: int = BOOTSTRAP_DRAWS,
                  alpha: float = 0.05) -> Dict[str, float]:
    """Percentile bootstrap CI on the mean of `deltas`."""
    n = len(deltas)
    rng = random.Random(42)
    means = []
    for _ in range(n_draws):
        s = sum(deltas[rng.randrange(n)] for _ in range(n))
        means.append(s / n)
    means.sort()
    lo = means[int(n_draws * alpha / 2)]
    hi = means[int(n_draws * (1 - alpha / 2))]
    mu = sum(deltas) / n
    return {"mean": mu, "ci_lo": lo, "ci_hi": hi}


def _load_per_match(path: Path) -> Dict[str, dict]:
    """Returns dict keyed by match_id."""
    rows = json.loads(path.read_text())
    return {r["match_id"]: r for r in rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n_sims", type=int, default=1000)
    ap.add_argument("--out", default=str(ROOT / "data" / "ablation_v11.json"))
    ap.add_argument("--scratch", default="/tmp/ablation_v11",
                    help="Directory to hold per-match dumps.")
    ap.add_argument("--skip_runs", action="store_true",
                    help="Skip backtests, just compute CIs from existing dumps.")
    args = ap.parse_args()

    scratch = Path(args.scratch)
    scratch.mkdir(parents=True, exist_ok=True)

    targets = sorted(mcp.PRODUCTION_ABLATED)
    print(f"v11 ablation re-validation: {len(targets)} modifiers, n_sims={args.n_sims}\n")

    baseline_path = scratch / "baseline.json"
    if not args.skip_runs:
        print("─── Running v11 baseline (PRODUCTION_ABLATED active) ───")
        _run_backtest(baseline_path, mcp.PRODUCTION_ABLATED, args.n_sims)
        print()

    for mod in targets:
        path = scratch / f"reenable_{mod}.json"
        if not args.skip_runs:
            print(f"─── Re-enabling: {mod} ───")
            # All others ablated, this one ON
            _run_backtest(path, mcp.PRODUCTION_ABLATED - {mod}, args.n_sims)
            print()

    # ── Bootstrap CI analysis ────────────────────────────────────────────
    base = _load_per_match(baseline_path)
    results = {}
    print("\nBootstrap-CI analysis  (positive Δbrier = re-enabling HURTS)")
    print("=" * 80)
    print(f"{'modifier':<22}  {'n':>4}  {'Δbrier':>10}  {'95% CI':>22}  verdict")
    print("─" * 80)

    summary_rows = []
    for mod in targets:
        var = _load_per_match(scratch / f"reenable_{mod}.json")
        common = sorted(set(base) & set(var))
        deltas = [var[mid]["brier"] - base[mid]["brier"] for mid in common]
        ci = _bootstrap_ci(deltas)
        if ci["ci_hi"] < 0:
            verdict = "RE-ENABLE (Δ<0)"
        elif ci["ci_lo"] > 0:
            verdict = "keep disabled"
        else:
            verdict = "NOISE - drop was unjustified"
        results[mod] = {
            "n_matches":      len(deltas),
            "mean_delta_brier": ci["mean"],
            "ci_95_lo":       ci["ci_lo"],
            "ci_95_hi":       ci["ci_hi"],
            "verdict":        verdict,
        }
        ci_str = f"[{ci['ci_lo']:+.4f}, {ci['ci_hi']:+.4f}]"
        print(f"{mod:<22}  {len(deltas):>4}  {ci['mean']:>+10.5f}  {ci_str:>22}  {verdict}")
        summary_rows.append((mod, ci))

    Path(args.out).write_text(json.dumps({
        "method": "bootstrap CI on per-match Brier delta vs v11 baseline",
        "n_bootstrap_draws": BOOTSTRAP_DRAWS,
        "n_sims_per_backtest": args.n_sims,
        "results": results,
    }, indent=2, default=lambda v: round(v, 6) if isinstance(v, float) else v))
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
