"""
build_archetypes.py — Generate per-year archetype assignment JSON files
that the front-end can load alongside fingerprints.

Trains style-archetype centroids on a stable "anchor" year (default 2024)
where data quality is highest, then applies those centroids to every
year's player population.  This gives consistent labels across years
(a "Power Server" in 2014 means the same thing as in 2024).

Output: data/{year}_archetypes.json with structure:
    {
      "Jannik Sinner": {
        "id": 4,
        "label": "Aggressive Returner",
        "blurb": "Strong serve plus aggressive returning vs 2nd serves",
        "z_scores": { "serve_lean": 0.12, "1st_reliance": 1.45, ... },
        "raw":      { "fspw": 80.4, "rpw_vs_1st": 30.2, ... }
      },
      ...
    }

Also writes data/archetypes_meta.json with the centroid + label catalog
so the UI can render tooltips / explainers.

Usage:
    python src/build_archetypes.py --k 5 --anchor-year 2024
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(Path(__file__).parent))

from comparison import load_all_fingerprints
from style_archetypes import (
    extract_features, z_scale, kmeans, label_centroid,
    FEATURE_NAMES, _euclid,
)
from archetype_matchup import filter_outliers


# Curated blurbs by base label. Disambiguation prefixes get extended versions.
ARCHETYPE_BLURBS = {
    "Power Server":
        "Big serve, weaker on return. Lives or dies by the first delivery.",
    "Big-Serve First-Striker":
        "Powerful serve plus dominance in short rallies. Wins or loses points fast.",
    "All-Courter":
        "Balanced game across serve, return, and rally length. The complete game.",
    "Return Specialist (1st-serve neutralizer)":
        "Elite at neutralising big first serves. Forces returners into rallies.",
    "Counterpuncher (2nd-serve attacker)":
        "Punishes second serves disproportionately. Forces servers to take risks.",
    "Two-Way Aggressor":
        "Strong first serve plus aggressive returning vs second serves.",
    "Defensive Returner":
        "Return-leaning baseliner who neutralises rather than attacks.",
    "First-Strike Attacker":
        "Wins points within 1-3 shots more often than the tour average.",
    "Grinder":
        "Long-rally specialist. Outlasts opponents in extended exchanges.",
    "Mixed":
        "Mixed style — does not fit one archetype cleanly.",
    "Outlier (sparse data)":
        "Sample too small to assign a stable archetype.",
}

# Disambiguation qualifier descriptions — prepended to base blurbs
QUALIFIER_BLURBS = {
    "Big-Serve":   "Most serve-dominant of this archetype.",
    "Aggressive":  "More variable serve than the steady version, often takes bigger swings.",
    "Hybrid":      "Sits between the extremes of this archetype.",
    "Light":       "Milder version of this archetype.",
    "Pure":        "Clearest expression of this archetype in the dataset.",
    "Steady":      "Higher serve consistency than the adaptive version.",
    "Adaptive":    "Mixes patterns more than the steady version.",
    "Variable":    "Less consistent than the others in this archetype.",
}

DEFAULT_BLURB = "Distinctive playing style derived from PBP clustering."


def build_blurb(label: str, base_label: str) -> str:
    """Compose a useful blurb for any (possibly disambiguated) label."""
    base = ARCHETYPE_BLURBS.get(base_label, DEFAULT_BLURB)
    if label == base_label:
        return base
    # Disambiguated — try to find the leading qualifier
    first_word = label.split()[0]
    qual = QUALIFIER_BLURBS.get(first_word)
    if qual:
        return f"{qual} {base}"
    return base

SUPPORTED_YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018, 2019,
                   2021, 2022, 2023, 2024]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--anchor-year", type=int, default=2024,
                    help="year used to define the archetype centroids")
    args = ap.parse_args()

    # ── 1. Train centroids on the anchor year ───────────────────────────────
    print(f"Training {args.k}-archetype centroids on {args.anchor_year}...")
    train_fps = load_all_fingerprints(args.anchor_year)
    # IMPORTANT: load_all_fingerprints returns a dict whose order comes from a
    # set union, which is non-deterministic across Python runs.  Sort by name
    # so the kmeans++ initialisation is reproducible across invocations.
    train_vectors = []
    for name in sorted(train_fps.keys()):
        f = extract_features(train_fps[name])
        if f is None or not filter_outliers(f):
            continue
        train_vectors.append(z_scale(f))
    print(f"  {len(train_vectors)} training players (after outlier filter)")

    _, centroids = kmeans(train_vectors, k=args.k, seed=42)

    # Post-hoc disambiguation — if two clusters get the same heuristic label,
    # split by their most distinctive remaining feature. This guarantees
    # every UI label is unique while still being descriptive.
    raw_labels = [label_centroid(c) for c in centroids]
    archetype_labels = list(raw_labels)
    seen: dict[str, list[int]] = {}
    for i, lab in enumerate(raw_labels):
        seen.setdefault(lab, []).append(i)

    # Pre-defined qualifier ladders per base label.  We pick from these in
    # order of magnitude on a discriminating axis so labels are always unique.
    # For "Power Server": rank by serve_lean (more = more serve-dominant).
    # For "Return Specialist": rank by serve_lean (more negative = more return-dominant).
    # For "All-Courter": rank by serve_consist.
    LADDERS = {
        "Power Server":       (["Big-Serve", "Aggressive", "Hybrid", "Light"], 0, "desc"),
        "Big-Serve First-Striker": (["Pure", "Hybrid", "Light"], 0, "desc"),
        "Return Specialist (1st-serve neutralizer)": (
            ["Pure", "Steady", "Aggressive", "Hybrid"], 0, "asc"),
        "Counterpuncher (2nd-serve attacker)": (["Aggressive", "Hybrid"], 2, "desc"),
        "Two-Way Aggressor":  (["Pure", "Hybrid"], 2, "desc"),
        "All-Courter":        (["Steady", "Adaptive", "Variable"], 4, "desc"),
        "Defensive Returner": (["Pure", "Steady", "Hybrid"], 0, "asc"),
        "First-Strike Attacker": (["Pure", "Hybrid"], 3, "desc"),
        "Grinder":            (["Pure", "Hybrid"], 3, "asc"),
        "Mixed":              (["#1", "#2", "#3", "#4", "#5"], 0, "desc"),
    }

    for lab, idxs in seen.items():
        if len(idxs) <= 1:
            continue
        ladder, axis_idx, direction = LADDERS.get(
            lab, (["#1", "#2", "#3", "#4", "#5"], 0, "desc"))
        # Sort by the discriminating axis
        reverse = (direction == "desc")
        idxs_sorted = sorted(idxs, key=lambda i: centroids[i][axis_idx], reverse=reverse)
        for rank, ci in enumerate(idxs_sorted):
            qualifier = ladder[min(rank, len(ladder) - 1)]
            if "(" in lab:
                noun_part, paren = lab.split("(", 1)
                archetype_labels[ci] = f"{qualifier} {noun_part.strip()} ({paren}".strip()
            else:
                archetype_labels[ci] = f"{qualifier} {lab}".strip()

    print(f"  Resolved labels:")
    for i, lab in enumerate(archetype_labels):
        marker = " (disambiguated)" if lab != raw_labels[i] else ""
        print(f"    [{i}] {lab}{marker}")

    # ── 2. Write meta catalog ───────────────────────────────────────────────
    meta = {
        "anchor_year": args.anchor_year,
        "k":           args.k,
        "feature_names": FEATURE_NAMES,
        "archetypes": [
            {
                "id":        i,
                "label":     archetype_labels[i],
                "blurb":     build_blurb(archetype_labels[i], raw_labels[i]),
                "centroid":  {nm: round(centroids[i][j], 3)
                              for j, nm in enumerate(FEATURE_NAMES)},
            }
            for i in range(args.k)
        ],
    }
    meta_path = ROOT / "data" / "archetypes_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2))
    print(f"  Wrote {meta_path}")

    # ── 3. Assign every player in every year ────────────────────────────────
    print()
    print("Assigning archetypes per year...")

    def assign(fp_dict):
        f = extract_features(fp_dict)
        if f is None:
            return None, None, None
        is_outlier = not filter_outliers(f)
        if is_outlier:
            return None, f, None
        v = z_scale(f)
        best, best_d = -1, float("inf")
        for ci, c in enumerate(centroids):
            d = _euclid(v, c)
            if d < best_d:
                best_d, best = d, ci
        return best, f, v

    for year in SUPPORTED_YEARS:
        fps = load_all_fingerprints(year)
        out = {}
        n_assigned = 0
        for name, fp in fps.items():
            arch_id, raw, z_vec = assign(fp)
            if arch_id is None:
                continue
            out[name] = {
                "id":       arch_id,
                "label":    archetype_labels[arch_id],
                "blurb":    build_blurb(archetype_labels[arch_id], raw_labels[arch_id]),
                "raw":      {k: round(v, 1) for k, v in raw.items()},
                "z_scores": {nm: round(z_vec[j], 3) for j, nm in enumerate(FEATURE_NAMES)},
            }
            n_assigned += 1

        # Write to BOTH data/ and data/processed/ for parity with other artifacts
        for d in [ROOT / "data", ROOT / "data" / "processed"]:
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{year}_archetypes.json").write_text(json.dumps(out, indent=2))
        print(f"  {year}: {n_assigned}/{len(fps)} players assigned")

    print()
    print("Done. Files written:")
    print(f"  data/archetypes_meta.json  (catalog of {args.k} archetypes)")
    print(f"  data/{{year}}_archetypes.json  ({len(SUPPORTED_YEARS)} files)")


if __name__ == "__main__":
    main()
