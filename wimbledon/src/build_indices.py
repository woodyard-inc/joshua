"""
Build Serve Index and Return Index for each PLAYER-SEASON on grass.

Uses ALL grass-court main-draw matches (Wimbledon + Halle + Queen's + Stuttgart +
Mallorca + Eastbourne + Newport + 's-Hertogenbosch, etc.). Each (player, year) is
treated as a separate entity so career arcs and peak seasons surface naturally —
"Federer 2006" and "Federer 2014" are different rows.

Indices are decomposed (waterfall) so each player-season can be read at four levels:

  Serve Index components:
    1stIn%      reliability of the weapon
    1stWon%     potency when in
    2ndWon%     quality of the safety net
    DF/sg       cost of the safety net (inverted)

  Return Index components:
    1stRetWon%  return points won vs opponent's 1st serve
    2ndRetWon%  return points won vs opponent's 2nd serve
    BPconv%     break points converted

Each Index is a z-scored composite of its components, scaled 0-100 across the pool.

Usage:
    python src/build_indices.py \
        --grass data/processed/all_grass_matches.csv \
        --out outputs/player_season_indices.csv
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd


MIN_SERVICE_GAMES = 30   # threshold for a player-season to qualify
START_YEAR = 1991


# ── Index component weights ──────────────────────────────────────
# Serve = point dominance + resilience under pressure − cost.
SERVE_WEIGHTS = {
    "first_won_pct":      0.30,   # 1st serve potency
    "second_won_pct":     0.20,   # safety net quality
    "first_in_pct":       0.15,   # reliability of the weapon
    "bp_saved_pct":       0.15,   # resilience: clutch saves under pressure
    "low_bp_faced_rate":  0.10,   # pressure absorption: how rarely the serve cracks
    "low_df_rate":        0.10,   # safety-net cost (inverted DF/sg)
}

# Return = point pressure + chance creation + conversion. Conversion is
# de-weighted because it has high variance on small samples — chance creation
# is the more stable underlying skill.
RETURN_WEIGHTS = {
    "first_ret_won_pct":     0.30,   # handling heat
    "second_ret_won_pct":    0.25,   # punishing the weak ball
    "bp_created_per_o_sg":   0.30,   # CHANCE CREATION — the skill
    "bp_conv_pct":           0.15,   # conversion (down-weighted; noisier)
}


# ── Helpers ──────────────────────────────────────────────────────
def safe_div(num, denom):
    return np.where(denom > 0, num / denom, np.nan)


def z_score(series: pd.Series) -> pd.Series:
    s = series.fillna(series.median())
    std = s.std()
    if std == 0 or np.isnan(std):
        return pd.Series(0.0, index=series.index)
    return (s - s.mean()) / std


def scale_0_100(series: pd.Series) -> pd.Series:
    lo, hi = series.min(), series.max()
    if hi == lo:
        return pd.Series(50.0, index=series.index)
    return ((series - lo) / (hi - lo) * 100).round(1)


# ── Step 1: explode each match into two player-perspective rows ──
def to_player_records(matches: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, m in matches.iterrows():
        date = m.get("tourney_date")
        year = m.get("year")
        if pd.isna(year):
            year = pd.to_datetime(date).year if pd.notna(date) else None

        for role, opp in [("w", "l"), ("l", "w")]:
            pid    = m["winner_id"]   if role == "w" else m["loser_id"]
            pname  = m["winner_name"] if role == "w" else m["loser_name"]
            won    = 1 if role == "w" else 0

            rows.append({
                "player_id":   pid,
                "player_name": pname,
                "year":        int(year) if year is not None else None,
                "won":         won,
                # serve raw counts
                "ace":     m.get(f"{role}_ace", np.nan),
                "df":      m.get(f"{role}_df", np.nan),
                "svpt":    m.get(f"{role}_svpt", np.nan),
                "first_in":  m.get(f"{role}_1stIn", np.nan),
                "first_won": m.get(f"{role}_1stWon", np.nan),
                "second_won": m.get(f"{role}_2ndWon", np.nan),
                "sg":      m.get(f"{role}_SvGms", np.nan),
                "bp_saved":  m.get(f"{role}_bpSaved", np.nan),
                "bp_faced":  m.get(f"{role}_bpFaced", np.nan),
                # opponent-perspective counts (for return stats)
                "o_svpt":     m.get(f"{opp}_svpt", np.nan),
                "o_first_in": m.get(f"{opp}_1stIn", np.nan),
                "o_first_won": m.get(f"{opp}_1stWon", np.nan),
                "o_second_won": m.get(f"{opp}_2ndWon", np.nan),
                "o_bp_faced":  m.get(f"{opp}_bpFaced", np.nan),
                "o_bp_saved":  m.get(f"{opp}_bpSaved", np.nan),
                "o_sg":        m.get(f"{opp}_SvGms", np.nan),   # opp service games (return-game count)
            })
    return pd.DataFrame(rows)


# ── Step 2: aggregate by (player, year) and compute components ───
def aggregate_player_seasons(records: pd.DataFrame) -> pd.DataFrame:
    records = records[records["year"].notna()]
    records = records[records["year"] >= START_YEAR]

    grp = records.groupby(["player_id", "player_name", "year"], as_index=False)

    agg = grp.agg(
        n_matches=("won", "size"),
        wins=("won", "sum"),
        # serve sums
        ace=("ace", "sum"),
        df=("df", "sum"),
        svpt=("svpt", "sum"),
        first_in=("first_in", "sum"),
        first_won=("first_won", "sum"),
        second_won=("second_won", "sum"),
        sg=("sg", "sum"),
        bp_saved=("bp_saved", "sum"),
        bp_faced=("bp_faced", "sum"),
        # opponent (return) sums
        o_svpt=("o_svpt", "sum"),
        o_first_in=("o_first_in", "sum"),
        o_first_won=("o_first_won", "sum"),
        o_second_won=("o_second_won", "sum"),
        o_bp_faced=("o_bp_faced", "sum"),
        o_bp_saved=("o_bp_saved", "sum"),
        o_sg=("o_sg", "sum"),
    )

    # ── Serve component rates ─────────────────────────────────
    agg["first_in_pct"]  = safe_div(agg["first_in"],  agg["svpt"])
    agg["first_won_pct"] = safe_div(agg["first_won"], agg["first_in"])
    second_svpt = agg["svpt"] - agg["first_in"]
    agg["second_won_pct"] = safe_div(agg["second_won"], second_svpt)
    agg["df_per_sg"]     = safe_div(agg["df"], agg["sg"])
    agg["ace_per_sg"]    = safe_div(agg["ace"], agg["sg"])
    agg["sg_held_pct"]   = safe_div(agg["sg"] - agg["bp_faced"] + agg["bp_saved"], agg["sg"])
    agg["service_pts_won_pct"] = safe_div(agg["first_won"] + agg["second_won"], agg["svpt"])

    # ── Resilience components (serve under pressure) ────────
    agg["bp_saved_pct"]    = safe_div(agg["bp_saved"], agg["bp_faced"])
    agg["bp_faced_per_sg"] = safe_div(agg["bp_faced"], agg["sg"])

    # ── Return component rates (vs opponent's serves) ────────
    o_second_svpt = agg["o_svpt"] - agg["o_first_in"]
    agg["first_ret_won_pct"]  = 1 - safe_div(agg["o_first_won"],  agg["o_first_in"])
    agg["second_ret_won_pct"] = 1 - safe_div(agg["o_second_won"], o_second_svpt)
    agg["bp_conv_pct"]        = safe_div(agg["o_bp_faced"] - agg["o_bp_saved"], agg["o_bp_faced"])
    agg["return_pts_won_pct"] = 1 - safe_div(agg["o_first_won"] + agg["o_second_won"], agg["o_svpt"])

    # ── Chance-creation component (return-side pressure) ────
    # The user's key insight: BP chances created per opponent service game is the
    # underlying skill; conversion has higher per-match variance.
    agg["bp_created_per_o_sg"] = safe_div(agg["o_bp_faced"], agg["o_sg"])

    # Inverted rates for serve composite (lower = better)
    agg["low_df_rate"]       = -agg["df_per_sg"]
    agg["low_bp_faced_rate"] = -agg["bp_faced_per_sg"]

    agg["grass_win_pct"] = agg["wins"] / agg["n_matches"]

    return agg


# ── Step 3: filter and build composite indices ───────────────────
def build_indices(df: pd.DataFrame) -> pd.DataFrame:
    df = df[df["sg"] >= MIN_SERVICE_GAMES].copy()

    # Z-score each component
    for col in set(SERVE_WEIGHTS) | set(RETURN_WEIGHTS):
        df[f"z_{col}"] = z_score(df[col])

    df["serve_raw"] = sum(w * df[f"z_{c}"] for c, w in SERVE_WEIGHTS.items())
    df["return_raw"] = sum(w * df[f"z_{c}"] for c, w in RETURN_WEIGHTS.items())

    df["serve_index"]  = scale_0_100(df["serve_raw"])
    df["return_index"] = scale_0_100(df["return_raw"])

    # Round display-friendly columns
    rate_cols = [
        "first_in_pct", "first_won_pct", "second_won_pct", "df_per_sg",
        "ace_per_sg", "service_pts_won_pct", "sg_held_pct",
        "bp_saved_pct", "bp_faced_per_sg",
        "first_ret_won_pct", "second_ret_won_pct", "bp_conv_pct",
        "bp_created_per_o_sg", "return_pts_won_pct", "grass_win_pct",
    ]
    for c in rate_cols:
        df[c] = df[c].round(4)

    out_cols = [
        "player_id", "player_name", "year", "n_matches", "sg", "svpt",
        "bp_faced", "bp_saved", "o_bp_faced", "o_bp_saved", "o_sg",
        "grass_win_pct",
        # serve waterfall
        "first_in_pct", "first_won_pct", "second_won_pct", "df_per_sg",
        "ace_per_sg", "service_pts_won_pct", "sg_held_pct",
        # serve resilience
        "bp_saved_pct", "bp_faced_per_sg",
        # return waterfall
        "first_ret_won_pct", "second_ret_won_pct",
        # return chance creation + conversion
        "bp_created_per_o_sg", "bp_conv_pct", "return_pts_won_pct",
        # indices
        "serve_index", "return_index",
    ]
    return df[out_cols].sort_values(["serve_index", "return_index"], ascending=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--grass", default="data/processed/all_grass_matches.csv")
    p.add_argument("--out",   default="outputs/player_season_indices.csv")
    args = p.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    print("Loading all-grass matches…")
    matches = pd.read_csv(args.grass, parse_dates=["tourney_date"])
    print(f"  {len(matches):,} grass matches")

    print("Building per-player-per-match records…")
    records = to_player_records(matches)

    print("Aggregating by (player, year)…")
    agg = aggregate_player_seasons(records)
    print(f"  {len(agg):,} player-seasons before threshold")

    print(f"Applying minimum threshold (≥ {MIN_SERVICE_GAMES} service games)…")
    indices = build_indices(agg)
    print(f"  {len(indices):,} qualifying player-seasons")

    print("\nTop Serve Index seasons:")
    print(indices.head(10)[["player_name", "year", "serve_index", "return_index",
                             "first_in_pct", "first_won_pct"]]
          .to_string(index=False))

    print("\nTop Return Index seasons:")
    print(indices.sort_values("return_index", ascending=False).head(10)
          [["player_name", "year", "serve_index", "return_index",
            "first_ret_won_pct", "second_ret_won_pct"]]
          .to_string(index=False))

    indices.to_csv(out, index=False)
    print(f"\nSaved → {out}")


if __name__ == "__main__":
    main()
