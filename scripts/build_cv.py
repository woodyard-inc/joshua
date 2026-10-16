#!/usr/bin/env python3
"""Build the designed CV pages from cv-data.json and templates/cv.template.html.

Outputs (public, committed):
    cv.html                       master (the only CV on the site)

Outputs (private, gitignored under build/private/):
    cv.html and cv-consumer-insights.html (the variant the Workshop uses for
    insights / strategy roles), with the real email and phone in the contact line, used
    only to print the PDFs. The public pages carry "Email on request |
    Phone on request" and never contain the real values.

Layout knobs live in cv-data.json under "_layout":
    page1Roles   how many roles sit on page one before "Experience (continued)"
    omit         bullets removed to hold two pages, matched by id (roles) or
                 by opening words (research bullets)
    eduResultInMeta
                 true sets each degree's first line (its result) on the
                 school | dates line, as the Research entry carries its mark;
                 false (default) keeps it as a bullet

Usage:  python3 scripts/build_cv.py
"""

import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates" / "cv.template.html"
DATA = ROOT / "cv-data.json"
PRIVATE = ROOT / "build" / "private"

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]

# Figures set in bold cobalt, as on the existing design. One per bullet.
EMPH = ["129-person division", "13%", "0.90+ correlation", "10,000+ rows weekly",
        "~3,500 roles", "40+ markets", "23 markets", "100% staffing commitment",
        "~$15K", "hundreds of millions", "6+ years"]

OUTPUTS = {"master": "cv.html", "consumer-insights": "cv-consumer-insights.html"}


def esc(t):
    return html.escape(str(t), quote=False)


# Phrases parsers should read as one keyword, held on one line.
KEEP_TOGETHER = ["Business Intelligence"]


def nb(h):
    """Hold hyphenated tokens and KEEP_TOGETHER phrases on one line (outside tags only)."""
    parts = re.split(r"(<[^>]+>)", h)
    for i, seg in enumerate(parts):
        if not seg.startswith("<"):
            seg = re.sub(r"\S*\w-\w\S*", lambda m: f'<span class="nb">{m.group(0)}</span>', seg)
            for k in KEEP_TOGETHER:
                seg = seg.replace(k, f'<span class="nb">{k}</span>')
            parts[i] = seg
    return "".join(parts)


def body(t, emphasise=True):
    t = esc(t)
    if emphasise:
        for e in EMPH:
            ee = esc(e)
            if ee in t:
                t = t.replace(ee, f"<strong>{ee}</strong>", 1)
                break
    return nb(t)


def piped(parts):
    """Join with a styled separator whose spaces are real text, so the line
    extracts as "A | B | C" in one piece."""
    sep = '<span class="sep"> | </span>'
    return sep.join(parts)


def stamp(cv, page):
    y, m = cv["meta"]["updated"].split("-")[:2]
    return piped([f"CV &middot; {MONTHS[int(m) - 1]} {y}", f"{page} / 2"])


def omitted(cv):
    return cv.get("_layout", {}).get("omit", [])


def role_html(r, omit):
    bullets = [b for b in r["bullets"] if b["id"] not in omit]
    # The title is one unit (role: team or account), set slightly tighter so
    # it reads, and parses, as one item before the separator.
    role_name = '<span class="role-name">' + nb(esc(r["title"])) + "</span>"
    li = "\n".join(f"      <li>{body(b['text'])}</li>" for b in bullets)
    return (
        '    <div class="entry">\n'
        f'      <p class="entry-title trim trim-line">{piped([role_name, esc(r["company"])])}</p>\n'
        f'      <p class="entry-meta trim trim-line">{piped([esc(r["where"]), esc(r["when"])])}</p>\n'
        f'      <ul class="cv-bullets">\n{li}\n      </ul>\n'
        "    </div>"
    )


def research_html(d, omit):
    # Every CV carries the condensed version; the full one is for research
    # roles and is chosen by the Workshop.
    bullets = [b for b in (d.get("short") or d["bullets"])
               if not any(b.startswith(o.split(":", 1)[1]) for o in omit if o.startswith("research:"))]
    li = "\n".join(f"      <li>{body(b, emphasise=False)}</li>" for b in bullets)
    meta = piped([esc(d["institution"]), esc(d["year"]), esc(d["mark"])])
    return (
        '    <div class="entry">\n'
        f'      <p class="entry-title trim trim-line">{esc(d["title"])}</p>\n'
        f'      <p class="entry-meta trim trim-line">{meta}</p>\n'
        f'      <ul class="cv-bullets">\n{li}\n      </ul>\n'
        "    </div>"
    )


def project_html(p):
    """The short project entry carried on every CV."""
    short = p["short"] if isinstance(p["short"], list) else [p["short"]]
    return (
        '    <div class="entry">\n'
        f'      <p class="entry-title trim trim-line">{esc(p["title"])}</p>\n'
        f'      <p class="entry-meta trim trim-line">{piped([esc(p["label"]), esc(p["year"])])}</p>\n'
        f'      <ul class="cv-bullets">\n' + "\n".join(f"      <li>{body(b, emphasise=False)}</li>" for b in short) + '\n      </ul>\n'
        "    </div>"
    )


