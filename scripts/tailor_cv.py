#!/usr/bin/env python3
"""Write a CV tailored to one job description, from the experience bank.

Usage:
  python3 scripts/tailor_cv.py --jd path/to/job.txt [--path A] [--slug name]
  python3 scripts/tailor_cv.py --plan output/<slug>/plan.json   (re-render, no API call)

Needs: anthropic, pyyaml (pip install anthropic pyyaml); Google Chrome,
pdftotext and pdfinfo on PATH; an Anthropic API key (ANTHROPIC_API_KEY, in
the environment or in the git-ignored .env at the repo root).

What it does
  1. Sends the job description, the path's rules (data/tailor_paths.json)
     and the private bank (private/experience_bank.yaml) to Claude, which
     writes the summary and bullets in the job's language and cites the
     bank ids behind each one. Structured output, so ids and role names
     can only be real ones.
  2. Checks every bullet with scripts/tailor_rules.py: cited ids from its
     own role, no figure / tool / client the cited entries don't hold, no
     broken claim limit, no never-claimed tool. Failures are removed.
  3. Lays the CV out with the designed template (scripts/build_cv.py) and
     prints it with Chrome, trimming the least relevant bullets until it
     holds two A4 pages.
  4. Writes output/<slug>/: the PDF, cv.txt (its extracted text),
     trace.json (bullet -> bank ids, plus what was removed and why),
     fit_report.md and plan.json (the model's raw plan, for re-rendering).

output/ is git-ignored: the PDF carries real contact details.
"""

import argparse
import copy
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_cv  # noqa: E402
import tailor_classify  # noqa: E402
import tailor_rules as rules  # noqa: E402
from cvprint import Printer  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BANK = ROOT / "private" / "experience_bank.yaml"
PATHS = ROOT / "data" / "tailor_paths.json"
OUTPUT = ROOT / "output"
MODEL = "claude-opus-5-5"

# Bullets the model writes per role (min, max), most recent role first.
ROLE_BULLETS = {"publicis": (3, 4), "horizon-bi": (2, 3), "uniqlo": (1, 2),
                "petco": (2, 3), "sprint-boost": (2, 3), "dunkin": (1, 2)}


# ---------------------------------------------------------------- the plan --

def load_env():
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            m = re.match(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*)\s*$", line)
            if m and m.group(1) not in os.environ:
                os.environ[m.group(1)] = m.group(2).strip().strip('"').strip("'")


def schema(bank, cv):
    ids = sorted(bank.entries)
    role_ids = [r["id"] for r in cv["roles"]]
    cited = {"type": "array", "items": {"type": "string", "enum": ids}}

    def obj(props):
        return {"type": "object", "properties": props, "required": list(props), "additionalProperties": False}

    text_ids = obj({"text": {"type": "string"}, "ids": cited})
    return obj({
        "job": obj({"company": {"type": "string"}, "title": {"type": "string"},
                    "slug": {"type": "string"}}),
        "summary": text_ids,
        "roles": {"type": "array", "items": obj({
            "role": {"type": "string", "enum": role_ids},
            "bullets": {"type": "array", "items": obj({"text": {"type": "string"}, "ids": cited,
                                                       "core": {"type": "boolean"}})}})},
        "dissertation": {"type": "array", "items": text_ids},
        "project": {"type": "array", "items": text_ids},
        "fit": obj({
            "requirements": {"type": "array", "items": obj({
                "requirement": {"type": "string"}, "ids": cited, "supported": {"type": "boolean"},
                "note": {"type": "string"}})},
            "strengths": {"type": "array", "items": text_ids},
            "gaps": {"type": "array", "items": obj({"gap": {"type": "string"}, "framing": {"type": "string"}})},
            "hooks": {"type": "array", "items": obj({
                "basis": {"type": "string", "enum": bank.data["personal"] + bank.data["positions"]},
                "hook": {"type": "string"}, "why": {"type": "string"}})},
            "near_misses": {"type": "array", "items": {"type": "string"}},
        }),
    })


