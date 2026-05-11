"""
Phase B — Canonical player name normalisation.

All pipeline scripts that read player names from raw data sources should
import and apply `normalize_name` so that every downstream JSON file uses
the same spelling.  This eliminates the need for app.js to handle data-
source divergence through NAME_ALIASES lookups.

Canonical form policy
─────────────────────
• Prefer the ATP match-level data (Jeff Sackmann atp_matches_YYYY.csv)
  spelling over the Slam PBP (YYYY-wimbledon-*.csv) spelling when they
  conflict, because the match-level data is the richer, longer history
  used by Elo and grass-profile builders.
• Exception: where the PBP spelling is overwhelmingly more common across
  all years and the match-level spelling is rare (e.g. "Jan Lennard Struff"),
  the PBP majority wins.
• Uppercase all "de / van / del" particles except where the official ATP
  tour page consistently lowercases them (none, as of 2024).

If a new alias surfaces, add it here — not in app.js NAME_ALIASES.
"""

# Maps EVERY known variant → the single canonical spelling.
# Keep sorted alphabetically by alias key for easy diff review.
CANONICAL_NAME_MAP: dict[str, str] = {
    # Alex De Minaur — 2024 PBP switched to lowercase "de"; standardise to uppercase
    "Alex de Minaur":                  "Alex De Minaur",

    # Botic Van De Zandschulp — 2024 PBP switched to lowercase "van"
    "Botic van De Zandschulp":         "Botic Van De Zandschulp",
    "Botic van de Zandschulp":         "Botic Van De Zandschulp",

    # Christian / Cristian Garin — occasional alternate spelling
    "Christian Garin":                 "Cristian Garin",

    # Felix Auger-Aliassime — ATP match CSV occasionally uses a hyphen
    "Felix Auger-Aliassime":           "Felix Auger Aliassime",

    # Jan Lennard Struff — hyphenated form appears in a handful of PBP files
    "Jan-Lennard Struff":              "Jan Lennard Struff",

    # Jo-Wilfried Tsonga — ATP match CSV uses hyphen; PBP is split;
    # use the hyphenated form (matches official ATP naming convention)
    "Jo Wilfried Tsonga":              "Jo-Wilfried Tsonga",

    # Juan Martin Del Potro — ATP match CSV lowercases "del"; normalise to title case
    "Juan Martin del Potro":           "Juan Martin Del Potro",

    # Paul-Henri Mathieu — PBP majority form; no-hyphen form maps here
    "Paul Henri Mathieu":              "Paul-Henri Mathieu",

    # Pierre Hugues Herbert — hyphenated form rare but appears
    "Pierre-Hugues Herbert":           "Pierre Hugues Herbert",

    # Stan / Stanislas Wawrinka — early PBP files use full forename
    "Stanislas Wawrinka":              "Stan Wawrinka",

    # Yen-Hsun Lu — no-hyphen form appears in some PBP files
    "Yen Hsun Lu":                     "Yen-Hsun Lu",
}


def normalize_name(name: str) -> str:
    """Return the canonical form of *name*, or *name* unchanged if not mapped."""
    return CANONICAL_NAME_MAP.get(name, name)


def normalize_series(series) -> "pd.Series":
    """Apply normalize_name element-wise to a pandas Series of player names."""
    return series.map(lambda n: CANONICAL_NAME_MAP.get(n, n) if isinstance(n, str) else n)
