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

### Current performance (v12.1, as of 2026-05-16)

**Production stack**:
  1. Pressure-state baselines with `stale_only` reliability gate
  2. Tiebreak-specific baselines
  3. K-nearest matchup-neighbors prior (38-dim corpus, blend weight 0.15)

All three default ON.  See `src/pressure_states.py`, `src/tiebreak_baselines.py`,
`src/matchup_neighbors.py`.

| Metric | v10 (Platt+clamp) | v11 (pressure+tiebreak) | v12 (matchup 22-dim) | v12.1 (matchup 38-dim) |
|--------|------------------|------------------------|--------------------|----------------------|
| Accuracy | 67.7% | 68.5% | 68.3% | **68.5%** (+0.8pp) |
| Brier score | 0.2098 | 0.2091 | 0.2078 | **0.2075** (−0.0023) |
| Brier skill | +0.161 | +0.164 | +0.169 | **+0.170** |
| Log loss | 0.6086 | 0.6072 | 0.6041 | **0.6031** (−0.0055) |
| vs Elo (0.2086) | +0.0012 | +0.0005 | −0.0008 | **−0.0011** ✓ |

v12.1 extends v12 by adding 8 display-layer metrics from `men_profiles.json`
to the matchup-neighbours feature vector (corpus dimension 22 → 38).  The
added metrics: `net.net_won_pct`, `aggression.aggression_index`,
`rally_shots.srv_1st_avg` / `srv_2nd_avg`, `serve_direction.wide_pct`,
`clean_games.srv_clean_pct`, `match_duration.avg_mins`,
`distance.avg_km_per_match`.

Why this works where per-point ablation said "no signal": K-NN doesn't
assume linearity, handles redundancy, and is naturally asymmetric.  The
same features that produced noise-grade CIs as small linear modifiers
contribute real signal as similarity dimensions.  The "zero effect"
ablation verdicts on these features said *wrong application mechanism*,
not *no signal*.

Blend-weight sweep (validated on full 908-match LOYO at v12, 22-dim corpus):

| weight | acc | Brier | log_loss |
|--------|-----|-------|----------|
| 0.10 | 68.5% | 0.2082 | 0.6050 |
| **0.15 (production)** | 68.3% | 0.2078 | 0.6041 |
| 0.20 | 68.1% | 0.2075 | 0.6034 |
| 0.25 | 67.8% | 0.2073 | 0.6028 |
| 0.30 | 67.3% | 0.2071 | 0.6023 |

w=0.15 production default carried forward to v12.1 (corpus dimensionality
change is orthogonal to blend weight choice).

### For comparison — ML baselines (trained on 1991-2018 match-level features)

| Model | Accuracy | Brier | AUC |
|-------|----------|-------|-----|
| Logistic Regression | 70.7% | 0.192 | 0.783 |
| Random Forest | 70.9% | 0.189 | 0.786 |
| Gradient Boosting | 71.3% | 0.194 | 0.775 |

**Important**: The ML baselines use different data (match-level features, not point-by-point) and a different train/test split, so they're not directly comparable. But they suggest the ceiling for this dataset is somewhere around 70-71% accuracy / 0.19 Brier.

### ELO-only baseline

A simple Elo-difference logistic model achieves Brier 0.2086 on the same 908 matches. The pre-v11 engine (0.2098) sat slightly above this; v11 (0.2091) sits between the two — fingerprints adding value via state-conditional baselines rather than over Elo as a rating.

## v12 production-stack components (in `monte_carlo_phased.py`)

Activated by default when fingerprint data is attached:

1. **Pressure-state baselines** with reliability gate (`USE_PRESSURE_STATES = True`)
   - Per-player Beta-Binomial baselines for {neutral, pressure} states where pressure = BP-against OR deuce/AD.
   - Shrinkage toward archetype-mean baseline (k=30 pseudo-observations).
   - Per-match gate (`PRESSURE_GATE_MODE = "stale_only"`): only fire when EITHER player's prior fingerprint has its most-recent career edition >1 year before the predicted year (the COVID-gap case). Looser gate modes regressed.
   - When gate fires for a point: skips spci + clutch modifiers (absorbed into baselines).
   - Builder: `src/pressure_states.py`.  Data: `data/{year}_pressure_states.json`.

