#!/usr/bin/env bash
set -euo pipefail

# Third-party scheduler entry for Beijing 23:00+ SME tuning refresh.
# The script does not assume a cron environment. It writes JSON artifacts under
# ifaenv so external platforms can ingest results without parsing terminal logs.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="/Users/neoclaw/claude/ifaenv/logs/sme_tuning"
OUT_DIR="/Users/neoclaw/claude/ifaenv/out/sme_tuning/nightly"
mkdir -p "${LOG_DIR}" "${OUT_DIR}"

cd "${ROOT_DIR}"

RUN_STAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/sme_nightly_tune_${RUN_STAMP}.log"
ARTIFACT_DIR="${OUT_DIR}/${RUN_STAMP}"
mkdir -p "${ARTIFACT_DIR}"

START_DATE="${SME_TUNE_START:-2026-01-01}"
MIN_SAMPLE_DAYS="${SME_TUNE_MIN_SAMPLE_DAYS:-60}"
PARAMS_PROFILE="${SME_MARKET_STRUCTURE_PROFILE:-baseline}"
PROMOTE_PROFILE="${SME_TUNE_PROMOTE_PROFILE:-}"
APPLY_PROMOTION="${SME_TUNE_APPLY_PROMOTION:-0}"

latest_label_date() {
  uv run python - <<'PY'
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    d = c.execute(text("SELECT max(trade_date) FROM sme.sme_labels_daily")).scalar_one()
print(d or "")
PY
}

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

END_DATE="${SME_TUNE_END:-$(latest_label_date)}"
if [[ -z "${END_DATE}" ]]; then
  echo "No mature sme_labels_daily rows found; cannot run tuning refresh." | tee "${LOG_FILE}"
  exit 2
fi

START_TS="$(date +%s)"
BEFORE_BYTES="$(measure_total_bytes)"

{
  echo "SME nightly tuning started_at=$(date)"
  echo "root=${ROOT_DIR}"
  echo "range=${START_DATE}:${END_DATE}"
  echo "min_sample_days=${MIN_SAMPLE_DAYS}"
  echo "params_profile=${PARAMS_PROFILE}"
  echo "promote_profile=${PROMOTE_PROFILE}"
  echo "apply_promotion=${APPLY_PROMOTION}"
  echo "artifact_dir=${ARTIFACT_DIR}"
  echo "before_total_bytes=${BEFORE_BYTES}"
} | tee "${LOG_FILE}"

set +e
uv run python -m ifa.cli sme compute market-structure \
  --start "${START_DATE}" \
  --end "${END_DATE}" \
  --params-profile "${PARAMS_PROFILE}" \
  --json > "${ARTIFACT_DIR}/market_structure_refresh.json" 2>>"${LOG_FILE}"
MS_STATUS=$?
set -e

set +e
uv run python -m ifa.cli sme compute strategy-eval \
  --start "${START_DATE}" \
  --end "${END_DATE}" \
  --json > "${ARTIFACT_DIR}/strategy_eval.json" 2>>"${LOG_FILE}"
EVAL_STATUS=$?
set -e

set +e
uv run python -m ifa.cli sme tuning-ready \
  --start "${START_DATE}" \
  --end "${END_DATE}" \
  --min-sample-days "${MIN_SAMPLE_DAYS}" \
  --json > "${ARTIFACT_DIR}/tuning_ready.json" 2>>"${LOG_FILE}"
READY_STATUS=$?
set -e

set +e
uv run python -m ifa.cli sme tune bucket-review \
  --start "${START_DATE}" \
  --end "${END_DATE}" \
  --min-sample-days "${MIN_SAMPLE_DAYS}" \
  --json > "${ARTIFACT_DIR}/bucket_review.json" 2>>"${LOG_FILE}"
REVIEW_STATUS=$?
set -e

PROMOTE_STATUS=0
if [[ -n "${PROMOTE_PROFILE}" ]]; then
  PROMOTE_ARGS=(
    uv run python -m ifa.cli sme tune promote-profile
    --candidate-profile "${PROMOTE_PROFILE}"
    --start "${START_DATE}"
    --end "${END_DATE}"
    --min-sample-days "${MIN_SAMPLE_DAYS}"
    --json
  )
  if [[ "${APPLY_PROMOTION}" == "1" ]]; then
    PROMOTE_ARGS+=(--apply)
  fi
  set +e
  "${PROMOTE_ARGS[@]}" > "${ARTIFACT_DIR}/promotion_decision.json" 2>>"${LOG_FILE}"
  PROMOTE_STATUS=$?
  set -e
fi

AFTER_BYTES="$(measure_total_bytes)"
END_TS="$(date +%s)"
ELAPSED_SECONDS=$((END_TS - START_TS))
DELTA_BYTES=$((AFTER_BYTES - BEFORE_BYTES))

uv run python - <<PY > "${ARTIFACT_DIR}/run_summary.json"
import json
payload = {
    "status": "success" if (${MS_STATUS} == 0 and ${EVAL_STATUS} == 0 and ${READY_STATUS} == 0 and ${REVIEW_STATUS} == 0 and ${PROMOTE_STATUS} == 0) else "failed",
    "started_at_epoch": ${START_TS},
    "finished_at_epoch": ${END_TS},
    "elapsed_seconds": ${ELAPSED_SECONDS},
    "start_date": "${START_DATE}",
    "end_date": "${END_DATE}",
    "min_sample_days": ${MIN_SAMPLE_DAYS},
    "params_profile": "${PARAMS_PROFILE}",
    "promote_profile": "${PROMOTE_PROFILE}",
    "apply_promotion": "${APPLY_PROMOTION}",
    "artifact_dir": "${ARTIFACT_DIR}",
    "log_file": "${LOG_FILE}",
    "exit_codes": {
        "market_structure": ${MS_STATUS},
        "strategy_eval": ${EVAL_STATUS},
        "tuning_ready": ${READY_STATUS},
        "bucket_review": ${REVIEW_STATUS},
        "promotion": ${PROMOTE_STATUS},
    },
    "storage": {
        "before_total_bytes": ${BEFORE_BYTES},
        "after_total_bytes": ${AFTER_BYTES},
        "delta_bytes": ${DELTA_BYTES},
    },
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY

{
  echo "SME nightly tuning finished_at=$(date)"
  echo "market_structure_exit_code=${MS_STATUS}"
  echo "strategy_eval_exit_code=${EVAL_STATUS}"
  echo "tuning_ready_exit_code=${READY_STATUS}"
  echo "bucket_review_exit_code=${REVIEW_STATUS}"
  echo "promotion_exit_code=${PROMOTE_STATUS}"
  echo "elapsed_seconds=${ELAPSED_SECONDS}"
  echo "after_total_bytes=${AFTER_BYTES}"
  echo "delta_bytes=${DELTA_BYTES}"
  echo "artifact_dir=${ARTIFACT_DIR}"
} | tee -a "${LOG_FILE}"

if [[ "${MS_STATUS}" -ne 0 ]]; then exit "${MS_STATUS}"; fi
if [[ "${EVAL_STATUS}" -ne 0 ]]; then exit "${EVAL_STATUS}"; fi
if [[ "${READY_STATUS}" -ne 0 ]]; then exit "${READY_STATUS}"; fi
if [[ "${REVIEW_STATUS}" -ne 0 ]]; then exit "${REVIEW_STATUS}"; fi
exit "${PROMOTE_STATUS}"
