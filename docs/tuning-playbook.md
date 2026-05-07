# Tuning Playbook — Cross-Family Overview

> **Audience**: 任何要调参或评估调参成果的人。
> **Per-family deep-dives**:
> - TA — [`docs/ta-tuning-playbook.md`](ta-tuning-playbook.md)
> - Stock Edge — [`docs/stock_edge_tuning_work_list.md`](stock_edge_tuning_work_list.md), [`docs/stock_edge_weekly_tune_runbook.md`](stock_edge_weekly_tune_runbook.md)
> - SmartMoney — [`docs/smartmoney-deep-dive.md`](smartmoney-deep-dive.md)
> - SME — [`docs/sme-data-logic-contracts.md`](sme-data-logic-contracts.md), [`docs/sme-mvp1-work-list.md`](sme-mvp1-work-list.md)

---

## V2.2 release tuning surface

| Family | What gets tuned | Maturity | Tooling | V2.2 status |
|---|---|---|---|---|
| **TA** | 28 setup thresholds, regime gating, decay weights | Iter 19 封版 ✅ | `ifa ta walk-forward` + `ifa ta backtest` | Production |
| **SmartMoney** | RF + XGB hyperparameters per phase, factor weights | v2026_05 frozen ✅ | `ifa smartmoney backtest` + `params freeze` | Production |
| **Stock Edge** | 决策层 horizons.weights.* (5d/10d/20d), continuous overlay bounds, cluster/signal shifts | T3.1 + T3.4 done; **T3.2 + T3.3 pending → V2.2.3** | `_search_overlay()` + `auto_promote_if_passing` | **Quality-improving** |
| **Research** | n/a (deterministic factor calc; LLM only narrates) | – | – | – |
| **SME** | Market-structure bucket ranking, continuous thresholds/weights/penalties, bucket-specific promotion rules | MVP1 ready ✅; first tuning signal says secondary/crowding_risk/avoid > primary | `ifa sme tuning-ready`, `ifa sme tune bucket-review`, `ifa sme tune promote-profile`, `scripts/sme_nightly_tune_2300.sh` | **Tuning-ready** |

**Bottom line for V2.2.2**: TA and SmartMoney YAMLs are frozen at production-grade parameters. SME now has persistent snapshots and forward-label evaluation, so it is tuning-ready. Stock Edge ships with the current YAML (working but suboptimal); V2.2.3 will land tuning improvements via T3.2 + T3.3.

---

## TA family

### What's tunable
- 19 setup thresholds (e.g., breakout volume ratio, retracement depth, MACD diff cutoff)
- Regime gating (which setups SUSPEND in which regime)
- Decay-based suspension thresholds (`OBSERVATION_ONLY` / `SUSPENDED`)
- Tier A/B universe sizes
- Position concentration limits (TierA same-L2 ≤3 / TierB ≤6 / total ≤7)

### Tools available
- **`ifa ta walk-forward --start ... --end ...`** — full walk-forward backtest. Persists `backtest_runs` + `backtest_metrics`.
- **`ifa ta backtest`** — single-period backtest.
- **`ifa ta coverage --date ... --lookback 30`** — per-setup hit-count monitor; flags `starved` / `low_coverage`.
- **`fast_rerank`** — ~50× speedup for ranker-only changes (no need to re-evaluate setups).
- **`ifa ta evaluate-judgments`** — auto-grade prior hypotheses against actual T+5/T+10/T+15 returns.

### Current state (V2.2 release)
- 360-day walk-forward validated
- iter 1-20 complete; **iter 19 is封版** (regime-aware market value gate trend ≥ 20 亿)
- All 28 setup thresholds in `params/ta_v2.2.yaml`
- See `docs/ta-tier-tuning-iteration-1.md` + `iteration-2.md` + `ta-tuning-playbook.md` for the full evolution

### When to re-tune
- Major regime shift (新 bull/bear market start → existing decay weights stale)
- Factor distribution drift detected via `coverage` CLI
- Adding a new setup family (e.g., M11 introduces O/D/Z/E)
- ROE / 基本面二筛 thresholds change

### Process
1. Read `docs/ta-tuning-playbook.md` (10 启发式 rules + iteration log)
2. `ifa ta walk-forward --start <window-start> --end <window-end>`
3. Compare metrics against frozen baseline `iter19`
4. If ≥75% of setups improve and ≥85% don't degrade → freeze new iter as `params/ta_v2.X.Y.yaml`
5. Apply via `params freeze --name vYYYY_MM`

---

## SmartMoney family

### What's tunable
- RF (短线 1-3 day) hyperparameters: max_depth, n_estimators, min_samples_split
- XGB (中长线 1-2 month) hyperparameters: learning_rate, max_depth, eta
- Factor weights (`flow.py` cluster_weights / signal_weights)
- Phase transition matrix (Bayesian per-sector adjustment)
- LLM augmentation modules (concept_cluster, regime_classifier, etc.)