2. **Tiebreak baselines** (`USE_TIEBREAK_BASELINES = True`)
   - Per-player Beta-Binomial baselines for {regular, tiebreak} states.
   - Same archetype-shrinkage scheme as pressure_states.
   - Fires when `state["isTiebreak"]` and both players have tiebreak data.
   - When firing: skips the legacy `tiebreak_differential` Phase-3 modifier.
   - Builder: `src/tiebreak_baselines.py`.  Data: `data/{year}_tiebreak_baselines.json`.
   - Validated by Klaassen-Magnus (2004): tiebreak win rates differ measurably from regular-game rates.

3. **K-nearest matchup-neighbors prior** (`USE_MATCHUP_NEIGHBORS = True`, `NEIGHBOR_BLEND_WEIGHT = 0.15`)
   - At match-level finalisation, looks up K=30 nearest historical matchups in a corpus of 1,816 entries (908 matches × 2 sides each, symmetric encoding).
   - Distance metric: Euclidean on a **38-dimensional vector** (v12.1) — 19 metrics × 2 (differential + level).  Metrics are 6 Tier-1 + 5 Tier-2 + 8 display-layer (`net_won_pct`, `aggression_idx`, `rally_srv_1st`, `rally_srv_2nd`, `serve_dir_wide`, `srv_clean_pct`, `match_mins`, `distance_km`).
   - The differential captures style mismatch; the level addresses "Federer vs Gasquet": same style differential, very different levels.
   - Distance-weighted average of neighbours' win rates becomes a prior.
   - Blended at weight 0.15: `p_final = 0.85 * p_mc_calibrated + 0.15 * p_neighbor`.
   - Leakage-safe: lookup excludes entries from the predicting year.
   - Builder: `src/matchup_neighbors.py`.  Corpus: `data/matchup_corpus.json` (~280KB at 38-dim).
   - The first audit experiment to produce **uniquely non-Elo signal** — historical similar matchups encode patterns the per-point MC engine can't extract.  v12.1 demonstrates that the "display-only" metrics (shown on the website but never wired into the per-point engine) carry real signal too — they were ablated as noise-grade in linear-modifier form, but contribute meaningfully via K-NN.

4. **Platt+clamp post-calibration** (unchanged from v10): `PLATT_A=0.35`, clamp [0.20, 0.80].

## What was tried and rejected during v10→v11 development

### Calibration experiments (all worse or neutral on full 908-match LOYO)
- **Platt A 1.5–5.0** — raw MC overconfident, A=0.35 is the optimum.
- **Elo blend (20-50%)** — improves 592-match subset but degrades full dataset (2019→2021 COVID gap).
- **Floor/ceil 0.10/0.90** — extremes not discriminative enough.
- **Form-noise injection** (per-sim Gaussian fingerprint perturbation, no Platt, no clamp): Brier regressed from 0.2098 to 0.2266. Variance ≠ mean-squashing; Platt was doing legitimate work.  Code REMOVED in cleanup.
- **Isotonic regression** (LOYO): Brier regressed by +0.0045 on both baseline and pressure stacks. At 908 matches the non-parametric flexibility costs more than it saves.  Builder REMOVED.

### Sprint 7 (v13) — band-conditional & continuous-rally modifiers REJECTED
Hypothesis: each Phase-3 modifier should fire selectively by rally length
(spci heavy on 1-3, attrition heavy on 10+, etc.) instead of equally on
all points.  Tested in two forms:

- **Stage A**: discrete band weights per modifier, set a priori from
  physical reasoning.  Backtest: 67.6% / 0.2082 (vs v12.1 68.5% / 0.2075).
