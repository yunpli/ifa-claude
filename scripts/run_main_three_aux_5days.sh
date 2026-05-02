#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Generate iFA "1 main + 3 auxiliary" reports for the last 5 trading days of
# April 2026, in production mode, with PDF.
#
# Output layout (V2.1.3+):
#   ~/claude/ifaenv/out/production/<YYYYMMDD>/<family>/CN_*.html
#                                                       CN_*.pdf
#
# Each report is timed in two phases:
#   · HTML phase: `ifa generate <family> --slot <slot>` (LLM-bound, slow)
#   · PDF  phase: `scripts/html_to_pdf.py <html>`       (Chrome, fast)
#
# Final summary table prints both timings per report so you can spot outliers.
#
# 1 main:        market    (morning + noon + evening)  ×5 = 15 reports
# 3 auxiliary:   macro     (morning + evening)         ×5 = 10 reports
#                asset     (morning + evening)         ×5 = 10 reports
#                tech      (morning + evening)         ×5 = 10 reports
#                                                Total = 45 reports
#
# Cost estimate: ~$30-90 in LLM calls
# Time estimate: ~90-225 min serial wall time
#
# Usage:
#   cd /Users/neoclaw/claude/ifa-claude
#   bash scripts/run_main_three_aux_5days.sh
#   bash scripts/run_main_three_aux_5days.sh --skip-existing
#   bash scripts/run_main_three_aux_5days.sh --dry-run
# ─────────────────────────────────────────────────────────────────────────────
set -u

cd "$(dirname "$0")/.."

DATES=(2026-04-24 2026-04-27 2026-04-28 2026-04-29 2026-04-30)
LOG=/tmp/ifa_main_3aux_run.log
TIMING_TSV=/tmp/ifa_main_3aux_timing.tsv
OUT_ROOT="$HOME/claude/ifaenv/out/production"

DRY_RUN=0
SKIP_EXISTING=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)        DRY_RUN=1 ;;
    --skip-existing)  SKIP_EXISTING=1 ;;
    *) echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

# CLI 'family' name → folder name on disk (market uses 'main' internally → 'market' folder)
folder_for_family() { case "$1" in market) echo market;; *) echo "$1";; esac; }

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
echo "Out   : $OUT_ROOT/<YYYYMMDD>/<family>/"         | tee -a "$LOG"
echo "Timing: $TIMING_TSV"                            | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"

# Initialise timing TSV with header
printf "report_date\tfamily\tslot\thtml_sec\tpdf_sec\ttotal_sec\thtml_path\tstatus\n" > "$TIMING_TSV"

declare -i ok=0 fail=0 skipped=0
declare -a failures=()

