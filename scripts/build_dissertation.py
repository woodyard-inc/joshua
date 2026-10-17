#!/usr/bin/env python3
"""Build dissertation-case-study.html from data/dissertation.json.

Every number on the page is formatted here from the data file, and the build
fails if the rendered page shows a number that did not come through these
formatters (years in citations, section and figure numbers excepted). The
charts are inline SVG and HTML tables, so the page needs no JavaScript.

Significance: the dissertation's tables print stargazer's default stars
(* p<.10, ** p<.05, *** p<.01). The page counts ** and *** as significant.

    python3 scripts/build_dissertation.py
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = json.loads((ROOT / "data" / "dissertation.json").read_text())
TEMPLATE = (ROOT / "templates" / "dissertation.template.html").read_text()
OUT = ROOT / "dissertation-case-study.html"

TRACED = set()      # every number string the formatters produced
NUM = r"(?<![\w.])(?:\d+(?:,\d{3})*(?:\.\d+)?|\.\d+)"
MINUS = "−"


def keep(s):
    s = str(s)
    for n in re.findall(NUM, s):
        TRACED.add(n)
    return s


def num(x, d=None):
    """A data value as text: thousands commas, real minus sign."""
    if isinstance(x, int) and d is None:
        s = f"{x:,}"
    elif d is None:
        s = f"{x}"
    else:
        s = f"{x:,.{d}f}"
    return keep(s.replace("-", MINUS))


def beta(x):
    """Published coefficients, trailing zeros dropped (0.290 -> 0.29)."""
    s = f"{abs(x):.4f}".rstrip("0")
    if len(s.split(".")[1]) < 2:
        s = f"{abs(x):.2f}"
    return keep((MINUS if x < 0 else "") + s)


def roman(n):
    """Masthead issue number: the case study number in Roman numerals."""
    out = ""
    for v, r in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while n >= v:
            out, n = out + r, n - v
    return out


def sig(stars):
    return stars in ("**", "***")


def pstr(stars):
    return keep({"***": "p < .01", "**": "p < .05"}.get(stars, "n.s."))


esc = html.escape
countries = DATA["countries"]
by_code = {c["code"]: c for c in countries}
models = DATA["country_models"]
constructs = DATA["constructs"]
ORDER = ["US", "AT", "ZA", "RU", "CN"]          # Fig. 1 column order


# ---------------------------------------------------------------- context
def build_context():
    meta, sample = DATA["meta"], DATA["sample"]
    fair = [models[c]["trust_fairness"] for c in ORDER]
    otr = [models[c]["org_trust"] for c in ORDER]
    fair_all = all(sig(v[2]) for v in fair)
    fair_mean = sum(v[0] for v in fair) / len(fair)
    otr_mean = sum(v[0] for v in otr) / len(otr)
    at_ratio = models["AT"]["trust_fairness"][0] / fair_mean
    cn = by_code["CN"]
    loosest = "**" if any(v[2] == "**" for v in fair) else "***"
    ctx = {
        "case": keep(meta["case_number"]),
        "year": keep(meta["year"]),
        "duration": keep(meta["duration"]),
        "mark": num(meta["mark_pct"]),
        "doc_page": num(meta["issp_doc_page"]),
        "issue": roman(int(meta["case_number"])),
        "rousseau": keep(meta["rousseau_year"]),
        "since_rousseau": num(meta["year"] - meta["rousseau_year"]),
        "n_total": num(sample["n_total"]),
        "n_countries_word": "five",
        "issp": keep(sample["issp_year"]),
        "likert_min": num(sample["likert_min"]),
        "likert_max": num(sample["likert_max"]),
        "alpha": num(sample["pca_alpha_min"]),
        "missing": num(sample["missing_rows"]),
        "n_measures": num(len(constructs)),
        "fair_count": num(sum(sig(v[2]) for v in fair)),
        "n_countries": num(len(ORDER)),
        "fair_min": num(min(v[0] for v in fair), 2),
        "fair_max": num(max(v[0] for v in fair), 2),
        "fair_p": pstr(loosest),
        "ot_min": beta(min(v[0] for v in otr)),
        "ot_max": beta(max(v[0] for v in otr)),
        "fair_mean": beta(round(fair_mean, 3)),
        "ot_mean": beta(round(otr_mean, 3)),
        "at_fair": beta(models["AT"]["trust_fairness"][0]),
        "at_ratio": num(round(at_ratio)),
        "hyp_total": num(meta["hypotheses_total"]),
        "hyp_supported": num(meta["hypotheses_supported"]),
        "us_emm": num(by_code["US"]["emm"]["mean"], 2),
        "cn_emm": num(cn["emm"]["mean"], 2),
        "cn_n": num(cn["n"]),
        "cn_share": num(round(100 * cn["n"] / sample["n_total"], 1), 1),
        "r2": num(DATA["pooled"]["r2"], 2),
        "k": num(DATA["pooled"]["predictors"]),
        "fe_min": beta(round(DATA["controls"]["financial_ease"]["min"], 2)),
        "fe_max": beta(round(DATA["controls"]["financial_ease"]["max"], 2)),
        "tukey_pairs": tukey_pairs(),
        "n_moderators": num(sum(len(m.get("blocks", [None])) for m in DATA["moderators"])),
        "dot_min": num(sample["ls_dot_min"]),
        "dot_max": num(sample["ls_dot_max"]),
        "ci": num(sample["ci_level_pct"]),
        "z": num(sample["ci_z"]),
        "scale_hdi": keep(sample["scale_hdi"]),
        "scale_index": keep(sample["scale_index"]),
    }
    assert fair_all, "the fairness claims assume significance in every country"
    ctx["fig1"] = fig1_heatmap(fair_mean, otr_mean)
    ctx["fig2"] = fig2_fairness(fair_mean, at_ratio)
    ctx["table1"] = table1_countries()
    ctx["table2"] = table2_moderators()
    return ctx


def tukey_pairs():
    short = lambda c: "the US" if c == "US" else by_code[c]["name"]
    parts = [f"{short(p['a'])} and {by_code[p['b']]['name']}" for p in DATA["tukey"]["ns_pairs"]]
    return ", and ".join(parts)


# ---------------------------------------------------------------- Fig. 1
COBALT = (0, 47, 167)
RED = (178, 58, 30)
PAPER = (255, 253, 248)


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def contrast(a, b):
    def lum(hexc):
        rgb = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        f = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def fill(b, scale):
    """Diverging fill: cobalt for positive, red for negative. Magnitudes below
    60% of the scale use light tints (ink text), the rest dark tones (white
    text), skipping the mid band where neither text colour reaches 4.5:1."""
    hue = COBALT if b > 0 else RED
    steps = [m / 100 for m in range(101)]
    ink_max = max(m for m in steps if contrast("#1a1a1a", mix(PAPER, hue, m)) >= 4.5)
    white_min = min(m for m in steps if contrast("#ffffff", mix(PAPER, hue, m)) >= 4.5)
    t = min(1, abs(b) / scale)
    m = 0.08 + (ink_max - 0.08) * t / 0.6 if t < 0.6 else white_min + (1 - white_min) * (t - 0.6) / 0.4
    return mix(PAPER, hue, m)


def best_text(bg):
    """White or ink, whichever reads better on this fill (WCAG contrast)."""
    fg = max(("#ffffff", "#1a1a1a"), key=lambda c: contrast(c, bg))
    assert contrast(fg, bg) >= 4.5, f"no readable text colour on {bg}"
    return fg


def fig1_heatmap(fair_mean, otr_mean):
    scale = max(abs(models[c][k["id"]][0]) for c in ORDER for k in constructs if not k.get("per_person"))
    head = "".join(f'<th scope="col"><abbr title="{esc(by_code[c]["name"])}">{c}</abbr></th>' for c in ORDER)
    rows, group = [], None
    for k in constructs:
        if k["group"] != group:
            group = k["group"]
            rows.append(f'<tr class="hm-group"><th scope="rowgroup" colspan="{len(ORDER) + 1}">{esc(group)}</th></tr>')
        key = k["id"] == "trust_fairness"
        label = esc(k["label"])
        if key:
            label += '<span class="hm-flag">Significant in all five, and the largest on average</span>'
        elif k.get("per_person"):
            label += '<span class="hm-sub">per person contacted</span>'
        cells = []
        for c in ORDER:
            b, se, st = models[c][k["id"]]
            if sig(st):
                bg = fill(b, scale)
                fg = best_text(bg)
                cells.append(f'<td style="background:{bg};color:{fg}" title="{esc(k["label"])}, {esc(by_code[c]["name"])}: β = {beta(b)}, {pstr(st)}">{beta(b)}</td>')
            else:
                cells.append(f'<td class="ns" title="{esc(k["label"])}, {esc(by_code[c]["name"])}: β = {beta(b)}, not significant">n.s.<span class="vh"> (β = {beta(b)})</span></td>')
        cls = ' class="hm-key"' if key else ""
        rows.append(f'<tr{cls}><th scope="row">{label}</th>{"".join(cells)}</tr>')
    lo, hi = beta(-scale), beta(scale)
    legend = (f'<div class="hm-legend" aria-hidden="true"><span>{lo}</span><span class="hm-ramp"></span><span>+{hi}</span>'
              f'<span class="hm-ns">n.s.</span><span>not significant (p ≥ .05)</span></div>')
    return f'''<div class="hm-scroll" tabindex="0" role="region" aria-label="Fig. 1, scrollable table">
<table class="heatmap">
<caption class="vh">Fig. 1: OLS coefficient of each social capital measure on life satisfaction, by country. n.s. = not significant at p &lt; .05.</caption>
<thead><tr><th scope="col"><span class="vh">Measure</span></th>{head}</tr></thead>
<tbody>{"".join(rows)}</tbody>
</table></div>{legend}'''


# ---------------------------------------------------------------- dot plots
def dotplot(rows, lo, hi, ticks, tick_fmt, width, label_w, title, desc, annotate=None, ref=None):
    """Horizontal dot-and-whisker plot. rows: (label, value, lo, hi, highlight)."""
    top, row_h, bottom = (30 if annotate else 8), 40, 34
    plot_l, plot_r = label_w, width - 16
    h = top + row_h * len(rows) + bottom
    x = lambda v: plot_l + (v - lo) / (hi - lo) * (plot_r - plot_l)
    out = [f'<svg class="dot" viewBox="0 0 {width} {h}" width="{width}" height="{h}" role="img" aria-labelledby="{title[0]} {desc[0]}">',
           f'<title id="{title[0]}">{esc(title[1])}</title><desc id="{desc[0]}">{esc(desc[1])}</desc>']
    for t in ticks:
        out.append(f'<line class="grid" x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{top}" y2="{h - bottom}"/>')
        out.append(f'<text class="tick" x="{x(t):.1f}" y="{h - bottom + 22}" text-anchor="middle">{tick_fmt(t)}</text>')
    if ref is not None:
        out.append(f'<line class="ref" x1="{x(ref):.1f}" x2="{x(ref):.1f}" y1="{top}" y2="{h - bottom}"/>')
    for i, (label, v, a, b, hl) in enumerate(rows):
        y = top + row_h * i + row_h / 2
        cls = ' class="hl"' if hl else ""
        out.append(f'<g{cls}><text class="lab" x="0" y="{y + 5:.1f}">{esc(label)}</text>'
                   f'<line class="ci" x1="{x(a):.1f}" x2="{x(b):.1f}" y1="{y:.1f}" y2="{y:.1f}"/>'
                   f'<circle cx="{x(v):.1f}" cy="{y:.1f}" r="6"/>')
        if hl and annotate:
            out.append(f'<text class="ann" x="{min(x(v), plot_r):.1f}" y="{y - 14:.1f}" text-anchor="{"end" if x(v) > (plot_l + plot_r) / 2 else "start"}">{esc(annotate)}</text>')
        out.append("</g>")
    out.append("</svg>")
    return "".join(out)


def two_sizes(make):
    return (f'<div class="chart chart--wide">{make(860, 150, "w")}</div>'
            f'<div class="chart chart--narrow">{make(360, 112, "n")}</div>')


def fig2_fairness(fair_mean, ratio):
    rows = []
    for c in sorted(ORDER, key=lambda c: -models[c]["trust_fairness"][0]):
        b, se, st = models[c]["trust_fairness"]
        z = DATA["sample"]["ci_z"]
        rows.append((by_code[c]["name"], b, b - z * se, b + z * se, c == "AT"))
    fmt = lambda t: keep(f"{t:.1f}")

    def make(w, lw, s):
        return dotplot(rows, -0.1, 0.4, [-0.1, 0, 0.1, 0.2, 0.3, 0.4], fmt, w, lw,
                       (f"f2t{s}", "Trust in fairness and life satisfaction, by country"),
                       (f"f2d{s}", "Coefficients with 95% confidence intervals, all above zero; Austria's is the largest."),
                       ref=0)

    table = "".join(f"<tr><th scope=\"row\">{esc(r[0])}</th><td>{beta(r[1])}</td><td>{num(r[2], 3)} to {num(r[3], 3)}</td></tr>" for r in rows)
    return two_sizes(make) + (f'<table class="vh"><caption>Fig. 2 data</caption><tr><th>Country</th><th>β</th><th>95% CI</th></tr>{table}</table>')


# ---------------------------------------------------------------- tables
STANDS_OUT = {
    "US": "The fewest significant links between social capital and satisfaction in the sample. Wealth may provide the floor, leaving bridging ties as the scarce resource.",
    "AT": "Trust in fairness and organisational trust both stand out from the rest of the sample.",
    "ZA": "The only country where social networks stayed significant in the pooled model. Community may compensate for institutional gaps.",
    "RU": "The highest power distance and lowest corruption-perception score in the sample. Trust in society matters most; stable relationships may count for more when the wider situation feels uncertain.",
    "CN": "The lowest satisfaction at average social capital, with societal care norms and organisational support mattering most.",
}


def ls_dot(v):
    lo, hi = DATA["sample"]["ls_dot_min"], DATA["sample"]["ls_dot_max"]
    return f'<span class="lsdot" aria-hidden="true"><i style="left:{100 * (v - lo) / (hi - lo):.1f}%"></i></span>'


def table1_countries():
    rows = []
    for c in sorted(ORDER, key=lambda c: -by_code[c]["emm"]["mean"]):
        r = by_code[c]
        rows.append(f'<tr><th scope="row">{esc(r["name"])}</th><td class="num">{num(r["emm"]["mean"], 2)}{ls_dot(r["emm"]["mean"])}</td>'
                    f'<td class="num">{num(r["n"])}</td><td class="num">{num(r["hdi"], 3)}</td>'
                    f'<td class="num">{num(r["individualism"], 1)}</td><td class="num">{num(r["hazard"], 1)}</td>'
                    f'<td class="txt">{STANDS_OUT[c]}</td></tr>')
    return f'''<div class="tbl-scroll" tabindex="0" role="region" aria-label="Table 1, scrollable">
<table class="data countries">
<thead><tr><th scope="col">Country</th><th scope="col" class="num">LS mean</th><th scope="col" class="num">n</th><th scope="col" class="num">HDI</th><th scope="col" class="num">Individ&shy;ualism</th><th scope="col" class="num">Hazard</th><th scope="col">What stands out</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div>'''


WHAT_CHANGED = {
    "hazard": "In riskier countries, trust in society, trust in fairness and social networks were less strongly linked to satisfaction; the societal care norm more strongly. Risk itself had no significant association.",
    "power_distance": "Higher power distance went with lower satisfaction overall, and with a stronger link for organisational support resources.",
    "masculinity": "As masculinity rose, trust in fairness mattered more and the elder care norm less.",
    "individualism": "Social networks mattered more in individualistic cultures; societal care and organisational contact mattered more in collectivist ones.",
    "state": "Each index went with higher satisfaction in its own right, and with weaker links for social networks, organisational support resources or organisational contact.",
    "income": "No significant main effect. As income rose, trust in society, daily contact and organisational contact mattered less; societal care mattered more.",
}
READING = {
    "hazard": "Infrastructure alone may not make lives better; community may (Aldrich &amp; Meyer, 2014; Sato et al., 2020; Toya &amp; Skidmore, 2014).",
    "power_distance": "Hierarchy may push people towards institutions for support.",
    "masculinity": "Competitive cultures may lean on fair systems to hold together, much as Hofstede's work on masculine cultures would suggest.",
    "individualism": "Without ready-made in-groups, people need bridges (Putnam, 2000); in collectivist cultures, care is expected from the in-group (Hofstede et al., 2010).",
    "state": "When the state carries more, social capital has to carry less (Bjørnskov, 2003a; Lambsdorff, 2003; Özcan &amp; Bjørnskov, 2011).",
    "income": "Perhaps the well-off feel they should look after others, or get more from giving than from money (Aknin et al., 2013; Easterlin, 2015).",
}


def coef_lines(main, terms, prefix=""):
    lines = [f'<span class="cl"><span class="ck">{prefix}Main</span> {beta(main[0])} <span class="cp">{pstr(main[1])}</span></span>']
    lines += [f'<span class="cl"><span class="ck">× {esc(t)}</span> {beta(b)} <span class="cp">{pstr(st)}</span></span>' for t, b, st in terms]
    return "".join(lines)


def table2_moderators():
    groups = [("given", "What a country is given"), ("governed", "What governments shape")]
    body = []
    for gid, gname in groups:
        rows = [f'<tr class="grp"><th colspan="4" scope="colgroup">{gname}</th></tr>']
        for m in (m for m in DATA["moderators"] if m["group"] == gid):
            if "blocks" in m:
                coefs = "".join(f'<span class="cb">{esc(b["label"])}</span>' + coef_lines(b["main"], b["terms"]) for b in m["blocks"])
            else:
                coefs = coef_lines(m["main"], m["terms"])
            rows.append(f'<tr><th scope="row">{esc(m["label"])}</th><td>{WHAT_CHANGED[m["id"]]}</td>'
                        f'<td class="coefs">{coefs}</td><td class="reading">{READING[m["id"]]}</td></tr>')
        body.append("<tbody>" + "".join(rows) + "</tbody>")
    return f'''<div class="tbl-scroll tbl-mod" tabindex="0" role="region" aria-label="Table 2, scrollable">
<table class="data moderators">
<thead><tr><th scope="col">Moderator</th><th scope="col">What changed</th><th scope="col">Key coefficients</th><th scope="col">My reading</th></tr></thead>
{"".join(body)}</table></div>'''


# ---------------------------------------------------------------- render + checks
def render():
    ctx = build_context()
    page = re.sub(r"\{\{(\w+)\}\}", lambda m: ctx[m.group(1)] if m.group(1) in ctx else sys.exit(f"missing token {m.group(1)}"), TEMPLATE)
    check_numbers(page)
    check_language(page)
    OUT.write_text(page)
    print(f"wrote {OUT.relative_to(ROOT)}")


def visible_text(page):
    t = re.sub(r"<!--.*?-->|<style.*?</style>|<script.*?</script>|<head>.*?</head>", " ", page, flags=re.S)
    t = re.sub(r'title="[^"]*"', " ", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return html.unescape(t)


def check_numbers(page):
    t = visible_text(page)
    # Not statistics: citation and edition years, ids and labels.
    t = re.sub(r"\b(19[5-9]\d|20[0-3]\d)[ab]?\b", " ", t)           # years
    t = re.sub(r"ISCO-08|MMXXVI|\b[A-Z]\d[a-z]?\b|\bP\d\b", " ", t)
    t = re.sub(r"(Fig\.|Table|§)\s*\d+", " ", t)
    t = re.sub(r"\b0[1-6]\b|\b00[1-3]\b", " ", t)                      # section and case numbers
    bad = sorted({n for n in re.findall(NUM, t) if n not in TRACED})
    if bad:
        for n in bad:
            i = t.find(n)
            print(f"  {n!r} in: ...{' '.join(t[max(0, i - 60):i + 40].split())}...", file=sys.stderr)
        sys.exit("numbers not traced to data/dissertation.json: " + ", ".join(bad))


BANNED = ["impact", "effect of", "drives", "proves", "leads to", "will lead", "is due to", "will create", "progressivism"]


def check_language(page):
    t = visible_text(page).lower()
    hits = [w for w in BANNED if w in t]
    if hits:
        sys.exit("causal language on the page: " + ", ".join(hits))


if __name__ == "__main__":
    render()
