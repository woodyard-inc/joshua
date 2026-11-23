/* CV variants: the one JavaScript copy of the rules, shared by cv-ats.html
   (the plain CV) and the Worker behind the CV Workshop (cf-worker bundles
   this file). scripts/build_cv.py mirrors resolve() for cv.html; keep the
   two in step.

   A variant names only what differs from the master text. Each variant may
   name the tailoring track it prints:
     Track A  Commercial  insights, markets, strategy   (consumer-insights)
     Track B  Research    applied research, behavioural science   (research)
*/

export const DEFAULT_SECTIONS = ['summary', 'experience', 'research', 'projects', 'education', 'skills', 'memberships'];

export const TRACKS = { A: 'Commercial', B: 'Research' };

/** [id, variant] of the variant that prints a track ("A" or "B"), or null. */
export function variantForTrack(cv, track) {
  return Object.entries(cv.variants || {}).find(([, v]) => v.track === track) || null;
}

/** A copy of the CV data with a variant's overrides applied. Anything the
    variant doesn't name is the master text. */
export function resolve(cv, v) {
  const c = JSON.parse(JSON.stringify(cv));
  v = v || {};
  const texts = v.bulletText || {};
  const orders = v.bulletOrder || {};
  c.roles.forEach((r) => {
    r.bullets.forEach((b) => { if (texts[b.id]) b.text = texts[b.id]; });
    const ord = orders[r.id];
    if (ord) {
      const rank = (b) => (ord.indexOf(b.id) === -1 ? ord.length : ord.indexOf(b.id));
      r.bullets.sort((a, b) => rank(a) - rank(b));
    }
  });
  c.summary = c.summaries[v.summary || 'master'] || c.summaries.master;
  if (v.dissertationFirst) c.dissertation.short = [v.dissertationFirst].concat(c.dissertation.short.slice(1));
  const edu = v.education || {};
  c.education = (c.education || []).filter((e) => !(edu[e.id] || {}).omit).map((e) => {
    const ov = edu[e.id] || {};
    if (ov.topics) e.topics = ov.topics;
    if (ov.topicsLabel) e.topicsLabel = ov.topicsLabel;
    e.meta = (e.meta || []).concat(ov.metaAppend || []);
    return e;
  });
  (c.skills || []).forEach((k) => { if ((v.skillText || {})[k.id]) k.text = v.skillText[k.id]; });
  return c;
}
