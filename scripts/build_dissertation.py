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
# Every visual uses one country order: life satisfaction at mean social capital, high to low.
ORDER = [c["code"] for c in sorted(DATA["countries"], key=lambda c: -c["emm"]["mean"])]


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
    others = [models[c]["trust_fairness"][0] for c in ORDER if c != "AT"]
    assert models["AT"]["trust_fairness"][0] / (sum(others) / len(others)) > 2, "copy says 'more than twice the other four countries''"
    ctx["h1a_card"] = h1a_card()
    ctx["h1b_card"] = h1b_card()
    ctx["h2a_card"] = h2a_card()
    ctx["h2b_card"] = h2b_card()
    ctx["reading_h1a"] = READINGS["h1a"]
    ctx["reading_h1b"] = READINGS["h1b"]
    ctx["full_table"] = full_table()
    ctx["country_cards"] = country_cards()
    ctx["table2"] = table2_moderators()
    return ctx


def tukey_pairs():
    short = lambda c: "the US" if c == "US" else by_code[c]["name"]
    parts = [f"{short(p['a'])} and {by_code[p['b']]['name']}" for p in DATA["tukey"]["ns_pairs"]]
    return ", and ".join(parts)


# ---------------------------------------------------------------- tints
INK = (20, 20, 20)
PAPER_WARM = (240, 235, 221)
PAPER = (255, 253, 248)
TERRACOTTA = (211, 82, 32)


def mix(a, b, t):
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def contrast(a, b):
    def lum(hexc):
        rgb = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        f = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
        return 0.2126 * f[0] + 0.7152 * f[1] + 0.0722 * f[2]
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def tint(t, hue=INK):
    """The kit's grey ramp (paper-warm to ink) for magnitudes; terracotta for
    negative coefficients. Text is ink or paper, whichever reads at 4.5:1."""
    bg = mix(PAPER_WARM, hue, 0.08 + 0.84 * max(0, min(1, t)))
    fg = max(("#141414", "#fffdf8"), key=lambda c: contrast(c, bg))
    if contrast(fg, bg) < 4.5:      # step out of the mid band where neither reads
        bg = mix(PAPER_WARM, hue, 0.08 if t < 0.5 else 0.92)
        fg = max(("#141414", "#fffdf8"), key=lambda c: contrast(c, bg))
    return f"background:{bg};color:{fg}"


def heads():
    return '<span class="corner"></span>' + "".join(
        f'<span class="col-head"><abbr title="{esc(by_code[c]["name"])}">{c}</abbr></span>' for c in ORDER)


def scorecard(lens, body, key):
    return (f'<div class="scorecard"><span class="lens">{lens}</span>'
            f'<div class="matrix" role="table">{heads()}{body}</div><p class="key">{key}</p></div>')


# ---------------------------------------------------------------- card visuals
def h1a_card():
    rows = [k for k in constructs if k["group"] == "Trust"]
    scale = max(models[c][k["id"]][0] for c in ORDER for k in rows)
    body = ""
    for k in rows:
        hl = k["id"] == "trust_fairness"
        body += f'<span class="row-head{" hl" if hl else ""}">{esc({"trust_society": "Society", "trust_fairness": "Fairness", "org_trust": "Org."}[k["id"]])}</span>'
        for c in ORDER:
            b, se, st = models[c][k["id"]]
            if sig(st):
                body += f'<span class="cell{" hl" if hl else ""}" style="{tint(b / scale)}" title="{esc(k["label"])}, {esc(by_code[c]["name"])}: β = {beta(b)}, {pstr(st)}">{beta(b)}</span>'
            else:
                body += f'<span class="cell ns" title="{esc(k["label"])}, {esc(by_code[c]["name"])}: β = {beta(b)}, not significant">n.s.</span>'
    return scorecard("Trust · β by country", body, "β, country OLS · darker = stronger · n.s. p ≥ .05")


def h1b_card():
    lo, hi = DATA["sample"]["ls_dot_min"], DATA["sample"]["ls_dot_max"]
    low = min(by_code[c]["emm"]["mean"] for c in ORDER)
    body = '<span class="row-head">LS mean</span>'
    for c in ORDER:
        v = by_code[c]["emm"]["mean"]
        w = 100 * (v - lo) / (hi - lo)
        body += (f'<span class="cell" title="{esc(by_code[c]["name"])}: {num(v, 2)}">{num(v, 2)}'
                 f'<span class="bar{" hl" if v == low else ""}" aria-hidden="true"><i style="width:{w:.0f}%"></i></span></span>')
    return scorecard(f"Life satisfaction · {num(DATA['sample']['likert_min'])}–{num(DATA['sample']['likert_max'])}",
                     body, f"Marginal means · bars {num(lo)}–{num(hi)}")


