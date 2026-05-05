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
    if p <= EPSILON or p >= 1 - EPSILON:
        return p
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
    best = None
    for a in grid:
        m = evaluate(with_raw, a)
        marker = ""
        if best is None or m["brier"] < best[1]["brier"]:
            best = (a, m)
        print(f"{a:>6.2f}  {m['accuracy']*100:>9.2f}%  {m['brier']:>10.4f}  {m['log_loss']:>10.4f}{marker}")

    print()
    print(f"Best by Brier: A={best[0]:.2f}  →  "
          f"acc={best[1]['accuracy']*100:.2f}%  "
          f"brier={best[1]['brier']:.4f}  "
          f"log_loss={best[1]['log_loss']:.4f}")

    # Fine sweep around the best
    a0 = best[0]
    fine = [round(a0 - 0.10 + i * 0.02, 3) for i in range(11)]
    print()
    print(f"Fine sweep around A={a0:.2f}:")
    print(f"{'A':>6}  {'accuracy':>10}  {'brier':>10}  {'log_loss':>10}")
    print("─" * 44)
    fine_best = None
    for a in fine:
        if a <= 0:
            continue
        m = evaluate(with_raw, a)
        if fine_best is None or m["brier"] < fine_best[1]["brier"]:
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
