"""
Master pipeline runner. Executes all steps in sequence.

Usage:
    # Download data and run full pipeline:
    python run_pipeline.py --download

    # Run pipeline on existing raw data:
    python run_pipeline.py
"""
import argparse
import subprocess
import sys


STEPS = [
    ("Data prep",          ["python", "src/data_prep.py", "--raw_dir", "data/raw",
                             "--out_wimbledon", "data/processed/wimbledon_matches.csv",
                             "--out_grass",     "data/processed/all_grass_matches.csv"]),
    ("Feature engineering", ["python", "src/feature_engineering.py",
                              "--wimbledon", "data/processed/wimbledon_matches.csv",
                              "--grass",     "data/processed/all_grass_matches.csv",
                              "--out", "data/processed/feature_matrix.csv"]),
    ("Train models",       ["python", "src/train_models.py",
                             "--features", "data/processed/feature_matrix.csv",
                             "--out_dir", "outputs"]),
    ("Build indices",      ["python", "src/build_indices.py",
                             "--grass", "data/processed/all_grass_matches.csv",
                             "--out", "outputs/player_season_indices.csv"]),
    ("Export outputs",     ["python", "src/export_outputs.py",
                             "--outputs_dir", "outputs",
                             "--docs_data", "data"]),
    # ── Fingerprint pipeline (new) ─────────────────────────────────────────
    ("Elo ratings",        ["python", "src/elo.py",
                             "--all_grass", "data/processed/all_grass_matches.csv",
                             "--out",       "data/processed/elo_ratings.json"]),
    ("Fingerprints",       ["python", "src/fingerprint.py", "--year", "all"]),
    # ── Player profiles (point-by-point showcase cards) ────────────────────
    ("Player profiles",    ["python", "src/build_player_profiles.py", "--year", "all"]),
]


def run_step(name, cmd, download=False):
    if name == "Data prep" and download:
        cmd = cmd + ["--download"]
    print(f"\n{'='*60}")
    print(f"  STEP: {name}")
    print(f"{'='*60}")
    result = subprocess.run(cmd, capture_output=False)
    if result.returncode != 0:
        print(f"\nStep '{name}' failed (exit {result.returncode}).", file=sys.stderr)
        sys.exit(result.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--download", action="store_true",
                        help="Download raw ATP data before running pipeline")
    parser.add_argument("--step", type=str, default=None,
                        help="Run only this step by name (partial match)")
    args = parser.parse_args()

    steps = STEPS
    if args.step:
        steps = [(n, c) for n, c in STEPS if args.step.lower() in n.lower()]
        if not steps:
            print(f"No step matching '{args.step}'. Available: {[n for n,_ in STEPS]}")
            sys.exit(1)

    for name, cmd in steps:
        run_step(name, cmd, download=args.download)

    print("\nPipeline complete. Check outputs/ and data/.")


if __name__ == "__main__":
    main()
