"""
Build era_stats.json — per-year tour means and standard deviations.

Used by the browser-side compare.js for z-scoring player metrics
against the field. Output goes to data/era_stats.json (served as
a frontend asset alongside the fingerprint files).

Usage:
    python src/build_era_stats.py
"""

from __future__ import annotations

import json
import numpy as np
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"

TIER1_METRICS = [
    "fsp_pct", "fspw_pct", "sspw_pct",
    "rpw_pct", "rpw_vs_1st_pct", "rpw_vs_2nd_pct",
    "sgw_pct", "rgw_pct",
]

TIER2_SCALAR = {
    "serve_entropy_pct": ("serve_entropy",        "pct_of_max"),
    "clutch_diff":       ("clutch_differential",  "value"),
    "df_pressure_delta": ("df_pressure_delta",    "value"),
    "bp_per_ret_game":   ("bp_creation_profile",  "bp_per_return_game"),
    "bp_conversion":     ("bp_creation_profile",  "bp_conversion"),
    "streak_init":       ("momentum_profile",      "streak_initiation_rate"),
    "streak_surv":       ("momentum_profile",      "streak_survival_rate"),
}


def _year_stats(fps: dict) -> dict:
    buckets: dict[str, list] = {}

    for fp in fps.values():
        # Tier 1
        t1 = fp.get("tier1", {})
        for m in TIER1_METRICS:
            v = t1.get(m, {})
            if isinstance(v, dict) and v.get("value") is not None:
                buckets.setdefault(m, []).append(v["value"])

        # Tier 2 scalars
        t2 = fp.get("tier2", {})
        for label, (key, sub) in TIER2_SCALAR.items():
            node = t2.get(key)
            if isinstance(node, dict):
                val = node.get(sub)
                if val is not None:
                    buckets.setdefault(label, []).append(val)

    result = {}
    for metric, vals in buckets.items():
        if len(vals) >= 5:
            result[metric] = {
                "mean": round(float(np.mean(vals)), 4),
                "sd":   round(max(float(np.std(vals)), 0.0001), 4),
                "n":    len(vals),
            }
    return result


def build(data_dir: Path = DATA_DIR) -> None:
    era_stats: dict = {}

    fp_files = sorted(data_dir.glob("*_fingerprints.json"))
    if not fp_files:
        print(f"No fingerprint files found in {data_dir}")
        return

    for fp_file in fp_files:
        year = int(fp_file.stem.split("_")[0])
        fps  = json.loads(fp_file.read_text())
        era_stats[str(year)] = _year_stats(fps)
        metrics = len(era_stats[str(year)])
        print(f"  {year}: {len(fps)} players, {metrics} metrics")

    out = data_dir / "era_stats.json"
    out.write_text(json.dumps(era_stats, indent=2))
    print(f"\nWritten → {out}  ({out.stat().st_size // 1024}KB)")


if __name__ == "__main__":
    build()