def system_prompt(bank, path, cv):
    roles = "\n".join(f"- {r['id']}: {r['title']}, {r['company']}, {r['when']} "
                      f"(write {ROLE_BULLETS[r['id']][0]}-{ROLE_BULLETS[r['id']][1]} bullets)"
                      for r in cv["roles"])
    entries = yaml.safe_dump({"entries": bank.data["entries"]}, sort_keys=False, allow_unicode=True, width=110)
    return f"""You write Joshua Woodyard's CV for one job at a time, from a bank of verified facts.

You have freedom in WORDING, SELECTION and EMPHASIS, and none on FACTS.

Allowed:
- Write new bullets in the job description's language, combining facts from one or more bank entries of the SAME role.
- Choose which entries to use per role and order bullets by relevance to this job (most relevant first). Mark a bullet "core" when this job most needs it; the rest may be trimmed for space.
- Use the job's synonyms only where a bank fact supports them, e.g. control-vs-exposed test -> "A/B test" or "incrementality test"; consolidating sources -> "data integration" or "ETL"; stakeholder trust -> "data storytelling" or "stakeholder management".
- Rewrite the summary per job, from bank facts only (any role's entries, plus pr_profile).
Not allowed:
- Any number, tool, client, outcome, responsibility or seniority that the cited entries don't contain. Any claim that breaks a claim limit (below, and on each entry).
- Anything from the personal bank or positions on the CV. They are for cover-letter hooks only.
Traceability: every bullet and the summary list the bank ids they draw on, and only those. A bullet with a fact its ids don't support will be deleted by an automatic check, so cite every entry you use and keep figures exactly as the bank states them (approximate figures keep their "~").

PATH {path['track']}: {path['label']}
Sub-type: {path['subtypeLabel']} ({path['subtypeAbout']}). Rank and phrase for that.
Used for: {path['useFor']}
Headline (fixed): {cv['taglines'][path['tagline']]}
Summary: {path['summaryBrief']} Opener: "{path['summaryOpener']}". 50-75 words, third person without pronouns, no first person.
Priority entries for this path (use when relevant): {', '.join(path['priority'])}
Dissertation (ed_diss, ed_msc): {path['dissertationBrief']} The title and mark are printed above these bullets; don't repeat the mark.
Project (pj_wimbledon): 1-2 bullets.
Skills rows are fixed by the path; do not write them.

Roles, most recent first (every role needs at least one bullet):
{roles}

Style: British English. Past tense for past roles, present tense for the current one. Start bullets with a verb. One idea per bullet, at most about 35 words. Specific and plain; no buzzwords, no em dashes.

Fit report: list the job's distinct requirements (8-15) with the ids that support each, or supported=false with ids empty when nothing in the bank does. Three strengths to lead with. The main gaps, each with one honest line of framing. Two or three cover-letter hooks whose basis is a personal-bank item or position that genuinely matches this employer (none if nothing fits). near_misses: any claim limit you came close to, and how you stayed inside it.

CLAIM LIMITS (never break these):
{chr(10).join('- ' + c for c in bank.data['claim_limits'])}
Never claim these tools: {', '.join(bank.never)}.

THE BANK
{entries}
PERSONAL BANK (hooks only): {json.dumps(bank.data['personal'])}
POSITIONS (hooks only, phrase as beliefs): {json.dumps(bank.data['positions'])}"""


def ask_claude(bank, path, cv, jd, hooks=()):
    import anthropic

    load_env()
    client = anthropic.Anthropic()
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        thinking={"type": "adaptive"},
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": schema(bank, cv)}},
        system=[{"type": "text", "text": system_prompt(bank, path, cv), "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Tailor the CV to this job description." + "".join(
            f"\nInclude a cover-letter hook whose basis is the personal-bank item or position about: {h}" for h in hooks)
            + f"\n\n<job_description>\n{jd}\n</job_description>"}],
    ) as stream:
        msg = stream.get_final_message()
    if msg.stop_reason == "refusal":
        raise SystemExit(f"model declined: {getattr(msg.stop_details, 'category', None)}")
    if msg.stop_reason == "max_tokens":
        raise SystemExit("model ran out of output tokens; try again")
    plan = json.loads(next(b.text for b in msg.content if b.type == "text"))
    u = msg.usage
    print(f"model {msg.model}, message {msg.id}: {u.input_tokens} in "
          f"(+{getattr(u, 'cache_read_input_tokens', 0) or 0} cached) / {u.output_tokens} out tokens")
    return plan, msg.model


