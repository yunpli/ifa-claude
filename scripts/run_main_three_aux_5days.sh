#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Generate iFA "1 main + 3 auxiliary" reports for the last 5 trading days of
# April 2026, in production mode, with PDF.
#
# 1 main:        market    (morning + noon + evening)  ×5 = 15 reports
# 3 auxiliary:   macro     (morning + evening)         ×5 = 10 reports
#                asset     (morning + evening)         ×5 = 10 reports
#                tech      (morning + evening)         ×5 = 10 reports
#                                                Total = 45 reports
#
# Output: ~/claude/ifaenv/out/production/CN_<family>_<slot>_<date>_<time>.{html,pdf}
#
# Cost estimate: ~$30-90 in LLM calls (depends on model + report size)
# Time estimate: ~90-225 min wall time (serial; LLM-bound)
#
# Usage:
#   cd /Users/neoclaw/claude/ifa-claude
#   bash scripts/run_main_three_aux_5days.sh                  # all 45
#   bash scripts/run_main_three_aux_5days.sh --skip-existing  # skip days
#                                                             # already done
#   bash scripts/run_main_three_aux_5days.sh --dry-run        # print only
#
# Resilience: each report is run independently. A single failure logs the
# error to /tmp/ifa_main_3aux_run.log and the loop continues to the next.
# Final summary at end.
# ─────────────────────────────────────────────────────────────────────────────
set -u   # unset-var safety; do NOT use -e (we want to continue on errors)

cd "$(dirname "$0")/.."

DATES=(2026-04-24 2026-04-27 2026-04-28 2026-04-29 2026-04-30)
LOG=/tmp/ifa_main_3aux_run.log
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

# Each entry: "family slot"
TASKS=(
  "market morning"
  "market noon"
  "market evening"
  "macro morning"
  "macro evening"
  "asset morning"
  "asset evening"
  "tech morning"
  "tech evening"
)

echo "iFA 1+3 production batch — $(date)"             | tee "$LOG"
echo "Dates : ${DATES[*]}"                            | tee -a "$LOG"
echo "Tasks : ${#TASKS[@]} per day × ${#DATES[@]} days = $((${#TASKS[@]} * ${#DATES[@]})) reports" | tee -a "$LOG"
echo "Output: $OUT_DIR"                               | tee -a "$LOG"
echo "Dry-run: $DRY_RUN  Skip-existing: $SKIP_EXISTING" | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"

declare -i ok=0 fail=0 skipped=0
declare -a failures=()

for d in "${DATES[@]}"; do
  d_compact="${d//-/}"  # 2026-04-30 → 20260430
  for t in "${TASKS[@]}"; do
    family="${t% *}"
    slot="${t#* }"
    label="$family $slot $d"

    # Skip-existing: any HTML matching the date+family+slot already?
    if [[ $SKIP_EXISTING -eq 1 ]]; then
      pattern="$OUT_DIR/CN_${family}_${slot}_*${d_compact}_*.html"
      if compgen -G "$pattern" >/dev/null; then
        echo "[SKIP] $label  (existing HTML found)" | tee -a "$LOG"
        skipped+=1
        continue
      fi
    fi

    cmd=(uv run python -m ifa.cli generate "$family"
         --slot "$slot"
         --report-date "$d"
         --mode production
         --triggered-by "v2.1.1-prod-batch"
         --generate-pdf)

    echo ""                                             | tee -a "$LOG"
    echo "── $(date '+%H:%M:%S') $label ──"             | tee -a "$LOG"
    if [[ $DRY_RUN -eq 1 ]]; then
      printf '  '; printf '%q ' "${cmd[@]}"; echo
      ok+=1
      continue
    fi

    if "${cmd[@]}" 2>&1 | tee -a "$LOG"; then
      # CLI returns 0 even when partial-failed; check the log for "Report saved"
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
done

echo ""                                                 | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"
echo "Done at $(date)"                                  | tee -a "$LOG"
echo "  OK:      $ok"                                   | tee -a "$LOG"
echo "  Failed:  $fail"                                 | tee -a "$LOG"
echo "  Skipped: $skipped"                              | tee -a "$LOG"
if (( fail > 0 )); then
  echo ""                                               | tee -a "$LOG"
  echo "Failures:"                                      | tee -a "$LOG"
  for f in "${failures[@]}"; do echo "  - $f"           | tee -a "$LOG"; done
fi
echo ""                                                 | tee -a "$LOG"
echo "Full log: $LOG"
echo "Output  : $OUT_DIR"
echo "PDFs    : ls $OUT_DIR/*.pdf | wc -l"
