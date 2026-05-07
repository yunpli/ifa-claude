#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 YEAR" >&2
  exit 1
fi

YEAR="$1"
START_DATE="${YEAR}-01-01"
END_DATE="${YEAR}-12-31"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="/Users/neoclaw/claude/ifaenv/logs/sme_backfill"
mkdir -p "${LOG_DIR}"

cd "${ROOT_DIR}"

LOG_FILE="${LOG_DIR}/sme_backfill_${YEAR}_$(date +%Y%m%d_%H%M%S).log"

measure_total_bytes() {
  uv run python - <<'PY'
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    total = c.execute(text("""
        SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'sme' AND c.relkind = 'r'
    """)).scalar_one()
print(int(total))
PY
}

print_table_sizes() {
  uv run python - <<'PY'
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    rows = c.execute(text("""
        SELECT c.relname,
               pg_total_relation_size(c.oid) AS total_bytes,
               pg_relation_size(c.oid) AS table_bytes,
               pg_indexes_size(c.oid) AS index_bytes
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'sme' AND c.relkind = 'r'
        ORDER BY pg_total_relation_size(c.oid) DESC
    """)).fetchall()
    for table, total, table_bytes, index_bytes in rows:
        count = c.execute(text(f"SELECT COUNT(*) FROM sme.{table}")).scalar_one()
        print(f"table={table}\trows={count}\ttotal_bytes={int(total)}\ttable_bytes={int(table_bytes)}\tindex_bytes={int(index_bytes)}")
PY
}

BEFORE_BYTES="$(measure_total_bytes)"
START_TS="$(date +%s)"

{
  echo "SME backfill ${YEAR} started_at=$(date)"
  echo "range=${START_DATE}:${END_DATE}"
  echo "before_total_bytes=${BEFORE_BYTES}"
  awk "BEGIN {printf \"before_total_gb=%.6f\\n\", ${BEFORE_BYTES}/1024/1024/1024}"
  echo "before_table_sizes_begin"
  print_table_sizes
  echo "before_table_sizes_end"
} | tee "${LOG_FILE}"

set +e
uv run python -m ifa.cli sme etl backfill \
  --source-mode prefer_smartmoney \
  --start "${START_DATE}" \
  --end "${END_DATE}" \
  --run-mode manual \
  --labels \
  --max-storage-gb 10 \
  --json 2>&1 | tee -a "${LOG_FILE}"
CMD_STATUS=${PIPESTATUS[0]}
set -e

END_TS="$(date +%s)"
AFTER_BYTES="$(measure_total_bytes)"
DELTA_BYTES=$((AFTER_BYTES - BEFORE_BYTES))
ELAPSED_SECONDS=$((END_TS - START_TS))

{
  echo "after_table_sizes_begin"
  print_table_sizes
  echo "after_table_sizes_end"
  echo "SME backfill ${YEAR} finished_at=$(date)"
  echo "exit_code=${CMD_STATUS}"
  echo "elapsed_seconds=${ELAPSED_SECONDS}"
  echo "before_total_bytes=${BEFORE_BYTES}"
  echo "after_total_bytes=${AFTER_BYTES}"
  echo "delta_bytes=${DELTA_BYTES}"
  awk "BEGIN {printf \"after_total_gb=%.6f\\n\", ${AFTER_BYTES}/1024/1024/1024}"
  awk "BEGIN {printf \"delta_gb=%.6f\\n\", ${DELTA_BYTES}/1024/1024/1024}"
  awk "BEGIN {if (${ELAPSED_SECONDS} > 0) printf \"delta_mb_per_min=%.3f\\n\", (${DELTA_BYTES}/1024/1024)/(${ELAPSED_SECONDS}/60); else print \"delta_mb_per_min=NA\"}"
} | tee -a "${LOG_FILE}"

exit "${CMD_STATUS}"