# ---------------------------------------------------------------- checking --

def vet(plan, bank, cv, path):
    """Apply the fact and claim checks. Returns kept items and the removals."""
    removed, notes = [], []
    order = [r["id"] for r in cv["roles"]]
    by_role = {r["role"]: r["bullets"] for r in plan["roles"]}
    roles = []
    for rid in order:
        kept = []
        for b in by_role.get(rid, []):
            bad = rules.check(b["text"], b["ids"], bank, role=rid)
            if bad:
                removed.append({"where": rid, "text": b["text"], "ids": b["ids"], "reasons": bad})
            else:
                kept.append({"text": b["text"], "ids": b["ids"], "core": b["core"]})
                notes += [f"{rid}: {n}" for n in rules.notes(b["text"], bank)]
        if not kept:
            raise SystemExit(f"every bullet for {rid} failed the checks; see the removals:\n"
                             + json.dumps(removed, indent=1))
        roles.append({"role": rid, "bullets": kept})

    def vet_list(items, where, allowed):
        out = []
        for b in items:
            bad = rules.check(b["text"], b["ids"], bank, kind="other")
            if any(i not in allowed for i in b["ids"]):
                bad.append(f"cites entries outside {where}")
            if bad:
                removed.append({"where": where, "text": b["text"], "ids": b["ids"], "reasons": bad})
            else:
                out.append({"text": b["text"], "ids": b["ids"]})
                notes.extend(f"{where}: {n}" for n in rules.notes(b["text"], bank))
        return out

    diss = vet_list(plan["dissertation"], "dissertation", {"ed_diss", "ed_msc"})
    proj = vet_list(plan["project"], "project", {"pj_wimbledon"})
    s = plan["summary"]
    bad = rules.check(s["text"], s["ids"], bank, kind="summary")
    opener = path.get("summaryOpener")
    if opener and not s["text"].startswith(opener):
        bad.append(f'does not open with the path\'s summary opener "{opener}"')
    if bad:
        removed.append({"where": "summary", "text": s["text"], "ids": s["ids"], "reasons": bad})
        summary = {"text": cv["summaries"]["master"], "ids": [], "source": "cv-data.json master summary (fallback)"}
    else:
        summary = {"text": s["text"], "ids": s["ids"], "source": "tailored"}
        notes += [f"summary: {n}" for n in rules.notes(s["text"], bank)]
    return {"summary": summary, "roles": roles, "dissertation": diss, "project": proj}, removed, notes


# ---------------------------------------------------------------- layout --

def tailored_cv(cv, path, kept, plan, page1):
    c = copy.deepcopy(cv)
    c["_layout"]["omit"] = []
    c["meta"]["updated"] = dt.date.today().strftime("%Y-%m")  # dated the day it's written
    c["summaries"]["tailored"] = kept["summary"]["text"]
    by_role = {r["role"]: r["bullets"] for r in plan["roles"]}
    for r in c["roles"]:
        r["bullets"] = [{"id": f"t-{r['id']}-{i}", "text": b["text"]} for i, b in enumerate(by_role[r["id"]])]
    for k in c["skills"]:
        k["text"] = path.get("skillText", {}).get(k["id"], k["text"])
        add = [x for x in path.get("skillAppend", {}).get(k["id"], [])
               if x.lower() not in k["text"].lower()]
        if add:
            k["text"] += ", " + ", ".join(add)
    d = c["dissertation"]
    mark = re.sub(r"^Dissertation mark:\s*", "", d["mark"]).replace(" (", ", ").rstrip(")")
    edu = path.get("education", {})
    c["education"] = [e for e in c["education"] if not edu.get(e.get("id"), {}).get("omit")]
    in_research = "research" in path["sections"]
    if in_research and plan["dissertation"]:
        d["short"] = [b["text"] for b in plan["dissertation"]]
    for e in c["education"]:
        e.update({k: v for k, v in edu.get(e.get("id"), {}).items() if k != "omit"})
        if e.get("id") == "msc" and plan["dissertation"] and not in_research:
            e["meta"] = list(e.get("meta", [])) + [f"Dissertation: {d['title']} ({mark})."] + [
                b["text"] for b in plan["dissertation"]]
    c["project"]["short"] = [b["text"] for b in plan["project"]] or c["project"]["short"]
    c["variants"]["tailored"] = {
        "label": "Tailored", "tagline": cv["taglines"][path["tagline"]], "summary": "tailored",
        "sections": path["sections"], "page1Roles": page1, "skillOrder": path["skillOrder"]}
    return c