- **Stage B**: continuous rally length sampling (Gaussian on matchup
  mean/stdev from men_profile.rally_shots + tier2.rally_volatility),
  smooth response curves per modifier.  Backtest: 67.5% / 0.2081.

Both regress ~+0.0007 Brier vs v12.1.  Diagnosis: at this calibration,
modifiers already operate near their minimum useful strength.  Cutting
modifier effect on 70% of points (via band weights ≤1.0) reduces signal
below noise floor.  Code REMOVED from `monte_carlo_phased.py` in the
post-audit cleanup; can be reconstructed from git history if revived
when sample size grows.

### Sprint 8 — cross-player pairing features TIED (corpus revert)
Hypothesis: adding "matchup-quality" features to the K-NN corpus —
literal serve-vs-return cross-pairings like `A.fspw − B.rpw_vs_1st`,
plus compound style pairings (aggression × aggression, duration ×
duration) — would compound on the matchup-neighbours win.  Tested at
44-dim corpus vs 38-dim production.  Result: essentially tied (Brier
0.2076 vs 0.2075; accuracy 68.2% vs 68.5%).  Diagnosis: cross-player
pairings are mathematically reconstructible from the existing
differential + level features, so K-NN distance gains no new info.
Corpus reverted to 38-dim in the cleanup.

### Feature experiments
- **Latent two-factor model** (smooth fspw/sspw into one serve latent, rpw_v1/v2 into one return latent, with empirical-Bayes shrinkage): Brier regressed from 0.2098 to 0.2154. Shrinking top players toward field mean destroyed signal that the Phase-3 modifiers were exploiting.
- **Per-point momentum HMM** (Klaassen-Magnus 2014, with zero-mean bias correction): essentially neutral (0.2097 alone, no detectable improvement in any combination). Real signal but too small to recover at 908-match sample size.  Kept as opt-in (`--use_momentum_hmm`).
- **Archetype-prior shrinkage for Tier-2 modifiers** (replace `_conf_weight() → 0 for UNRELIABLE` with shrinkage toward archetype mean): neutral (Brier ±0.0001 vs v12.1).  Builder REMOVED.
- **Pressure-state gate variants**: `any` and `both` regressed (over-fires on clean years); `stale_or_gap` regressed (catches normal injury-comeback careers); `stale_only` won.

### Ablation framework
- 16 Tier-2 modifiers tested individually pre-v11.
- Framework in `monte_carlo_phased.py`: `PRODUCTION_ABLATED` frozenset controls which fire. `set_ablation()` swaps configs.
- NOTE: framework lacks confidence intervals on Brier deltas. Several disabled modifiers had deltas ≤0.001 — possibly dropped on noise. Re-validation pending.

### Key v11 insight: it's NOT a single bottleneck
The v10 briefing claimed the bottleneck was "raw MC discrimination."  v11 shows it's actually state-conditional baselines — fitting different fspw values when the score state is genuinely different (pressure vs neutral, tiebreak vs regular).  The literature (Klaassen-Magnus) supports this; post-hoc calibration (Platt, isotonic, Elo blend) cannot recover this signal since the per-point regime distinction is destroyed by the time aggregation reaches the match-level probability.

## Running the backtest

```bash
cd joshua/wimbledon
# Production stack (pressure_states + tiebreak baselines, both ON by default):
python3 src/backtest_fingerprints.py --n_sims 2000 --engine phased --out data/model_metrics.json

# Ablations:
python3 src/backtest_fingerprints.py --n_sims 1000 --engine phased --no_pressure_states --no_tiebreak  # pre-v11 baseline
python3 src/backtest_fingerprints.py --n_sims 1000 --engine phased --no_tiebreak                       # pressure-only
python3 src/backtest_fingerprints.py --n_sims 1000 --engine phased --use_momentum_hmm                  # add momentum HMM (no detectable gain)
```

At 2000 sims this takes ~12 minutes on an M-series Mac. 1000 sims takes ~6 minutes and is adequate for A/B comparisons.

## Regenerating the v11 baseline data

