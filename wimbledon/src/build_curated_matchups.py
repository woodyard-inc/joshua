"""
Build curated_matchups.json — pre-computed engine output for famous matchups.

These populate the "Try these" featured section in the browser matchup panel.
Output goes to data/curated_matchups.json.

Usage:
    python src/build_curated_matchups.py
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from comparison import compare, load_fingerprint, DATA_DIR
from markov import TennisMarkov

# Famous matchups: (player_a, player_b, year, label)
CURATED = [
    ("Roger Federer",     "Novak Djokovic",      2019, "2019 Final"),
    ("Roger Federer",     "Rafael Nadal",         2019, "2019 Semi-Final"),
    ("Novak Djokovic",    "Roberto Bautista Agut",2019, "2019 Semi-Final"),
    ("Novak Djokovic",    "Kevin Anderson",       2018, "2018 Final"),
    ("Novak Djokovic",    "Rafael Nadal",         2018, "2018 Semi-Final"),
    ("Roger Federer",     "Marin Cilic",          2017, "2017 Final"),
    ("Novak Djokovic",    "Matteo Berrettini",    2021, "2021 Final"),
    ("Carlos Alcaraz",    "Novak Djokovic",       2023, "2023 Final"),
    ("Carlos Alcaraz",    "Novak Djokovic",       2024, "2024 Final"),
]


def build(data_dir: Path = DATA_DIR) -> None:
    results = []

    for player_a, player_b, year, label in CURATED:
        try:
            fp_a = load_fingerprint(player_a, year, data_dir)
            fp_b = load_fingerprint(player_b, year, data_dir)
        except FileNotFoundError as e:
            print(f"  SKIP {label}: {e}")
            continue

        if fp_a is None or fp_b is None:
            print(f"  SKIP {label}: player not found")
            continue

        try:
            out = compare(fp_a, fp_b, year=year, surface="grass")
        except Exception as e:
            print(f"  ERROR {label}: {e}")
            continue

        markov = TennisMarkov(out.p_serve_a, out.p_serve_b)
        dist   = markov.score_distribution()

        score_dist = {
            f"{sa}-{sb}": round(p, 4)
            for (sa, sb), p in sorted(dist.items())
        }

        results.append({
            "label":    label,
            "year":     year,
            "player_a": out.player_a,
            "player_b": out.player_b,
            "p_serve_a": round(out.p_serve_a, 4),
            "p_serve_b": round(out.p_serve_b, 4),
            "p_win_a":  round(markov.p_match(), 4),
            "axes": {
                "serve_return":   round(out.axis_serve_return,   3),
                "rally_shape":    round(out.axis_rally_shape,    3),
                "pressure":       round(out.axis_pressure,       3),
                "durability":     round(out.axis_durability,     3),
                "break_pressure": round(out.axis_break_pressure, 3),
            },
            "weighted_edge":  round(out.weighted_edge, 4),
            "edge_narrative": out.edge_narrative,
            "score_dist":     score_dist,
            "p_hold_a": round(markov.p_hold_a(), 4),
            "p_hold_b": round(markov.p_hold_b(), 4),
        })
        print(f"  ✓ {label}: {out.player_a} vs {out.player_b} — "
              f"P(A wins) = {markov.p_match()*100:.1f}%  edge={out.weighted_edge:+.3f}")

    out_file = data_dir / "curated_matchups.json"
    out_file.write_text(json.dumps(results, indent=2))
    print(f"\nWritten → {out_file}  ({len(results)} matchups)")


if __name__ == "__main__":
    build()
