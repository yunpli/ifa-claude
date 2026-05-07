#!/usr/bin/env bash
set -euo pipefail

# Third-party scheduler entry for Beijing 04:00 SME briefing.
# First checks whether the Beijing run date is an A-share trading day. If not,
# it prints a structured no-op JSON and exits 0 so delivery agents can send a
# "non-trading day" notification without treating the job as failed.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="/Users/neoclaw/claude/ifaenv/logs/sme_briefing"
OUT_ROOT="/Users/neoclaw/claude/ifaenv/out"
mkdir -p "${LOG_DIR}"

cd "${ROOT_DIR}"

RUN_STAMP="$(TZ=Asia/Shanghai date +%Y%m%d_%H%M%S)"
LOG_FILE="${LOG_DIR}/sme_briefing_${RUN_STAMP}.log"
START_TS="$(date +%s)"

GATE_ARGS=(uv run python scripts/sme_daily_gate.py --kind brief --brief-target previous-trading-day --json)
if [[ -n "${SME_RUN_DATE:-}" ]]; then
  GATE_ARGS+=(--date "${SME_RUN_DATE}")
fi
GATE_JSON="$("${GATE_ARGS[@]}")"
echo "${GATE_JSON}" | tee "${LOG_FILE}"

GATE_ACTION="$(GATE_JSON="${GATE_JSON}" uv run python - <<'PY'
import json, os
print(json.loads(os.environ["GATE_JSON"])["action"])
PY
)"
TARGET_TRADE_DATE="$(GATE_JSON="${GATE_JSON}" uv run python - <<'PY'
import json, os
print(json.loads(os.environ["GATE_JSON"]).get("target_trade_date") or "")
PY
)"

if [[ "${GATE_ACTION}" == "skip" ]]; then
  exit 0
fi
if [[ "${GATE_ACTION}" != "run" || -z "${TARGET_TRADE_DATE}" ]]; then
  echo "SME briefing gate did not return a runnable target date." | tee -a "${LOG_FILE}"
  exit 2
fi

RUN_MODE="${SME_BRIEF_RUN_MODE:-production}"
FORMAT="${SME_BRIEF_FORMAT:-html}"
PARAMS_PROFILE="${SME_MARKET_STRUCTURE_PROFILE:-}"
EXTERNAL_SUMMARY="${SME_EXTERNAL_SUMMARY:-}"
TARGET_COMPACT="${TARGET_TRADE_DATE//-/}"
OUTPUT_DIR="${OUT_ROOT}/${RUN_MODE}/${TARGET_COMPACT}/sme"
mkdir -p "${OUTPUT_DIR}"
OUTPUT_PATH="${OUTPUT_DIR}/CN_sme_brief_${TARGET_COMPACT}_$(TZ=Asia/Shanghai date +%H%M).${FORMAT}"

{
  echo "SME briefing started_at_bjt=$(TZ=Asia/Shanghai date)"
  echo "root=${ROOT_DIR}"
  echo "log=${LOG_FILE}"
  echo "run_mode=${RUN_MODE}"
  echo "format=${FORMAT}"
  echo "target_trade_date=${TARGET_TRADE_DATE}"
  echo "output_path=${OUTPUT_PATH}"
} | tee -a "${LOG_FILE}"

CMD=(
  uv run python -m ifa.cli sme brief
  --date "${TARGET_TRADE_DATE}"
  --format "${FORMAT}"
  --run-mode "${RUN_MODE}"
  --output "${OUTPUT_PATH}"
)
if [[ -n "${PARAMS_PROFILE}" ]]; then
  CMD+=(--params-profile "${PARAMS_PROFILE}")
fi
if [[ -n "${EXTERNAL_SUMMARY}" ]]; then
  CMD+=(--external-summary "${EXTERNAL_SUMMARY}")
fi

set +e
"${CMD[@]}" 2>&1 | tee -a "${LOG_FILE}"
BRIEF_STATUS=${PIPESTATUS[0]}
set -e

END_TS="$(date +%s)"
ELAPSED_SECONDS=$((END_TS - START_TS))

uv run python - <<PY | tee -a "${LOG_FILE}"
import json
payload = {
    "status": "success" if ${BRIEF_STATUS} == 0 else "failed",
    "action": "run",
    "job_kind": "brief",
    "target_trade_date": "${TARGET_TRADE_DATE}",
    "run_mode": "${RUN_MODE}",
    "format": "${FORMAT}",
    "output_path": "${OUTPUT_PATH}",
    "log_file": "${LOG_FILE}",
    "elapsed_seconds": ${ELAPSED_SECONDS},
    "exit_code": ${BRIEF_STATUS},
}
print(json.dumps(payload, ensure_ascii=False, indent=2))
PY

exit "${BRIEF_STATUS}"