### Tools available
- **`ifa smartmoney backtest --start ... --end ...`** — backtest with persisted `backtest_runs` + `backtest_metrics`
- **`ifa smartmoney params freeze --name vYYYY_MM --from-backtest <run-id>`** — freeze YAML version
- **`ifa smartmoney params list / archive vYYYY_MM`** — manage param versions

### Current state (V2.2 release)
- v2026_05 RF + XGB models frozen ✅
- OOS validation 2025-11 → 2026-04 passed
- 360 trade-day backtest 2021-01 → 2026-04 baseline established

### When to re-tune
- New factor added (e.g., a new flow signal)
- ML model degradation detected (OOS rank IC < +0.03)
- Regime classifier confidence drops below 0.7

### Process
1. `ifa smartmoney compute --report-date <recent>` to populate fresh factors
2. `ifa smartmoney backtest --start <oos_start> --end <oos_end> --no-ml` (rule-based only first)
3. If rule baseline drifted: investigate factor source data
4. Train ML: see `ifa.families.smartmoney.ml.trainer` (RF) and `trainer_v2` (XGB)
5. `params freeze` new version

---

## SME family

### What's tunable

- Market-structure bucket ranking: `primary`, `secondary`, `defensive`, `repair`, `avoid`, `crowding_risk`.
- Continuous thresholds and weights for flow intensity, breadth, concentration, institutional/event-flow proxy, small/mid-order proxy, and risk penalties.
- Bucket-specific promotion and demotion rules.
- Scenario classification language is not a tuning target; it is customer-facing compression of the structured snapshot.

### Current state (V2.2.2 release)

- 2021-now SME derived tables are backfilled locally and under the 10GB storage budget.
- `sme_market_structure_daily` persists daily strategy snapshots.
- `sme_strategy_eval_daily` joins buckets to 1/3/5/10/20 trading-day forward labels.
- YTD readout: `secondary`, `crowding_risk`, and `avoid` currently beat `primary`; first tuning work should rebuild bucket ranking and thresholds rather than add more narrative.

### Tools available

```bash
uv run python -m ifa.cli sme tuning-ready --start 2026-01-01 --end 2026-04-30 --json
uv run python -m ifa.cli sme tune bucket-review --start 2026-01-01 --end 2026-04-30 --json
uv run python -m ifa.cli sme tune promote-profile --profile mvp1_ytd_candidate --start 2026-01-01 --end 2026-04-30 --apply --json
scripts/sme_nightly_tune_2300.sh
```

### When to re-tune

- Weekly weekend tuning after enough new mature labels accumulate.
- Immediately after a logic-version change in market-structure, state, diffusion, or labels.
- After a visible regime shift where OOS/OOC bucket performance drifts.

### Process

1. Run the 22:40 incremental first so the latest source day is materialized.
2. Run weekend tuning with a stable long window:
   `SME_TUNE_START=2021-01-01 SME_TUNE_MIN_SAMPLE_DAYS=120 scripts/sme_nightly_tune_2300.sh`.
3. Review `bucket_review.json` and `run_summary.json`; promote only if the candidate profile improves OOS/OOC signal quality and has enough mature-label coverage.
4. Let `ifa sme tune promote-profile --apply` write `active_profile` to YAML; do not hand-edit active profiles after a search.

---

## Stock Edge family

### What's tunable

