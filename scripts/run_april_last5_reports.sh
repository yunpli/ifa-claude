#!/usr/bin/env bash
#
# Generate ningbo evening reports for the last 5 trading days of April 2026
# in PRODUCTION mode, with dual-track scoring.
#
# Usage:
#     bash scripts/run_april_last5_reports.sh
#
# Each report takes ~2-4 min (LLM narrative is the bottleneck). Total ~20-25 min
# (PDF rendering adds ~10-20s per report).
#
# Output:
#   HTML: ~/ifaenv/out/production/<date>/ningbo/CN_ningbo_evening_<date>_HHMM.html
#   PDF:  ~/ifaenv/out/production/<date>/ningbo/CN_ningbo_evening_<date>_HHMM.pdf
#
# Pre-flight checks (run once before this script):
#   1. ML models are active in registry:
#        ifa ningbo registry status
#   2. Historical dual recs are backfilled (so consensus matrix shows ★★★+):
#        ifa ningbo backfill-dual --days 30 --mode manual

set -e   # abort on first error
cd "$(dirname "$0")/.."

DATES=(
    "2026-04-24"
    "2026-04-27"
    "2026-04-28"
    "2026-04-29"
    "2026-04-30"
)

echo "════════════════════════════════════════════════════════"
echo "  Generating ${#DATES[@]} ningbo dual-mode reports (PRODUCTION)"
echo "  Dates: ${DATES[*]}"
echo "════════════════════════════════════════════════════════"

SUCCESS=0
FAILURES=()

for D in "${DATES[@]}"; do
    echo ""
    echo "── [$D] starting ──────────────────────────────────"
    START_TS=$(date +%s)

    if uv run python -m ifa.cli ningbo evening \
        --report-date "$D" \
        --scoring dual \
        --mode production \
        --generate-pdf \
        2>&1 | tail -5
    then
        ELAPSED=$(( $(date +%s) - START_TS ))
        echo "  ✓ done in ${ELAPSED}s"
        SUCCESS=$((SUCCESS + 1))
    else
        echo "  ❌ failed for $D"
        FAILURES+=("$D")
    fi
done

echo ""
echo "════════════════════════════════════════════════════════"
echo "  Summary: $SUCCESS / ${#DATES[@]} reports generated"
if [ ${#FAILURES[@]} -gt 0 ]; then
    echo "  Failures: ${FAILURES[*]}"
fi
echo "  Output dir: ~/ifaenv/out/production/"
echo "════════════════════════════════════════════════════════"
echo ""
echo "Quick links (HTML + PDF):"
for D in "${DATES[@]}"; do
    DCOMPACT=${D//-/}
    HTML=$(ls -t ~/ifaenv/out/production/${DCOMPACT}/ningbo/*.html 2>/dev/null | head -1)
    PDF=$(ls -t ~/ifaenv/out/production/${DCOMPACT}/ningbo/*.pdf  2>/dev/null | head -1)
    if [ -n "$HTML" ]; then
        echo "  $D"
        echo "    HTML: file://${HTML}"
        [ -n "$PDF" ] && echo "    PDF:  file://${PDF}"
    fi
done