for d in "${DATES[@]}"; do
  d_compact="${d//-/}"
  for t in "${TASKS[@]}"; do
    family="${t% *}"
    slot="${t#* }"
    folder="$(folder_for_family "$family")"
    out_dir="$OUT_ROOT/$d_compact/$folder"
    label="$family $slot $d"

    # Skip-existing: any HTML matching this family/slot/date in nested dir?
    if [[ $SKIP_EXISTING -eq 1 ]]; then
      pattern="$out_dir/CN_${family}_${slot}_*${d_compact}_*.html"
      if compgen -G "$pattern" >/dev/null; then
        echo "[SKIP] $label  (existing HTML in $out_dir)" | tee -a "$LOG"
        printf "%s\t%s\t%s\t-\t-\t-\t-\tSKIP\n" "$d" "$folder" "$slot" >> "$TIMING_TSV"
        skipped+=1
        continue
      fi
    fi

    echo ""                                             | tee -a "$LOG"
    echo "── $(date '+%H:%M:%S') $label ──"             | tee -a "$LOG"

    if [[ $DRY_RUN -eq 1 ]]; then
      echo "  [DRY] uv run python -m ifa.cli generate $family --slot $slot --report-date $d --mode production --triggered-by v2.1.3-prod-batch" | tee -a "$LOG"
      echo "  [DRY] uv run python scripts/html_to_pdf.py <html_path>" | tee -a "$LOG"
      printf "%s\t%s\t%s\t-\t-\t-\t-\tDRY\n" "$d" "$folder" "$slot" >> "$TIMING_TSV"
      ok+=1
      continue
    fi

    # ── Phase 1: HTML generation ────────────────────────────────────────────
    t0=$(date +%s)
    html_output=$(uv run python -m ifa.cli generate "$family" \
        --slot "$slot" \
        --report-date "$d" \
        --mode production \
        --triggered-by "v2.1.3-prod-batch" 2>&1)
    rc_html=$?
    t1=$(date +%s)
    html_sec=$((t1 - t0))

    echo "$html_output" | tee -a "$LOG" >/dev/null

    if [[ $rc_html -ne 0 ]]; then
      echo "  ✗ HTML failed (rc=$rc_html, ${html_sec}s)" | tee -a "$LOG"
      printf "%s\t%s\t%s\t%d\t-\t%d\t-\tFAIL_HTML\n" "$d" "$folder" "$slot" "$html_sec" "$html_sec" >> "$TIMING_TSV"
      fail+=1
      failures+=("$label  (HTML rc=$rc_html)")
      continue
    fi

    # Parse "Report saved: <path>" from CLI output
    html_path=$(echo "$html_output" | grep -E "^Report saved:" | head -1 | sed -E 's/^Report saved:[[:space:]]*//')
    if [[ -z "$html_path" || ! -f "$html_path" ]]; then
      echo "  ✗ Could not parse HTML path from CLI output (${html_sec}s)" | tee -a "$LOG"
      printf "%s\t%s\t%s\t%d\t-\t%d\t-\tNO_PATH\n" "$d" "$folder" "$slot" "$html_sec" "$html_sec" >> "$TIMING_TSV"
      fail+=1
      failures+=("$label  (no HTML path)")
      continue
    fi
    echo "  ✓ HTML  ${html_sec}s  → $html_path" | tee -a "$LOG"

    # ── Phase 2: PDF generation ─────────────────────────────────────────────
    t2=$(date +%s)
    if uv run python scripts/html_to_pdf.py "$html_path" 2>&1 | tee -a "$LOG"; then
      t3=$(date +%s)
      pdf_sec=$((t3 - t2))
      total_sec=$((html_sec + pdf_sec))
      echo "  ✓ PDF   ${pdf_sec}s  (total ${total_sec}s)" | tee -a "$LOG"
      printf "%s\t%s\t%s\t%d\t%d\t%d\t%s\tOK\n" "$d" "$folder" "$slot" "$html_sec" "$pdf_sec" "$total_sec" "$html_path" >> "$TIMING_TSV"
      ok+=1
    else
      t3=$(date +%s)
      pdf_sec=$((t3 - t2))
      total_sec=$((html_sec + pdf_sec))
      echo "  ✗ PDF failed (${pdf_sec}s)" | tee -a "$LOG"
      printf "%s\t%s\t%s\t%d\t%d\t%d\t%s\tFAIL_PDF\n" "$d" "$folder" "$slot" "$html_sec" "$pdf_sec" "$total_sec" "$html_path" >> "$TIMING_TSV"
      fail+=1
      failures+=("$label  (PDF failed; HTML ok)")
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

# ── Aggregate timing summary ────────────────────────────────────────────────
echo ""                                                 | tee -a "$LOG"
echo "── Timing summary (sec) ──"                       | tee -a "$LOG"
if command -v awk >/dev/null; then
  awk -F'\t' 'NR>1 && $8=="OK" {
        h+=$4; p+=$5; n++;
        if ($4>hmax) {hmax=$4; hmax_label=$1" "$2" "$3}
        if ($5>pmax) {pmax=$5; pmax_label=$1" "$2" "$3}
    }
    END {
        if (n>0) {
            printf "  reports:        %d\n", n
            printf "  HTML  total:    %d s   (%.1f min) | mean %.1f s | max %d s (%s)\n", h, h/60, h/n, hmax, hmax_label
            printf "  PDF   total:    %d s   (%.1f min) | mean %.1f s | max %d s (%s)\n", p, p/60, p/n, pmax, pmax_label
            printf "  TOTAL elapsed:  %d s   (%.1f min)\n", h+p, (h+p)/60
        }
    }' "$TIMING_TSV" | tee -a "$LOG"
fi

echo ""
echo "Log     : $LOG"
echo "Timing  : $TIMING_TSV  (TSV — open in Excel / pandas)"
echo "Output  : $OUT_ROOT/<YYYYMMDD>/<family>/"
