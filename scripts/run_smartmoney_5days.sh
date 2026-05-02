#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Generate SmartMoney evening reports for the last 5 trading days of April
# 2026, in production mode, with PDF.
#
# Output: ~/claude/ifaenv/out/production/CN_smartmoney_evening_<date>_<time>.{html,pdf}
#
# Cost estimate: ~$5-15 in LLM calls
# Time estimate: ~10-25 min wall time
#
# DOES THIS NEED A FRESH COMPUTE / RETRAIN FIRST?
# -----------------------------------------------
# No — verified. SmartMoney factor SQL joins raw_sw_daily on `l1_code`
# (see ifa/families/smartmoney/factors/flow.py:521 and similar).
# The V2.1.1 backfill only added L2 rows; L1 rows were already present and
# unchanged. The persisted ML model (v2026_05) and `factor_daily` /
# `sector_state_daily` rows produced under V2.1 remain valid under V2.1.1.
#
# If you DO want a fresh compute (e.g. to pick up recent code changes that
# you've made in the parallel CC CLI session), see
#   scripts/recompute_smartmoney_optional.sh
# but it is NOT a prerequisite for these report runs.
#
# Usage:
#   cd /Users/neoclaw/claude/ifa-claude
#   bash scripts/run_smartmoney_5days.sh
#   bash scripts/run_smartmoney_5days.sh --skip-existing
#   bash scripts/run_smartmoney_5days.sh --dry-run
# ─────────────────────────────────────────────────────────────────────────────
set -u

cd "$(dirname "$0")/.."

DATES=(2026-04-24 2026-04-27 2026-04-28 2026-04-29 2026-04-30)
LOG=/tmp/ifa_smartmoney_run.log
OUT_DIR="$HOME/claude/ifaenv/out/production"

DRY_RUN=0
SKIP_EXISTING=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)        DRY_RUN=1 ;;
    --skip-existing)  SKIP_EXISTING=1 ;;
    *) echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

echo "iFA SmartMoney evening production batch — $(date)" | tee "$LOG"
echo "Dates : ${DATES[*]}"                                | tee -a "$LOG"
echo "Output: $OUT_DIR"                                   | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"

declare -i ok=0 fail=0 skipped=0
declare -a failures=()

for d in "${DATES[@]}"; do
  d_compact="${d//-/}"
  label="smartmoney evening $d"

  if [[ $SKIP_EXISTING -eq 1 ]]; then
    pattern="$OUT_DIR/CN_smartmoney_evening_${d_compact}_*.html"
    if compgen -G "$pattern" >/dev/null; then
      echo "[SKIP] $label  (existing HTML found)" | tee -a "$LOG"
      skipped+=1
      continue
    fi
  fi

  cmd=(uv run python -m ifa.cli smartmoney evening
       --report-date "$d"
       --mode production
       --triggered-by "v2.1.1-prod-batch"
       --generate-pdf)

  echo ""                                              | tee -a "$LOG"
  echo "── $(date '+%H:%M:%S') $label ──"              | tee -a "$LOG"
  if [[ $DRY_RUN -eq 1 ]]; then
    printf '  '; printf '%q ' "${cmd[@]}"; echo
    ok+=1
    continue
  fi

  if "${cmd[@]}" 2>&1 | tee -a "$LOG"; then
    if tail -50 "$LOG" | grep -q "Report saved:"; then
      ok+=1
    else
      fail+=1
      failures+=("$label  (no Report-saved line)")
    fi
  else
    fail+=1
    failures+=("$label  (non-zero exit)")
  fi
done

echo ""                                                  | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"
echo "Done at $(date)"                                   | tee -a "$LOG"
echo "  OK:      $ok"                                    | tee -a "$LOG"
echo "  Failed:  $fail"                                  | tee -a "$LOG"
echo "  Skipped: $skipped"                               | tee -a "$LOG"
if (( fail > 0 )); then
  echo ""                                                | tee -a "$LOG"
  echo "Failures:"                                       | tee -a "$LOG"
  for f in "${failures[@]}"; do echo "  - $f"            | tee -a "$LOG"; done
fi
echo ""                                                  | tee -a "$LOG"
echo "Full log: $LOG"
echo "PDFs    : ls $OUT_DIR/CN_smartmoney_evening_*.pdf"
