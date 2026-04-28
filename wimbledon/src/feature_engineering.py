"""
Build the pre-match feature matrix for Wimbledon match prediction.

For each match we compute rolling historical stats for both players
using only information available before that match begins, then create
A-B delta features to represent the head-to-head gap.

Rolling history is computed from ALL grass matches (Wimbledon + Halle + Queen's +
Stuttgart + Eastbourne + Mallorca + 's-Hertogenbosch + Newport, etc.). Prediction
target rows are Wimbledon main-draw matches only.

Usage:
    python src/feature_engineering.py \
        --wimbledon  data/processed/wimbledon_matches.csv \
        --grass      data/processed/all_grass_matches.csv \
        --out        data/processed/feature_matrix.csv
"""
import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from tqdm import tqdm


LOOKBACK_DAYS = 365          # 52-week grass window
GRASS_FORM_N = 10            # recent grass match count


# ── helpers ────────────────────────────────────────────────────────────────

def safe_div(num, denom, fill=np.nan):
    with np.errstate(invalid="ignore", divide="ignore"):
        result = np.where(denom > 0, num / denom, fill)
    return result


def _serve_stats(row, prefix):
    """Return dict of serve metrics for one side of a match row."""
    ace   = row.get(f"{prefix}_ace", np.nan)
    df    = row.get(f"{prefix}_df", np.nan)
    svpt  = row.get(f"{prefix}_svpt", np.nan)
    first = row.get(f"{prefix}_1stIn", np.nan)
    fw    = row.get(f"{prefix}_1stWon", np.nan)
    sw    = row.get(f"{prefix}_2ndWon", np.nan)
    sgs   = row.get(f"{prefix}_SvGms", np.nan)
    bps   = row.get(f"{prefix}_bpSaved", np.nan)
    bpf   = row.get(f"{prefix}_bpFaced", np.nan)
    return {
        "ace_per_sg":       safe_div(ace, sgs),
        "df_per_sg":        safe_div(df, sgs),
        "first_serve_pct":  safe_div(first, svpt),
        "first_won_pct":    safe_div(fw, first),
        "bp_saved_pct":     safe_div(bps, bpf),
        "sgs_won_pct":      safe_div(sgs - bpf + bps, sgs),   # approx hold%
        "return_pts_won_pct": np.nan,   # filled from opponent below
        "bp_conv_pct":      np.nan,     # filled from opponent below
    }


def extract_match_records(df: pd.DataFrame) -> pd.DataFrame:
    """
    Expand each match into two player-perspective rows so we can build
    rolling histories per player.
    """
    records = []
    for _, row in df.iterrows():
        date = row["tourney_date"]
        year = row.get("year", pd.to_datetime(date).year)

        for role, opp_role in [("w", "l"), ("l", "w")]:
            pid   = row[f"{role}inner_id"]   if role == "w" else row["loser_id"]
            pname = row[f"{role}inner_name"] if role == "w" else row["loser_name"]
            won   = 1 if role == "w" else 0

            s = _serve_stats(row, role)
            o = _serve_stats(row, opp_role)

            # Return pts won = 1 - opponent's serve pts won
            opp_svpt  = row.get(f"{opp_role}_svpt", np.nan)
            opp_total = (row.get(f"{opp_role}_1stWon", 0) or 0) + (row.get(f"{opp_role}_2ndWon", 0) or 0)
            if opp_svpt and opp_svpt > 0:
                s["return_pts_won_pct"] = 1 - safe_div(opp_total, opp_svpt)

            # BP converted = opponent bpFaced - opponent bpSaved
            o_bpf = row.get(f"{opp_role}_bpFaced", np.nan)
            o_bps = row.get(f"{opp_role}_bpSaved", np.nan)
            if not np.isnan(o_bpf) and not np.isnan(o_bps) and o_bpf > 0:
                s["bp_conv_pct"] = safe_div(o_bpf - o_bps, o_bpf)

            records.append({
                "date":      date,
                "year":      year,
                "player_id": pid,
                "player_name": pname,
                "won":       won,
                "rank":      row.get(f"{role}inner_rank" if role == "w" else "loser_rank"),
                "age":       row.get(f"{role}inner_age"  if role == "w" else "loser_age"),
                "hand":      row.get(f"{role}inner_hand" if role == "w" else "loser_hand"),
                **s,
            })

    return pd.DataFrame(records).sort_values("date").reset_index(drop=True)


