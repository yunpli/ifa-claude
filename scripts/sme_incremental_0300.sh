#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="/Users/neoclaw/claude/ifaenv/logs/sme_incremental"
mkdir -p "${LOG_DIR}"

cd "${ROOT_DIR}"

LOG_FILE="${LOG_DIR}/sme_incremental_$(TZ=Asia/Shanghai date +%Y%m%d_%H%M%S).log"
START_TS="$(date +%s)"

GATE_ARGS=(uv run python scripts/sme_daily_gate.py --kind incremental --json)
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
  echo "SME incremental gate did not return a runnable target date." | tee -a "${LOG_FILE}"
  exit 2
fi

{
  echo "SME incremental started_at_bjt=$(TZ=Asia/Shanghai date)"
  echo "root=${ROOT_DIR}"
  echo "log=${LOG_FILE}"
  echo "target_trade_date=${TARGET_TRADE_DATE}"
} | tee -a "${LOG_FILE}"

set +e
uv run python -m ifa.cli sme etl incremental \
  --as-of "${TARGET_TRADE_DATE}" \
  --run-mode production \
  --source-mode prefer_smartmoney \
  --labels \
  --json 2>&1 | tee -a "${LOG_FILE}"
INCR_STATUS=${PIPESTATUS[0]}
set -e

set +e
uv run python -m ifa.cli sme doctor \
  --check schema,sources,units,contracts \
  --date auto \
  --json 2>&1 | tee -a "${LOG_FILE}"
DOCTOR_STATUS=${PIPESTATUS[0]}
set -e

END_TS="$(date +%s)"
ELAPSED_SECONDS=$((END_TS - START_TS))

{
  echo "SME incremental finished_at_bjt=$(TZ=Asia/Shanghai date)"
  echo "incremental_exit_code=${INCR_STATUS}"
  echo "doctor_exit_code=${DOCTOR_STATUS}"
  echo "elapsed_seconds=${ELAPSED_SECONDS}"
} | tee -a "${LOG_FILE}"

if [[ "${INCR_STATUS}" -ne 0 ]]; then
  exit "${INCR_STATUS}"
fi
exit "${DOCTOR_STATUS}"
