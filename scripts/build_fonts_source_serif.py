#!/usr/bin/env python3
"""Static Source Serif 4 instances for the dissertation case study.

Google's Source Serif 4 is a variable font (weight and optical-size axes):
about 240 KB for the four files the page needed, most of it variation data.
The page uses three styles, so this cuts fixed instances at text optical
size and keeps Google's Latin range plus α and β:

    fonts/original/SourceSerif4-*.woff2   variable sources (SIL OFL)
    fonts/SourceSerif4-{Regular,Semibold,Italic}[-greek].woff2

    /tmp/fontenv/bin/python scripts/build_fonts_source_serif.py
"""
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools import subset

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "fonts" / "original"
OUT = ROOT / "fonts"
OPSZ = 15   # body paragraphs wrap exactly as with the variable font at this size

STYLES = [
    ("Regular", "SourceSerif4-latin.woff2", "SourceSerif4-greek.woff2", 400),
    ("Semibold", "SourceSerif4-latin.woff2", "SourceSerif4-greek.woff2", 600),
    ("Italic", "SourceSerif4-Italic-latin.woff2", "SourceSerif4-Italic-greek.woff2", 400),
]
# The ranges Google serves for these subsets (see the @font-face rules).
LATIN = [*range(0x20, 0x100), 0x131, 0x152, 0x153, 0x2BB, 0x2BC, 0x2C6, 0x2DA, 0x2DC,
         0x304, 0x308, 0x329, *range(0x2000, 0x2070), 0x20AC, 0x2122, 0x2191, 0x2193,
         0x2212, 0x2215, 0xFEFF, 0xFFFD]
GREEK = [0x3B1, 0x3B2]   # the page uses only α and β


def build(src, out, wght, unicodes):
    font = TTFont(SRC / src)
    axes = {"wght": wght, "opsz": OPSZ}
    font = instantiateVariableFont(font, {k: v for k, v in axes.items()
                                          if k in [a.axisTag for a in font["fvar"].axes]})
    opts = subset.Options()
    opts.flavor = "woff2"
    opts.layout_features = ["kern", "liga", "onum", "lnum", "tnum", "pnum", "frac", "sups"]
    opts.name_IDs = ["*"]
    opts.notdef_outline = True
    sub = subset.Subsetter(opts)
    sub.populate(unicodes=unicodes)
    sub.subset(font)
    font.flavor = "woff2"
    font.save(OUT / out)
    print(f"{out}: {(OUT / out).stat().st_size / 1024:.1f} KB")


for name, latin, greek, wght in STYLES:
    build(latin, f"SourceSerif4-{name}.woff2", wght, LATIN)
    build(greek, f"SourceSerif4-{name}-greek.woff2", wght, GREEK)