def index_card(rows, lens, key):
    body = ""
    for field, label, d in rows:
        vals = [by_code[c][field] for c in ORDER]
        lo, hi = min(vals), max(vals)
        body += f'<span class="row-head">{label}</span>'
        for c in ORDER:
            v = by_code[c][field]
            body += f'<span class="cell" style="{tint((v - lo) / (hi - lo) if hi > lo else 0.5)}" title="{esc(by_code[c]["name"])}: {num(v, d)}">{num(v, d)}</span>'
    return scorecard(lens, body, key)


def h2a_card():
    return index_card([("hazard", "Hazard", 1), ("power_distance", "Power dist.", 1), ("masculinity", "Masculinity", 1), ("individualism", "Individualism", 1)],
                      f"What a country is given · {keep(DATA['sample']['scale_index'])}",
                      "INFORM hazard · Hofstede · darker = higher")


def h2b_card():
    return index_card([("hdi", "HDI", 3), ("spi", "SPI", 2), ("cpi", "CPI", 1)],
                      "What governments shape",
                      f"HDI {keep(DATA['sample']['scale_hdi'])} · SPI, CPI {keep(DATA['sample']['scale_index'])} · darker = higher")


READINGS = {
    "h1a": "The surprise was organisational support resources, which went with lower satisfaction, the opposite of what I expected. People may dislike having to rely on institutions for what they need. It took me a week to see it.",
    "h1b": "In China, guanxi (關係) networks are also how knowledge, licences, projects and careers flow. When social capital is an economic necessity rather than an extra, an average amount may not feel like enough (He &amp; Gebhardt, 2015).",
}


# ---------------------------------------------------------------- all 11 measures (disclosure)
def full_table():
    scale = max(abs(models[c][k["id"]][0]) for c in ORDER for k in constructs if not k.get("per_person"))
    rows, group = [], None
    for k in constructs:
        if k["group"] != group:
            group = k["group"]
            rows.append(f'<tr class="grp"><th colspan="{len(ORDER) + 1}" scope="rowgroup">{esc(group)}</th></tr>')
        label = esc(k["label"]) + ('<span class="sub">per person contacted</span>' if k.get("per_person") else "")
        cells = ""
        for c in ORDER:
            b, se, st = models[c][k["id"]]
            if sig(st):
                style = tint(abs(b) / scale, TERRACOTTA if b < 0 else INK)
                cells += f'<td style="{style}">{beta(b)}</td>'
            else:
                cells += f'<td class="ns">n.s.<span class="vh"> (β = {beta(b)})</span></td>'
        cls = ' class="hl"' if k["id"] == "trust_fairness" else ""
        rows.append(f'<tr{cls}><th scope="row">{label}</th>{cells}</tr>')
    z = DATA["sample"]["ci_z"]
    ci = " · ".join(f'{c} {num(models[c]["trust_fairness"][0] - z * models[c]["trust_fairness"][1], 3)} to '
                    f'{num(models[c]["trust_fairness"][0] + z * models[c]["trust_fairness"][1], 3)}' for c in ORDER)
    head = "".join(f'<th scope="col"><abbr title="{esc(by_code[c]["name"])}">{c}</abbr></th>' for c in ORDER)
    return f'''<details class="more" id="all-measures">
<summary>All {num(len(constructs))} measures, by country</summary>
<div class="fulltable-wrap" tabindex="0" role="region" aria-label="All measures, scrollable">
<table class="full"><caption class="vh">OLS coefficient of each social capital measure on life satisfaction, by country</caption>
<thead><tr><th scope="col">Measure</th>{head}</tr></thead><tbody>{"".join(rows)}</tbody></table>
<p class="fc">Country-level OLS coefficients, ISSP {keep(DATA["sample"]["issp_year"])}; grey cells are not significant at p &lt; .05, terracotta cells negative. Trust in fairness, {num(DATA["sample"]["ci_level_pct"])}% intervals (β ± {num(z)} SE): {ci}.</p>
</div></details>'''


