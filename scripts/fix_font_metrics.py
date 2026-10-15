#!/usr/bin/env python3
"""Correct the vertical metrics of the CV's body serif at the source.

Why: Cormorant Garamond ships with a tall ascender box (0.924em) and a deep
descender (0.287em) that bear little relation to the letters actually set on
the CV. Browsers centre text inside that box, so every line sits a little
off-centre and spacing has to be nudged by hand with odd margins. Setting the
line metrics to the real extents of the glyphs we use makes line boxes match
the ink, so spacing variables mean what they say.

What it does, for each font the licence permits us to modify:
  1. measures the highest yMax and lowest yMin across basic Latin
     (A-Z, a-z, 0-9, common punctuation; no accented letters);
  2. sets hhea ascent/descent and OS/2 typo ascender/descender to those
     values, both line gaps to 0, and turns on USE_TYPO_METRICS;
  3. leaves usWinAscent/usWinDescent untouched, so accented capitals are
     never clipped on Windows;
  4. renames the family to "<Original> CV" so it can never be confused with
     the stock font, as the SIL OFL expects of a modified version;
  5. subsets to Latin-1 plus the punctuation the CV uses, keeping kern and
     liga, and writes woff2 into fonts/.

Originals are kept untouched in fonts/original/.

Only Cormorant Garamond is modified. It is SIL OFL 1.1 with no Reserved Font
Name. National Codes (Adobe, all rights reserved) and Helvetica (Apple /
Linotype) are not ours to alter; they are corrected in CSS instead, with
ascent-override / descent-override / line-gap-override, using the
measurements this script prints for them.

Usage:  python3 scripts/fix_font_metrics.py
Needs:  fontTools, brotli
"""

import string
import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

ROOT = Path(__file__).resolve().parent.parent
FONTS = ROOT / "fonts"
ORIGINAL = FONTS / "original"

# Fonts we are licensed to modify:  source in fonts/original -> output name
MODIFIABLE = {
    "CormorantGaramond-Regular.ttf": "CormorantGaramondCV-Regular.woff2",
    "CormorantGaramond-Bold.ttf": "CormorantGaramondCV-Bold.woff2",
    "CormorantGaramond-LightItalic.ttf": "CormorantGaramondCV-LightItalic.woff2",
}

# Fonts we may only measure, for CSS overrides (read from fonts/, as served):
# name -> characters to measure
# (None = MEASURE_CHARS). National only ever sets the name, and its digit "1"
# is a stylistic outlier reaching 0.482em / -0.194em against 0.296em for every
# letter, so it is measured on letters alone.
MEASURE_ONLY = {
    "NationalCodesUniversal.woff2": string.ascii_uppercase + string.ascii_lowercase,
    "Helvetica.woff2": None,
    "Helvetica-Bold.woff2": None,
}

# Basic Latin used to measure the real ink extents. Common punctuation only;
# accented letters are deliberately excluded so their marks don't inflate the
# line box (usWin metrics still cover them, so nothing clips).
MEASURE_CHARS = (
    string.ascii_uppercase
    + string.ascii_lowercase
    + string.digits
    + ".,;:!?'\"()-&/%+~@#*"
    + "‘’“”–—·•|→"
)

# Latin-1 plus the general punctuation the CV sets.
SUBSET_UNICODES = (
    list(range(0x20, 0x7F))
    + list(range(0xA0, 0x100))
    + [0x2018, 0x2019, 0x201C, 0x201D, 0x2013, 0x2014, 0x00B7, 0x2022, 0x007C, 0x2192]
)

RENAME_IDS = (1, 3, 4, 6, 16)