**Two distinct tuning surfaces** (don't confuse):

#### Surface 1 — Decision layer weights (what production actually uses)
Path: `decision_layer.horizons.<h>.weights.*` per horizon (5d / 10d / 20d):
- 5d: 17 weights (cluster + signal shifts)
- 10d: 22 weights
- 20d: 26 weights

Production decision flow:
```
compute_strategy_matrix()      # 85 strategies × ts_code × as_of
   ↓
build_decision_layer(weights)  # apply per-horizon weights
   ↓
decision_score per (ts_code, horizon, as_of)
```

#### Surface 2 — Continuous overlay (what `_search_overlay` was tuning)
Path: handcrafted 9-term tanh formula in `optimizer.py:_evaluate_overlay()`.

⚠️ **Known precondition** (memory `project_stock_edge_optimizer_surrogate_bug.md`): historical optimizer was tuning Surface 2 (a proxy formula), NOT Surface 1 (production decision layer). **V2.2.3 T3.2 fixes this** by wiring `compute_strategy_matrix → build_decision_layer` into the evaluation pipeline. Until then, treat optimizer output with skepticism.

### Current state (V2.2 release)

**Done**:
- T1.1 K-fold consistency gate (G9)
- T1.2 Bootstrap CI gate (G5) — 1000 iterations, 95% CI lower bound > 0
- T1.3 Regime-bucketed gate (G4) — ≥75% buckets must improve
- T1.4 Auto-promote `auto_promote_if_passing`
- T2.1 IC warmstart + negative weights + multi-iteration
- T2.2 Multi-objective TPE (rank_ic_quality + zero-floor)
- T3.1 DB I/O batching (~50% wall-time reduction)
- T3.4 Decision ledger `stock.tuning_promotion_log` + git tag automation

**Pending** (V2.2.3):
- **T3.2** ML 跨日期复用 (sklearn fit cache by ts_code × fit_window)
- **T3.3** 扩 panel 100 stocks × 24 dates final tuning (K=6 folds)

### Acceptance gate (V2.2.3)

| Horizon | Target | Current (4-fold × 50 stocks × 12 dates) |
|---|---|---|
| 5d val rank IC, K-fold median | ≥ +0.03 + ≥3/4 folds positive | +0.004, 2/4 ⚠ |
| 10d val rank IC, K-fold median | ≥ +0.04 + ≥3/4 folds positive | +0.029, 2/4 ⚠ |
| **20d val rank IC, K-fold median** | **≥ +0.05 + 全部 folds positive** | **+0.034, 4/4 ✅** |
| Cross-fold std/median | ≤ 1.5 | TBD |
| Bootstrap CI 95% lower | > 0 | TBD |
| Regime-bucketed | ≥ 75% buckets improve | TBD |

### Tools available

**Existing (V2.2)**:
```bash
ifa stock edge --ts-code 600519.SH --as-of 2026-04-30
# Single-stock decision generation. Reads frozen YAML.
```

```bash
# Manual backtest invocation
uv run python -c "
from ifa.families.stock.backtest.runner import run_backtest
run_backtest(...)
"
```

```bash
# Decision ledger inspection
psql -h localhost -p 55432 -U ifa -d ifavr -c "
SELECT * FROM stock.tuning_promotion_log
ORDER BY timestamp DESC LIMIT 10;
"
```

**Pending V2.2.3**:
- ❌ No standalone `ifa stock tune` CLI yet — must be invoked programmatically
- ❌ No `auto_promote_if_passing` end-to-end CLI flow
- ❌ No script for routine T3.2 ML cache warmup

### V2.2.3 deliverables (3-4 days)

1. **T3.2 ML 跨日期复用** (2 days)
   - Add sklearn fit cache to `ifa.families.stock.factor.compute_strategy_matrix`
   - Cache key: `(ts_code, fit_window_start..fit_window_end)`
   - Cache hit-rate monitor; PIT correctness preserved
   - Acceptance: hit-rate ≥ 70% on 100×24 panel

2. **T3.3 扩 panel 终验** (1 day wall-clock)
   - K=6 folds × 100 stocks × 24 dates = 2400 rows
   - Estimated 30-45 min wall time
   - Produce production-grade variant YAML

3. **`auto_promote_if_passing` end-to-end** (1h)
   - Single command: tune → gate → promote → git tag → ledger entry
   - Should be safe to run repeatedly

4. **Operations doc updates** (2h)
   - `docs/OPERATIONS.md` — add Stock Edge tuning weekly cron
   - `docs/database-schema.md` — add `stock.tuning_promotion_log` schema

### When to re-tune (Stock Edge)

- Quarterly: routine refresh as factor data accumulates
- After major data updates (e.g., new TuShare endpoint joins, new fundamental factor)
- After regime classifier confidence drops or distribution shifts
- After observed live decisions (manual + production) deviate from backtest baseline by > 2σ

---

## Cross-family tuning — when to do what

| Symptom | Likely cause | Which family to tune |
|---|---|---|
| Reports look reasonable but signals weak | Factor distribution drift | SmartMoney (rule-based first) |
| TA setup fires too often / never | Threshold drift | TA (run `coverage` first) |
| Stock Edge `decision_score` doesn't predict | Decision layer weights stale | Stock Edge (V2.2.3) |
| Regime classifier wrong half the time | Transition matrix or feature drift | SmartMoney + TA (regime is shared) |
| Research factors look correct but story confusing | Prompt drift, not tuning | LLM prompt (Phase L work) |

---

## Don't:

❌ Tune any family on a sample smaller than 30 trade days × 50 stocks (statistical noise dominates)
❌ Promote a parameter version without bootstrap CI verification (G5 gate)
❌ Skip the decision ledger entry (`stock.tuning_promotion_log` is the audit trail)
❌ Tune Stock Edge surface 2 (the tanh proxy) without first verifying surface 1 wiring (memory project_stock_edge_optimizer_surrogate_bug.md)
❌ Re-train SmartMoney ML on stale `factor_daily` (re-run `compute` first)

## Do:

✅ Read the family's iteration log before tuning (`docs/ta-tier-tuning-iteration-N.md`)
✅ Run `coverage` / health checks first to detect data drift
✅ Use auto-promote gates (G4 / G5 / G9) — they catch overfit
✅ Always tag the git commit and write to ledger
✅ Notify operators before promoting — production reports may need warm-up runs

---

## Roadmap

- **V2.2.2** (shipped): SME MVP1 → persistent snapshots + forward labels + tuning-ready brief
- **V2.2.3** (1 week): Stock Edge T3.2 + T3.3 → variant YAML auto-promote; deferred Research/TA/Stock Edge intraday scope
- **V2.3** (4 weeks): Cross-family auto-tuning daemon (rolling weekly tune all 3 families with notifications)
- **V2.4** (8+ weeks): Online learning prototype for SmartMoney (vs. batch re-train)

---

Last updated: V2.2.0 release (2026-05-06).