def fit(cv, path, kept, out):
    """Most bullets that hold two pages: drop the least relevant non-core
    bullet from the role with the most to spare, then project and
    dissertation detail, then core bullets, never emptying a role."""
    prio = set(path.get("priority", []))
    plan = {"roles": [{"role": r["role"], "bullets": [{**b, "core": b["core"] or bool(prio & set(b["ids"]))}
                                                      for b in r["bullets"]]} for r in kept["roles"]],
            "dissertation": list(kept["dissertation"]), "project": list(kept["project"])}
    trimmed = []
    with Printer() as pr:
        while True:
            for k in (3, 2, 4, 1, 5):
                pages, a4 = pr.html(build_cv.build(tailored_cv(cv, path, kept, plan, k), "tailored", True),
                                    f"tailored-{out.parent.name}.html", out)
                if pages == 2:
                    return plan, k, trimmed, a4
            cut = trim_one(plan)
            if not cut:
                raise SystemExit("could not fit two pages; shorten bullets in the bank or the plan")
            trimmed.append(cut)


def trim_one(plan):
    def drop_from(core_ok):
        ok = lambda b: core_ok or not b["core"]  # noqa: E731
        cands = [r for r in plan["roles"] if len(r["bullets"]) > 1 and any(ok(b) for b in r["bullets"])]
        if not cands:
            return None
        # Most bullets first; on a tie the older role gives way.
        r = max(reversed(cands), key=lambda r: len(r["bullets"]))
        b = r["bullets"].pop(max(i for i, b in enumerate(r["bullets"]) if ok(b)))
        return {"where": r["role"], "text": b["text"], "ids": b["ids"]}

    cut = drop_from(False)
    if cut:
        return cut
    for key in ("project", "dissertation"):
        if len(plan[key]) > 1:
            b = plan[key].pop()
            return {"where": key, "text": b["text"], "ids": b["ids"]}
    return drop_from(True)


# ---------------------------------------------------------------- outputs --

def alnum(t):
    return re.sub(r"[^a-z0-9]", "", t.lower())


def post_checks(pdf, final, bank, path, cv):
    text = subprocess.run(["pdftotext", str(pdf), "-"], capture_output=True, text=True).stdout
    flat = alnum(text)
    problems = []
    printed = [b for r in final["roles"] for b in r["bullets"]] + final["dissertation"] + final["project"]
    for b in printed:
        if alnum(b["text"])[:60] not in flat:
            problems.append(f"bullet not found in the PDF text: {b['text'][:60]}")
    problems += rules.never_claimed(text, bank)
    for rid, msg, pat, scope in rules.CLAIM_RULES:
        if scope == "all" and re.search(pat, re.sub(r"\s+", " ", text), re.I):
            problems.append(f"claim limit [{rid}] in the PDF text: {msg}")
    return text, problems