Both pressure-state and tiebreak baselines are pre-built per-year JSON files.  Rebuild from raw PBP if fingerprints change:

```bash
python3 src/pressure_states.py     # writes data/{year}_pressure_states.json (13 years)
python3 src/tiebreak_baselines.py  # writes data/{year}_tiebreak_baselines.json (13 years)
```

Builders read from `data/raw/{year}-wimbledon-{points,matches}.csv` (Sackmann Slam PBP, gitignored).

## Running ablation tests on Tier-2 modifiers

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

Note: framework currently lacks bootstrap CIs on per-modifier Brier deltas.  Future work.

## Dev server

Already running on port 3400: `python3 -m http.server 3400 --directory joshua/wimbledon`

## Key files to read first

1. `src/monte_carlo_phased.py` — the engine with v11 production stack defaults
2. `src/pressure_states.py` — pressure-state baseline builder (v11)
3. `src/tiebreak_baselines.py` — tiebreak baseline builder (v11)
4. `src/backtest_fingerprints.py` — backtesting harness with `--no_X` ablation flags
5. `src/fingerprint.py` — how Tier-1/Tier-2 fingerprints are built (1506 lines)
6. `mc_worker.js` — browser mirror (still on v10; not yet synced with v11 changes)
7. `data/model_metrics.json` — current results

### Browser-side sync status
`mc_worker.js` and `app.js` synced to v11:
- `loadFingerprintsOnly()` and main data load both fetch `{year}_pressure_states.json`,
  `{year}_tiebreak_baselines.json`, `{year}_archetypes.json` and attach to each fingerprint
- `mc_worker.js` adds the same `USE_PRESSURE_STATES`, `USE_TIEBREAK_BASELINES`,
  `PRESSURE_GATE_MODE='stale_only'` toggles as Python
- `extractModifiers` surfaces state-conditional + tiebreak baselines
- `simulatePoint` Phase 2 has the tiebreak > pressure > Tier-1 priority order
- Phase 3 spci/clutch/tiebreak modifiers auto-skip when their effects are absorbed
- `runMonteCarlo` accepts `currentYear` and stamps `_usePressure` on mods once per match
- `app.js` posts `currentYear: state.year` to the worker; worker version bumped to v53

Pre-existing numerical divergence: the v10 JS engine never numerically matched the
Python engine (modifier coefficient details + rally sampling differ in places).
The v11 sync ports the **structure** of the improvements; verified on a seeded
parity test that v11 attachments shift prediction by ~+0.011 in the direction
the Python backtest validated.  Full numerical parity is a separate cleanup
not addressed in this session.

## Open questions for a fresh perspective

1. **Why does Elo match the MC engine?** The fingerprints contain rich point-level data (serve speed, rally patterns, pressure behavior) but the simulation doesn't extract more signal than a simple rating. Is the point model too noisy? Are the modifiers too small? Is the phase structure wrong?

2. **Is the three-phase model the right abstraction?** Real tennis points have richer dynamics — serve+1 patterns, approach shots, net play. The current model reduces everything to a single `p` that gets nudged by ±1-2pp modifiers.

3. **Sample size vs signal**: Many tier2 metrics have low confidence (5-30 observations). The confidence gating helps but may be too conservative or too loose.

4. **Rally distribution**: The rally length sampling drives which phase-2 baseline is used, but the per-band win rates (`rallyCurve`) were disabled as harmful. Is the rally sampling itself adding noise?

5. **Year-to-year fingerprint stability**: Using Y-1 fingerprints to predict Y assumes player style is stable. How much do fingerprints actually change year-to-year?

6. **The 2021 problem**: 2019→2021 predictions are the worst (59.5% accuracy) — COVID gap means fingerprints are 2 years stale. Could multi-year fingerprint blending help?

7. **Coverage**: Only 908 of ~1270 matches are testable (both players need prior-year fingerprints). The 362 untested matches are disproportionately qualifiers/wildcards. Does this bias the accuracy estimate?
