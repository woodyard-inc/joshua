#!/usr/bin/env python3
"""Check the exported CV PDFs against the CV rules.

Usage:  python3 scripts/verify_cv.py [export-dir]    (default: build/exports)
Needs:  pdfplumber, and pdftotext / pdfinfo / pdffonts on PATH.

Prints a report and exits non-zero if any hard check fails.
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import pdfplumber

ROOT = Path(__file__).resolve().parent.parent
EXPORTS = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "build" / "exports"
CV = json.loads((ROOT / "cv-data.json").read_text(encoding="utf-8"))

MUST = ["Behavioural Science", "Behavioural Economics", "Econometrics", "Regression Analysis",
        "Statistical Modelling", "Survey Research", "Qualitative Research", "Consumer Insights",
        "Consumer Behaviour", "Audience Analysis", "Competitive Analysis", "Data Analysis",
        "Microsoft Excel", "Business Intelligence", "ETL", "Data Governance",
        "Data Visualisation", "Nielsen", "Kantar", "Mintel", "MRI-Simmons", "Comscore",
        "Dissertation mark: 71%"]
MUST_NOT = ["Key Achievements", "Sept", "only universal", "40,000", "NielsenIQ",
            "first-class honours", "N7", "England"]

CAP = {"CormorantGaramondCV": 0.625, "Helvetica-Bold": 0.720, "Helvetica": 0.717,
       "Arial-BoldMT": 0.716, "ArialMT": 0.716, "NationalCodes": 0.288}
# Top of the tallest ink, as a fraction of the em: what the CV's spacing is
# measured to (cv.css trims Cormorant at its ascent, Helvetica at cap height).
INK = {"CormorantGaramondCV": 0.738, "Helvetica-Bold": 0.720, "Helvetica": 0.717,
       "Arial-BoldMT": 0.716, "ArialMT": 0.716, "NationalCodes": 0.296}

CLIENTS = ["Petco", "Sprint", "Boost Mobile", "Dunkin'"]
SECTION_HEADS = ["EXPERIENCE", "RESEARCH", "PROJECTS", "EDUCATION", "SKILLS&LANGUAGES"]

failures = []


def check(ok, label, detail=""):
    print(f"   [{'PASS' if ok else 'FAIL'}] {label}" + (f"  — {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def rendered_text():
    """Every string the CV pages actually set (not the whole corpus: the
    project and tailoring fields are never printed)."""
    parts = [CV["summaries"]["master"]] + [v["tagline"] for v in CV["variants"].values()]
    parts += list(CV["contact"].values())
    for r in CV["roles"]:
        parts += [r["title"], r["company"], r["where"], r["when"]] + [b["text"] for b in r["bullets"]]
    d = CV["dissertation"]
    parts += [d["title"], d["institution"], d["year"], d["mark"]] + d["bullets"]
    for e in CV["education"]:
        parts += [e["degree"], e["school"], e["when"], e.get("topics", "")] + e.get("meta", [])
    parts += [f'{s["label"]} {s["text"]}' for s in CV["skills"]]
    parts += [f'{l["name"]} ({l["level"]})' for l in CV["languages"]]
    return "\n".join(parts)


def text_of(pdf):
    return subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout


def info(pdf, key):
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True).stdout
    m = re.search(rf"^{key}:\s+(.*)$", out, re.M)
    return m.group(1).strip() if m else ""


def cap_ratio(fontname, table=CAP):
    base = fontname.split("+", 1)[-1]
    for k, v in table.items():
        if base.startswith(k):
            return v
    return 0.70


def lines_of(page):
    """Group chars into visual lines keyed by baseline."""
    rows = {}
    for ch in page.chars:
        if not ch["text"].strip():
            continue
        base = round(page.height - ch["matrix"][5], 1)
        rows.setdefault(base, []).append(ch)
    out = []
    for base, chars in sorted(rows.items()):
        chars.sort(key=lambda c: c["x0"])
        cap_top = min(base - cap_ratio(c["fontname"]) * c["size"] for c in chars)
        ink_top = min(base - cap_ratio(c["fontname"], INK) * c["size"] for c in chars)
        out.append({"base": base, "cap_top": cap_top, "ink_top": ink_top,
                    "text": "".join(c["text"] for c in chars)})
    return out


def gaps(pdf):
    """Section rule -> cap top below it; title baseline -> dates cap top."""
    rule_gaps, title_gaps, entry_gaps = [], [], []
    titles = {r["title"] for r in CV["roles"]}
    with pdfplumber.open(pdf) as doc:
        for page in doc.pages:
            lines = lines_of(page)
            rules = [r for r in page.rects
                     if r["height"] <= 2.5 and r["width"] > page.width * 0.6
                     and (r.get("non_stroking_color") in ([0.078], [0.0784], (0.078,), None)
                          or True)]
            for r in rules:
                # only rules that sit directly under a section heading
                above = [l for l in lines if l["base"] < r["top"] and r["top"] - l["base"] < 12]
                if not above or above[-1]["text"].replace(" ", "") not in SECTION_HEADS + [
                        "EXPERIENCE(CONTINUED)"]:
                    continue
                below = [l for l in lines if l["cap_top"] > r["bottom"] - 0.5]
                if below:
                    nxt = min(below, key=lambda l: l["cap_top"])
                    rule_gaps.append((above[-1]["text"][:22], round(nxt["ink_top"] - r["bottom"], 2),
                                      round(nxt["cap_top"] - r["bottom"], 2)))
            for i, l in enumerate(lines[:-1]):
                if l["text"].replace(" ", "") in {t.replace(" ", "") for t in titles}:
                    title_gaps.append((l["text"][:30], round(lines[i + 1]["cap_top"] - l["base"], 2)))
                    prev = lines[i - 1] if i else None
                    if prev and not any(prev["text"].replace(" ", "").startswith(h) for h in SECTION_HEADS):
                        entry_gaps.append((l["text"][:30], round(l["cap_top"] - prev["base"], 2)))
    return rule_gaps, title_gaps, entry_gaps


def verify(pdf, designed):
    print(f"\n== {pdf.name}")
    t = text_of(pdf)
    flat = re.sub(r"\s+", " ", t)
    size = info(pdf, "Page size")
    check("A4" in size, "A4", size)
    check(info(pdf, "Pages") == "2", "2 pages", info(pdf, "Pages"))

    name_ok = "Joshua Woodyard" in t.split("\n", 6)[0:6].__str__()
    if designed:
        lower = "joshua woodyard" in t
        print(f"   [NOTE] name extracts as {'lowercase (wordmark kept, as agreed)' if lower else 'proper case'}")
    else:
        check(name_ok, 'name reads "Joshua Woodyard"')

    c = CV["contact"]
    want = f"{c['location']} | {c['email']} | {c['phone']} | {c['linkedin']}"
    check(want in t, "contact line in one piece", want)

    prof = CV["summaries"]["master"]
    tail = prof[-40:]
    after = flat[flat.find(tail) + len(tail):].strip() if tail in flat else ""
    first = CV["roles"][0]["title"]
    check(re.match(r"(?i)^experience\b", after) is not None, "Experience heading directly after Profile",
          after[:40])
    check(after.lower().startswith("experience " + first.lower()), "then the Publicis title line", first)

    lines = [l.strip() for l in t.splitlines() if l.strip()]
    # Every Experience entry: the title alone, then "Company | City, Country | Dates".
    for r in CV["roles"]:
        tl, follow = r["title"], f"{r['company']} | {r['where']} | {r['when']}"
        idx = [i for i, l in enumerate(lines) if l == tl]
        ok = bool(idx) and lines[idx[0] + 1] == follow
        check(ok, f"title then company line: {r['id']}", f"{tl} / {follow}")
        bad = [x for x in ["–", "|"] + CLIENTS if x in tl]
        check(not bad, f"title line clean (no en dash, pipe or client): {r['id']}", ", ".join(bad) or tl)

    # Company names: three Horizon Media jobs, one Publicis Groupe, one Publicis Media.
    company_lines = [l for l in lines if any(l.startswith(c + " | ") for c in ("Horizon Media", "Publicis Groupe", "Publicis Media", "UNIQLO"))]
    for co, n in (("Horizon Media", 3), ("Publicis Groupe", 1), ("Publicis Media", 1)):
        on_lines = sum(l.startswith(co + " | ") for l in company_lines)
        anywhere = flat.count(co)
        check(on_lines == n, f'"{co}" on {n} company line(s)', f"{on_lines} on company lines, {anywhere} in the whole text")

    # Client names only inside bullets: every line that names a client must be
    # part of a bullet's text, and never a title or company line.
    bullets_flat = re.sub(r"\s+", " ", " ".join(b["text"] for r in CV["roles"] for b in r["bullets"]))
    for cl in CLIENTS:
        hits = [l for l in lines if cl in l]
        stray = [l for l in hits if l.lstrip("• ").strip() not in bullets_flat]
        check(hits and not stray, f'"{cl}" only inside bullets', f"{len(hits)} line(s)" + (f"; stray: {stray[0][:50]}" if stray else ""))

    if designed:
        p2 = [l.strip() for l in subprocess.run(["pdftotext", "-f", "2", "-l", "2", str(pdf), "-"],
                                                capture_output=True, text=True).stdout.splitlines() if l.strip()]
        check(bool(p2) and p2[0].lower() == "experience (continued)", "page 2 opens with Experience (continued)",
              p2[0] if p2 else "(empty)")

    source = rendered_text()
    for w in ["Universität", "St George's", "–", "—"]:
        if w in ("–", "—") and w not in source:
            print(f"   [SKIP] extracts: {w!r}  — the CV never sets it")
            continue
        check(w in t, f"extracts: {w!r}")
    check(re.search(r"^\s*[•·]\s*$", t, re.M) is None, "no bullet glyph alone on a line")

    for w in MUST:
        check(w in flat, f"present: {w}")
    horizon = next(r for r in CV["roles"] if r["id"] == "horizon-bi")
    htitle = horizon["title"]
    check("Business Intelligence" in htitle and htitle in t, "Business Intelligence in the Horizon title line")
    start = flat.find(first)
    exp = flat[start:flat.find(CV["dissertation"]["title"], start)]
    for w in ["Meta", "Google"]:
        check(w in exp, f"{w} in an Experience bullet")

    for w in MUST_NOT:
        hits = [m.start() for m in re.finditer(re.escape(w), flat)]
        if w == "England":
            hits = [h for h in hits if flat[max(0, h - 4):h] != "New "]
        check(not hits, f"absent: {w!r}")
    check(re.search(r"MRI(?!-Simmons)", flat) is None, 'every "MRI" is "MRI-Simmons"')
    check(re.search(r"–\s*Current\b", flat) is None, '"Current" not used as an end date')

    if designed:
        rg, tg, eg = gaps(pdf)
        if rg:
            spread = max(g for _, g, _c in rg) - min(g for _, g, _c in rg)
            check(spread <= 0.5, f"rule -> ink gap consistent ({len(rg)} rules)",
                  f"{min(g for _,g,_c in rg):.2f}–{max(g for _,g,_c in rg):.2f}pt, spread {spread:.2f}")
            for lbl, g, c in rg:
                print(f"         {lbl:<24} ink {g:.2f}pt   cap {c:.2f}pt")
        if tg:
            spread = max(g for _, g in tg) - min(g for _, g in tg)
            check(spread <= 0.5, f"title -> company line gap consistent ({len(tg)} titles)",
                  f"{min(g for _,g in tg):.2f}–{max(g for _,g in tg):.2f}pt, spread {spread:.2f}")
        if tg and eg:
            check(max(g for _, g in tg) * 1.5 < min(g for _, g in eg),
                  "title -> company gap clearly smaller than the gap between entries",
                  f"title -> company {max(g for _,g in tg):.2f}pt vs entry -> entry {min(g for _,g in eg):.2f}–{max(g for _,g in eg):.2f}pt")


def main():
    pdfs = sorted(EXPORTS.glob("*.pdf"))
    if not pdfs:
        print(f"no PDFs in {EXPORTS}")
        return 2
    fonts = subprocess.run(["pdffonts", str(EXPORTS / "Joshua Woodyard CV.pdf")],
                           capture_output=True, text=True).stdout
    print("== embedded fonts, designed master")
    print(fonts)
    check("CormorantGaramondCV-" in fonts, "corrected Cormorant Garamond CV embedded")
    for pdf in pdfs:
        verify(pdf, designed="(ATS)" not in pdf.name)
    print(f"\n{len(failures)} failure(s)" + (": " + "; ".join(failures) if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
