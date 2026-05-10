"""
platt_sweep.py — Offline calibration sweep.

Reads per-match results JSON (with `p_win_1_raw` saved), sweeps PLATT_A
across a grid, and reports accuracy / Brier / log-loss for each value.

Usage:
    python src/platt_sweep.py /tmp/phased_per_match.json
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path


EPSILON = 1e-9


def platt(p: float, a: float) -> float:
    """Apply Platt calibration with input clamping (matches v4 production behaviour).

    Pre-v4: bypassed calibration entirely when p_raw was 0/1 — letting
    extreme MC predictions through and exploding log-loss on misses.
    Post-v4: input is clamped to [1e-6, 1-1e-6] so the sigmoid always runs.
    """
    p = max(1e-6, min(1 - 1e-6, p))
    logit = math.log(p / (1 - p))
    return 1.0 / (1.0 + math.exp(-a * logit))


def evaluate(results, a):
    correct = brier = log_loss = 0
    n = 0
    for r in results:
        raw = r.get("p_win_1_raw")
        if raw is None:
            continue
        cal = platt(raw, a)
        actual = r["actual_1_wins"]
        correct += int((cal > 0.5) == (actual == 1))
        brier   += (cal - actual) ** 2
        log_loss += -(actual * math.log(max(cal, EPSILON)) +
                      (1 - actual) * math.log(max(1 - cal, EPSILON)))
        n += 1
    return {
        "accuracy": correct / n,
        "brier":    brier / n,
        "log_loss": log_loss / n,
        "n":        n,
    }


def main():
    if len(sys.argv) < 2:
        print("Usage: python platt_sweep.py <per_match_results.json>")
        sys.exit(1)

    path = Path(sys.argv[1])
    results = json.loads(path.read_text())
    with_raw = [r for r in results if r.get("p_win_1_raw") is not None]
    print(f"Loaded {len(results)} matches ({len(with_raw)} with raw probabilities)")
    print()

    # Coarse sweep
    grid = [0.20, 0.25, 0.30, 0.35, 0.40, 0.42, 0.45, 0.50,
            0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90, 1.00]

    print(f"{'A':>6}  {'accuracy':>10}  {'brier':>10}  {'log_loss':>10}")
    print("─" * 44)
    best_brier = best_loss = None
    for a in grid:
        m = evaluate(with_raw, a)
        if best_brier is None or m["brier"] < best_brier[1]["brier"]:
            best_brier = (a, m)
        if best_loss is None or m["log_loss"] < best_loss[1]["log_loss"]:
            best_loss = (a, m)
        print(f"{a:>6.2f}  {m['accuracy']*100:>9.2f}%  {m['brier']:>10.4f}  {m['log_loss']:>10.4f}")

    print()
    print(f"Best by Brier   : A={best_brier[0]:.2f}  →  "
          f"acc={best_brier[1]['accuracy']*100:.2f}%  "
          f"brier={best_brier[1]['brier']:.4f}  "
          f"log_loss={best_brier[1]['log_loss']:.4f}")
    print(f"Best by log-loss: A={best_loss[0]:.2f}  →  "
          f"acc={best_loss[1]['accuracy']*100:.2f}%  "
          f"brier={best_loss[1]['brier']:.4f}  "
          f"log_loss={best_loss[1]['log_loss']:.4f}")
    # Use log-loss as the primary metric for the fine sweep — it's much more
    # sensitive to the tail-calibration issue we're trying to fix.
    best = best_loss

    # Fine sweep around the best (by log-loss)
    a0 = best[0]
    fine = [round(a0 - 0.10 + i * 0.02, 3) for i in range(11)]
    print()
    print(f"Fine sweep around log-loss optimum A={a0:.2f}:")
    print(f"{'A':>6}  {'accuracy':>10}  {'brier':>10}  {'log_loss':>10}")
    print("─" * 44)
    fine_best = None
    for a in fine:
        if a <= 0:
            continue
        m = evaluate(with_raw, a)
        if fine_best is None or m["log_loss"] < fine_best[1]["log_loss"]:
            fine_best = (a, m)
        print(f"{a:>6.2f}  {m['accuracy']*100:>9.2f}%  {m['brier']:>10.4f}  {m['log_loss']:>10.4f}")

    print()
    print(f"Optimal PLATT_A: {fine_best[0]:.3f}")
    print(f"  accuracy = {fine_best[1]['accuracy']*100:.2f}%")
    print(f"  brier    = {fine_best[1]['brier']:.4f}")
    print(f"  log_loss = {fine_best[1]['log_loss']:.4f}")

    # Also report current production setting (A=0.42)
    cur = evaluate(with_raw, 0.42)
    print()
    print(f"Current production (A=0.42):")
    print(f"  accuracy = {cur['accuracy']*100:.2f}%")
    print(f"  brier    = {cur['brier']:.4f}")
    print(f"  log_loss = {cur['log_loss']:.4f}")

    delta_brier = cur["brier"] - fine_best[1]["brier"]
    delta_acc   = (fine_best[1]["accuracy"] - cur["accuracy"]) * 100
    print()
    print(f"Improvement from re-fit: brier {delta_brier:+.4f}, accuracy {delta_acc:+.2f}pp")


if __name__ == "__main__":
    main()
