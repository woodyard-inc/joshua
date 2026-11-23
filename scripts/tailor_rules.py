"""Fact and claim checks for tailored CV text (used by scripts/tailor_cv.py).

The tailor may reword, select and combine bank facts; it may not add any.
These checks are deterministic, so they hold whatever the model writes:

  trace    every bullet cites bank ids, all from its own role
  numbers  every figure in a bullet appears in the entries it cites
  tools    every named tool appears in the entries it cites
  clients  every named client appears in the entries it cites
  limits   the bank's claim limits, as patterns (CLAIM_RULES)
  never    no tool from the bank's never_claim_tools list, anywhere

A bullet that fails any check is removed, never patched. Soft checks
(NOTES) don't remove anything; they are listed in the fit report as close
calls worth a second look before sending.
"""

import re

WORD_NUMBERS = {"two": "2", "three": "3", "four": "4", "five": "5", "six": "6", "seven": "7",
                "eight": "8", "nine": "9", "ten": "10", "eleven": "11", "twelve": "12",
                "dozen": "12", "twenty": "20"}

# Tools and platforms the checks recognise in text. The bank's own tools
# and never-claim list are added at load time.
KNOWN_TOOLS = ["Python", "R", "SQL", "T-SQL", "Snowflake", "KNIME", "Alteryx", "SPSS", "Tableau",
               "Power BI", "Power Query", "Excel", "Claude", "Claude Code", "Copilot", "ChatGPT",
               "JavaScript", "Nielsen", "Kantar", "Mintel", "MRI-Simmons", "Comscore", "Cuebiq",
               "Google", "Meta", "Spark", "Airflow", "AWS", "Azure", "GCP", "Mixpanel", "Amplitude",
               "Optimizely", "Statsig", "Stata", "SAS", "Qualtrics", "Hex", "Mode", "Redshift"]
# Client names come from the private bank ("clients", and
# "never_on_cv_clients"), so they never sit in this public file.

S = r"[^.;]"  # within one sentence or clause
CLAIM_RULES = [
    # (id, message, pattern, scope); scope: "all", or a role id the bullet must belong to,
    # or "cites:<bank id>" for bullets citing that entry.
    ("mmm", "calls media work MMM / a media mix model; no MMM was built",
     r"\bMMMs?\b|marketing[- ]mix model|media[- ]mix model", "all"),
    ("kantar-ran", "says the Boost test was designed or run by Joshua; Kantar ran it",
     rf"(?<!Kantar-)(?<!Kantar )(?<!who )(?<!which )(?<!that )\b(designed|designing|design|ran|run|running|conducted|set up|built|launched|executed)\b{S}{{0,50}}\b(tests?|experiments?|A/B|trials?)\b",
     "cites:hz_boost"),
    ("publicis-sql", "claims SQL or Snowflake in the Publicis Groupe role",
     r"(?<![\w-])(T-)?SQL\b|Snowflake", "publicis"),
    ("uniqlo-adopted", "implies the UNIQLO recommendation was adopted or changed results",
     r"\b(adopted|implemented|rolled out|actioned|led to|resulted in|changed|increased|lifted|drove|grew|improved|boosted|was used)\b",
     "uniqlo"),
    ("scale-3500", "uses ~3,500 roles as data scale",
     rf"3,?500(?!\s*(-\s*)?roles?\b)|3,?500{S}{{0,40}}\b(rows|records|data ?points|datasets?|scale)\b", "all"),
    ("fieldwork-40", "presents 40+ markets as research fieldwork",
     rf"40\+?\s*markets{S}{{0,60}}\b(fieldwork|surveys?|respondents|interviews|studies)\b|\b(fieldwork|surveys?|respondents|interviews|studies)\b{S}{{0,60}}40\+?\s*markets",
     "all"),
    ("on-time", "claims transitions finished on time / within 90 days",
     rf"\bon[- ]time\b|\bwithin (the )?90 days\b|\b(hit|met|meeting|achieved|delivered)\b{S}{{0,20}}90-day|\b(all|every|each)\b{S}{{0,30}}\b(11|eleven)\b{S}{{0,40}}complian",
     "all"),
    ("wimbledon-baseline", "claims the simulator beats the regression baseline",
     rf"\b(beat|beats|beating|outperform\w*|better than|exceed\w*|surpass\w*|improv\w* on)\b{S}{{0,50}}\b(baseline|regression)",
     "all"),
    ("moderation", "claims moderating focus groups or interviews",
     rf"\bmoderat\w*|\b(ran|led|conducted|facilitated|hosted)\b{S}{{0,30}}\b(focus groups?|interviews?)\b", "all"),
    ("line-management", "claims line management",
     r"line[- ]manag\w*|direct reports?|\bmanag\w* (a |an |the )?(team|\d+ (analysts|people|staff))\b|\bled (a |an |the )?team\b|\bteam lead\b|\bheaded (a |the )?team\b",
     "all"),
    ("power-of-one", 'writes "Power of One"', r"(?i)power of one", "all"),
    ("nielseniq", 'writes "NielsenIQ"; it is Nielsen', r"(?i)nielsen\s*iq", "all"),
    ("elo", 'describes Elo as a standalone prediction; say "Elo-weighted serve and return ratings"',
     r"\bElo\b(?!-weighted)", "all"),
    ("ds-title", '"Data Scientist" as a job title held',
     r"\b(as|worked as|role as|title of) (a |the )?(senior |lead )?data scientist", "all"),
]

