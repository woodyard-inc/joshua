#!/usr/bin/env python3
"""Track A sub-type classifier: insight, marketing or product.

The Python twin of cf-worker/src/classify.js (the Workshop's Worker); both
apply data/tailor_paths.json A.classifier the same way, so the Workshop and
the local tailor classify a job alike. Keep the two in step.

Usage: python3 scripts/tailor_classify.py job.txt [job2.txt ...]
"""

import json
import re
import sys
from collections import Counter
from pathlib import Path

PATHS = Path(__file__).resolve().parent.parent / "data" / "tailor_paths.json"
SUBTYPES = ["insight", "marketing", "product"]


def _snippet(s):
    return re.sub(r"\s+", " ", s).strip().lower()[:60]


def classify(jd, cfg):
    text = jd or ""
    title = text.lstrip()[: cfg["titleChars"]]
    found = {"insight": [], "marketing": [], "product": []}
    for key in ("insight", "marketing"):
        for p in cfg[key]["phrases"]:
            hits = [_snippet(m.group(0)) for m in re.finditer(p, text, re.I)]
            if hits:
                top = Counter(hits).most_common(1)[0][0]
                found[key].append({"phrase": top, "count": len(hits)})
    for s in cfg["product"]["signals"]:
        m = re.search(s["pattern"], title if s.get("where") == "title" else text, re.I)
        if m:
            found["product"].append({"phrase": _snippet(m.group(0)), "signal": s["name"], "count": 1})

    scores = {
        "insight": len(found["insight"]) * cfg["insight"]["weight"],
        "marketing": len(found["marketing"]) * cfg["marketing"]["weight"],
        "product": (len(found["product"]) * cfg["product"]["weight"]
                    if len(found["product"]) >= cfg["product"]["minSignals"] else 0),
    }
    ranked = sorted(SUBTYPES, key=lambda s: -scores[s])  # stable: ties keep SUBTYPES order, as in JS
    lead = scores[ranked[0]] - scores[ranked[1]]
    close = lead <= cfg["margin"]
    subtype = cfg["closeCallDefault"] if close else ranked[0]
    runner = next(s for s in ranked if s != subtype)
    drivers = [f"{d['phrase']} ({d['signal']})" if d.get("signal") else d["phrase"]
               for d in sorted(found[subtype], key=lambda d: -d["count"])[:3]]
    return {"subtype": subtype, "closeCall": close, "lead": lead, "runnerUp": runner, "scores": scores,
            "drivers": drivers, "productSignals": len(found["product"]), "found": found}


def config():
    return json.loads(PATHS.read_text(encoding="utf-8"))["A"]


def describe(r, labels):
    s = f"{labels[r['subtype']]}"
    if r["closeCall"]:
        s += f" (close call, defaulted; lead {r['lead']})"
    s += f" | scores " + ", ".join(f"{labels[k]} {v}" for k, v in r["scores"].items())
    s += f" | runner-up {labels[r['runnerUp']]} | product signals {r['productSignals']}/4"
    s += "\n    driven by: " + ("; ".join(r["drivers"]) or "(no phrases)")
    return s


def main():
    a = config()
    labels = {k: v["label"] for k, v in a["subtypes"].items()}
    for f in sys.argv[1:]:
        print(f"{Path(f).name}: {describe(classify(Path(f).read_text(encoding='utf-8'), a['classifier']), labels)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
