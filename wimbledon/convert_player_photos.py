#!/usr/bin/env python3
"""
convert_player_photos.py — Convert "profile pictures to upload/*.png" → players/*.webp

Maps the (often misspelled) source filenames onto canonical
Player_Name_With_Underscores.webp filenames the app expects.

Source PNGs are 2752x1536 (matches existing WebP photos), so no resizing
is needed.  Quality 80 produces ~300-500KB files.
"""

from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).parent
SRC  = ROOT / "profile pictures to upload"
DST  = ROOT / "players"
QUALITY = 80

# source PNG basename (without .png) → canonical Player_Name (without .webp)
MAPPING = {
    "Auger Assime":          "Felix_Auger_Aliassime",
    "Bagdadis":              "Marcos_Baghdatis",
    "Bublik":                "Alexander_Bublik",
    "Damir Džumhur":         "Damir_Dzumhur",
    "Dimitrov":              "Grigor_Dimitrov",
    "Dustin Brown":          "Dustin_Brown",
    "Fogini":                "Fabio_Fognini",
    "Garin":                 "Cristian_Garin",
    "Gilles muller":         "Gilles_Muller",
    "Haase":                 "Robin_Haase",
    "Isner":                 "John_Isner",
    "J Mónaco":              "Juan_Monaco",
    "Kohlschrieber_new":     "Philipp_Kohlschreiber",
    "Marin Cilic":           "Marin_Cilic",
    "Medvedev":              "Daniil_Medvedev",
    "Monfils":               "Gael_Monfils",
    "Moutet":                "Corentin_Moutet",
    "Nick Ky":               "Nick_Kyrgios",
    "Nikoloz Basilashvili":  "Nikoloz_Basilashvili",
    "Olpelka":               "Reilly_Opelka",
    "Paire":                 "Benoit_Paire",
    "Pavlásek":              "Adam_Pavlasek",
    "Roberto_Bautista-Agut": "Roberto_Bautista_Agut",
    "Rublev":                "Andrey_Rublev",
    "Ruud":                  "Casper_Ruud",
    "Sam QUERRY":            "Sam_Querrey",
    "Schwatzman":            "Diego_Schwartzman",
    "Seppi":                 "Andreas_Seppi",
    "Stan Wawrinka":         "Stanislas_Wawrinka",
    "Struff":                "Jan_Lennard_Struff",
    "TOmic":                 "Bernard_Tomic",
    "Thiem":                 "Dominic_Thiem",
    "Tiafoe":                "Frances_Tiafoe",
    "Tsonga":                "Jo-Wilfried_Tsonga",
    "Zverev":                "Alexander_Zverev",
    "berrettini":            "Matteo_Berrettini",
    "cuevas":                "Pablo_Cuevas",
    "de minaur":             "Alex_De_Minaur",
    "gilles simon":          "Gilles_Simon",
    "goffin":                "David_Goffin",
    "nadal":                 "Rafael_Nadal",
    "norrie":                "Cameron_Norrie",
    "taylor fritz":          "Taylor_Fritz",
    "tsispas":               "Stefanos_Tsitsipas",
}

# "Firefly.png" intentionally skipped — unclear which player


def main():
    if not shutil.which("cwebp"):
        sys.exit("ERROR: cwebp not on PATH. Install via `brew install webp`.")

    DST.mkdir(parents=True, exist_ok=True)
    n_done = n_overwrote = n_missing = 0
    total_bytes = 0

    for src_base, dst_base in sorted(MAPPING.items(), key=lambda x: x[1]):
        src = SRC / f"{src_base}.png"
        dst = DST / f"{dst_base}.webp"

        if not src.exists():
            print(f"  MISS  {src_base}.png (source missing)")
            n_missing += 1
            continue

        existed = dst.exists()
        result = subprocess.run(
            ["cwebp", "-q", str(QUALITY), "-quiet", str(src), "-o", str(dst)],
            capture_output=True,
        )
        if result.returncode != 0:
            print(f"  FAIL  {src_base}.png  →  {dst.name}")
            print(f"        {result.stderr.decode().strip()}")
            continue

        size = dst.stat().st_size
        total_bytes += size
        marker = "  (overwriting existing)" if existed else ""
        if existed:
            n_overwrote += 1
        n_done += 1
        print(f"  OK    {src_base + '.png':<32} → {dst.name:<32} ({size//1024:>4} KB){marker}")

    print()
    print(f"Done: {n_done} converted ({n_overwrote} overwrote existing), {n_missing} missing")
    print(f"Total output: {total_bytes//1024} KB ({total_bytes/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