def ink_extents(font, chars=None):
    """Highest yMax and lowest yMin over the given characters (default
    MEASURE_CHARS) that are present in the font."""
    cmap = font.getBestCmap()
    glyf = font["glyf"] if "glyf" in font else None
    gs = font.getGlyphSet()
    from fontTools.pens.boundsPen import BoundsPen

    ymax, ymin = None, None
    for ch in (chars or MEASURE_CHARS):
        gname = cmap.get(ord(ch))
        if not gname:
            continue
        if glyf is not None:
            g = glyf[gname]
            if g.numberOfContours == 0 and not g.isComposite():
                continue
            g.recalcBounds(glyf)
            lo, hi = g.yMin, g.yMax
        else:
            pen = BoundsPen(gs)
            gs[gname].draw(pen)
            if not pen.bounds:
                continue
            lo, hi = pen.bounds[1], pen.bounds[3]
        ymax = hi if ymax is None else max(ymax, hi)
        ymin = lo if ymin is None else min(ymin, lo)
    return ymax, ymin


def metrics_row(font, label):
    upm = font["head"].unitsPerEm
    hhea, os2 = font["hhea"], font["OS/2"]
    f = lambda v: f"{v / upm:.3f}"
    return {
        "font": label,
        "upm": upm,
        "ascent": f(hhea.ascent),
        "descent": f(-hhea.descent),
        "lineGap": f(hhea.lineGap),
        "capHeight": f(getattr(os2, "sCapHeight", 0) or 0),
        "xHeight": f(getattr(os2, "sxHeight", 0) or 0),
    }


def rename(font):
    for rec in font["name"].names:
        if rec.nameID not in RENAME_IDS:
            continue
        try:
            text = rec.toUnicode()
        except UnicodeDecodeError:
            continue
        new = (text.replace("Cormorant Garamond", "Cormorant Garamond CV")
                   .replace("CormorantGaramond", "CormorantGaramondCV"))
        if new != text:
            rec.string = new


def fix(src_name, out_name):
    font = TTFont(ORIGINAL / src_name)
    before = metrics_row(font, src_name)

    ymax, ymin = ink_extents(font)
    hhea, os2 = font["hhea"], font["OS/2"]
    hhea.ascent, hhea.descent, hhea.lineGap = ymax, ymin, 0
    os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = ymax, ymin, 0
    os2.fsSelection |= 1 << 7                       # USE_TYPO_METRICS
    # usWinAscent / usWinDescent deliberately left as shipped.

    if "MVAR" in font:                               # variable fonts only
        mvar = font["MVAR"].table
        mvar.ValueRecord = [r for r in mvar.ValueRecord
                            if r.ValueTag not in ("hasc", "hdsc", "hlgp")]

    rename(font)

    opts = subset.Options()
    opts.layout_features = ["kern", "liga"]
    opts.flavor = "woff2"
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.notdef_outline = True
    sub = subset.Subsetter(options=opts)
    sub.populate(unicodes=SUBSET_UNICODES)
    sub.subset(font)
    font.flavor = "woff2"
    font.save(FONTS / out_name)

    after = metrics_row(TTFont(FONTS / out_name), out_name)
    return before, after


def measure_only(name, chars=None):
    font = TTFont(FONTS / name)          # read in place; never copied or modified
    upm = font["head"].unitsPerEm
    ymax, ymin = ink_extents(font, chars)
    return metrics_row(font, name), {
        "ascent-override": f"{ymax / upm * 100:.1f}%",
        "descent-override": f"{-ymin / upm * 100:.1f}%",
        "line-gap-override": "0%",
    }


def print_table(rows, title):
    cols = ["font", "upm", "ascent", "descent", "lineGap", "capHeight", "xHeight"]
    print(f"\n{title}")
    print("  " + "  ".join(f"{c:<38}" if c == "font" else f"{c:>9}" for c in cols))
    for r in rows:
        print("  " + "  ".join(f"{str(r[c]):<38}" if c == "font" else f"{str(r[c]):>9}" for c in cols))


def main():
    befores, afters = [], []
    for src, out in MODIFIABLE.items():
        b, a = fix(src, out)
        befores.append(b)
        afters.append(a)
    print_table(befores, "BEFORE (as shipped), fractions of the em")
    print_table(afters, "AFTER (corrected, renamed, subset)")

    print("\nNot modified (licence) - CSS overrides to use instead:")
    for name, chars in MEASURE_ONLY.items():
        row, overrides = measure_only(name, chars)
        print_table([row], f"  {name} as shipped")
        print("    " + "; ".join(f"{k}: {v}" for k, v in overrides.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