def edu_html(e, result_in_meta=False):
    meta = list(e.get("meta", []))
    line = [esc(e["school"]), esc(e["when"])]
    if result_in_meta and meta:
        line.append(esc(meta.pop(0)))
    items = [f"      <li>{body(m, emphasise=False)}</li>" for m in meta]
    if e.get("topics"):
        items.append(f'      <li class="topics"><b>Topics</b>{nb(esc(e["topics"]))}</li>')
    ul = f'      <ul class="cv-bullets edu-bullets">\n' + "\n".join(items) + "\n      </ul>\n" if items else ""
    return (
        '    <div class="entry">\n'
        f'      <p class="entry-title trim trim-line">{nb(esc(e["degree"]))}</p>\n'
        f'      <p class="entry-meta trim trim-line">{piped(line)}</p>\n'
        f"{ul}"
        "    </div>"
    )


def skills_html(cv, order):
    by_id = {s["id"]: s for s in cv["skills"]}
    rows = []
    for sid in order:
        if sid == "languages":
            label = "Languages"
            text = ", ".join(f'{l["name"]} ({l["level"]})' for l in cv["languages"])
        else:
            label, text = by_id[sid]["label"], by_id[sid]["text"]
        rows.append(
            '    <div class="skill-row">\n'
            f'      <p class="k trim trim-block">{esc(label)}</p>\n'
            f'      <p class="v trim trim-block">{nb(esc(text))}</p>\n'
            "    </div>"
        )
    return "\n".join(rows)


def section(heading, inner):
    return f'  <section class="cv-section">\n    <h2 class="trim trim-line">{heading}</h2>\n{inner}\n  </section>'


def contact(cv, private):
    c = cv["contact"]
    email = c["email"] if private else "Email on request"
    phone = c["phone"] if private else "Phone on request"
    li = f'<a href="https://{c["linkedin"]}">{esc(c["linkedin"])}</a>'
    return piped([esc(c["location"]), esc(email), esc(phone), li])


def build(cv, variant_id, private):
    v = cv["variants"][variant_id]
    layout = cv.get("_layout", {})
    omit = omitted(cv)
    k = layout.get("page1Roles", 4)
    roles = cv["roles"]

    page1 = section("Experience", "\n".join(role_html(r, omit) for r in roles[:k]))
    page2 = []
    if roles[k:]:
        page2.append(section("Experience (continued)", "\n".join(role_html(r, omit) for r in roles[k:])))
    page2.append(section("Research", research_html(cv["dissertation"], omit)))
    if cv.get("project", {}).get("short"):
        page2.append(section("Projects", project_html(cv["project"])))
    in_meta = layout.get("eduResultInMeta", False)
    page2.append(section("Education", "\n".join(edu_html(e, in_meta) for e in cv["education"])))
    page2.append(section("Skills &amp; Languages", skills_html(cv, v["skillOrder"])))

    tagline_parts = [p.strip() for p in v["tagline"].split("|")]
    ats = "cv-ats.html" + ("" if variant_id == "master" else f"?variant={variant_id}")

    fills = {
        "PAGE_TITLE": "Joshua Woodyard · CV" + ("" if variant_id == "master" else f" · {v['label']}"),
        "TAGLINE_TEXT": esc(v["tagline"]),
        "TAGLINE": piped([esc(p) for p in tagline_parts]),
        "CONTACT": contact(cv, private),
        "STAMP_1": stamp(cv, 1),
        "STAMP_2": stamp(cv, 2),
        "PROFILE": body(cv["summaries"]["master"]),
        "PAGE1_SECTIONS": page1,
        "PAGE2_SECTIONS": "\n\n".join(page2),
        "VARIANT_LABEL": esc(v["label"]),
        "ATS_HREF": "{{ROOT}}" + ats,
    }
    out = TEMPLATE.read_text(encoding="utf-8")
    for key, val in fills.items():
        out = out.replace("{{" + key + "}}", val)
    out = out.replace("{{ROOT}}", "../../" if private else "")
    leftover = re.findall(r"\{\{[A-Z0-9_]+\}\}", out)
    if leftover:
        raise SystemExit(f"unfilled placeholders: {sorted(set(leftover))}")
    return out


def main():
    cv = json.loads(DATA.read_text(encoding="utf-8"))
    PRIVATE.mkdir(parents=True, exist_ok=True)
    for vid, name in OUTPUTS.items():
        # Only the master CV is published; other variants are used by the
        # Workshop's tailoring and printed privately, never shown on the site.
        if vid == "master":
            public = build(cv, vid, private=False)
            assert cv["contact"]["email"] not in public, "real email leaked into a public page"
            assert cv["contact"]["phone"] not in public, "real phone leaked into a public page"
            (ROOT / name).write_text(public, encoding="utf-8")
        (PRIVATE / name).write_text(build(cv, vid, private=True), encoding="utf-8")
        print(f"built {name}  ({'public + ' if vid == 'master' else ''}private copy)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
