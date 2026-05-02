#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Generate SmartMoney evening reports for the last 5 trading days of April
# 2026, in production mode, with PDF.
#
# Output layout (V2.1.3+):
#   ~/claude/ifaenv/out/production/<YYYYMMDD>/smartmoney/CN_smartmoney_evening_*.html
#                                                         CN_smartmoney_evening_*.pdf
#
# Each report timed in two phases:
#   · HTML phase: `ifa smartmoney evening`              (LLM-bound, slow)
#   · PDF  phase: `scripts/html_to_pdf.py <html>`       (Chrome, fast)
#
# Cost estimate: ~$5-15 in LLM calls
# Time estimate: ~10-25 min wall time
#
# V2.1.2 PREREQUISITE
# -------------------
# V2.1.2 changed factor SQL to use L2-own pct_change (was L1 proxy), so
# `factor_daily` / `sector_state_daily` and the persisted RF/XGB models need
# regeneration before generating fresh reports. Aborts unless either:
#   · marker file /tmp/.ifa_smartmoney_recompute_v2.1.2.done exists
#     (set by scripts/recompute_smartmoney_required.sh on success), OR
#   · You pass --skip-prereq-check
#
# Usage:
#   cd /Users/neoclaw/claude/ifa-claude
#   bash scripts/recompute_smartmoney_required.sh   # ~30-90 min, RUN FIRST
#   bash scripts/run_smartmoney_5days.sh
#   bash scripts/run_smartmoney_5days.sh --skip-existing
#   bash scripts/run_smartmoney_5days.sh --dry-run
#   bash scripts/run_smartmoney_5days.sh --skip-prereq-check
# ─────────────────────────────────────────────────────────────────────────────
set -u

cd "$(dirname "$0")/.."

DATES=(2026-04-24 2026-04-27 2026-04-28 2026-04-29 2026-04-30)
LOG=/tmp/ifa_smartmoney_run.log
TIMING_TSV=/tmp/ifa_smartmoney_timing.tsv
OUT_ROOT="$HOME/claude/ifaenv/out/production"

DRY_RUN=0
SKIP_EXISTING=0
SKIP_PREREQ=0
PREREQ_MARKER="/tmp/.ifa_smartmoney_recompute_v2.1.2.done"
for arg in "$@"; do
  case "$arg" in
    --dry-run)             DRY_RUN=1 ;;
    --skip-existing)       SKIP_EXISTING=1 ;;
    --skip-prereq-check)   SKIP_PREREQ=1 ;;
    *) echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

# V2.1.2 prerequisite guard
if [[ $SKIP_PREREQ -eq 0 && $DRY_RUN -eq 0 ]]; then
  if [[ ! -f "$PREREQ_MARKER" ]]; then
    cat <<EOF >&2
ERROR: V2.1.2 prerequisite not met.

The factor SQL was changed in V2.1.2 to use per-L2 pct_change (was L1 proxy).
You must run scripts/recompute_smartmoney_required.sh first, which will:
  1. Recompute factor_daily / sector_state_daily / stock_signals for V2.1.2
  2. Retrain RF + XGB models on the new factor distributions
  3. Save a marker at $PREREQ_MARKER

Run:
  bash scripts/recompute_smartmoney_required.sh

To bypass this guard (NOT recommended): pass --skip-prereq-check
EOF
    exit 3
  fi
fi

echo "iFA SmartMoney evening production batch — $(date)" | tee "$LOG"
echo "Dates : ${DATES[*]}"                                | tee -a "$LOG"
echo "Out   : $OUT_ROOT/<YYYYMMDD>/smartmoney/"           | tee -a "$LOG"
echo "Timing: $TIMING_TSV"                                | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"

printf "report_date\tslot\thtml_sec\tpdf_sec\ttotal_sec\thtml_path\tstatus\n" > "$TIMING_TSV"

declare -i ok=0 fail=0 skipped=0
declare -a failures=()

