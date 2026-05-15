"""
latent_factors.py — Two-axis latent factor model for player serve / return.

Replaces the four correlated Tier 1 metrics (fspw, sspw, rpw_vs_1st, rpw_vs_2nd)
with two orthogonal latents per player-year:

    serve_S   — single latent that jointly explains fspw and sspw
    return_R  — single latent that jointly explains rpw_vs_1st and rpw_vs_2nd

Why: the four metrics are correlated proxies of the same two underlying
constructs.  Treating them as independent inputs over-parameterises the
per-point baseline and is one structural reason the engine reproduces
an Elo curve.  A single latent per axis (a) shrinks small-sample noise
by borrowing strength across the two observations, (b) regularises the
estimate via empirical-Bayes shrinkage toward the field mean.

Output: a per-year JSON file with, for each player:

    {
      "serve_z": float,                  # latent serve, z-units
      "return_z": float,                  # latent return, z-units
      "smoothed_fspw_pct": float,
      "smoothed_sspw_pct": float,
      "smoothed_rpw_vs_1st_pct": float,
      "smoothed_rpw_vs_2nd_pct": float,
      "n_eff": float,                     # reliability used for shrinkage
      "shrinkage": float                  # weight applied to raw vs prior
    }

Pooled fit constants (means, stds, factor loadings) are stored in
data/latent_factors_meta.json so the engine can reproduce the smoothing.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).parent.parent
DATA_DIR = ROOT / "data"

YEARS = [2011, 2012, 2013, 2014, 2015, 2016, 2017, 2018,
         2019, 2021, 2022, 2023, 2024]

SERVE_METRICS  = ["fspw_pct", "sspw_pct"]
RETURN_METRICS = ["rpw_vs_1st_pct", "rpw_vs_2nd_pct"]

SHRINKAGE_K = 200.0

MIN_PTS_FOR_FIT = 60


def _t1_value(fp: dict, key: str) -> Optional[float]:
    node = (fp.get("tier1") or {}).get(key)
    if isinstance(node, dict):
        return node.get("value")
    return None


def _player_n_eff(fp: dict, key: str) -> float:
    """Best available reliability proxy: per-metric n_eff if present, else
    fingerprint-level n_points (which scales linearly with sample size)."""
    node = (fp.get("tier1") or {}).get(key) or {}
    n = node.get("n_eff")
    if n is not None:
        return float(n)
    return float(fp.get("n_points") or 0)


def _load_year_fps(year: int) -> Dict[str, dict]:
    path = DATA_DIR / f"{year}_fingerprints.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text())


def _collect_observations() -> List[dict]:
    """Pool all (player, year) observations with all four metrics present."""
    rows = []
    for year in YEARS:
        fps = _load_year_fps(year)
        for player, fp in fps.items():
            vals = {k: _t1_value(fp, k) for k in SERVE_METRICS + RETURN_METRICS}
            if any(v is None for v in vals.values()):
                continue
            n_pts = float(fp.get("n_points") or 0)
            if n_pts < MIN_PTS_FOR_FIT:
                continue
            rows.append({
                "player": player,
                "year":   year,
                "n_pts":  n_pts,
                **vals,
            })
    return rows


def _fit_one_factor(values: np.ndarray, weights: np.ndarray
                    ) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """
    Fit a one-factor model to a 2-column matrix of standardised observations.

    Model:  z_i = lambda_i * f + epsilon_i,  Var(f) = 1
    Returns (loadings, residual_vars, factor_scores, factor_score_var)

    Solved by maximising weighted likelihood on the 2x2 covariance via
    a closed-form decomposition: with 2 indicators, set the loading vector
    to the principal eigenvector of the weighted covariance matrix and
    rescale so factor variance = 1.  Residual vars are the diagonal of
    Cov - lambda lambda'.
    """
    w = weights / weights.sum()
    mu = (values * w[:, None]).sum(axis=0)
    centred = values - mu
    cov = (centred * w[:, None]).T @ centred
    cov *= len(weights) / (len(weights) - 1)

    eigvals, eigvecs = np.linalg.eigh(cov)
    idx = np.argmax(eigvals)
    pc1 = eigvecs[:, idx]
    if pc1.sum() < 0:
        pc1 = -pc1
    lam = pc1 * math.sqrt(eigvals[idx])

    psi = np.maximum(np.diag(cov) - lam ** 2, 1e-4)

    # Bartlett scoring: f_hat = (lambda' Psi^-1 lambda)^-1 lambda' Psi^-1 z
    inv_psi = 1.0 / psi
    info = (lam ** 2 * inv_psi).sum()
    score_coef = (lam * inv_psi) / info
    f_hat = centred @ score_coef
    f_var = 1.0 / info  # posterior variance of factor estimate
    return lam, psi, f_hat, np.full_like(f_hat, f_var)


def _shrink(z: np.ndarray, n: np.ndarray, k: float = SHRINKAGE_K
            ) -> Tuple[np.ndarray, np.ndarray]:
    """Empirical-Bayes shrinkage toward 0 (the field mean)."""
    weight = n / (n + k)
    return z * weight, weight


def fit_and_export(out_dir: Path = DATA_DIR) -> dict:
    rows = _collect_observations()
    if not rows:
        raise RuntimeError("no observations")

    print(f"Pooled observations: {len(rows)} player-years")

    serve_obs  = np.array([[r["fspw_pct"],       r["sspw_pct"]]       for r in rows])
    return_obs = np.array([[r["rpw_vs_1st_pct"], r["rpw_vs_2nd_pct"]] for r in rows])
    weights    = np.array([r["n_pts"] for r in rows])

    serve_mu  = (serve_obs  * (weights / weights.sum())[:, None]).sum(axis=0)
    return_mu = (return_obs * (weights / weights.sum())[:, None]).sum(axis=0)
    serve_sd  = serve_obs.std(axis=0, ddof=1)
    return_sd = return_obs.std(axis=0, ddof=1)

    serve_z  = (serve_obs  - serve_mu)  / serve_sd
    return_z = (return_obs - return_mu) / return_sd

    s_lam, s_psi, s_f, _ = _fit_one_factor(serve_z, weights)
    r_lam, r_psi, r_f, _ = _fit_one_factor(return_z, weights)

    s_corr = float(np.corrcoef(serve_z[:, 0], serve_z[:, 1])[0, 1])
    r_corr = float(np.corrcoef(return_z[:, 0], return_z[:, 1])[0, 1])
    print(f"  serve  fspw↔sspw correlation        = {s_corr:+.3f}")
    print(f"  return rpw_vs_1st↔rpw_vs_2nd corr   = {r_corr:+.3f}")
    print(f"  serve  loadings  (fspw, sspw)        = ({s_lam[0]:+.3f}, {s_lam[1]:+.3f})")
    print(f"  return loadings  (rpw_v1, rpw_v2)    = ({r_lam[0]:+.3f}, {r_lam[1]:+.3f})")

    s_shrunk, s_w = _shrink(s_f, weights)
    r_shrunk, r_w = _shrink(r_f, weights)

    smoothed_serve  = serve_mu  + s_shrunk[:, None] * (s_lam * serve_sd)
    smoothed_return = return_mu + r_shrunk[:, None] * (r_lam * return_sd)

    by_year: Dict[int, Dict[str, dict]] = {y: {} for y in YEARS}
    for i, r in enumerate(rows):
        by_year[r["year"]][r["player"]] = {
            "serve_z":  round(float(s_shrunk[i]), 4),
            "return_z": round(float(r_shrunk[i]), 4),
            "smoothed_fspw_pct":       round(float(smoothed_serve[i, 0]),  2),
            "smoothed_sspw_pct":       round(float(smoothed_serve[i, 1]),  2),
            "smoothed_rpw_vs_1st_pct": round(float(smoothed_return[i, 0]), 2),
            "smoothed_rpw_vs_2nd_pct": round(float(smoothed_return[i, 1]), 2),
            "n_pts":      r["n_pts"],
            "shrinkage_serve":  round(float(s_w[i]), 3),
            "shrinkage_return": round(float(r_w[i]), 3),
        }

    for year, players in by_year.items():
        path = out_dir / f"{year}_latent_factors.json"
        path.write_text(json.dumps(players, indent=2))
        print(f"  wrote {path.name}: {len(players)} players")

    meta = {
        "method":     "Confirmatory one-factor model per axis (serve, return).",
        "shrinkage_k": SHRINKAGE_K,
        "min_pts_for_fit": MIN_PTS_FOR_FIT,
        "n_observations":  len(rows),
        "serve_mu":   serve_mu.tolist(),
        "serve_sd":   serve_sd.tolist(),
        "serve_lam":  s_lam.tolist(),
        "serve_psi":  s_psi.tolist(),
        "return_mu":  return_mu.tolist(),
        "return_sd":  return_sd.tolist(),
        "return_lam": r_lam.tolist(),
        "return_psi": r_psi.tolist(),
        "serve_internal_corr":  s_corr,
        "return_internal_corr": r_corr,
        "axes_orthogonality_corr": float(np.corrcoef(s_f, r_f)[0, 1]),
    }
    (out_dir / "latent_factors_meta.json").write_text(json.dumps(meta, indent=2))
    print(f"  wrote latent_factors_meta.json")
    print(f"  serve↔return orthogonality (raw factor scores): {meta['axes_orthogonality_corr']:+.3f}")
    return meta


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out_dir", default=str(DATA_DIR))
    args = parser.parse_args()
    fit_and_export(Path(args.out_dir))


if __name__ == "__main__":
    main()
