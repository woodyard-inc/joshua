"""
Load and clean ATP match data from Jeff Sackmann's tennis_atp repo.
Filters to Wimbledon main-draw singles and exports processed match table.

Usage:
    python src/data_prep.py --raw_dir data/raw --out data/processed/wimbledon_matches.csv
    python src/data_prep.py --download  # fetch CSV files from GitHub first
"""
import argparse
import os
import sys
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

# Phase B: canonical name normalisation applied during clean()
# so all downstream CSVs use a single, consistent spelling per player.
from canonical_names import normalize_series


SACKMANN_BASE = "https://raw.githubusercontent.com/JeffSackmann/tennis_atp/master"
MATCH_FILE_YEARS = range(1991, 2026)

MATCH_COLS_KEEP = [
    "tourney_id", "tourney_name", "surface", "tourney_date",
    "match_num", "round", "best_of", "minutes",
    "winner_id", "winner_name", "winner_hand", "winner_ht", "winner_age", "winner_rank",
    "loser_id",  "loser_name",  "loser_hand",  "loser_ht",  "loser_age",  "loser_rank",
    "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon", "w_SvGms",
    "w_bpSaved", "w_bpFaced",
    "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon", "l_SvGms",
    "l_bpSaved", "l_bpFaced",
]


def download_match_files(raw_dir: Path) -> None:
    raw_dir.mkdir(parents=True, exist_ok=True)
    for year in tqdm(MATCH_FILE_YEARS, desc="Downloading match files"):
        fname = f"atp_matches_{year}.csv"
        dest = raw_dir / fname
        if dest.exists():
            continue
        url = f"{SACKMANN_BASE}/{fname}"
        r = requests.get(url, timeout=30)
        if r.status_code == 200:
            dest.write_bytes(r.content)
        else:
            print(f"  Warning: {year} returned {r.status_code}", file=sys.stderr)


def load_raw_matches(raw_dir: Path) -> pd.DataFrame:
    frames = []
    files = sorted(raw_dir.glob("atp_matches_*.csv"))
    if not files:
        sys.exit(
            f"No match files found in {raw_dir}. "
            "Run with --download or place files manually."
        )
    for f in tqdm(files, desc="Loading match files"):
        try:
            df = pd.read_csv(f, low_memory=False)
            frames.append(df)
        except Exception as e:
            print(f"  Skipping {f.name}: {e}", file=sys.stderr)
    return pd.concat(frames, ignore_index=True)


def filter_wimbledon(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only Wimbledon main-draw singles (not qualifying, not doubles)."""
    mask_name = df["tourney_name"].str.contains("Wimbledon", case=False, na=False)
    # tourney_level W = Grand Slam; exclude qualifying (Q rounds)
    if "tourney_level" in df.columns:
        mask_level = df["tourney_level"] == "G"
        mask_name = mask_name & mask_level
    mask_round = ~df["round"].isin(["Q1", "Q2", "Q3", "BR"])
    return df[mask_name & mask_round].copy()


def filter_grass(df: pd.DataFrame) -> pd.DataFrame:
    """Keep all grass-surface main-draw singles (Wimbledon, Halle, Queen's, Stuttgart, Mallorca, etc)."""
    mask_surface = df["surface"].astype(str).str.lower() == "grass"
    mask_round = ~df["round"].isin(["Q1", "Q2", "Q3", "BR"])
    return df[mask_surface & mask_round].copy()


def filter_main_draw_all_surfaces(df: pd.DataFrame) -> pd.DataFrame:
    """All ATP main-draw singles regardless of surface — used by the Elo
    builder so R_overall reflects all-surface form while R_surface
    only updates on grass matches.

    Keeping qualifying out keeps the Elo updating on competitive matches
    only (avoids inflating tour ratings from low-stakes results)."""
    mask_round = ~df["round"].isin(["Q1", "Q2", "Q3", "BR"])
    mask_surface = df["surface"].astype(str).str.lower().isin(
        ["hard", "clay", "grass", "carpet"])
    return df[mask_round & mask_surface].copy()


def clean(df: pd.DataFrame) -> pd.DataFrame:
    present = [c for c in MATCH_COLS_KEEP if c in df.columns]
    df = df[present].copy()

    df["tourney_date"] = pd.to_datetime(df["tourney_date"].astype(str), format="%Y%m%d", errors="coerce")
    df["year"] = df["tourney_date"].dt.year

    for col in ["winner_rank", "loser_rank"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    stat_cols = [c for c in df.columns if c.startswith(("w_", "l_")) and c not in ("w_ace",)]
    stat_cols += ["w_ace", "l_ace"]
    for col in stat_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # Drop rows without a decisive result (walkovers, retirements kept — handled downstream)
    df = df.dropna(subset=["winner_id", "loser_id", "tourney_date"])
    df = df.sort_values("tourney_date").reset_index(drop=True)

    # Phase B: normalise player name spellings so all downstream files
    # (all_grass_matches.csv, wimbledon_matches.csv, all_atp_matches.csv)
    # use a single canonical form per player.
    for col in ("winner_name", "loser_name"):
        if col in df.columns:
            df[col] = normalize_series(df[col])

    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw_dir",      default="data/raw")
    parser.add_argument("--out_wimbledon", default="data/processed/wimbledon_matches.csv")
    parser.add_argument("--out_grass",     default="data/processed/all_grass_matches.csv")
    parser.add_argument("--out_atp",       default="data/processed/all_atp_matches.csv",
                        help="Full main-draw ATP match feed for the Elo builder")
    parser.add_argument("--download", action="store_true",
                        help="Download raw files from GitHub before processing")
    args = parser.parse_args()

    raw_dir = Path(args.raw_dir)
    Path(args.out_wimbledon).parent.mkdir(parents=True, exist_ok=True)

    if args.download:
        print("Downloading ATP match files…")
        download_match_files(raw_dir)

    print("Loading raw match files…")
    raw = load_raw_matches(raw_dir)
    print(f"Total matches loaded: {len(raw):,}")

    # Full ATP main-draw feed (every surface) — used by the Elo builder
    # so R_overall properly reflects cross-surface form while R_surface
    # only updates on grass matches.
    atp = filter_main_draw_all_surfaces(raw)
    atp = clean(atp)
    print(f"All ATP main-draw matches: {len(atp):,}")
    print(f"  surface mix: {atp['surface'].value_counts().to_dict()}")
    atp.to_csv(args.out_atp, index=False)
    print(f"Saved → {args.out_atp}")

    # All grass matches (for player-season indices and rolling history)
    grass = filter_grass(raw)
    grass = clean(grass)
    print(f"All grass matches: {len(grass):,}")
    grass.to_csv(args.out_grass, index=False)
    print(f"Saved → {args.out_grass}")

    # Wimbledon subset (for prediction target rows)
    wim = filter_wimbledon(raw)
    wim = clean(wim)
    print(f"Wimbledon matches: {len(wim):,}")
    wim.to_csv(args.out_wimbledon, index=False)
    print(f"Saved → {args.out_wimbledon}")


if __name__ == "__main__":
    main()
