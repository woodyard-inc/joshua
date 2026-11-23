#!/usr/bin/env bash
# Fit, build and print the six CV PDFs with Chrome, at A4 (two pages each).
#
#   Joshua Woodyard CV.pdf                              designed, master
#   Joshua Woodyard CV (ATS).pdf                        plain, master
#   Joshua Woodyard CV - Consumer Insights.pdf          designed, variant
#   Joshua Woodyard CV - Consumer Insights (ATS).pdf    plain, variant
#   Joshua Woodyard CV - Research.pdf                   designed, research variant (track B)
#   Joshua Woodyard CV - Research (ATS).pdf             plain, research variant
#
# The designed PDFs are printed from build/private/, which carries the real
# email and phone; the public pages carry "on request". build/ is gitignored,
# so neither the private pages nor the PDFs can be committed by accident.
#
# Usage:  scripts/export_cv.sh [output-dir]      (default: build/exports)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$ROOT/build/exports}"
PORT=8799
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

mkdir -p "$OUT"
# The master may run past two pages; fit each variant back to two first.
python3 "$ROOT/scripts/fit_cv.py"
python3 "$ROOT/scripts/build_cv.py"

( cd "$ROOT" && python3 -m http.server "$PORT" --bind 127.0.0.1 >/dev/null 2>&1 ) &
SERVER=$!
trap 'kill $SERVER 2>/dev/null || true' EXIT
for _ in $(seq 1 30); do curl -sf "http://127.0.0.1:$PORT/cv.html" >/dev/null && break; sleep 0.2; done

print() {  # url  filename
  "$CHROME" --headless=new --disable-gpu --no-pdf-header-footer \
    --virtual-time-budget=8000 --print-to-pdf="$OUT/$2" \
    "http://127.0.0.1:$PORT/$1" >/dev/null 2>&1
  printf '  %-52s %s pages  %s\n' "$2" \
    "$(pdfinfo "$OUT/$2" | awk '/^Pages/{print $2}')" \
    "$(pdfinfo "$OUT/$2" | awk '/^Page size/{print $7,$8}')"
}

echo "Printing to $OUT"
print "build/private/cv.html"                        "Joshua Woodyard CV.pdf"
print "cv-ats.html"                                  "Joshua Woodyard CV (ATS).pdf"
print "build/private/cv-consumer-insights.html"      "Joshua Woodyard CV - Consumer Insights.pdf"
print "cv-ats.html?variant=consumer-insights"        "Joshua Woodyard CV - Consumer Insights (ATS).pdf"
print "build/private/cv-research.html"               "Joshua Woodyard CV - Research.pdf"
print "cv-ats.html?variant=research"                 "Joshua Woodyard CV - Research (ATS).pdf"