def slugify(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")[:60]


def fit_report(plan, final, removed, notes, trimmed, bank, jd, meta):
    f = plan["fit"]
    L = [f"# Fit report: {plan['job']['title']}, {plan['job']['company']}", "",
         f"Path {meta['path']} ({meta['pathLabel']}), {meta['subtype']} ({meta['subtypeSource']}"
         + (f"; driven by {', '.join(meta['classification']['drivers'])}; runner-up "
            f"{meta['classification']['runnerUp']}" if meta.get("classification") else "")
         + (", close call" if meta.get("classification") and meta["classification"]["closeCall"] else "")
         + f"). Written {meta['generated']} by {meta['model']}. "
         "Bank ids refer to private/experience_bank.yaml."
         + (f" Left out for this path: {', '.join(meta['excluded'])}." if meta["excluded"] else ""), "",
         "## Requirements and evidence", "", "| Requirement | Evidence | Note |", "|---|---|---|"]
    for r in f["requirements"]:
        ev = ", ".join(r["ids"]) if r["supported"] and r["ids"] else "NOT SUPPORTED"
        L.append(f"| {r['requirement']} | {ev} | {r['note']} |")
    L += ["", "## Lead with", ""] + [f"{i}. {s['text']} ({', '.join(s['ids'])})" for i, s in enumerate(f["strengths"], 1)]
    L += ["", "## Gaps, framed honestly", ""] + [f"- **{g['gap'].rstrip('.')}.** {g['framing']}" for g in f["gaps"]]
    asked = [t for t in bank.never if re.search(rules.tool_pattern(t), jd, re.I)]
    if asked:
        L.append(f"- **The job names {', '.join(asked)}.** Never claimed on the CV. "
                 "Point to the nearest real tools instead (see the Skills rows).")
    L += ["", "## Cover-letter hooks", ""]
    L += [f"- {h['hook']} (from: \"{h['basis']}\"; why it fits: {h['why']})" for h in f["hooks"]] or ["- None that genuinely fit this employer."]
    L += ["", "## Claim limits: close calls", ""]
    if removed:
        L += ["Removed by the checks (not on the CV):"] + [
            f"- {x['where']}: \"{x['text']}\" ({'; '.join(x['reasons'])})" for x in removed] + [""]
    if notes:
        L += ["Kept, worth a second look before sending:"] + [f"- {n}" for n in notes] + [""]
    if f["near_misses"]:
        L += ["The model's own notes:"] + [f"- {n}" for n in f["near_misses"]] + [""]
    if not (removed or notes or f["near_misses"]):
        L += ["None.", ""]
    if trimmed:
        L += ["## Trimmed to hold two pages", ""] + [f"- {t['where']}: \"{t['text']}\"" for t in trimmed] + [""]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--jd", type=Path, help="job description text file")
    ap.add_argument("--plan", type=Path, help="re-render a saved plan.json (no API call)")
    ap.add_argument("--path", default="A", help="tailoring path in data/tailor_paths.json (default A)")
    ap.add_argument("--slug", help="output folder name (default: from the job)")
    ap.add_argument("--subtype", help="Track A sub-type: insight, marketing or product (default: classify the JD)")
    ap.add_argument("--hook", action="append", default=[],
                    help="a personal-bank item or position the fit report must use as a hook (repeatable)")
    args = ap.parse_args()
    if not (args.jd or args.plan):
        ap.error("give --jd or --plan")

    cv = json.loads(build_cv.DATA.read_text(encoding="utf-8"))
    paths = json.loads(PATHS.read_text(encoding="utf-8"))
    if args.path not in paths:
        raise SystemExit(f"unknown path {args.path!r}; defined: {[k for k in paths if not k.startswith('_')]}")
    path = paths[args.path]
    saved = json.loads(args.plan.read_text(encoding="utf-8")) if args.plan else None
    jd = saved["jd"] if saved else args.jd.read_text(encoding="utf-8")

    # Track A sub-type: chosen, or classified from the JD exactly as the
    # Workshop does. Its headline, summary opener and skills rows apply.
    classification, labels = None, {}
    if "subtypes" in path:
        classification = tailor_classify.classify(jd, path["classifier"])
        subtype = args.subtype or classification["subtype"]
        if subtype not in path["subtypes"]:
            raise SystemExit(f"unknown sub-type {subtype!r}; one of {list(path['subtypes'])}")
        sub = path["subtypes"][subtype]
        labels = {k: v["label"] for k, v in path["subtypes"].items()}
        print(("chosen: " if args.subtype else "classified: ") + tailor_classify.describe(classification, labels))
        path = {**path, "subtype": subtype, "subtypeLabel": sub["label"], "subtypeAbout": sub["about"],
                **{k: sub[k] for k in ("tagline", "summaryOpener", "skillOrder", "skillAppend") if k in sub}}

    # Entries the path leaves out unless the job asks for them (e.g. pm_ops).
    data = yaml.safe_load(BANK.read_text(encoding="utf-8"))
    excluded = [i for i, pat in path.get("exclude", {}).items() if not re.search(pat, jd, re.I)]
    data["entries"] = [e for e in data["entries"] if e["id"] not in excluded]
    bank = rules.Bank(data)

    if saved:
        plan, model = saved["plan"], saved["model"]
    else:
        plan, model = ask_claude(bank, path, cv, jd, args.hook)
    slug = args.slug or slugify(f"{plan['job']['company']} {plan['job']['title']}") or "job"
    out = OUTPUT / slug
    out.mkdir(parents=True, exist_ok=True)
    (out / "plan.json").write_text(json.dumps({"model": model, "jd": jd, "plan": plan}, indent=1, ensure_ascii=False))

    kept, removed, notes = vet(plan, bank, cv, path)
    pdf = out / f"Joshua Woodyard CV - {plan['job']['company']}.pdf"
    final, page1, trimmed, a4 = fit(cv, path, kept, pdf)
    final["summary"] = kept["summary"]
    text, problems = post_checks(pdf, final, bank, path, cv)
    (out / "cv.txt").write_text(text, encoding="utf-8")

    for h in args.hook:
        if not any(h.lower() in x["basis"].lower() or h.lower() in x["hook"].lower() for x in plan["fit"]["hooks"]):
            problems.append(f'no cover-letter hook about "{h}"')
    meta = {"path": args.path, "pathLabel": path["label"], "model": model, "excluded": excluded,
            "subtype": path.get("subtypeLabel"), "subtypeSource": "chosen" if args.subtype else "auto",
            "classification": classification and {
                **{k: classification[k] for k in ("closeCall", "lead", "drivers")},
                "auto": labels[classification["subtype"]], "runnerUp": labels[classification["runnerUp"]],
                "scores": {labels[k]: v for k, v in classification["scores"].items()}},
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M")}
    roles = {r["id"]: r for r in cv["roles"]}
    trace = {
        "job": plan["job"], **meta, "pdf": pdf.name, "pages": 2, "a4": a4, "page1Roles": page1,
        "headline": {"text": cv["taglines"][path["tagline"]], "source": f"cv-data.json taglines.{path['tagline']}"},
        "summary": final["summary"],
        "experience": [{"role": r["role"], "title": roles[r["role"]]["title"], "company": roles[r["role"]]["company"],
                        "bullets": [{"text": b["text"], "ids": b["ids"]} for b in r["bullets"]]} for r in final["roles"]],
        ("research" if "research" in path["sections"] else "education"): {"dissertation": final["dissertation"]},
        "projects": final["project"],
        "skills": {"source": "cv-data.json skills rows, reordered and edited by data/tailor_paths.json", "order": path["skillOrder"]},
        "removed": removed, "trimmedForSpace": trimmed, "postCheckProblems": problems,
    }
    (out / "trace.json").write_text(json.dumps(trace, indent=1, ensure_ascii=False))
    (out / "fit_report.md").write_text(fit_report(plan, final, removed, notes, trimmed, bank, jd, meta))

    n = sum(len(r["bullets"]) for r in final["roles"])
    print(f"wrote {out.relative_to(ROOT)}/: {pdf.name} (2 pages{', A4' if a4 else ', NOT A4'}, "
          f"{n} role bullets), cv.txt, trace.json, fit_report.md, plan.json")
    print(f"removed by checks: {len(removed)}, trimmed for space: {len(trimmed)}, notes: {len(notes)}")
    if problems:
        print("POST-CHECK PROBLEMS:\n  " + "\n  ".join(problems))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
