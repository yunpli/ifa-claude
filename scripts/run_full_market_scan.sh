#!/usr/bin/env bash
# Full-market peer-universe scan for the Research family.
#
# What it does:
#   · Discovers every SW L2 with at least one stock in smartmoney.sw_member_monthly
#     for the current month (~120 L2 cohorts).
#   · For each L2, runs `ifa research scan-universe --l2 <code> --full
#     --concurrency 3` in turn.
#   · 24h freshness skip is on by default — re-running is cheap.
#
# Where the data goes:
#   · api_cache rows  → research.api_cache (PostgreSQL, port 55432)
#   · factor_value    → research.factor_value
#   · run audit       → research.scan_run
#   · No filesystem writes other than this script's log.
#
# Estimated time (cold cache): 5-8 hours wall clock.
# Estimated time (warm cache, all fresh): ~10-15 minutes.
#
# Safe to:
#   · Ctrl-C any time. Skip-fresh ensures resumed run skips completed L2s.
#   · Re-run repeatedly. Idempotent.
#
# Usage:
#   bash scripts/run_full_market_scan.sh                     # all L2s
#   bash scripts/run_full_market_scan.sh --concurrency 5     # faster (Tushare permitting)
#   bash scripts/run_full_market_scan.sh --no-skip-fresh     # force recompute everything

set -euo pipefail

cd "$(dirname "$0")/.."

CONCURRENCY=3
SKIP_FRESH_FLAG="--skip-fresh"

while [[ $# -gt 0 ]]; do
  case $1 in
    --concurrency) CONCURRENCY="$2"; shift 2;;
    --no-skip-fresh) SKIP_FRESH_FLAG="--no-skip-fresh"; shift;;
    --help|-h)
      sed -n '1,30p' "$0"; exit 0;;
    *) echo "Unknown arg: $1"; exit 2;;
  esac
done

mkdir -p tmp
LOG="tmp/full_scan_$(date -u +%Y%m%dT%H%M%SZ).log"
echo "→ Log file: $LOG"
echo "→ Concurrency: $CONCURRENCY"
echo "→ Skip fresh: $SKIP_FRESH_FLAG"
echo

echo "Discovering SW L2 cohorts for the current month …"
L2_LIST=$(uv run python - <<'PY'
from datetime import date
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    rows = c.execute(text("""
        SELECT DISTINCT l2_code
        FROM smartmoney.sw_member_monthly
        WHERE snapshot_month = date_trunc('month', CURRENT_DATE)::date
          AND l2_code IS NOT NULL
        ORDER BY l2_code
    """)).fetchall()
print(' '.join(r[0] for r in rows))
PY
)

read -r -a L2_CODES <<< "$L2_LIST"
TOTAL=${#L2_CODES[@]}
echo "→ ${TOTAL} L2 cohorts discovered"
echo

START_TS=$(date +%s)
i=0
for L2 in "${L2_CODES[@]}"; do
  i=$((i+1))
  echo "════════════════════════════════════════════════"
  echo "  [$i/$TOTAL] L2=$L2  $(date '+%Y-%m-%d %H:%M:%S')"
  echo "════════════════════════════════════════════════"
  uv run ifa research scan-universe \
    --l2 "$L2" \
    --full \
    --concurrency "$CONCURRENCY" \
    $SKIP_FRESH_FLAG 2>&1 | tee -a "$LOG"
  echo
done

END_TS=$(date +%s)
ELAPSED=$((END_TS - START_TS))
echo "════════════════════════════════════════════════"
echo "  Full-market scan done in ${ELAPSED}s ($(printf '%dh%02dm' $((ELAPSED/3600)) $(((ELAPSED%3600)/60))))"
echo "════════════════════════════════════════════════"

echo
echo "Quick health check — last 10 scan_run audit rows:"
uv run python - <<'PY'
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    rows = c.execute(text("""
        SELECT l2_code, l2_name, status, members_total,
               scanned, skipped_fresh, failed,
               EXTRACT(EPOCH FROM (completed_at - started_at))::int AS dur_s
        FROM research.scan_run
        ORDER BY started_at DESC LIMIT 10
    """)).fetchall()
    for r in rows: print(' ', r)

    succ = c.execute(text("""
        SELECT status, COUNT(*) FROM research.scan_run
        WHERE started_at > NOW() - INTERVAL '12 hours'
        GROUP BY status ORDER BY 1
    """)).fetchall()
    print('\n  Recent (12h) status distribution:')
    for r in succ: print(f'   {r[0]}: {r[1]}')

    n = c.execute(text("""
        SELECT COUNT(DISTINCT ts_code) FROM research.factor_value
    """)).scalar()
    print(f'\n  Total stocks in factor_value: {n}')
PY
