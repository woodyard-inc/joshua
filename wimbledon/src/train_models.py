"""
Train logistic regression and random forest models on the Wimbledon feature matrix.
Uses a time-based train/test split (train ≤ 2018, test ≥ 2019).

Usage:
    python src/train_models.py \
        --features data/processed/feature_matrix.csv \
        --out_dir outputs
"""
import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.preprocessing import StandardScaler


FEATURE_DISPLAY_NAMES = {
    "delta_rank":               "Ranking Advantage",
    "delta_grass_win_pct_52w":  "Grass Win % (52w)",
    "delta_grass_form_n":       "Grass Form (last 10)",
    "delta_first_serve_pct":    "1st Serve %",
    "delta_first_won_pct":      "1st Serve Won %",
    "delta_sgs_won_pct":        "Service Games Won %",
    "delta_ace_per_sg":         "Aces per Service Game",
    "delta_return_pts_won_pct": "Return Points Won %",
    "delta_bp_conv_pct":        "Break Points Converted %",
    "delta_bp_saved_pct":       "Break Points Saved %",
    "delta_df_per_sg":          "Double Fault Rate",
}

TEST_YEAR = 2019


def load_features(path: Path):
    df = pd.read_csv(path, parse_dates=["match_date"])
    feature_cols = sorted([c for c in df.columns if c.startswith("delta_")])
    df = df.dropna(subset=feature_cols, thresh=int(len(feature_cols) * 0.7))
    return df, feature_cols


def split(df, feature_cols):
    train = df[df["year"] < TEST_YEAR]
    test  = df[df["year"] >= TEST_YEAR]
    X_tr = train[feature_cols].fillna(0).values
    y_tr = train["target"].values
    X_te = test[feature_cols].fillna(0).values
    y_te = test["target"].values
    return X_tr, y_tr, X_te, y_te


def train_logistic(X_tr, y_tr, X_te, y_te, feature_cols):
    scaler = StandardScaler()
    X_tr_s = scaler.fit_transform(X_tr)
    X_te_s = scaler.transform(X_te)

    model = LogisticRegression(C=1.0, max_iter=1000, random_state=42)
    model.fit(X_tr_s, y_tr)

    acc = accuracy_score(y_te, model.predict(X_te_s))
    auc = roc_auc_score(y_te, model.predict_proba(X_te_s)[:, 1])

    # Signed standardized coefficients (positive = helps player A win)
    coefs = model.coef_[0]
    # For delta features, negative rank means higher-ranked (lower number) player A,
    # so flip rank sign so "positive = player A is better ranked"
    rank_idx = feature_cols.index("delta_rank") if "delta_rank" in feature_cols else -1
    display_coefs = coefs.copy()
    if rank_idx >= 0:
        display_coefs[rank_idx] *= -1  # flip: lower rank number = better

    importance = pd.DataFrame({
        "feature":    feature_cols,
        "display":    [FEATURE_DISPLAY_NAMES.get(f, f) for f in feature_cols],
        "lr_coef":    display_coefs,
    })

    return importance, {"model": "Logistic Regression", "accuracy": round(acc, 4), "auc": round(auc, 4)}


def train_rf(X_tr, y_tr, X_te, y_te, feature_cols):
    model = RandomForestClassifier(
        n_estimators=500, max_depth=6, min_samples_leaf=10,
        random_state=42, n_jobs=-1
    )
    model.fit(X_tr, y_tr)

    acc = accuracy_score(y_te, model.predict(X_te))
    auc = roc_auc_score(y_te, model.predict_proba(X_te)[:, 1])

    importance = pd.DataFrame({
        "feature": feature_cols,
        "display": [FEATURE_DISPLAY_NAMES.get(f, f) for f in feature_cols],
        "rf_importance": model.feature_importances_,
    })

    return importance, {"model": "Random Forest", "accuracy": round(acc, 4), "auc": round(auc, 4)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--features", default="data/processed/feature_matrix.csv")
    parser.add_argument("--out_dir", default="outputs")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Loading feature matrix…")
    df, feature_cols = load_features(Path(args.features))
    print(f"  {len(df):,} matches, {len(feature_cols)} features")
    print(f"  Train: <{TEST_YEAR}  |  Test: ≥{TEST_YEAR}")

    X_tr, y_tr, X_te, y_te = split(df, feature_cols)
    print(f"  Train rows: {len(X_tr):,}  |  Test rows: {len(X_te):,}")

    print("Training logistic regression…")
    lr_imp, lr_metrics = train_logistic(X_tr, y_tr, X_te, y_te, feature_cols)
    print(f"  Accuracy {lr_metrics['accuracy']:.1%}  AUC {lr_metrics['auc']:.3f}")

    print("Training random forest…")
    rf_imp, rf_metrics = train_rf(X_tr, y_tr, X_te, y_te, feature_cols)
    print(f"  Accuracy {rf_metrics['accuracy']:.1%}  AUC {rf_metrics['auc']:.3f}")

    # Merge importances
    combined = lr_imp.merge(rf_imp[["feature", "rf_importance"]], on="feature", how="outer")
    combined = combined.sort_values("rf_importance", ascending=False)

    combined.to_csv(out_dir / "feature_importance.csv", index=False)
    print(f"Saved → {out_dir}/feature_importance.csv")

    metrics = {
        "logistic_regression": lr_metrics,
        "random_forest": rf_metrics,
        "train_years": f"1991–{TEST_YEAR - 1}",
        "test_years":  f"{TEST_YEAR}–2023",
    }
    (out_dir / "model_metrics.json").write_text(json.dumps(metrics, indent=2))
    print(f"Saved → {out_dir}/model_metrics.json")


if __name__ == "__main__":
    main()
