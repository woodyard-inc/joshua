"""
archetype_means.py — Compute per-archetype mean Tier-2 modifier values.

Sprint 4b foundation: the engine currently zeroes a modifier when its
fingerprint-level confidence is UNRELIABLE.  This throws away style
information for sparse-data players.  Better: shrink the modifier value
toward the player's archetype-mean modifier value, weighted by confidence.

This builder scans all year fingerprints + archetype assignments, computes
the per-archetype weighted mean of each modifier value, and writes a
single JSON the engine loads at init.

Modifiers covered:
  spci.modifier_delta
  clutch_differential.modifier_delta
  tiebreak_differential.value
  hold_after_break.value
  attrition_slope.value
  serve_entropy.pct_of_max
  set_transition_delta.value   (currently in PRODUCTION_ABLATED but useful)
  first_serve_pressure.value   (currently in PRODUCTION_ABLATED but useful)

For each modifier × archetype combination we report the n_eff-weighted
mean across (player, year) pairs.  Players whose tier2 record is
unavailable or whose n_eff is zero contribute nothing.

Output: data/archetype_modifier_means.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018,
         2019, 2021, 2022, 2023, 2024]

# Modifier extraction recipes: (output_name, tier2_key, value_path, value_xform)
# value_path is a list of keys to traverse; value_xform converts to applied units.
# These match what extract_modifiers / simulate_point use downstream.
MODIFIER_SPECS = [
    # name                       tier2_key                     path                xform
    ("spci_modifier_delta",      "spci",                       ["modifier_delta"], lambda v: v),
    ("clutch_modifier_delta",    "clutch_differential",        ["modifier_delta"], lambda v: v),
    ("tiebreak_value",           "tiebreak_differential",      ["value"],          lambda v: v),
    ("hold_after_break_value",   "hold_after_break",           ["value"],          lambda v: v),
    ("attrition_value",          "attrition_slope",            ["value"],          lambda v: v),
    ("serve_entropy_pct",        "serve_entropy",              ["pct_of_max"],     lambda v: v),
    ("set_transition_value",     "set_transition_delta",       ["value"],          lambda v: v),
    ("first_serve_pressure",     "first_serve_pressure",       ["value"],          lambda v: v),
    ("df_pressure_delta",        "df_pressure_delta",          ["modifier_delta"], lambda v: v),
]


def _extract_value(t2: dict, key: str, path: List[str]):
    node = t2.get(key)
    if not isinstance(node, dict):
        return None
    if node.get("available") is False:
        return None
    for k in path:
        if not isinstance(node, dict):
            return None
        node = node.get(k)
    return node


def _archetype_for(name: str, archetypes: Dict[str, dict]):
    rec = archetypes.get(name) or {}
    return rec.get("id")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DATA_DIR / "archetype_modifier_means.json"))
    args = ap.parse_args()

    # Accumulators: result[mod_name][archetype_id] = (sum_w_value, sum_w)
    acc: Dict[str, Dict[int, List[float]]] = {
        spec[0]: {} for spec in MODIFIER_SPECS
    }

    n_observations = 0
    for year in YEARS:
        fps_path = DATA_DIR / f"{year}_fingerprints.json"
        arch_path = DATA_DIR / f"{year}_archetypes.json"
        if not fps_path.exists() or not arch_path.exists():
            continue
        fps = json.loads(fps_path.read_text())
        archetypes = json.loads(arch_path.read_text())

        for name, fp in fps.items():
            aid = _archetype_for(name, archetypes)
            if aid is None:
                continue
            t2 = fp.get("tier2") or {}
            n_eff = float(fp.get("n_eff_matches") or 0)
            if n_eff <= 0:
                continue
            n_observations += 1

            for mod_name, t2_key, path, xform in MODIFIER_SPECS:
                v = _extract_value(t2, t2_key, path)
                if v is None:
                    continue
                vx = xform(float(v))
                bucket = acc[mod_name].setdefault(aid, [0.0, 0.0])
                bucket[0] += vx * n_eff
                bucket[1] += n_eff

    # Compute weighted means
    out = {}
    for mod_name in acc:
        out[mod_name] = {}
        for aid, (sum_w_v, sum_w) in acc[mod_name].items():
            if sum_w > 0:
                out[mod_name][str(aid)] = round(sum_w_v / sum_w, 6)

    # Also include field-wide fallback (no archetype) for each modifier
    field_means = {}
    for mod_name in acc:
        sw_v = sum(b[0] for b in acc[mod_name].values())
        sw   = sum(b[1] for b in acc[mod_name].values())
        field_means[mod_name] = round(sw_v / sw, 6) if sw > 0 else 0.0

    payload = {
        "n_player_years": n_observations,
        "per_archetype":  out,
        "field_mean":     field_means,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2))

    print(f"Computed archetype-mean modifier values from {n_observations} player-years.\n")
    print(f"{'modifier':<28}  {'field_mean':>12}  per-archetype range")
    for mod_name in [s[0] for s in MODIFIER_SPECS]:
        vals = list(out[mod_name].values())
        rng = f"[{min(vals):+.4f}, {max(vals):+.4f}]" if vals else "—"
        print(f"  {mod_name:<28}  {field_means[mod_name]:+12.4f}  {rng}")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
