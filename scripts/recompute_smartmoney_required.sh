#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# REQUIRED for V2.1.2+ — fresh SmartMoney compute + retrain + OOS validation.
#
# WHY THIS IS NOW REQUIRED
# ------------------------
# V2.1.2 changed all 5 smartmoney factor SQL joins from `l1_code` to L2-with-
# L1-fallback. The factor inputs (`pct_change` per L2 sector) now reflect
# real L2 divergence instead of inheriting the L1 parent's value. Sample
# 2026-04-30: 电子 L1's 6 children spread 5.73% — ML model previously saw
# 0% spread. Re-compute + retrain is needed to pick up these new signals.
#
# Earlier V2.1.0 / V2.1.1: this script was optional. As of V2.1.2 it is a
# prerequisite for any fresh SmartMoney report run.
#
# WHAT IT DOES (in order)
# -----------------------
#   1. Recompute factor_daily / sector_state / leader / candidate for the
#      training window (2021-01-04 → 2025-10-31) and OOS window
#      (2025-11-01 → today). Idempotent (UPSERT) — overwrites stale L1-proxy
#      values with V2.1.2 L2 values.
#   2. Train RF (short, horizon=1d) + XGBoost (long, horizon=20d) on SW L2
#      in-sample; evaluate on OOS. Persists to
#      ~/claude/ifaenv/models/smartmoney/<version>/.
#   3. Print the OOS metric summary.
#
# Cost: TuShare-free (DB-only); CPU-bound; ~30-90 min total.
# Disk: model files ~10-50 MB per version.
#
# Usage:
#   cd /Users/neoclaw/claude/ifa-claude
#   bash scripts/recompute_smartmoney_required.sh
#   bash scripts/recompute_smartmoney_required.sh --version v2026_05_v2  # A/B
#   bash scripts/recompute_smartmoney_required.sh --skip-compute         # only retrain
# ─────────────────────────────────────────────────────────────────────────────
set -u

cd "$(dirname "$0")/.."

VERSION="v2026_05"
IS_START="2021-01-04"
IS_END="2025-10-31"
OOS_START="2025-11-01"
OOS_END="2026-04-30"

SKIP_COMPUTE=0
LOG=/tmp/ifa_smartmoney_recompute.log

for arg in "$@"; do
  case "$arg" in
    --skip-compute)   SKIP_COMPUTE=1 ;;
    --version)        shift; VERSION="$1" ;;
    --version=*)      VERSION="${arg#--version=}" ;;
    *) echo "Unknown arg: $arg"; exit 2 ;;
  esac
done

echo "SmartMoney recompute + retrain — $(date)"     | tee "$LOG"
echo "Version    : $VERSION"                        | tee -a "$LOG"
echo "In-sample  : $IS_START → $IS_END"             | tee -a "$LOG"
echo "OOS        : $OOS_START → $OOS_END"           | tee -a "$LOG"
echo "Skip-compute: $SKIP_COMPUTE"                  | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"

# ── 1. Compute (factor_daily / sector_state / leader / candidate) ────────────
if [[ $SKIP_COMPUTE -eq 0 ]]; then
  echo ""                                            | tee -a "$LOG"
  echo "── [1/2] Computing factor_daily / sector_state / leader / candidate ──" | tee -a "$LOG"
  echo "    Range: $IS_START → $OOS_END"             | tee -a "$LOG"

  uv run python -m ifa.cli smartmoney compute \
      --start "$IS_START" --end "$OOS_END" \
      --mode production 2>&1 | tee -a "$LOG"
else
  echo "── [1/2] Skipped (--skip-compute)"           | tee -a "$LOG"
fi

# ── 2. Train RF + XGBoost; evaluate OOS; persist ─────────────────────────────
echo ""                                              | tee -a "$LOG"
echo "── [2/2] Training RF (short=1d) + XGB (long=20d); OOS eval; persist ──" | tee -a "$LOG"

uv run python -m ifa.cli smartmoney train \
    --in-sample-start "$IS_START" --in-sample-end "$IS_END" \
    --oos-start "$OOS_START" --oos-end "$OOS_END" \
    --version "$VERSION" \
    --short-horizon 1 --long-horizon 20 \
    --source sw_l2 \
    --mode production 2>&1 | tee -a "$LOG"

echo ""                                              | tee -a "$LOG"
echo "================================================================================" | tee -a "$LOG"
echo "Done at $(date)"                               | tee -a "$LOG"
echo "Models : ~/claude/ifaenv/models/smartmoney/$VERSION/" | tee -a "$LOG"
echo "Log    : $LOG"

# Drop V2.1.2 prerequisite marker so run_smartmoney_5days.sh proceeds.
PREREQ_MARKER="/tmp/.ifa_smartmoney_recompute_v2.1.2.done"
date > "$PREREQ_MARKER"
echo "Marker : $PREREQ_MARKER"

echo ""
echo "Next: run  bash scripts/run_smartmoney_5days.sh"
