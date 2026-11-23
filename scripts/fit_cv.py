#!/usr/bin/env python3
"""Fit every printed CV to two A4 pages.

The master text in cv-data.json holds everything and may run past two
pages. For each variant, this prints the designed CV and the ATS CV and,
while one runs over, drops one bullet at a time:

  - from the role with the most bullets left (on a tie, the older role),
  - its lowest-ranked bullet (the last one, in the variant's bulletOrder),
  - never an alwaysKeep bullet, and never a role's last bullet.

The designed CV also tries other page-one splits before dropping anything.
Cuts can't see which page ran over, so once a fit is found each dropped
bullet is tried back in (strongest first) and kept if the CV still fits.
The result is written to cv-data.json as variants.<id>.fit
({omit, atsOmit, page1Roles}), which build_cv.py and cv-ats.html apply, so
the live pages match the PDFs. Run by scripts/export_cv.sh.

Usage: python3 scripts/fit_cv.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_cv  # noqa: E402
from cvprint import Printer  # noqa: E402

DATA = build_cv.DATA
NOTE = "Written by scripts/fit_cv.py (run by export_cv.sh) to hold two A4 pages; don't edit by hand."


def ranked_roles(cv, vid):
    """Each role's droppable bullet ids, strongest first, as the variant orders them."""
    c = build_cv.resolve(cv, vid)
    return [(r["id"], [b["id"] for b in r["bullets"]], {b["id"] for b in r["bullets"] if b.get("alwaysKeep")})
            for r in c["roles"]]


def next_cut(roles, dropped):
    best = None
    for i, (rid, ids, keep) in enumerate(roles):
        left = [b for b in ids if b not in dropped]
        spare = [b for b in left if b not in keep]
        if len(left) > 1 and spare and (best is None or len(left) >= best[0]):
            best = (len(left), spare[-1])   # >= : the older role wins a tie
    return best[1] if best else None


def write(cv, vid, fit):
    cv["variants"][vid]["fit"] = {"_note": NOTE, **fit}
    DATA.write_text(json.dumps(cv, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def fill_back(dropped, fits, roles):
    """Try each dropped bullet back in, strongest first (the reverse of the
    order dropped), and keep it if the CV still fits. Rank holds within a
    role: a weaker bullet never comes back while a stronger one from its
    role is out, and a stronger one that won't fit on its own is swapped in
    for the weakest one still printed below it. `fits(omit)` -> bool."""
    role = {b: (ids, keep) for _, ids, keep in roles for b in ids}
    out = list(dropped)
    for b in reversed(dropped):
        if b not in out:
            continue
        ids, keep = role[b]
        if any(x in out for x in ids[:ids.index(b)] if x not in keep):
            continue  # a stronger bullet in this role is still out
        trial = [x for x in out if x != b]
        if fits(trial):
            out = trial
            continue
        for w in reversed(ids[ids.index(b) + 1:]):  # weakest first
            if w not in out and w not in keep and fits(trial + [w]):
                out = trial + [w]
                break
    return out


def fit_designed(pr, cv, vid, tmp):
    v = cv["variants"][vid]
    k0 = v.get("page1Roles", cv["_layout"].get("page1Roles", 4))
    n = len(cv["roles"])
    # The configured split, then one role either way; further never helps and
    # each try is a print.
    splits = [k for k in (k0, k0 - 1, k0 + 1) if 1 <= k <= n]
    roles, dropped = ranked_roles(cv, vid), []

    def split_that_fits(omit):
        for k in splits:
            trial = json.loads(json.dumps(cv))
            trial["variants"][vid]["fit"] = {"omit": omit, "page1Roles": k}
            pages, _ = pr.html(build_cv.build(trial, vid, private=True), f"fit-{vid}.html", tmp / f"{vid}.pdf")
            if pages <= 2:
                return k
        return None

    while split_that_fits(dropped) is None:
        cut = next_cut(roles, set(dropped))
        if not cut:
            raise SystemExit(f"{vid}: cannot fit two pages without dropping an alwaysKeep bullet")
        dropped = dropped + [cut]
    dropped = fill_back(dropped, lambda omit: split_that_fits(omit) is not None, roles)
    return {"omit": dropped, "page1Roles": split_that_fits(dropped)}


def fit_ats(pr, cv, vid, fit, tmp):
    roles, dropped = ranked_roles(cv, vid), []
    path = "cv-ats.html" + ("" if vid == "master" else f"?variant={vid}")

    def fits(omit):
        write(cv, vid, {**fit, "atsOmit": omit})
        return pr.url(path, tmp / f"{vid}-ats.pdf")[0] <= 2

    while not fits(dropped):
        cut = next_cut(roles, set(dropped))
        if not cut:
            raise SystemExit(f"{vid} (ATS): cannot fit two pages without dropping an alwaysKeep bullet")
        dropped = dropped + [cut]
    return fill_back(dropped, fits, roles)


def main():
    cv = json.loads(DATA.read_text(encoding="utf-8"))
    for v in cv["variants"].values():
        v.pop("fit", None)
    text = {b["id"]: b["text"] for r in cv["roles"] for b in r["bullets"]}
    with Printer() as pr, tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        for vid in build_cv.OUTPUTS:
            fit = fit_designed(pr, cv, vid, tmp)
            fit["atsOmit"] = fit_ats(pr, cv, vid, fit, tmp)
            write(cv, vid, fit)
            print(f"{vid}: page 1 holds {fit['page1Roles']} roles")
            for label, ids in (("designed", fit["omit"]), ("ATS", fit["atsOmit"])):
                print(f"  {label}: " + ("all bullets kept" if not ids else f"left out {len(ids)}:"))
                for i in ids:
                    print(f"    - {i}: {text[i][:90]}")
    for f in (build_cv.ROOT / "build" / "private").glob("fit-*.html"):
        f.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
