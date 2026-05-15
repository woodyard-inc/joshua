# What Wins on Grass — Project Briefing

## What this is

A portfolio project by Joshua Woodyard: a static single-page site that lets users explore Wimbledon men's draw player fingerprints (2011-2024) and run head-to-head Monte Carlo match simulations in the browser. The site is served via `python3 -m http.server 3400 --directory joshua/wimbledon` from the parent directory.

## Architecture

```
joshua/wimbledon/
├── index.html              # Single page, all sections
├── app.js                  # 3000-line frontend: player profiles, leaderboard, comparison UI
├── mc_worker.js            # Web Worker: browser-side Monte Carlo engine (must mirror Python)
├── styles.css              # All styles
├── src/
│   ├── fingerprint.py      # Builds player fingerprints from point-by-point data
│   ├── monte_carlo_phased.py   # Python MC engine (backtest reference, must match mc_worker.js)
│   ├── backtest_fingerprints.py # Leave-one-year-out backtesting harness
│   ├── build_grass_profiles.py  # ATP grass-court match aggregates
│   ├── elo.py              # Kovalchik-style adaptive surface Elo
│   ├── ablation_study.py   # Feature ablation testing framework
│   └── ... (25+ pipeline scripts)
├── data/
│   ├── raw/                # Jeff Sackmann's Slam PBP CSVs (2011-2024)
│   │   └── {year}-wimbledon-{points,matches}.csv
│   ├── processed/          # Pipeline output
│   │   └── {year}_{fingerprints,grass_profiles,archetypes}.json
│   ├── {year}_fingerprints.json  # Published fingerprints (copied to site root)
│   ├── model_metrics.json  # Current backtest results
│   └── curated_matchups.json
```

## Data pipeline

1. **Raw data**: Jeff Sackmann's open Slam point-by-point dataset. ~48K points per year. Columns include serve speed, direction, rally count, distance run, serve depth, etc.
2. **Elo** (`elo.py`): Adaptive surface Elo from all ATP grass matches (not just Wimbledon).
3. **Fingerprints** (`fingerprint.py`): Per-player weighted stats using recency decay + opponent quality weighting. Two tiers:
   - **Tier 1**: fsp%, fspw%, sspw%, rpw%, rpw_vs_1st%, rpw_vs_2nd%, sgw%, rgw% — all with Beta-Binomial credible intervals
   - **Tier 2**: serve_entropy, rally_win_curve, clutch_differential, spci, rally_volatility, df_pressure_delta, tiebreak_differential, set_transition_delta, attrition_slope, hold_after_break, serve_speed_courage, serve_depth_entropy, distance_run_efficiency, etc.
4. **Grass profiles** (`build_grass_profiles.py`): ATP grass-court match aggregates merged into fingerprints to fill coverage gaps.

## The Monte Carlo engine

Both `monte_carlo_phased.py` (Python, used for backtesting) and `mc_worker.js` (browser, used live) implement the same three-phase point simulation. **They must stay in sync.**

### Three-phase point model

**Phase 1 — SERVE**: Draw 1st serve in/out from `fsp_pct`. If out, check for double fault (conditional DF rate with pressure modifier). Result: 1st or 2nd serve context.

**Phase 2 — RALLY**: Sample rally length band (1-3, 4-6, 7-9, 10+ shots) from a blended distribution (grass prior + server curve + returner curve, reweighted by serve type). Compute baseline `p` from serve-type-specific win rate (`fspw` for 1st serve, `sspw` for 2nd), adjusted by returner's serve-type-specific return quality (`rpw_vs_1st` or `rpw_vs_2nd`).

**Phase 3 — MODIFY**: Apply contextual modifiers to `p`:
- **Active** (proven net-positive in ablation):
  - `serveEntropy` — serve direction unpredictability
  - `spci` — serve performance composite index under pressure
  - `clutch` — returner clutch differential at break/deuce points
  - `rgw` — return games won (most valuable single modifier, r=0.314)
  - `tiebreak` — tiebreak differential
  - `holdAfterBreak` — post-break hold rate
  - `attrition` — physical decay per set
  - `rallyVolDirect` — rally volatility as direct advantage (r=+0.233 on grass)
  - `dfPressure` — DF rate change at break points (neutral but kept)
- **Disabled** (in `PRODUCTION_ABLATED`, proved net-harmful or neutral):
  - `rallyCurve`, `bpConversion`, `rallyVolatility` (direction-based), `courtSideRally`, `courtSideServe`, `firstServePressure`, `momentum`, `setTransition`, `distanceRunEff`, `serveDepthEntropy`, `serveSpeedCourage`

### Calibration

Raw MC probabilities are overconfident (range 0-100%). Post-processing:
1. **Platt sigmoid**: `p_cal = sigmoid(0.35 * logit(p_raw))` — compresses toward 50%
2. **Floor/ceiling**: clamp to [0.20, 0.80]

These were grid-searched with leave-one-year-out CV on 908 matches.

## Backtesting methodology

