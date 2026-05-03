#!/usr/bin/env bash
#
# Generate ningbo evening reports for the last 5 trading days of April 2026
# in PRODUCTION mode with dual-track scoring + PDF + per-run timing log.
#
# Usage:
#     bash scripts/run_april_last5_reports_timed.sh
#
# Output:
#   HTML + PDF: ~/ifaenv/out/production/<date>/ningbo/CN_ningbo_evening_*
#   Timing log: ~/ifaenv/logs/ningbo_april_batch_<UTC>.log
#               (also tee'd to stdout)
#
# Pre-flight (run once before this script if you skipped it):
#   uv run ifa ningbo registry status
#   uv run ifa ningbo backfill-dual --days 30 --mode manual

set -e
cd "$(dirname "$0")/.."

DATES=(
    "2026-04-24"
    "2026-04-27"
    "2026-04-28"
    "2026-04-29"
    "2026-04-30"
)
# 注：原脚本错把 04-25 (周六) 当交易日。4月最后5个交易日实际是 24, 27, 28, 29, 30。

# Timing log location
LOG_DIR="$HOME/ifaenv/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/ningbo_april_batch_$(date -u +%Y%m%dT%H%M%SZ).log"

# ── Helper: log to both stdout AND log file ─────────────────────────────────
log() { echo "$@" | tee -a "$LOG_FILE"; }

log "════════════════════════════════════════════════════════════"
log "  Ningbo April-last-5 Production Batch"
log "  Started:  $(date '+%Y-%m-%d %H:%M:%S %Z') (UTC: $(date -u +%Y-%m-%dT%H:%M:%SZ))"
log "  Host:     $(hostname)"
log "  Dates:    ${DATES[*]}"
log "  Log file: $LOG_FILE"
log "════════════════════════════════════════════════════════════"

BATCH_START=$(date +%s)

SUCCESS=0
FAILURES=()
declare -a TIMINGS   # holds "DATE  STATUS  ELAPSED_SEC  PATH" rows

for D in "${DATES[@]}"; do
    log ""
    log "── [$D] start at $(date '+%H:%M:%S') ──────────────────────"
    START_TS=$(date +%s)
    START_HUMAN=$(date '+%Y-%m-%d %H:%M:%S')

    # Capture full output to log (and the "Report saved:" line for path extraction)
    OUT=$(uv run python -m ifa.cli ningbo evening \
        --report-date "$D" \
        --scoring dual \
        --mode production \
        --generate-pdf \
        2>&1) && RC=0 || RC=$?

    END_TS=$(date +%s)
    END_HUMAN=$(date '+%Y-%m-%d %H:%M:%S')
    ELAPSED=$(( END_TS - START_TS ))

    # Echo last few lines of output so user sees what happened
    echo "$OUT" | tail -8 | tee -a "$LOG_FILE"

    if [ $RC -eq 0 ]; then
        log ""
        log "  ✓ [$D] SUCCESS  start=$START_HUMAN  end=$END_HUMAN  elapsed=${ELAPSED}s ($(( ELAPSED / 60 ))m$(( ELAPSED % 60 ))s)"
        SUCCESS=$((SUCCESS + 1))
        TIMINGS+=("$D  SUCCESS  ${ELAPSED}s")
    else
        log ""
        log "  ❌ [$D] FAILED (exit $RC)  start=$START_HUMAN  end=$END_HUMAN  elapsed=${ELAPSED}s"
        FAILURES+=("$D")
        TIMINGS+=("$D  FAILED   ${ELAPSED}s")
    fi
done

BATCH_END=$(date +%s)
BATCH_ELAPSED=$(( BATCH_END - BATCH_START ))

log ""
log "════════════════════════════════════════════════════════════"
log "  Batch summary"
log "  Finished:    $(date '+%Y-%m-%d %H:%M:%S %Z')"
log "  Success:     $SUCCESS / ${#DATES[@]}"
if [ ${#FAILURES[@]} -gt 0 ]; then
    log "  Failures:    ${FAILURES[*]}"
fi
log "  Total time:  ${BATCH_ELAPSED}s ($(( BATCH_ELAPSED / 60 ))m$(( BATCH_ELAPSED % 60 ))s)"
log "  Avg/report:  $(( BATCH_ELAPSED / ${#DATES[@]} ))s"
log "════════════════════════════════════════════════════════════"

log ""
log "Per-report timings:"
for row in "${TIMINGS[@]}"; do
    log "  $row"
done

log ""
log "Output paths (HTML + PDF):"
for D in "${DATES[@]}"; do
    DCOMPACT=${D//-/}
    HTML=$(ls -t ~/ifaenv/out/production/${DCOMPACT}/ningbo/*.html 2>/dev/null | head -1)
    PDF=$(ls -t ~/ifaenv/out/production/${DCOMPACT}/ningbo/*.pdf  2>/dev/null | head -1)
    if [ -n "$HTML" ]; then
        log "  $D"
        log "    HTML: file://${HTML}"
        [ -n "$PDF" ] && log "    PDF:  file://${PDF}"
    fi
done

log ""
log "Full log saved to: $LOG_FILE"