def rolling_player_stats(records: pd.DataFrame, match_date, player_id,
                          current_rank) -> dict:
    """
    Compute rolling pre-match stats for one player up to (not including) match_date.
    """
    hist = records[(records["player_id"] == player_id) &
                   (records["date"] < match_date)].copy()

    # 52-week grass window
    cutoff_52w = match_date - pd.Timedelta(days=LOOKBACK_DAYS)
    hist_52w = hist[hist["date"] >= cutoff_52w]

    grass_win_pct_52w = hist_52w["won"].mean() if len(hist_52w) >= 3 else np.nan
    grass_form_n = hist_52w.tail(GRASS_FORM_N)["won"].sum() if len(hist_52w) >= 1 else np.nan

    # Career grass decide set win%
    dec = hist.dropna(subset=["won"])

    stat_cols = ["ace_per_sg", "df_per_sg", "first_serve_pct", "first_won_pct",
                 "bp_saved_pct", "sgs_won_pct", "return_pts_won_pct", "bp_conv_pct"]

    # Use 52-week window for serve/return stats (recency weighted by default via tail)
    recent = hist_52w if len(hist_52w) >= 5 else hist.tail(20)
    avgs = {col: recent[col].mean() for col in stat_cols}

    return {
        "rank":               current_rank,
        "grass_win_pct_52w":  grass_win_pct_52w,
        "grass_form_n":       grass_form_n,
        **avgs,
    }


def build_feature_matrix(matches: pd.DataFrame,
                          grass_records: pd.DataFrame) -> pd.DataFrame:
    """
    For each Wimbledon match create one row with A-B delta features.
    Player A is randomly assigned to avoid positional leakage.
    """
    rng = np.random.default_rng(42)
    rows = []

    for _, match in tqdm(matches.iterrows(), total=len(matches), desc="Engineering features"):
        date = match["tourney_date"]

        # Randomly assign winner/loser to A/B
        flip = rng.integers(0, 2)
        if flip == 0:
            a_id, a_name, a_rank = match["winner_id"], match["winner_name"], match["winner_rank"]
            b_id, b_name, b_rank = match["loser_id"],  match["loser_name"],  match["loser_rank"]
            target = 1
        else:
            a_id, a_name, a_rank = match["loser_id"],  match["loser_name"],  match["loser_rank"]
            b_id, b_name, b_rank = match["winner_id"], match["winner_name"], match["winner_rank"]
            target = 0

        a_stats = rolling_player_stats(grass_records, date, a_id, a_rank)
        b_stats = rolling_player_stats(grass_records, date, b_id, b_rank)

        feature_keys = list(a_stats.keys())
        deltas = {f"delta_{k}": (a_stats[k] - b_stats[k])
                  for k in feature_keys
                  if not (np.isnan(a_stats[k]) or np.isnan(b_stats[k]))}

        rows.append({
            "match_date":  date,
            "year":        match.get("year"),
            "round":       match.get("round"),
            "player_a_id": a_id,
            "player_a":    a_name,
            "player_b_id": b_id,
            "player_b":    b_name,
            "target":      target,
            **deltas,
        })

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--wimbledon", default="data/processed/wimbledon_matches.csv")
    parser.add_argument("--grass",     default="data/processed/all_grass_matches.csv")
    parser.add_argument("--out",       default="data/processed/feature_matrix.csv")
    args = parser.parse_args()

    wim_path = Path(args.wimbledon)
    grass_path = Path(args.grass)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not wim_path.exists():
        raise FileNotFoundError(f"{wim_path} not found. Run src/data_prep.py first.")
    if not grass_path.exists():
        print(f"  Warning: {grass_path} not found — falling back to Wimbledon-only history.")
        grass_path = wim_path

    print("Loading Wimbledon target matches…")
    wim = pd.read_csv(wim_path, parse_dates=["tourney_date"])
    print(f"  {len(wim):,} Wimbledon matches")

    print("Loading all-grass history pool…")
    grass = pd.read_csv(grass_path, parse_dates=["tourney_date"])
    print(f"  {len(grass):,} grass-court matches (Wimbledon + Halle + Queen's + ...)")

    print("Building per-player history records from all-grass pool…")
    grass_records = extract_match_records(grass)

    print(f"Building feature matrix for {len(wim):,} Wimbledon target matches…")
    fm = build_feature_matrix(wim, grass_records)

    # Drop rows with too many missing features
    feature_cols = [c for c in fm.columns if c.startswith("delta_")]
    fm = fm.dropna(subset=feature_cols, thresh=int(len(feature_cols) * 0.6))

    print(f"Feature matrix: {len(fm):,} rows × {len(feature_cols)} features")
    fm.to_csv(out_path, index=False)
    print(f"Saved → {out_path}")


if __name__ == "__main__":
    main()