for d in "${DATES[@]}"; do
  d_compact="${d//-/}"
  out_dir="$OUT_ROOT/$d_compact/smartmoney"
  label="smartmoney evening $d"

  if [[ $SKIP_EXISTING -eq 1 ]]; then
    pattern="$out_dir/CN_smartmoney_evening_${d_compact}_*.html"
    if compgen -G "$pattern" >/dev/null; then
      echo "[SKIP] $label  (existing HTML in $out_dir)" | tee -a "$LOG"
      printf "%s\tevening\t-\t-\t-\t-\tSKIP\n" "$d" >> "$TIMING_TSV"
      skipped+=1
      continue
    fi
  fi

  echo ""                                              | tee -a "$LOG"
  echo "── $(date '+%H:%M:%S') $label ──"              | tee -a "$LOG"

  if [[ $DRY_RUN -eq 1 ]]; then
    echo "  [DRY] uv run python -m ifa.cli smartmoney evening --report-date $d --mode production --triggered-by v2.1.3-prod-batch" | tee -a "$LOG"
    echo "  [DRY] uv run python scripts/html_to_pdf.py <html_path>" | tee -a "$LOG"
    printf "%s\tevening\t-\t-\t-\t-\tDRY\n" "$d" >> "$TIMING_TSV"
    ok+=1
    continue
  fi

  # ── Phase 1: HTML ────────────────────────────────────────────────────────
  t0=$(date +%s)
  html_output=$(uv run python -m ifa.cli smartmoney evening \
      --report-date "$d" \
      --mode production \
      --triggered-by "v2.1.3-prod-batch" 2>&1)
  rc_html=$?
  t1=$(date +%s)
  html_sec=$((t1 - t0))

  echo "$html_output" | tee -a "$LOG" >/dev/null

  if [[ $rc_html -ne 0 ]]; then
    echo "  ✗ HTML failed (rc=$rc_html, ${html_sec}s)" | tee -a "$LOG"
    printf "%s\tevening\t%d\t-\t%d\t-\tFAIL_HTML\n" "$d" "$html_sec" "$html_sec" >> "$TIMING_TSV"
    fail+=1
    failures+=("$label  (HTML rc=$rc_html)")
    continue
  fi

  html_path=$(echo "$html_output" | grep -E "^Report saved:" | head -1 | sed -E 's/^Report saved:[[:space:]]*//')
  if [[ -z "$html_path" || ! -f "$html_path" ]]; then
    echo "  ✗ Could not parse HTML path (${html_sec}s)" | tee -a "$LOG"
    printf "%s\tevening\t%d\t-\t%d\t-\tNO_PATH\n" "$d" "$html_sec" "$html_sec" >> "$TIMING_TSV"
    fail+=1
    failures+=("$label  (no HTML path)")
    continue
  fi
  echo "  ✓ HTML  ${html_sec}s  → $html_path"          | tee -a "$LOG"

  # ── Phase 2: PDF ─────────────────────────────────────────────────────────
  t2=$(date +%s)
  if uv run python scripts/html_to_pdf.py "$html_path" 2>&1 | tee -a "$LOG"; then
    t3=$(date +%s)
    pdf_sec=$((t3 - t2))
    total_sec=$((html_sec + pdf_sec))
    echo "  ✓ PDF   ${pdf_sec}s  (total ${total_sec}s)" | tee -a "$LOG"
    printf "%s\tevening\t%d\t%d\t%d\t%s\tOK\n" "$d" "$html_sec" "$pdf_sec" "$total_sec" "$html_path" >> "$TIMING_TSV"
    ok+=1
  else
    t3=$(date +%s)
    pdf_sec=$((t3 - t2))
    total_sec=$((html_sec + pdf_sec))
    echo "  ✗ PDF failed (${pdf_sec}s)"                | tee -a "$LOG"
    printf "%s\tevening\t%d\t%d\t%d\t%s\tFAIL_PDF\n" "$d" "$html_sec" "$pdf_sec" "$total_sec" "$html_path" >> "$TIMING_TSV"
    fail+=1
    failures+=("$label  (PDF failed; HTML ok)")
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
echo "── Timing summary (sec) ──"                        | tee -a "$LOG"
if command -v awk >/dev/null; then
  awk -F'\t' 'NR>1 && $7=="OK" {
        h+=$3; p+=$4; n++;
        if ($3>hmax) {hmax=$3; hmax_d=$1}
        if ($4>pmax) {pmax=$4; pmax_d=$1}
    }
    END {
        if (n>0) {
            printf "  reports:        %d\n", n
            printf "  HTML  total:    %d s   (%.1f min) | mean %.1f s | max %d s (%s)\n", h, h/60, h/n, hmax, hmax_d
            printf "  PDF   total:    %d s   (%.1f min) | mean %.1f s | max %d s (%s)\n", p, p/60, p/n, pmax, pmax_d
            printf "  TOTAL elapsed:  %d s   (%.1f min)\n", h+p, (h+p)/60
        }
    }' "$TIMING_TSV" | tee -a "$LOG"
fi

echo ""
echo "Log     : $LOG"
echo "Timing  : $TIMING_TSV"
echo "Output  : $OUT_ROOT/<YYYYMMDD>/smartmoney/"
