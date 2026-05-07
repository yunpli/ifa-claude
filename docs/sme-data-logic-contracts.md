# Smart Money Enhanced Data and Logic Contracts

> Last updated: 2026-05-07
> Scope: SME MVP1 derived data under `sme.*`
> Source mode: `prefer_smartmoney`

This document is the mandatory developer reference for SME data-layer and logic-layer changes. If an implementation changes units, PIT rules, feature formulas, state definitions, labels, or ETL semantics, update this document in the same change.

## 1. Versioning

Current logic versions are defined in `ifa/families/sme/versions.py` and written to `sme.sme_etl_runs.quality_summary_json` at run completion.

| Component | Version | Meaning |
|---|---:|---|
| Schema | `sme_mvp1_schema_v1` | Initial SME MVP1 tables |
| Stock orderflow | `stock_orderflow_v1_3` | Tushare `net_mf_amount` treated as official net inflow; bucket net is audit-only, not a quality blocker |
| Sector orderflow | `sector_orderflow_v1_1` | SW L2 PIT member aggregation from stock orderflow; L2 return falls back to member aggregate when official SW L2 index return is unavailable |
| Diffusion | `diffusion_v1_2` | PIT member universe with rolling 1/3/5/10 trading-day stock-level flow breadth and compounded returns |
| State machine | `state_machine_v1_1` | Rule-based states with reachable `rebound` and risk flags |
| Labels | `labels_forward_v1_1` | Future compounded trading-day return and relative rank labels for 1/3/5/10/20d; NULL return labels are deleted, not written |

Operational rule: a logic version bump that changes historical feature values requires recomputing the affected table family over the intended backtest/training window.

## 1.1 Trading-Day Semantics

All references to "previous day", "next day", "last N days", and "future h days" mean **trading days**, never calendar days.

Required implementation rule:

- Use `ifa.families.sme.data.calendar.trading_dates(...)`, `previous_trade_date(...)`, `next_trade_date(...)`, or SQL row numbers over a canonical trading calendar.
- Do not express trading windows as `date +/- N` calendar days.
- Do not use one source table, such as `raw_daily`, as a hidden trading calendar. On 2021-05-10, `raw_moneyflow` and `raw_daily_basic` existed while `raw_daily` was missing; anchoring SME to `raw_daily` silently dropped a real source trade date.

Root cause of the earlier bug:

- The initial MVP used `smartmoney.raw_daily` as the anchor because OHLCV is usually the densest source. That assumption failed when a source-specific gap existed.
- Future prevention: calendar construction must be explicit and documented. Source readiness should be checked separately from trading-day existence.

## 2. Source Contracts

SME MVP1 reads existing `smartmoney.*` tables only. It must not update those tables.

| Logical Source | Physical Table | Main Use | Required Date Field |
|---|---|---|---|
| `moneyflow` | `smartmoney.raw_moneyflow` | Stock-level order-size moneyflow | `trade_date` |
| `daily` | `smartmoney.raw_daily` | OHLC, pct change, amount | `trade_date` |
| `daily_basic` | `smartmoney.raw_daily_basic` | turnover, volume ratio, market cap | `trade_date` |
| `sw_member` | `smartmoney.raw_sw_member` | PIT SW membership intervals | `in_date`, `out_date` |
| `sw_member_monthly` | `smartmoney.sw_member_monthly` | Source coverage audit / compatibility | `snapshot_month` |
| `sw_daily` | `smartmoney.raw_sw_daily` | SW L2 index return | `trade_date` |

Tushare official references used for unit and field interpretation:

