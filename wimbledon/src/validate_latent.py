"""
validate_latent.py — Sanity A/B for the latent-factor baseline.

The full Monte Carlo backtest (backtest_fingerprints.py) needs the raw
Sackmann PBP CSVs in data/raw/, which are gitignored.  This script runs
a leakage-free A/B that does NOT need raw PBP: matches are reconstructed
from {year}_men_profiles.json `match_summaries` (which IS in the repo)
and predicted analytically with the Markov engine using a per-point
serve probability derived from prior-year fingerprints.

Two configurations are evaluated on the same match set:

  baseline  — fspw, sspw, rpw_vs_1st, rpw_vs_2nd taken raw from the
              prior-edition fingerprint (with grass-profile blend)
  latent    — same metrics replaced with the two-factor smoothed values
              from {prior_year}_latent_factors.json

The analytical Markov engine collapses per-point p into match win prob,
matching the deterministic core of the MC engine without simulation noise.
The A/B answers: do the smoothed metrics produce a more accurate per-point
baseline than the raw ones?

Usage:
    python src/validate_latent.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from comparison import load_all_fingerprints
from markov import p_match_win

YEARS = [2014, 2015, 2016, 2017, 2018, 2019, 2021, 2022, 2023, 2024]
SUPPORTED_PRIOR = {
    2014: 2013, 2015: 2014, 2016: 2015, 2017: 2016, 2018: 2017,
    2019: 2018, 2021: 2019, 2022: 2021, 2023: 2022, 2024: 2023,
}

GRASS_RPW_VS_1ST_AVG = 25.0
GRASS_RPW_VS_2ND_AVG = 40.0
GRASS_AVG_FSPW       = 72.0
GRASS_AVG_SSPW       = 56.0
GRASS_AVG_FSP        = 63.0


def _t1(fp: dict, key: str, default: float) -> float:
    node = (fp.get("tier1") or {}).get(key) or {}
    if isinstance(node, dict) and node.get("value") is not None:
        return float(node["value"])
    return default


def per_point_prob(fp_srv: dict, fp_ret: dict, latent: bool) -> float:
    """
    Returns p(server wins a generic point) using the Phase-2 baseline of
    monte_carlo_phased.simulate_point but blended across 1st/2nd serve mix.
    No Phase-3 modifiers.  No Platt.  Pure baseline comparison.
    """
    fsp = _t1(fp_srv, "fsp_pct", GRASS_AVG_FSP) / 100

    if latent and fp_srv.get("latent_factors"):
        lf = fp_srv["latent_factors"]
        fspw = lf.get("smoothed_fspw_pct", _t1(fp_srv, "fspw_pct", GRASS_AVG_FSPW))
        sspw = lf.get("smoothed_sspw_pct", _t1(fp_srv, "sspw_pct", GRASS_AVG_SSPW))
    else:
        fspw = _t1(fp_srv, "fspw_pct", GRASS_AVG_FSPW)
        sspw = _t1(fp_srv, "sspw_pct", GRASS_AVG_SSPW)

    if latent and fp_ret.get("latent_factors"):
        lf = fp_ret["latent_factors"]
        rpw_v1 = lf.get("smoothed_rpw_vs_1st_pct", _t1(fp_ret, "rpw_vs_1st_pct", GRASS_RPW_VS_1ST_AVG))
        rpw_v2 = lf.get("smoothed_rpw_vs_2nd_pct", _t1(fp_ret, "rpw_vs_2nd_pct", GRASS_RPW_VS_2ND_AVG))
    else:
        rpw_v1 = _t1(fp_ret, "rpw_vs_1st_pct", GRASS_RPW_VS_1ST_AVG)
        rpw_v2 = _t1(fp_ret, "rpw_vs_2nd_pct", GRASS_RPW_VS_2ND_AVG)

    p1 = fspw / 100 - (rpw_v1 - GRASS_RPW_VS_1ST_AVG) / 100
    p2 = sspw / 100 - (rpw_v2 - GRASS_RPW_VS_2ND_AVG) / 100
    p  = fsp * p1 + (1 - fsp) * p2
    return max(0.05, min(0.95, p))


def attach_latent(fps: Dict[str, dict], year: int) -> Dict[str, dict]:
    lf_path = ROOT / "data" / f"{year}_latent_factors.json"
    if not lf_path.exists():
        return fps
    latents = json.loads(lf_path.read_text())
    for player, fp in fps.items():
        if player in latents:
            fp["latent_factors"] = latents[player]
    return fps


def reconstruct_matches(year: int) -> List[dict]:
    """
    Walk every player's match_summaries; pair them by match_id.
    The winner is the player with the higher points_won total.
    Returns list of {match_id, p1, p2, p1_won}.
    """
    path = ROOT / "data" / f"{year}_men_profiles.json"
    if not path.exists():
        return []
    profiles = json.loads(path.read_text())

    by_match: Dict[str, List[Tuple[str, int, int, str]]] = {}
    for player, prof in profiles.items():
        for m in prof.get("match_summaries", []) or []:
            by_match.setdefault(m["match_id"], []).append(
                (player, int(m["points_won"]), int(m["points_played"]), m.get("round", ""))
            )

    matches = []
    for mid, parts in by_match.items():
        if len(parts) != 2:
            continue
        a, b = parts
        if a[1] == b[1]:
            continue
        winner = a[0] if a[1] > b[1] else b[0]
        matches.append({
            "match_id": mid,
            "year":     year,
            "p1":       a[0],
            "p2":       b[0],
            "p1_won":   1 if winner == a[0] else 0,
            "round":    a[3],
        })
    return matches


def evaluate(year: int, latent: bool) -> Tuple[List[dict], int, int]:
    prior = SUPPORTED_PRIOR.get(year)
    if prior is None:
        return [], 0, 0
    fps = load_all_fingerprints(prior, data_dir=ROOT / "data", merge_grass=True)
    if latent:
        fps = attach_latent(fps, prior)

    matches = reconstruct_matches(year)
    out, no_fp = [], 0
    for m in matches:
        fp1, fp2 = fps.get(m["p1"]), fps.get(m["p2"])
        if fp1 is None or fp2 is None:
            no_fp += 1
            continue
        p_serve_a = per_point_prob(fp1, fp2, latent=latent)
        p_serve_b = per_point_prob(fp2, fp1, latent=latent)
        p_a_match = p_match_win(p_serve_a, p_serve_b, best_of=5)

        actual = m["p1_won"]
        out.append({
            "match_id": m["match_id"],
            "year":     year,
            "p1":       m["p1"],
            "p2":       m["p2"],
            "p_a":      p_a_match,
            "actual":   actual,
            "correct":  (p_a_match > 0.5) == (actual == 1),
            "brier":    (p_a_match - actual) ** 2,
            "log_loss": -(actual * math.log(max(p_a_match, 1e-9))
                          + (1 - actual) * math.log(max(1 - p_a_match, 1e-9))),
        })
    return out, no_fp, len(matches)


def summarise(rows: List[dict], label: str):
    if not rows:
        print(f"  {label}: no rows")
        return
    n = len(rows)
    acc = sum(r["correct"] for r in rows) / n
    bri = sum(r["brier"]   for r in rows) / n
    ll  = sum(r["log_loss"] for r in rows) / n
    print(f"  {label:>10}  n={n:4d}  acc={acc:.3%}  brier={bri:.4f}  log_loss={ll:.4f}")
    return {"n": n, "accuracy": acc, "brier": bri, "log_loss": ll}


def main():
    print("Latent Factor A/B — Markov sanity validator")
    print("=" * 64)
    print("(Match outcomes reconstructed from match_summaries; raw PBP not required)")
    print()

    base_all, lat_all = [], []
    per_year = {}
    for year in YEARS:
        print(f"\n[{year}]  ← prior fingerprints from {SUPPORTED_PRIOR[year]}")
        base, no_fp_b, total = evaluate(year, latent=False)
        lat,  no_fp_l, _     = evaluate(year, latent=True)

        # Restrict to intersection (in case latent attach excluded a player)
        base_keys = {r["match_id"] for r in base}
        lat_keys  = {r["match_id"] for r in lat}
        common = base_keys & lat_keys
        base = [r for r in base if r["match_id"] in common]
        lat  = [r for r in lat  if r["match_id"] in common]

        print(f"  matches={total}  evaluable={len(common)}  "
              f"skipped_no_fp={no_fp_b}")
        b = summarise(base, "baseline")
        l = summarise(lat,  "latent")
        per_year[year] = {"baseline": b, "latent": l, "n": len(common)}
        base_all.extend(base)
        lat_all.extend(lat)

    print("\n" + "=" * 64)
    print("OVERALL")
    print("=" * 64)
    b = summarise(base_all, "baseline")
    l = summarise(lat_all,  "latent")

    if b and l:
        d_acc = l["accuracy"] - b["accuracy"]
        d_bri = l["brier"]    - b["brier"]
        d_ll  = l["log_loss"] - b["log_loss"]
        print(f"\n  Δ accuracy = {d_acc:+.3%}")
        print(f"  Δ Brier    = {d_bri:+.4f}   (negative = improvement)")
        print(f"  Δ log loss = {d_ll:+.4f}   (negative = improvement)")

    out = {
        "method": ("Markov per-point baseline; matches reconstructed from "
                   "men_profiles match_summaries (winner = higher points_won)."),
        "best_of": 5,
        "configs": ["baseline_raw_tier1", "latent_factor_smoothed"],
        "per_year": per_year,
        "overall": {"baseline": b, "latent": l},
    }
    out_path = ROOT / "data" / "latent_validation.json"
    out_path.write_text(json.dumps(out, indent=2, default=lambda x: round(x, 6) if isinstance(x, float) else x))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
