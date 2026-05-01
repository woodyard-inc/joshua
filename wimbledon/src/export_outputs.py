"""
Export pipeline outputs as JSON for the static frontend app.
Copies outputs/ → docs/data/ and converts CSV → JSON.

Usage:
    python src/export_outputs.py --outputs_dir outputs --docs_data docs/data
"""
import argparse
import json
from pathlib import Path

import pandas as pd


def export_feature_importance(csv_path: Path, out_path: Path) -> None:
    df = pd.read_csv(csv_path)
    df = df.sort_values("rf_importance", ascending=False)

    records = []
    for _, row in df.iterrows():
        records.append({
            "feature": row["display"],
            "lr_coef":       round(float(row["lr_coef"]),        4) if pd.notna(row.get("lr_coef"))        else None,
            "rf_importance": round(float(row["rf_importance"]),  4) if pd.notna(row.get("rf_importance"))  else None,
        })

    out_path.write_text(json.dumps(records, indent=2))
    print(f"  Exported {len(records)} features → {out_path}")


def export_player_seasons(csv_path: Path, out_path: Path) -> None:
    df = pd.read_csv(csv_path)
    records = []
    for _, r in df.iterrows():
        def pct(v):
            return round(float(v) * 100, 1) if pd.notna(v) else None
        records.append({
            "player_id":   str(r["player_id"]),
            "player_name": r["player_name"],
            "year":        int(r["year"]),
            "id":          f"{r['player_id']}-{int(r['year'])}",
            "n_matches":   int(r["n_matches"]),
            "service_games": int(r["sg"]) if pd.notna(r["sg"]) else None,
            "grass_win_pct": pct(r["grass_win_pct"]),
            # ── serve waterfall ──────────────────
            "first_in_pct":      pct(r["first_in_pct"]),
            "first_won_pct":     pct(r["first_won_pct"]),
            "second_won_pct":    pct(r["second_won_pct"]),
            "df_per_sg":         round(float(r["df_per_sg"]), 3) if pd.notna(r["df_per_sg"]) else None,
            "ace_per_sg":        round(float(r["ace_per_sg"]), 3) if pd.notna(r["ace_per_sg"]) else None,
            "service_pts_won_pct": pct(r["service_pts_won_pct"]),
            "sg_held_pct":       pct(r["sg_held_pct"]),
            # ── serve resilience under pressure ──
            "bp_faced":         int(r["bp_faced"]) if pd.notna(r["bp_faced"]) else None,
            "bp_saved":         int(r["bp_saved"]) if pd.notna(r["bp_saved"]) else None,
            "bp_saved_pct":     pct(r["bp_saved_pct"]),
            "bp_faced_per_sg":  round(float(r["bp_faced_per_sg"]), 3) if pd.notna(r["bp_faced_per_sg"]) else None,
            # ── return waterfall ─────────────────
            "first_ret_won_pct":  pct(r["first_ret_won_pct"]),
            "second_ret_won_pct": pct(r["second_ret_won_pct"]),
            "return_pts_won_pct": pct(r["return_pts_won_pct"]),
            # ── return chance creation + conversion ──
            "o_bp_faced":         int(r["o_bp_faced"]) if pd.notna(r["o_bp_faced"]) else None,
            "o_bp_saved":         int(r["o_bp_saved"]) if pd.notna(r["o_bp_saved"]) else None,
            "bp_created_per_o_sg": round(float(r["bp_created_per_o_sg"]), 3) if pd.notna(r["bp_created_per_o_sg"]) else None,
            "bp_conv_pct":        pct(r["bp_conv_pct"]),
            # ── composite indices ────────────────
            "serve_index":  round(float(r["serve_index"]),  1),
            "return_index": round(float(r["return_index"]), 1),
        })
    out_path.write_text(json.dumps(records, indent=2))
    print(f"  Exported {len(records)} player-seasons → {out_path}")


def copy_metrics(json_path: Path, out_path: Path) -> None:
    data = json.loads(json_path.read_text())
    out_path.write_text(json.dumps(data, indent=2))
    print(f"  Copied metrics → {out_path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outputs_dir", default="outputs")
    parser.add_argument("--docs_data",   default="data")
    args = parser.parse_args()

    src = Path(args.outputs_dir)
    dst = Path(args.docs_data)
    dst.mkdir(parents=True, exist_ok=True)

    print("Exporting pipeline outputs to frontend data directory…")

    if (src / "feature_importance.csv").exists():
        export_feature_importance(src / "feature_importance.csv",
                                   dst / "feature_importance.json")

    if (src / "player_season_indices.csv").exists():
        export_player_seasons(src / "player_season_indices.csv",
                               dst / "player_seasons.json")

    if (src / "model_metrics.json").exists():
        copy_metrics(src / "model_metrics.json",
                     dst / "model_metrics.json")

    print("Done.")


if __name__ == "__main__":
    main()
