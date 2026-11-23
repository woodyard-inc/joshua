/* Track A sub-type classifier: insight, marketing or product.

   Deterministic and shared with the local tailor (scripts/tailor_classify.py
   applies the same rules, in the same way). The rules live in
   data/tailor_paths.json (A.classifier); keep the two implementations in step.

   Insight and marketing score one point (times weight) per phrase pattern
   found in the job description. A-Product needs explicit product signals:
   at least minSignals of them, and then scores weight per signal; with fewer
   it scores 0. A lead of `margin` points or fewer is a close call and falls
   back to closeCallDefault (A-Marketing), flagged. */

const SUBTYPES = ["insight", "marketing", "product"];

function snippet(s) {
  return s.replace(/\s+/g, " ").trim().toLowerCase().slice(0, 60);
}

export function classifyTrackA(jd, cfg) {
  const text = String(jd || "");
  const title = text.trimStart().slice(0, cfg.titleChars);
  const found = { insight: [], marketing: [], product: [] };

  for (const key of ["insight", "marketing"]) {
    for (const p of cfg[key].phrases) {
      const hits = [...text.matchAll(new RegExp(p, "gi"))].map((m) => snippet(m[0]));
      if (hits.length) {
        const top = hits.sort((a, b) => hits.filter((x) => x === b).length - hits.filter((x) => x === a).length)[0];
        found[key].push({ phrase: top, count: hits.length });
      }
    }
  }
  for (const s of cfg.product.signals) {
    const m = (s.where === "title" ? title : text).match(new RegExp(s.pattern, "i"));
    if (m) found.product.push({ phrase: snippet(m[0]), signal: s.name, count: 1 });
  }

  const scores = {
    insight: found.insight.length * cfg.insight.weight,
    marketing: found.marketing.length * cfg.marketing.weight,
    product: found.product.length >= cfg.product.minSignals ? found.product.length * cfg.product.weight : 0,
  };
  const ranked = [...SUBTYPES].sort((a, b) => scores[b] - scores[a]);
  const lead = scores[ranked[0]] - scores[ranked[1]];
  const closeCall = lead <= cfg.margin;
  const subtype = closeCall ? cfg.closeCallDefault : ranked[0];
  const runnerUp = ranked.find((s) => s !== subtype);
  const drivers = [...found[subtype]].sort((a, b) => b.count - a.count).slice(0, 3)
    .map((d) => d.signal ? `${d.phrase} (${d.signal})` : d.phrase);

  return {
    subtype, closeCall, lead, runnerUp, scores, drivers,
    productSignals: found.product.length,
    found,
  };
}