# ---------------------------------------------------------------- Fig. 01 country cards
CARD_NOTE = {
    "US": "The fewest significant links between social capital and satisfaction. Wealth may provide the floor; bridging ties are the scarce resource.",
    "AT": "Trust in fairness and organisational trust both stand out from the rest of the sample.",
    "RU": "Highest power distance and lowest corruption-perception score in the sample. Trust in society matters most.",
    "ZA": "The only country where social networks stayed significant in the pooled model.",
    "CN": "Lowest satisfaction at average social capital. Societal norms and resources stood out in the pooled comparison.",
}


def country_cards():
    n_max = max(by_code[c]["n"] for c in ORDER)
    cards = []
    for c in ORDER:
        r = by_code[c]
        stats = [("HDI", r["hdi"] * 100, num(r["hdi"], 2)),
                 ("Indiv.", r["individualism"] * 10, num(r["individualism"], 1)),
                 ("Hazard", r["hazard"] * 10, num(r["hazard"], 1)),
                 ("n", 100 * r["n"] / n_max, num(r["n"]))]
        rows = "".join(f'<div class="stat"><span class="lbl">{lbl}</span><div class="bar"><div class="fill" style="width:{w:.0f}%"></div></div><span class="v">{v}</span></div>'
                       for lbl, w, v in stats)
        cards.append(f'''<article class="country-card">
<div class="top-rule"></div>
<span class="code">{c}</span>
<span class="name">{esc(r["name"])}</span>
<div class="ls">{num(r["emm"]["mean"], 2)}</div>
<span class="ls-cap">Life satisfaction · {num(DATA["sample"]["likert_min"])}–{num(DATA["sample"]["likert_max"])}</span>
<div class="stats">{rows}</div>
<p class="finding">{CARD_NOTE[c]}</p>
</article>''')
    pairs = ", and ".join(f'{"the US" if p["a"] == "US" else by_code[p["a"]]["name"]} and {by_code[p["b"]]["name"]}' for p in DATA["tukey"]["ns_pairs"])
    return f'''<section class="country-strip" id="fig-01" aria-labelledby="fig-01-t">
<div class="strip-head">
<p class="strip-title" id="fig-01-t"><span class="figno">Fig. 01</span><span class="strip-name">Life satisfaction at mean social capital, by country</span></p>
<p class="lede">Where you live still shapes how satisfied you are, even at the same level of social connection. At average social capital, China was lowest, and only {pairs}, were statistically level.</p>
</div>
<div class="country-cards">{"".join(cards)}</div>
<p class="fc">Estimated marginal means of life satisfaction ({num(DATA["sample"]["likert_min"])}–{num(DATA["sample"]["likert_max"])}) with social capital held at its sample mean, from the pooled OLS model. Bars: HDI (UN Human Development Index, {keep(DATA["sample"]["scale_hdi"])}); Indiv. (Hofstede individualism, {keep(DATA["sample"]["scale_index"])}); Hazard (INFORM natural-hazard risk, {keep(DATA["sample"]["scale_index"])}); n, respondents after imputation.</p>
</section>'''


# ---------------------------------------------------------------- Table 01: moderation
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


def coef_lines(main, terms):
    lines = [f'<span class="cl"><span class="ck">Main</span> {beta(main[0])} <span class="cp">{pstr(main[1])}</span></span>']
    lines += [f'<span class="cl"><span class="ck">× {esc(t)}</span> {beta(b)} <span class="cp">{pstr(st)}</span></span>' for t, b, st in terms]
    return "".join(lines)


def table2_moderators():
    groups = [("given", "What a country is given · H2a"), ("governed", "What governments shape · H2b")]
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
    return f'''<div class="mod-table" id="table-01">
<p class="tcap" id="table-01-t"><span class="figno">Table 01</span>How national context changes the link between social capital and life satisfaction</p>
<div class="tbl-scroll" tabindex="0" role="region" aria-labelledby="table-01-t">
<table aria-labelledby="table-01-t">
<thead><tr><th scope="col">Moderator</th><th scope="col">What changed</th><th scope="col">Key coefficients</th><th scope="col">My reading</th></tr></thead>
{"".join(body)}</table></div>
<p class="vh">Coefficients are interaction terms and the moderator's main term, from pooled OLS models with one moderator at a time; only terms significant at p &lt; .05 are listed.</p>
</div>'''


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