- Stock `moneyflow`: [Tushare doc 170](https://tushare.pro/document/2?doc_id=170)
- Daily basic: [Tushare doc 328](https://tushare.pro/document/2?doc_id=328)

## 3. Unit Contracts

All money-like derived columns in SME use integer yuan when the column ends with `_yuan`.

| Source Field | Source Unit | Target Field | Target Unit | Factor |
|---|---:|---|---:|---:|
| `raw_moneyflow.*_amount` | 万元 | `*_amount_yuan` | 元 | 10,000 |
| `raw_moneyflow.net_mf_amount` | 万元 | `net_mf_amount_yuan` | 元 | 10,000 |
| `raw_daily.amount` | 千元 | `amount_yuan` | 元 | 1,000 |
| `raw_daily_basic.total_mv` | 万元 | `total_mv_yuan` | 元 | 10,000 |
| `raw_daily_basic.circ_mv` | 万元 | `circ_mv_yuan` | 元 | 10,000 |

The canonical registry lives in `ifa/families/sme/data/units.py` and is materialized to `sme.sme_unit_registry` by `sme doctor --check units`.

## 4. PIT Membership

Target table: `sme.sme_sw_member_daily`

Daily membership is materialized from interval rows:

```sql
raw_sw_member.in_date <= trade_date
AND (raw_sw_member.out_date IS NULL OR raw_sw_member.out_date > trade_date)
AND raw_sw_member.l2_code IS NOT NULL
```

Primary key is `(trade_date, l2_code, ts_code)`, not `(trade_date, ts_code)`, because a stock can appear in more than one hierarchy slice during classification changes.

Historical recompute requirement:

- If `raw_sw_member` changes, recompute `sme_sw_member_daily` and every downstream table for affected dates.
- If only a future SW member refresh changes current intervals, recompute from the earliest changed `in_date` or `out_date`.

## 5. Stock Orderflow

Target table: `sme.sme_stock_orderflow_daily`

Main formulas:

```text
sm_net_yuan     = buy_sm_amount_yuan  - sell_sm_amount_yuan
md_net_yuan     = buy_md_amount_yuan  - sell_md_amount_yuan
lg_net_yuan     = buy_lg_amount_yuan  - sell_lg_amount_yuan
elg_net_yuan    = buy_elg_amount_yuan - sell_elg_amount_yuan
main_net_yuan   = lg_net_yuan + elg_net_yuan
retail_net_yuan = sm_net_yuan + md_net_yuan
main_net_ratio  = main_net_yuan / amount_yuan
retail_net_ratio = retail_net_yuan / amount_yuan
elg_net_ratio   = elg_net_yuan / amount_yuan
```

Important v1.1/v1.3 correction:

- `net_mf_amount_yuan` is Tushare official net inflow, converted to yuan.
- `net_recomputed_yuan` is **not** an attempted recomputation of `net_mf_amount_yuan`.
- `net_recomputed_yuan = sm_net_yuan + md_net_yuan + lg_net_yuan + elg_net_yuan` is a bucket balance check.
- `reconciliation_error_yuan = net_recomputed_yuan`.
- As of `stock_orderflow_v1_3`, bucket balance is audit-only and does not mark a row degraded by itself. Certain BJ/920-series rows use a different bucket behavior where bucket net can equal official net inflow; treating this as bad data was overfitting a normal A-share invariant to another market segment.

Reason: Tushare reports buy/sell amounts by order size as turnover buckets. Empirically these buckets usually balance close to zero because every transaction has both sides. Comparing that balance to official `net_mf_amount` incorrectly marks almost all rows degraded.

Missing source rule:

- Missing OHLCV fields remain NULL. They must not be converted to 0.
- A stock row is degraded when `amount_yuan` or `pct_chg` is NULL, because ratios and price-confirmation flags cannot be safely interpreted.

Root cause of the earlier bug:

- `COALESCE(raw_daily.amount, 0)` confused missing data with real zero turnover. This made a source gap look like a valid zero amount.
- Future prevention: only financial quantities that are semantically absent-as-zero may use `COALESCE(..., 0)`. Missing market data must propagate as NULL and set an explicit quality flag.

Affected fields after v1.1:

- `quality_flag`
- `net_recomputed_yuan`
- `reconciliation_error_yuan`

Historical recompute requirement:

- Recompute `sme_stock_orderflow_daily` for all dates used in backtests/tuning.
- Then recompute `sme_sector_orderflow_daily`, `sme_sector_diffusion_daily`, `sme_sector_state_daily`, and `sme_labels_daily` for the same window.

Validation:

```bash
uv run python -m ifa.cli sme doctor --check contracts --date YYYY-MM-DD
```

Expected:

- `stock_orderflow_balance_error` should be `ok` or small degraded count.
- `stock_orderflow_quality` should not be dominated by degraded rows.
- `stock_orderflow_ratio_ranges` should have zero blocked rows.

## 6. Sector Orderflow

Target table: `sme.sme_sector_orderflow_daily`

The sector table aggregates stock rows by `(trade_date, l2_code)` using `sme_sw_member_daily` as the PIT member set.

Main formulas:

```text
coverage_ratio = matched_stock_count / member_count
sector_amount_yuan = SUM(stock.amount_yuan)
sector_return_equal_weight = AVG(stock.pct_chg)
sector_return_amount_weight = SUM(stock.pct_chg * amount_yuan) / SUM(amount_yuan)
main_net_yuan = SUM(stock.main_net_yuan)
retail_net_yuan = SUM(stock.retail_net_yuan)
main_net_ratio = main_net_yuan / sector_amount_yuan
main_positive_breadth = AVG(stock.main_net_yuan > 0)
top5_main_net_share = SUM(top 5 positive stock main_net_yuan) / sector main_net_yuan
```

`sector_return_sw_index` uses `smartmoney.raw_sw_daily.pct_change` when an exact L2 code is available. Current local `raw_sw_daily` is mostly SW L1, so many L2 joins are unavailable. In `sector_orderflow_v1_1`, SME falls back to member-level aggregate return:

```text
sector_return_sw_index = COALESCE(raw_sw_daily.pct_change,
                                  sector_return_amount_weight,
                                  sector_return_equal_weight)
```

The field name is retained for schema compatibility, but downstream training should remember that some rows are official SW index return and some are member aggregate fallback. A future schema should add `sector_return_source`.

Root cause of the earlier bug:

- SW L1 and L2 codes both use `801xxx.SI`, so a simple "table has rows" check missed a level mismatch.
- Future prevention: source contracts must validate code level and join coverage, not just row count.

Quality:

- `quality_flag = degraded` when `coverage_ratio < 0.80`.
- Degraded sectors can still be useful for exploratory analysis but should not silently enter model training without coverage filters.

## 7. Diffusion

Target table: `sme.sme_sector_diffusion_daily`

Version `diffusion_v1_2` uses the current PIT member universe for each target date. For each stock in that target-date universe, SME looks back over the last 1/3/5/10 trading dates and computes cumulative `main_net_yuan` signs.

Main formulas:

```text
flow_breadth_1d  = AVG(stock 1d main_net_yuan > 0)
flow_breadth_3d  = AVG(stock rolling 3d main_net_yuan > 0)
flow_breadth_5d  = AVG(stock rolling 5d main_net_yuan > 0)
flow_breadth_10d = AVG(stock rolling 10d main_net_yuan > 0)
diffusion_slope_5_10 = flow_breadth_5d - flow_breadth_10d
```

Returns:

- `leader_return_1d` is target-date leader return.
- `leader_return_3d` and `leader_return_5d` are compounded rolling returns, not averages of daily returns.
- `median_member_return_5d` and `tail_member_return_5d` are distribution statistics of member-level rolling 5d compounded returns.

State labels:

| `diffusion_phase` | Meaning |
|---|---|
| `broad_diffusion` | Broad stock-level participation and positive leader confirmation |
| `midcap_following` | Moderate breadth with positive leader |
| `leader_confirmed` | Leader positive but breadth weak |
| `diffusion_breakdown` | Very low breadth |
| `leader_only` | Fallback when leader dominates but breadth is not healthy |

Historical recompute requirement:

- Any change in stock orderflow, membership, or diffusion formula requires recomputing diffusion for the affected window.
- Because rolling windows need history, recompute from at least 10 trading days before the first target date when doing manual repairs.

## 8. State Machine

Target table: `sme.sme_sector_state_daily`

Version `state_machine_v1_1` is deterministic and rule-based. It intentionally avoids ML so MVP1 can be audited before parameter tuning.

Current states:

| State | Interpretation |
|---|---|
| `acceleration` | Strong main flow, strong diffusion, positive sector return |
| `diffusion` | Main flow and breadth are healthy |
| `ignition` | Early positive main flow with price not yet broken |
| `rebound` | Main flow positive while sector index is still weak |
| `dormant` | Low conviction or weak/early flow |
| `distribution` | Main outflow while price is still up |
| `cooldown` | Main outflow with weak price |

Risk flags:

| Flag | Meaning |
|---|---|
| `flow_concentrated` | Top 5 stocks dominate positive main flow |
| `leader_crowded` | Acceleration led by crowded leaders |
| `retail_chase` | Retail net positive while main flow is not |
| `main_out_price_up` | Price is up despite main outflow |

Historical recompute requirement:

- Recompute state after any sector orderflow or diffusion recompute.

## 9. Labels

Target table: `sme.sme_labels_daily`

Labels are sector-level forward labels for research, backtesting, and future model training. They are not predictions.

For each horizon `h in (1, 3, 5, 10, 20)`:

```text
future_return = compounded return over next h sector observations
future_max_runup = max single-observation return in the next h observations
future_drawdown = min single-observation return in the next h observations
future_excess_return_vs_market = future_return - same-date market average
future_excess_return_vs_l1 = future_return - same-date SW L1 average
future_rank_pct = same-date percentile rank by future_return
future_top_quantile_label = future_rank_pct >= 0.80
future_heat_delta = future heat - current heat
```

Maturity rule:

- A label is written only when all `h` future observations exist.
- Therefore latest label date normally lags latest feature date by up to 20 trading days.
- Labels with NULL return inputs are deleted/recomputed, not retained from old logic. The label writer deletes the requested `(date range, horizon)` before inserting mature labels.

Root cause of the earlier bug:

- Upsert-only label generation left old NULL labels in place after the return fallback was fixed.
- Future prevention: when label eligibility can shrink or change, recomputation must delete the affected label window before inserting.

## 10. Required Validation Flow

After any data/logic change:

```bash
uv run python -m py_compile ifa/families/sme/**/*.py ifa/cli/sme.py
uv run pytest tests/sme -q
uv run python -m ifa.cli sme doctor --check schema,sources,units,contracts --date YYYY-MM-DD --json
uv run python -m ifa.cli sme status --json
```

After a historical recompute:

```bash
uv run python -m ifa.cli sme etl audit --start YYYY-01-01 --end YYYY-12-31 --json
```

For annual backfills, prefer:

```bash
./scripts/sme_backfill_2025.sh
```

The annual script records actual elapsed time, total SME storage before/after, storage delta, and per-table row/storage details under `/Users/neoclaw/claude/ifaenv/logs/sme_backfill/`.

## 11. Market Structure Strategy Snapshot

CLI: `uv run python -m ifa.cli sme market-structure --date auto --json`

Client conclusion CLI: `uv run python -m ifa.cli sme market-structure --date auto --client`

Persistence CLI: `uv run python -m ifa.cli sme market-structure --date auto --persist --json`

Range persistence CLI: `uv run python -m ifa.cli sme compute market-structure --start 2026-01-01 --end auto --json`

Module: `ifa.families.sme.analysis.market_structure`

Target table: `sme.sme_market_structure_daily`

This is an MVP1 strategy interpreter and a persisted tuning artifact. It reads
only local SME and `smartmoney` sources, then stores the daily classification
snapshot so walk-forward tuning and OOC/OOS validation do not depend on mutable
report-time regeneration.

Inputs:

- `smartmoney.raw_index_daily`: index close, pct change, turnover for major indices.
- `smartmoney.raw_daily`: whole-market breadth and turnover.
- `sme.sme_sector_orderflow_daily`: SW L2 flow, return, breadth, concentration, leader.
- `sme.sme_sector_diffusion_daily`: flow diffusion, leader/member spread, top members.
- `sme.sme_sector_state_daily`: deterministic sector state and risk flags.

Main classifications:

| Output | Rule family |
|---|---|
| `flow_outflows` | Negative main flow classified as `panic_sell`, `active_de_risk`, `high_low_switch`, or `controlled_outflow` |
| `flow_inflows` | Positive main flow classified as `chase_high`, `defensive`, `institutional_absorption`, `long_config`, `event_trade`, or `tactical_inflow` |
| `strong_return_weak_flow` | Return strong but main-flow ratio/breadth weak; flags congestion and tail risk |
| `suppressed_repair` | Large decline with outflow convergence or long compression with current positive main flow |
| `beneficiary_buckets` | Primary, secondary, desensitized, and suppressed-repair directions |
| `capital_state` | Risk appetite up/down, defensive switch, event trade, high-low switch, mainline repricing, or mixed rotation |
| `scenario_1_3_trade_days` | Conditional paths for risk escalation, risk easing, and event drag |

Actor profile:

- `main_net_yuan` = large + extra-large order bucket pressure.
- `retail_net_yuan` = small + medium order bucket pressure.
- `institution_lhb_proxy_net_bn_yuan` comes from `smartmoney.raw_top_inst` rows where `exalter='机构专用'`.
- `lhb_event_net_bn_yuan` comes from `smartmoney.raw_top_list.net_amount`.

Important boundary:

- `raw_top_inst` / `raw_top_list` are event-driven 龙虎榜 disclosures, not a full-market institutional ownership ledger. SME labels them as institution-seat proxy and event-flow proxy; they can strengthen or weaken the narrative, but cannot alone prove what all institutions did.
- `market_structure_v1_1` persists both the full `snapshot_json` for audit and `client_conclusion_json` for simple report rendering.

External variables:

- MVP1 accepts `--external-summary` as an input from an LLM/web search step.
- External summaries are not treated as source-of-truth numerical data and are not written into SME factor tables.
- Future production integration should store external-variable source URLs, generated time, model name, and prompt hash in a separate audit table if the output becomes client-facing.

Known data gap:

- Real intraday path / 分时走势 is not persisted in SME MVP1. The interpreter reports this explicitly and uses daily OHLC, turnover, breadth, and flow structure until minute/realtime snapshots are added.

Display note:

- `sme_sector_orderflow_daily.top5_main_net_share` is computed as top-5 positive main flow divided by sector net main flow. It can exceed 1 when the rest of the sector is net negative. `market-structure` exposes `top5_main_net_share_raw` for audit and caps `top5_main_net_share` to `[0, 1]` for client-facing concentration display.

Client-facing report rule:

- The final report must be conclusion-first and process-light. `--client` and `client_conclusion` hide raw evidence, formulas, and intermediate fields. They keep only bottom-line judgement, directions to focus/watch/avoid, defensive or repair candidates, crowding risk, and 1-3 trading-day scenarios.
- The standalone SME brief uses `ifa/families/sme/templates/brief.html`. It may borrow the old SmartMoney report's information hierarchy, but it must not include, import, or depend on old SmartMoney/core render templates.
- Brief titles are based on the observed trade date, not the generation date, for example `2026年5月6日资金结构简报`. The header must also show the Beijing report generation timestamp.
- Human-facing flow tables must include compact data support. `main_net_bn_yuan` is the primary ranking direction; extra-large, large, small/medium retail proxy, institution-seat proxy, and LHB event proxy are displayed as supporting context.
- Without explicit `--output`, `ifa sme brief` writes to the IFA standard report layout: `<IFA_OUTPUT_ROOT>/<run_mode>/<YYYYMMDD>/sme/CN_sme_brief_<YYYYMMDD>_<HHMM>.<ext>`.
- Full JSON is for audit, tuning, and third-party machine integration; it is not the default shape for human-facing investment communication.

Daily production gate:

- Third-party schedulers may run every calendar day, but SME production scripts must check the Beijing run date against `smartmoney.trade_cal` before doing work.
- `scripts/sme_daily_gate.py --kind incremental|brief --json` returns `action=run` on trading days and `action=skip` on non-trading days.
- `scripts/sme_incremental_2240.sh` delegates to `scripts/sme_incremental_0300.sh`; both use the gate before ETL.
- Recommended production briefing schedule is evening, after ETL: `scripts/sme_briefing_2310.sh` runs with `--brief-target same-day`, so the observed report date is the same Beijing trading date.
- `scripts/sme_briefing_0400.sh` is retained only for legacy early-morning use with `--brief-target previous-trading-day`; it is not the recommended production schedule.
- On a non-trading Beijing run date, both briefing scripts print `status=non_trade_day` and exit 0.

## 12. Strategy Evaluation

Target table: `sme.sme_strategy_eval_daily`

CLI:

```bash
uv run python -m ifa.cli sme compute strategy-eval --start 2026-01-01 --end auto --json
uv run python -m ifa.cli sme tuning-ready --start 2026-01-01 --end auto --json
uv run python -m ifa.cli sme tune bucket-review --start 2026-01-01 --end auto --json
```

Purpose:

- Join persisted strategy buckets from `sme_market_structure_daily` with mature forward labels from `sme_labels_daily`.
- Evaluate actual realized effect by bucket and horizon before tuning parameters.
- Make `avoid` and `crowding_risk` comparable with long buckets by sign-normalizing `avg_signal_score`: higher is always better.

Buckets:

| Bucket | Direction | Interpretation |
|---|---|---|
| `primary` | long | 一级受益方向 |
| `secondary` | long | 二级观察方向 |
| `defensive` | long | 脱敏/防御方向 |
| `repair` | long | 压制修复方向 |
| `avoid` | avoid | 回避/减仓方向 |
| `crowding_risk` | avoid | 拥挤/尾端风险方向 |

Metrics:

| Column | Meaning |
|---|---|
| `avg_future_return` | Bucket average future return |
| `avg_future_excess_return_vs_market` | Bucket average excess return versus same-date SW L2 market average |
| `avg_signal_score` | Direction-normalized market excess; positive means the recommendation was directionally useful |
| `success_rate` | Long bucket: excess > 0; avoid bucket: excess < 0 |
| `top_quantile_rate` | Share of bucket labels in same-date top 20% future-return quantile |
| `heat_up_rate` | Share whose future heat improved |
| `quality_flag` | `degraded` when the bucket has fewer than 3 matched sectors |

Tuning rule:

- Parameter search should optimize OOS/OOC `avg_signal_score`, `success_rate`, and drawdown/runup tradeoff, not the number of implemented rules.
- Report-time narrative must not be used as a training label.

Current MVP1 smoke result:

- 2026-01-01 → 2026-04-30 persisted market-structure snapshots: 77 trade dates.
- `sme_strategy_eval_daily`: 2,026 rows.
- `tuning-ready` marks horizons 1/3/5/10 as ready with `min_sample_days=60`; 20d is still sample-thin in this YTD slice.
- Early signal: `secondary` and `crowding_risk` are more useful than current `primary` definitions, so the first tuning loop should improve bucket ranking and avoid/primary thresholds before adding more narrative complexity.

Nightly integration scripts:

```bash
scripts/sme_incremental_2240.sh
scripts/sme_nightly_tune_2300.sh
```

Operational contract:

- `sme_incremental_2240.sh` delegates to the canonical production incremental script and is intended for a Beijing 22:40 external scheduler.
- `sme_nightly_tune_2300.sh` is intended for Beijing 23:00+ after incremental ETL. It automatically detects the latest mature label date, refreshes market-structure snapshots, recomputes strategy eval, runs tuning readiness, and emits:
  - `market_structure_refresh.json`
  - `strategy_eval.json`
  - `tuning_ready.json`
  - `bucket_review.json`
  - `run_summary.json`
- Artifacts are written under `/Users/neoclaw/claude/ifaenv/out/sme_tuning/nightly/<timestamp>/`.
- Logs are written under `/Users/neoclaw/claude/ifaenv/logs/sme_tuning/`.

Parameter contract:

- Market-structure parameters live in `ifa/families/sme/params/market_structure_v1.yaml`.
- Continuous parameters are the default tuning surface: thresholds, weights, and penalties are continuous because flow intensity, breadth, concentration, and returns are continuous variables.
- Discrete parameters are reserved for structural choices only, for example `primary.mode`.
- Good tuning results are promoted through `ifa sme tune promote-profile`; the command automatically checks sample readiness and evaluated profile coverage before it can write `active_profile` back to YAML with `--apply`.
- Nightly can evaluate a profile with `SME_MARKET_STRUCTURE_PROFILE=<profile>` and optionally promote with `SME_TUNE_PROMOTE_PROFILE=<profile> SME_TUNE_APPLY_PROMOTION=1`.