`backtest_fingerprints.py` runs leave-one-year-out: predict year Y using fingerprints built from year Y-1 data only. No data leakage. Tests 2014-2024 (skip 2020, no tournament). 908 total matches where both players have prior-year fingerprints.

### Current performance (v10, as of 2025-05-15)

| Metric | Value |
|--------|-------|
| Accuracy | 67.6% |
| Brier score | 0.2098 |
| Brier skill | +0.161 (vs naive 0.25) |
| Log loss | 0.6088 |
| Matches | 908 |

### For comparison — ML baselines (trained on 1991-2018 match-level features)

| Model | Accuracy | Brier | AUC |
|-------|----------|-------|-----|
| Logistic Regression | 70.7% | 0.192 | 0.783 |
| Random Forest | 70.9% | 0.189 | 0.786 |
| Gradient Boosting | 71.3% | 0.194 | 0.775 |

**Important**: The ML baselines use different data (match-level features, not point-by-point) and a different train/test split, so they're not directly comparable. But they suggest the ceiling for this dataset is somewhere around 70-71% accuracy / 0.19 Brier.

### ELO-only baseline

A simple Elo-difference logistic model achieves Brier 0.2086 on the same 908 matches — essentially matching the full MC engine (0.2098). This is the central puzzle: the fingerprint-based point simulation currently adds minimal value over a simple rating.

## What has been tried and what didn't work

### Calibration experiments (all rejected on full 908-match dataset)
- Platt A from 1.5 to 5.0 — raw MC is too overconfident, A=0.35 is correct
- ELO blend (20-50% weight) — improves on 592-match subset but degrades full dataset, especially across 2019→2021 COVID gap where stale Elo hurts
- Floor/ceiling at 0.10/0.90 — extremes aren't discriminative enough
- Isotonic regression calibration — overfit on this sample size

### Feature/modifier experiments (ablation tested)
- 16 modifiers tested individually. 7 were net-harmful and disabled. Of the new tier2 features tested (rallyVolDirect, distanceRunEff, serveDepthEntropy, serveSpeedCourage), only rallyVolDirect improved Brier.
- The ablation framework is in `monte_carlo_phased.py`: `PRODUCTION_ABLATED` frozenset controls which modifiers fire. `set_ablation()` swaps configs for testing.

### Key insight: the bottleneck is NOT calibration

The improvement path is not post-hoc calibration (Platt, Elo blend, bounds). All tested, all worse or neutral on the full dataset. The bottleneck is **raw MC discrimination at the point level** — the `simulatePoint()` function needs to produce raw probabilities that better separate winners from losers before any calibration is applied.

## Running the backtest

```bash
cd joshua/wimbledon
python3 src/backtest_fingerprints.py --n_sims 2000 --engine phased --out data/model_metrics.json
```

At 2000 sims this takes ~12 minutes on an M-series Mac. 1000 sims takes ~6 minutes and is adequate for A/B comparisons.

## Running ablation tests

```bash
cd joshua/wimbledon
python3 src/ablation_study.py  # runs full ablation matrix
```

Or use the ablation API in Python:
```python
from monte_carlo_phased import set_ablation, PRODUCTION_ABLATED
set_ablation(PRODUCTION_ABLATED - {"someFeature"})  # enable a feature
set_ablation(PRODUCTION_ABLATED | {"someFeature"})  # disable a feature
```

## Dev server

Already running on port 3400: `python3 -m http.server 3400 --directory joshua/wimbledon`

## Key files to read first

1. `src/monte_carlo_phased.py` — the engine (704 lines)
2. `mc_worker.js` — browser mirror (1108 lines, must match Python)
3. `src/backtest_fingerprints.py` — backtesting harness (325 lines)
4. `src/fingerprint.py` — how fingerprints are built (1506 lines)
5. `data/model_metrics.json` — current results

## Open questions for a fresh perspective

1. **Why does Elo match the MC engine?** The fingerprints contain rich point-level data (serve speed, rally patterns, pressure behavior) but the simulation doesn't extract more signal than a simple rating. Is the point model too noisy? Are the modifiers too small? Is the phase structure wrong?

2. **Is the three-phase model the right abstraction?** Real tennis points have richer dynamics — serve+1 patterns, approach shots, net play. The current model reduces everything to a single `p` that gets nudged by ±1-2pp modifiers.

3. **Sample size vs signal**: Many tier2 metrics have low confidence (5-30 observations). The confidence gating helps but may be too conservative or too loose.

4. **Rally distribution**: The rally length sampling drives which phase-2 baseline is used, but the per-band win rates (`rallyCurve`) were disabled as harmful. Is the rally sampling itself adding noise?

5. **Year-to-year fingerprint stability**: Using Y-1 fingerprints to predict Y assumes player style is stable. How much do fingerprints actually change year-to-year?

6. **The 2021 problem**: 2019→2021 predictions are the worst (59.5% accuracy) — COVID gap means fingerprints are 2 years stale. Could multi-year fingerprint blending help?

7. **Coverage**: Only 908 of ~1270 matches are testable (both players need prior-year fingerprints). The 362 untested matches are disproportionately qualifiers/wildcards. Does this bias the accuracy estimate?