NOTES = [
    ("ab-synonym", 'calls the Kantar control-vs-exposed test an "A/B" or "incrementality" test (allowed synonym; be ready to say Kantar ran it)',
     r"(?i)\bA/B\b|incrementality"),
    ("3500", 'uses "~3,500 roles" (allowed as programme size, not data scale)', r"3,?500"),
    ("40-markets", 'uses "40+ markets" (staffing-analytics coverage only)', r"40\+\s*markets"),
    ("90-day", "mentions 90-day targets (never imply they were all met)", r"90-day"),
    ("data-scientist", '"data scientist" in the text (positioning only, never a title held)', r"(?i)data scientist"),
]


def norm_num(tok):
    t = tok.replace("$", "").replace(",", "").replace("~", "").replace("+", "").strip().lower()
    t = t.rstrip("%")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s?(k|m)?", t)
    if not m:
        return t
    x = float(m.group(1)) * {"k": 1e3, "m": 1e6}.get(m.group(2) or "", 1)
    return f"{x:.6f}".rstrip("0").rstrip(".")


def numbers(text):
    found = {norm_num(m) for m in re.findall(r"\$?\d[\d,]*(?:\.\d+)?\s?[KkMm]?(?![A-Za-z])", text)}
    for w, n in WORD_NUMBERS.items():
        if re.search(rf"\b{w}\b", text, re.I):
            found.add(n)
    return found


def tool_pattern(tool):
    if tool == "R":
        return r"(?<![\w&/'-])R(?![\w&/'-])"
    return rf"(?<![\w-]){re.escape(tool)}(?![\w-])"


class Bank:
    def __init__(self, data):
        self.data = data
        self.entries = {e["id"]: e for e in data["entries"]}
        self.never = list(data.get("never_claim_tools", []))
        self.clients = list(data.get("clients", []))
        self.never_clients = list(data.get("never_on_cv_clients", []))
        bank_tools = {t for e in data["entries"] for t in e.get("tools", [])}
        self.tools = sorted((set(KNOWN_TOOLS) | bank_tools | set(self.never)) - {"M", "APIs"},
                            key=len, reverse=True)

    def text(self, ids):
        parts = []
        for i in ids:
            e = self.entries[i]
            parts += [e.get("context", "")] + e.get("actions", []) + e.get("tools", []) + e.get("results", [])
        return " ".join(parts)

    def role_of(self, i):
        return self.entries[i]["role"]


def check(text, ids, bank, role=None, kind="bullet"):
    """Hard violations for one piece of tailored text (empty list = keep)."""
    bad = []
    if not ids:
        return ["cites no bank entry"]
    unknown = [i for i in ids if i not in bank.entries]
    if unknown:
        return [f"cites unknown ids {unknown}"]
    if kind == "bullet" and role and any(bank.role_of(i) != role for i in ids):
        bad.append(f"cites entries from another role ({', '.join(i for i in ids if bank.role_of(i) != role)})")
    source = bank.text(ids)
    extra = numbers(text) - numbers(source)
    if extra:
        bad.append(f"figures not in the cited entries: {', '.join(sorted(extra))}")
    for t in bank.tools:
        if re.search(tool_pattern(t), text) and t not in bank.never:
            if not re.search(tool_pattern(t), source, re.I if t != "R" else 0):
                bad.append(f'tool "{t}" not in the cited entries')
    for c in bank.clients:
        if re.search(rf"\b{re.escape(c)}", text) and not re.search(rf"\b{re.escape(c)}", source):
            bad.append(f'client "{c}" not in the cited entries')
    bad += never_claimed(text, bank)
    bad += [f'names "{c}", a client that never appears on a CV' for c in bank.never_clients
            if re.search(rf"\b{re.escape(c)}\b", text)]
    for rid, msg, pat, scope in CLAIM_RULES:
        if scope.startswith("cites:"):
            if scope[6:] not in ids:
                continue
        elif scope != "all" and scope != role:
            continue
        if re.search(pat, text, re.I):
            bad.append(f"claim limit [{rid}]: {msg}")
    return bad


def never_claimed(text, bank):
    return [f'never-claimed tool "{t}"' for t in bank.never if re.search(tool_pattern(t), text, re.I)]


def notes(text, bank=None):
    return [f"[{nid}] {msg}" for nid, msg, pat in NOTES if re.search(pat, text)]
