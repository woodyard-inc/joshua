# joshua

Joshua Woodyard's portfolio, CV and CV Workshop, served by GitHub Pages at
https://woodyard-inc.github.io/joshua/.

## Pages (public URLs: keep these names)

| URL | What | Source |
|---|---|---|
| `index.html` | Portfolio home | hand-edited |
| `uniqlo-case-study.html` | Case study 001 | hand-edited |
| `dissertation-case-study.html` | Case study 002 | **generated**: `scripts/build_dissertation.py` |
| `wimbledon/` | Case study 003, the What Wins on Grass app | its own project; see `wimbledon/CLAUDE.md` |
| `cv.html` | Designed CV | **generated**: `scripts/build_cv.py` |
| `cv-ats.html` | Plain CV for applicant-tracking systems | hand-edited, reads `cv-data.json` |
| `tailor.html` | CV Workshop (password-gated) | hand-edited, calls the Worker |

These URLs appear on the CV, in sent PDFs and in shared links, so they are
not renamed. `cv-data.json` stays at the root because the deployed Worker
fetches it by URL (`cf-worker/wrangler.toml`), and `images/share-card.jpg`
keeps its path because social sites cache share previews by URL.

## Layout

```
foundations.css        shared design system: tokens, fonts, nameplate, footer
cv.css                 CV styles (cv.html and the Workshop)
cv-data.json           CV content: the one source for cv.html, cv-ats.html,
                       the Workshop and the Worker
cv-variant.js          CV variant rules (overrides, section order, tracks),
                       shared by cv-ats.html and the Worker; build_cv.py
                       mirrors it. Track A = Commercial, Track B = Research
data/                  page data (dissertation.json: every number on case study 002)
templates/             page templates for the generated pages
scripts/               build_* make outputs; export_* and verify_* check the CV
images/home/           portfolio home images
images/uniqlo/         case study 001 images
images/share-card.*    social share card and its source page
fonts/                 served fonts (OFL licences alongside)
fonts/original/        unmodified font sources the build scripts read
cf-worker/             Cloudflare Worker behind the CV Workshop
wimbledon/             case study 003 app, data pipeline in wimbledon/src
build/                 local output (PDF exports, screenshots); git-ignored
```

## Builds

```sh
python3 scripts/build_cv.py                # cv.html (+ private copies in build/private)
bash scripts/export_cv.sh                  # PDFs to build/exports
python3 scripts/verify_cv.py               # checks those PDFs
python3 scripts/build_dissertation.py      # dissertation-case-study.html; fails on
                                           # untraced numbers or causal wording
python3 scripts/build_fonts_cormorant.py   # Cormorant Garamond CV (metric-corrected)
```

The font script needs fontTools (and brotli for woff2). Edit templates and
data, not the generated pages.

## CV tailor (local)

```sh
python3 scripts/tailor_cv.py --jd job.txt            # Path A (commercial / product analytics)
python3 scripts/tailor_cv.py --plan output/<slug>/plan.json   # re-render, no API call
```

Writes a CV for one job from the experience bank, in the job's language, and
cites the bank entries behind every bullet. `scripts/tailor_rules.py` removes
any bullet with a figure, tool or client its entries don't hold, or one that
breaks a claim limit. Output in `output/<slug>/`: the PDF (two A4 pages),
`cv.txt`, `trace.json` (bullet to bank ids), `fit_report.md` and `plan.json`.

- `private/experience_bank.yaml`: the bank, claim limits, personal bank.
  Git-ignored (client names and personal details; the repo is public).
- `data/tailor_paths.json`: each path's headline, section order, skills rows.
- `output/`: git-ignored (real contact details).
- Needs `pip install anthropic pyyaml`, Chrome, and an Anthropic API key in
  the environment or in the git-ignored `.env` (`ANTHROPIC_API_KEY=...`).

The Workshop (`tailor.html`) and the printed variants are separate and
unchanged by it.
