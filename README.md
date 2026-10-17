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
python3 scripts/build_fonts_source_serif.py  # Source Serif 4 static instances
```

The font scripts need fontTools (and brotli for woff2). Edit templates and
data, not the generated pages.
