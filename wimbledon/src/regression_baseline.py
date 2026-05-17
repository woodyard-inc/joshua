"""
regression_baseline.py — Apples-to-apples comparison vs the MC engine.

Trains supervised ML predictors on the EXACT same matchup feature vectors
the MC engine's matchup-neighbours layer uses, with the EXACT same LOYO
methodology (predict year Y using everything else).

Three models tested for context:
  - Logistic Regression  (linear)
  - Random Forest         (non-linear, tree ensemble)
  - Gradient Boosting     (non-linear, boosted trees)

Each is trained on the 38-dim matchup feature vectors from data/matchup_corpus.json
(the same vectors the K-NN layer queries against).  Leave-one-year-out
cross-validation: for each test year, train on all OTHER years' matches,
predict on the held-out year, aggregate.

Output: prints accuracy + Brier + log-loss for each model, plus the
MC-engine numbers from data/model_metrics.json for direct comparison.

Usage:
    python3 src/regression_baseline.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"
EPSILON = 1e-9


def _safe_log(p):
    return math.log(max(p, EPSILON))


def _summary(preds, labels):
    n = len(preds)
    preds = np.asarray(preds)
    labels = np.asarray(labels)
    acc = float(((preds > 0.5) == (labels == 1)).mean())
    brier = float(((preds - labels) ** 2).mean())
    log_l = float(np.mean([
        -(a * _safe_log(p) + (1 - a) * _safe_log(1 - p))
        for p, a in zip(preds, labels)
    ]))
    return {"n": n, "accuracy": acc, "brier": brier, "log_loss": log_l}


def loyo_eval(entries, model_factory):
    """Leave-one-year-out CV.  `model_factory()` returns a fresh
    scikit-learn classifier with sane defaults."""
    years = sorted({e["year"] for e in entries})
    preds = []
    labels = []
    per_year = {}

    for test_year in years:
        train = [e for e in entries if e["year"] != test_year]
        test  = [e for e in entries if e["year"] == test_year]

        X_train = np.array([e["features"] for e in train])
        y_train = np.array([e["won_left"] for e in train])
        X_test  = np.array([e["features"] for e in test])
        y_test  = np.array([e["won_left"] for e in test])

        model = model_factory()
        model.fit(X_train, y_train)
        p_test = model.predict_proba(X_test)[:, 1]

        preds.extend(p_test.tolist())
        labels.extend(y_test.tolist())
        per_year[test_year] = _summary(p_test, y_test)

    overall = _summary(preds, labels)
    return {"overall": overall, "per_year": per_year}


def main():
    corpus_path = DATA_DIR / "matchup_corpus.json"
    print(f"Loading {corpus_path}")
    corpus = json.loads(corpus_path.read_text())
    entries = corpus["entries"]
    print(f"  {len(entries)} entries · {len(corpus['feature_names'])} features\n")

    models = {
        "Logistic Regression":
            lambda: LogisticRegression(max_iter=1000, C=1.0),
        "Random Forest":
            lambda: RandomForestClassifier(n_estimators=200, max_depth=8,
                                           min_samples_leaf=5, random_state=42, n_jobs=-1),
        "Gradient Boosting":
            lambda: GradientBoostingClassifier(n_estimators=200, max_depth=3,
                                               learning_rate=0.05, random_state=42),
    }

    print("Running LOYO cross-validation on each model...\n")
    results = {}
    for name, factory in models.items():
        print(f"  {name}...")
        results[name] = loyo_eval(entries, factory)
        o = results[name]["overall"]
        print(f"    acc={o['accuracy']:.3%}  brier={o['brier']:.4f}  log_loss={o['log_loss']:.4f}")

    # ── Comparison vs MC engine ──────────────────────────────────────────
    mc = (json.loads((DATA_DIR / "model_metrics.json").read_text())
          .get("mc_fingerprint_backtest", {}))

    print()
    print("=" * 72)
    print("HEAD-TO-HEAD vs the MC engine (same features, same LOYO, same matches)")
    print("=" * 72)
    print(f"{'Model':<24}  {'Accuracy':>9}  {'Brier':>7}  {'Log-loss':>9}")
    print("-" * 72)
    for name, r in results.items():
        o = r["overall"]
        print(f"{name:<24}  {o['accuracy']:>9.3%}  {o['brier']:>7.4f}  {o['log_loss']:>9.4f}")
    print(f"{'MC engine (v12.1)':<24}  {mc.get('accuracy', 0):>9.3%}  "
          f"{mc.get('brier', 0):>7.4f}  {mc.get('log_loss', 0):>9.4f}")

    # Save
    out_path = DATA_DIR / "regression_baseline.json"
    out = {
        "method": "LOYO on the matchup corpus, same features as MC engine",
        "n_entries": len(entries),
        "n_features": len(corpus["feature_names"]),
        "models": {name: r["overall"] for name, r in results.items()},
        "per_year": {name: r["per_year"] for name, r in results.items()},
        "mc_reference": {
            "accuracy": mc.get("accuracy"),
            "brier":    mc.get("brier"),
            "log_loss": mc.get("log_loss"),
        },
    }
    out_path.write_text(json.dumps(out, indent=2,
                                   default=lambda v: round(v, 6) if isinstance(v, float) else v))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
