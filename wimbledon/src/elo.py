"""
Surface-specific Elo rating system for tennis players.

Uses:
  - Adaptive K-factor: K(N) = 250 / (N + 5)^0.4   (Kovalchik-style)
  - Adjusted Elo:      R_adj = alpha * R_surface + (1-alpha) * R_overall
  - Quality weight:    q = R_adj_opponent / mean(R_active)

Both an overall Elo (all surfaces) and a surface-specific Elo are maintained
per player. When a match is played on the target surface (default: grass) the
surface Elo is also updated; otherwise only the overall Elo moves.

PER-YEAR SNAPSHOTS:
  process_matches() can capture each player's rating state immediately
  before a list of cutoff dates (typically the start of each year's
  Wimbledon edition).  This avoids the look-ahead leakage that would
  otherwise occur if a fingerprint built for predicting Wimbledon Y
  used a player's all-time-final Elo to weight prior-year matches.

Input:   data/processed/all_atp_matches.csv     (full ATP feed; from data_prep.py)
                                                 — required so R_overall properly
                                                   reflects all-surface form
                                                   (R_surface only updates on grass).
         data/processed/all_grass_matches.csv   (legacy fallback; produces
                                                 R_overall == R_surface.)
Output:  data/processed/elo_ratings.json        (id → latest snapshot
                                                 + per-year snapshots block)

Usage:
    python src/elo.py
    python src/elo.py --out data/processed/elo_ratings.json
    python src/elo.py --all_atp data/processed/all_atp_matches.csv
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd


# ── constants ──────────────────────────────────────────────────────────────

DEFAULT_RATING   = 1500.0
ALPHA_GRASS      = 0.60    # blend: 60% surface Elo + 40% overall
MIN_ACTIVE_MATCHES = 5     # minimum grass matches to count as "active"
QUALITY_CLAMP    = (0.5, 2.0)   # min/max quality weight


# ── Elo maths ──────────────────────────────────────────────────────────────

def k_factor(n: int) -> float:
    """
    Adaptive K-factor (Kovalchik 2016):
        K(N) = 250 / (N + 5)^0.4
    Fast calibration early; slower updates for established players.
    """
    return 250.0 / ((n + 5) ** 0.4)


def expected_win(ra: float, rb: float) -> float:
    """Standard Elo expected win probability for player A vs player B."""
    return 1.0 / (1.0 + 10.0 ** ((rb - ra) / 400.0))


# ── per-player data ────────────────────────────────────────────────────────

class PlayerRecord:
    """Mutable Elo state for a single player."""
    __slots__ = ("name", "n_overall", "n_surface", "R_overall", "R_surface")

    def __init__(self, name: str):
        self.name      = name
        self.n_overall = 0
        self.n_surface = 0
        self.R_overall = DEFAULT_RATING
        self.R_surface = DEFAULT_RATING

    def adjusted(self, alpha: float = ALPHA_GRASS) -> float:
        """Blended surface-adjusted rating."""
        return alpha * self.R_surface + (1.0 - alpha) * self.R_overall

    def snapshot(self, alpha: float = ALPHA_GRASS) -> dict:
        return {
            "name":       self.name,
            "n_overall":  self.n_overall,
            "n_surface":  self.n_surface,
            "R_overall":  round(self.R_overall, 1),
            "R_surface":  round(self.R_surface, 1),
            "R_adjusted": round(self.adjusted(alpha), 1),
        }


# ── main engine ────────────────────────────────────────────────────────────

class GrassElo:
    """
    Maintains per-player overall + surface Elo ratings, updated
    chronologically from a match DataFrame.

    Quick-start
    -----------
    elo = GrassElo()
    elo.process_matches(all_grass_df)          # builds Elo history
    elo.save("data/processed/elo_ratings.json")

    # Look up a player snapshot at any time:
    snap = elo.get_snapshot_by_name("Andy Murray")

    # Get a quality weight for fingerprint weighting:
    mean_r = elo.mean_active_rating()
    qw = elo.quality_weight_by_name("Marin Cilic", mean_active=mean_r)
    """

    def __init__(self):
        # keyed by player_id (int from Sackmann data)
        self._players:     Dict[int, PlayerRecord] = {}
        self._id_to_name:  Dict[int, str]          = {}
        self._name_to_id:  Dict[str, int]          = {}
        # chronological match log for audit / replay
        self._history:     List[dict]              = []
        # Snapshots taken just before each "cutoff date" passed to
        # process_matches (typically the start of each year's Wimbledon).
        # Structure: {year_or_label: {player_id: snapshot_dict}}
        self._year_snapshots: Dict = {}

    # ── internals ──────────────────────────────────────────────────────────

    def _get_or_create(self, player_id: int, name: str) -> PlayerRecord:
        if player_id not in self._players:
            rec = PlayerRecord(name)
            self._players[player_id] = rec
            self._id_to_name[player_id] = name
            self._name_to_id[name] = player_id
        return self._players[player_id]

    def _update_pair(self, winner: PlayerRecord, loser: PlayerRecord,
                     on_surface: bool) -> None:
        """Update Elo ratings for one match outcome."""
        # Overall Elo (every match)
        ew = expected_win(winner.R_overall, loser.R_overall)
        winner.R_overall += k_factor(winner.n_overall) * (1.0 - ew)
        loser.R_overall  += k_factor(loser.n_overall)  * (0.0 - (1.0 - ew))
        winner.n_overall += 1
        loser.n_overall  += 1

        # Surface Elo (only when match is on the target surface)
        if on_surface:
            es = expected_win(winner.R_surface, loser.R_surface)
            winner.R_surface += k_factor(winner.n_surface) * (1.0 - es)
            loser.R_surface  += k_factor(loser.n_surface)  * (0.0 - (1.0 - es))
            winner.n_surface += 1
            loser.n_surface  += 1

    # ── public API ─────────────────────────────────────────────────────────

    def process_matches(self, df: pd.DataFrame,
                        target_surface: str = "grass",
                        snapshot_dates: Optional[Dict] = None) -> None:
        """
        Feed all matches in chronological order.

        Required columns:
            winner_id, winner_name, loser_id, loser_name,
            tourney_date, surface

        snapshot_dates: optional {label: date_string_or_Timestamp}.
            For each label, capture every player's current rating just
            BEFORE we process any match on or after that date.  Labels
            are typically year ints (e.g., {2017: "2017-07-03"} for the
            start of Wimbledon 2017).
        """
        df = df.sort_values("tourney_date").reset_index(drop=True)
        surf_lower = target_surface.lower()

        # Pre-sort cutoffs by date so we can pop them as we cross them.
        # Each entry: (cutoff_ts, label).
        cutoffs: List = []
        if snapshot_dates:
            for label, dt in snapshot_dates.items():
                cutoffs.append((pd.Timestamp(dt), label))
            cutoffs.sort(key=lambda x: x[0])

        for _, row in df.iterrows():
            match_dt = pd.Timestamp(row["tourney_date"])

            # Capture snapshots for any cutoff that this match has reached.
            while cutoffs and match_dt >= cutoffs[0][0]:
                _, label = cutoffs.pop(0)
                self._year_snapshots[label] = {
                    pid: rec.snapshot() for pid, rec in self._players.items()
                }

            wid  = int(row["winner_id"])
            lid  = int(row["loser_id"])
            surf = str(row.get("surface", "")).lower()

            w_rec = self._get_or_create(wid, str(row["winner_name"]))
            l_rec = self._get_or_create(lid, str(row["loser_name"]))

            # Snapshot BEFORE the update (pre-match ratings)
            w_pre = round(w_rec.adjusted(), 1)
            l_pre = round(l_rec.adjusted(), 1)

            on_target = (surf == surf_lower)
            self._update_pair(w_rec, l_rec, on_surface=on_target)

            self._history.append({
                "date":         str(row["tourney_date"])[:10],
                "surface":      surf,
                "winner_id":    wid,
                "winner_name":  w_rec.name,
                "loser_id":     lid,
                "loser_name":   l_rec.name,
                "w_pre_adj":    w_pre,
                "l_pre_adj":    l_pre,
            })

        # Capture any cutoffs that fell after the last match (defensive).
        for _, label in cutoffs:
            self._year_snapshots[label] = {
                pid: rec.snapshot() for pid, rec in self._players.items()
            }

    # ── rating lookups ─────────────────────────────────────────────────────

    def adjusted_rating(self, player_id: int,
                        alpha: float = ALPHA_GRASS) -> Optional[float]:
        rec = self._players.get(player_id)
        return rec.adjusted(alpha) if rec else None

    def adjusted_rating_by_name(self, name: str,
                                alpha: float = ALPHA_GRASS) -> Optional[float]:
        pid = self._name_to_id.get(name)
        return self.adjusted_rating(pid, alpha) if pid is not None else None

    def mean_active_rating(self, min_matches: int = MIN_ACTIVE_MATCHES,
                           alpha: float = ALPHA_GRASS) -> float:
        """
        Mean adjusted Elo of players with ≥ min_matches on the surface.
        Used as the denominator for quality weights.
        """
        ratings = [
            rec.adjusted(alpha)
            for rec in self._players.values()
            if rec.n_surface >= min_matches
        ]
        return sum(ratings) / len(ratings) if ratings else DEFAULT_RATING

    def quality_weight(self, player_id: int,
                       mean_active: Optional[float] = None,
                       alpha: float = ALPHA_GRASS) -> float:
        """
        Opponent-quality weight for match weighting:
            q = R_adj_opponent / mean(R_active)
        Clamped to QUALITY_CLAMP so fringe/elite don't dominate.
        """
        if mean_active is None:
            mean_active = self.mean_active_rating(alpha=alpha)
        r_adj = self.adjusted_rating(player_id, alpha)
        if r_adj is None or mean_active == 0:
            return 1.0
        raw = r_adj / mean_active
        lo, hi = QUALITY_CLAMP
        return max(lo, min(hi, raw))

    def quality_weight_by_name(self, name: str,
                               mean_active: Optional[float] = None,
                               alpha: float = ALPHA_GRASS) -> float:
        pid = self._name_to_id.get(name)
        if pid is None:
            return 1.0   # unknown player → neutral weight
        return self.quality_weight(pid, mean_active, alpha)

    # ── snapshots ──────────────────────────────────────────────────────────

    def get_snapshot(self, player_id: int,
                     alpha: float = ALPHA_GRASS) -> Optional[dict]:
        rec = self._players.get(player_id)
        return rec.snapshot(alpha) if rec else None

    def get_snapshot_by_name(self, name: str,
                             alpha: float = ALPHA_GRASS) -> Optional[dict]:
        pid = self._name_to_id.get(name)
        return self.get_snapshot(pid, alpha) if pid is not None else None

    def all_snapshots(self, alpha: float = ALPHA_GRASS) -> dict:
        """All player snapshots keyed by player_id (str for JSON compat)."""
        return {str(pid): rec.snapshot(alpha)
                for pid, rec in self._players.items()}

    # ── year-specific snapshot lookups (look-ahead-safe) ──────────────────

    def snapshot_at(self, player_id: int, year, alpha: float = ALPHA_GRASS) -> Optional[dict]:
        """Return the snapshot captured just before `year`'s cutoff date.
        Falls back to the latest snapshot if no per-year snapshot exists."""
        year_snap = self._year_snapshots.get(year)
        if year_snap is None:
            return self.get_snapshot(player_id, alpha)
        return year_snap.get(player_id)

    def snapshot_at_by_name(self, name: str, year,
                            alpha: float = ALPHA_GRASS) -> Optional[dict]:
        pid = self._name_to_id.get(name)
        return self.snapshot_at(pid, year, alpha) if pid is not None else None

    def adjusted_rating_at_by_name(self, name: str, year,
                                   alpha: float = ALPHA_GRASS) -> Optional[float]:
        snap = self.snapshot_at_by_name(name, year, alpha)
        return snap["R_adjusted"] if snap else None

    def mean_active_rating_at(self, year,
                              min_matches: int = MIN_ACTIVE_MATCHES,
                              alpha: float = ALPHA_GRASS) -> float:
        """Mean adjusted Elo of active players AS OF the given year cutoff."""
        year_snap = self._year_snapshots.get(year)
        if year_snap is None:
            return self.mean_active_rating(min_matches, alpha)
        ratings = [s["R_adjusted"] for s in year_snap.values()
                   if s["n_surface"] >= min_matches]
        return sum(ratings) / len(ratings) if ratings else DEFAULT_RATING

    def quality_weight_at_by_name(self, name: str, year,
                                  mean_active: Optional[float] = None,
                                  alpha: float = ALPHA_GRASS) -> float:
        """Year-aware quality weight — opponent's adjusted Elo as of the
        season cutoff, divided by the mean active rating of that season."""
        if mean_active is None:
            mean_active = self.mean_active_rating_at(year, alpha=alpha)
        r_adj = self.adjusted_rating_at_by_name(name, year, alpha)
        if r_adj is None or mean_active == 0:
            return 1.0
        raw = r_adj / mean_active
        lo, hi = QUALITY_CLAMP
        return max(lo, min(hi, raw))

    # ── persistence ────────────────────────────────────────────────────────

    def save(self, path: Path, save_history: bool = False) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        # Per-year snapshots: {year_label: {player_id_str: snapshot}}
        year_snaps_serialised = {
            str(label): {str(pid): snap for pid, snap in snap_dict.items()}
            for label, snap_dict in self._year_snapshots.items()
        }

        data = {
            "ratings":         self.all_snapshots(),
            "year_snapshots":  year_snaps_serialised,
            "meta": {
                "total_players":   len(self._players),
                "total_matches":   len(self._history),
                "year_snap_count": len(self._year_snapshots),
                "alpha_grass":     ALPHA_GRASS,
            },
        }
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        print(f"Elo ratings saved → {path}  "
              f"({len(self._players):,} players, {len(self._history):,} matches, "
              f"{len(self._year_snapshots)} year-snapshots)")

        if save_history:
            hist_path = path.parent / (path.stem + "_history.json")
            with open(hist_path, "w") as f:
                json.dump(self._history, f, indent=2)
            print(f"Elo history saved → {hist_path}")

    @classmethod
    def load(cls, path: Path) -> "GrassElo":
        """Reload a previously saved rating snapshot (no history replay)."""
        path = Path(path)
        with open(path) as f:
            data = json.load(f)

        elo = cls()
        for pid_str, snap in data["ratings"].items():
            pid = int(pid_str)
            rec = PlayerRecord(snap["name"])
            rec.n_overall = snap["n_overall"]
            rec.n_surface = snap["n_surface"]
            rec.R_overall = snap["R_overall"]
            rec.R_surface = snap["R_surface"]
            elo._players[pid]           = rec
            elo._id_to_name[pid]        = snap["name"]
            elo._name_to_id[snap["name"]] = pid

        # Year-snapshots: keys come back as strings from JSON; coerce ints
        # for year labels and player IDs.
        for label_str, snap_dict in (data.get("year_snapshots") or {}).items():
            try:
                label = int(label_str)
            except ValueError:
                label = label_str
            elo._year_snapshots[label] = {
                int(pid): snap for pid, snap in snap_dict.items()
            }

        print(f"Elo ratings loaded ← {path}  ({len(elo._players):,} players, "
              f"{len(elo._year_snapshots)} year-snapshots)")
        return elo


# ── CLI ────────────────────────────────────────────────────────────────────

def build_from_csv(matches_csv: Path,
                   wimbledon_csv: Optional[Path] = None,
                   target_surface: str = "grass",
                   snapshot_dates: Optional[Dict] = None) -> GrassElo:
    """
    Build Elo from a match-feed CSV produced by data_prep.py.

    Recommended input: data/processed/all_atp_matches.csv (full ATP feed
    across every surface).  This is required for R_overall to actually
    differ from R_surface — when fed grass-only data, both ratings
    update on every match and end up identical.

    snapshot_dates: optional {label: date} for capturing per-year
    snapshots (see GrassElo.process_matches docstring).
    """
    if not matches_csv.exists():
        raise FileNotFoundError(
            f"Match CSV not found: {matches_csv}\n"
            "Run:  python src/data_prep.py --download"
        )

    print(f"Loading matches from {matches_csv}…")
    df = pd.read_csv(matches_csv, low_memory=False,
                     parse_dates=["tourney_date"])

    required = {"winner_id", "winner_name", "loser_id", "loser_name",
                "tourney_date", "surface"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing columns in CSV: {missing}")

    df = df.dropna(subset=["winner_id", "loser_id", "tourney_date"])
    surf_mix = df["surface"].astype(str).str.lower().value_counts().to_dict()
    print(f"Processing {len(df):,} matches across "
          f"{df['tourney_date'].dt.year.nunique()} seasons "
          f"(surface mix: {surf_mix})…")

    elo = GrassElo()
    elo.process_matches(df, target_surface=target_surface,
                        snapshot_dates=snapshot_dates)
    return elo


def main():
    parser = argparse.ArgumentParser(
        description="Build surface-specific Elo ratings from Sackmann ATP data"
    )
    parser.add_argument(
        "--all_atp",
        default="data/processed/all_atp_matches.csv",
        help="Full ATP match feed (every surface) — required for R_overall vs R_surface to differ"
    )
    parser.add_argument(
        "--all_grass",
        default=None,
        help="DEPRECATED — pass an all-grass CSV here only if you want the legacy "
             "behaviour where R_overall == R_surface."
    )
    parser.add_argument(
        "--out",
        default="data/processed/elo_ratings.json",
        help="Output path for Elo rating snapshot"
    )
    parser.add_argument(
        "--history", action="store_true",
        help="Also save match-by-match Elo history"
    )
    args = parser.parse_args()

    # Per-year Wimbledon start dates → Elo snapshots captured just before
    # each year's main draw begins, so opponent-quality lookups don't peek
    # forward. Hard-coded here so the Elo module stays self-contained.
    SNAPSHOT_DATES = {
        2011: "2011-06-27", 2012: "2012-06-25", 2013: "2013-06-24",
        2014: "2014-06-23", 2015: "2015-06-29", 2016: "2016-06-27",
        2017: "2017-07-03", 2018: "2018-07-02", 2019: "2019-07-01",
        2021: "2021-06-28", 2022: "2022-06-27", 2023: "2023-07-03",
        2024: "2024-07-01", 2025: "2025-06-30",
    }

    csv_path = Path(args.all_grass) if args.all_grass else Path(args.all_atp)
    if args.all_grass:
        print("WARNING: using grass-only CSV — R_overall and R_surface will be identical.")

    elo = build_from_csv(csv_path, snapshot_dates=SNAPSHOT_DATES)
    elo.save(Path(args.out), save_history=args.history)

    # Print a few top-rated players as a sanity check
    print("\nTop 10 by adjusted grass Elo:")
    top = sorted(
        [(rec.adjusted(), rec.name, rec.n_surface)
         for rec in elo._players.values()],
        reverse=True
    )[:10]
    for rank, (r_adj, name, n_surf) in enumerate(top, 1):
        print(f"  {rank:2}. {name:<25}  R_adj={r_adj:.0f}  n_grass={n_surf}")


if __name__ == "__main__":
    main()
