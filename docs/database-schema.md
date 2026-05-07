# IFAVR — Database Schema Reference

Multiple logical schemas live in the same PostgreSQL 16 cluster (port 55432):

| Schema | DB | Purpose |
|---|---|---|
| `public` | `ifavr` / `ifavr_test` | Core reporting tables — shared across all report families |
| `smartmoney` | `ifavr` / `ifavr_test` | SmartMoney-specific ETL, factor, ML, and backtest tables |
| `research` | `ifavr` / `ifavr_test` | Research family company identity, cache, report assets, factor memory, and PDF extract memory |
| `sme` | `ifavr` / `ifavr_test` | Smart Money Enhanced PIT SW L2 orderflow, diffusion/state, forward labels, market-structure snapshots, and tuning artifacts |

All migrations are managed by Alembic (`alembic upgrade head`).
All timestamps are `TIMESTAMPTZ` stored in UTC; rendering converts to `Asia/Shanghai`.

---

## Design principles

1. **One database, two physical instances.** `ifavr` (production/manual) and
   `ifavr_test` (test) are separate databases on the same cluster. Mode selection
   happens at the connection URL level — test runs can never touch production data.

2. **`run_mode` column on `report_runs`.** Even within a single DB, every run
   records whether it was `manual` or `production`, so review queries can filter correctly.

3. **UUID PKs via `gen_random_uuid()`** (PostgreSQL built-in). Time-ordered v7
   is a future upgrade option; v4 is sufficient now.

4. **JSONB for flexible payloads** (`value_json`, `content_json`, `evidence_json`,
   `params_json`). Strict `TEXT CHECK` columns for stable business attributes (slot, status, role).

5. **Soft enums via `TEXT CHECK`** rather than native PostgreSQL enums — vocabulary
   is still evolving, and `ALTER TYPE ADD VALUE` cannot run in a transaction.

6. **No ON DELETE CASCADE on report_runs → children.** A run is the immutable
   unit of work. Deletion is an explicit ops action.

---

## Part 1 — Core reporting schema (`public.*`)

### `report_runs`
Every report generation = one row. Children join on `report_run_id`.

| Column | Type | Notes |
|---|---|---|
| `report_run_id` | UUID PK | `gen_random_uuid()` |
| `market` | TEXT CHECK | `china_a`, `hk`, `us`, `cross_asset` |
| `report_family` | TEXT CHECK | `macro`, `asset`, `tech`, `main`, `smartmoney`, `weekend`, `briefing`, `adhoc` |
| `report_type` | TEXT CHECK | `morning_long`, `midday_long`, `evening_long`, `briefing`, `adhoc_*` |
| `report_date` | DATE | NOT NULL — the trading date the report covers |
| `slot` | TEXT CHECK | `morning`, `midday`, `evening`, `weekend_sat`, `weekend_sun`, `adhoc`, … |
| `timezone_name` | TEXT | NOT NULL, e.g. `Asia/Shanghai` |
| `data_cutoff_at` | TIMESTAMPTZ | NOT NULL — no input after this cutoff is used |
| `status` | TEXT CHECK | `running` → `succeeded`/`failed`/`partial`/`superseded` |
| `run_mode` | TEXT CHECK | `test`, `manual`, `production` |
| `triggered_by` | TEXT | nullable — operator id, `cron`, etc. |
| `template_version` | TEXT | NOT NULL — e.g. `smartmoney_evening_v0.1` |
| `prompt_version` | TEXT | NOT NULL — prompt bundle version |
| `output_html_path` | TEXT | nullable until rendered |
| `started_at` | TIMESTAMPTZ | NOT NULL, default `now()` |
| `completed_at` | TIMESTAMPTZ | nullable |
| `fallback_used` | BOOLEAN | true if any section degraded to fallback |
| `error_summary` | TEXT | nullable |

### `report_sections`
Final per-section content. This is what the Jinja2 renderer reads.

| Column | Type | Notes |
|---|---|---|
| `section_id` | UUID PK | |
| `report_run_id` | UUID FK | → `report_runs` |
| `section_key` | TEXT | e.g. `sm_market_pulse`, `sm_strategy_view` |
| `section_title` | TEXT | Chinese display title |
| `section_order` | INT | render order |
| `content_json` | JSONB | structured payload consumed by Jinja templates |
| `prompt_name` | TEXT | nullable — e.g. `TONE_INSTRUCTIONS` |
| `prompt_version` | TEXT | nullable |
| `model_output_id` | UUID | FK → `report_model_outputs` (nullable for rule-only sections) |
| `fallback_used` | BOOLEAN | default false |
| `created_at` | TIMESTAMPTZ | |

**Unique:** `(report_run_id, section_key)`.

### `report_judgments`
Verifiable hypotheses emitted by LLM sections. Auto-reviewed the next morning.

| Column | Type | Notes |
|---|---|---|
| `judgment_id` | UUID PK | |
| `report_run_id` | UUID FK | |
| `section_key` | TEXT | which section produced this |
| `judgment_type` | TEXT | `hypothesis`, `macro_tone`, `risk`, `mapping` |
| `judgment_text` | TEXT | the verifiable claim |
| `target` | TEXT | what it's about (板块名, 指数, etc.) |
| `horizon` | TEXT | `next_day`, `next_2d`, `this_week` |
| `confidence` | TEXT CHECK | `high`, `medium`, `low` |
| `validation_method` | TEXT | how the next report validates this |
| `review_status` | TEXT CHECK | `pending` → `validated`/`partial`/`failed`/`not_applicable` |
| `created_at` | TIMESTAMPTZ | |

### `report_model_outputs`
Audit trail for every LLM call.

| Column | Type | Notes |
|---|---|---|
| `model_output_id` | UUID PK | |
| `report_run_id` | UUID FK | |
| `section_key` | TEXT | |
| `prompt_name` | TEXT | |
| `prompt_version` | TEXT | |
| `model_name` | TEXT | actual model id returned |
| `endpoint` | TEXT | `primary` / `fallback` |
| `parsed_json` | JSONB | structured LLM output |
| `status` | TEXT CHECK | `parsed`, `parse_failed`, `fallback_used`, `error` |
| `prompt_tokens` | INT | |
| `completion_tokens` | INT | |
| `latency_seconds` | NUMERIC(10,3) | |
| `created_at` | TIMESTAMPTZ | |

### Macro pre-job tables

**`macro_text_derived_indicators`** — structured indicators (M2, new RMB loans,
social financing) extracted from TuShare `news`/`major_news`/`npr` by
`macro_text_derived_capture_job`. Unique on `(source_url, indicator_name, reported_period)`.

**`macro_policy_event_memory`** — active policy events curated by
`macro_policy_event_memory_job`. Filtered by `status = 'active'` for report reads.

---

## Part 2 — SmartMoney schema (`smartmoney.*`)

### Raw tables (TuShare caches)

All raw tables follow TuShare column shapes exactly. PK is typically
`(trade_date, ts_code)` or a UUID for multi-record-per-day tables.

| Table | Source API | Key columns |
|---|---|---|
| `raw_daily` | `daily` | OHLCV for ~5400 A-share stocks |
| `raw_daily_basic` | `daily_basic` | 换手率, PE, PB, 市值 |
| `raw_moneyflow` | `moneyflow` | 超大/大/中/小单 买卖金额, 净流入 |
| `raw_moneyflow_ind_dc` | `moneyflow_ind_dc` | 东财板块资金流 (排名 + 净额) |
| `raw_moneyflow_ind_ths` | `moneyflow_ind_ths` | 同花顺行业资金流 |
| `raw_moneyflow_hsgt` | `moneyflow_hsgt` | 北向/南向资金 |
| `raw_margin` | `margin` | 融资融券余额 |
| `raw_limit_list_d` | `limit_list_d` | 涨停/跌停明细 |
| `raw_kpl_concept` | `kpl_concept` | 开盘啦概念板块日数据 |
| `raw_kpl_concept_cons` | `kpl_concept_cons` | 开盘啦概念成员 |
| `raw_kpl_list` | `kpl_list` | 开盘啦连板/首板榜 |
| `raw_top_list` | `top_list` | 龙虎榜日数据 |
| `raw_top_inst` | `top_inst` | 龙虎榜机构席位 |
| `raw_ths_hot` | `ths_hot` | 同花顺热门 A 股 (null ts_code 外市行过滤) |
| `raw_dc_hot` | `dc_hot` | 东财热门 A 股 (null ts_code 外市行过滤) |
| `raw_dc_index` | `dc_index` | 东财自定义指数 |
| `raw_dc_member` | `dc_member` | 东财板块成员 |
| `raw_block_trade` | `block_trade` | 大宗交易 |
| `raw_sw_daily` | `sw_industry_detail` | 申万行业日数据 |
| `raw_index_daily` | `index_daily` | 主要指数 (沪深300/创业/科创等) |
| `raw_cyq_chips` | `cyq_chips` | 筹码分布 (backfill 时可 skip) |

### Business tables

**`factor_daily`** — Four factor scores per sector × source × date.

| Column | Type | Notes |
|---|---|---|
| `factor_id` | UUID PK | |
| `trade_date` | DATE | |
| `sector_code` | TEXT | TuShare sector code |
| `sector_source` | TEXT CHECK | `dc`, `sw`, `ths`, `kpl` |
| `sector_name` | TEXT | |
| `heat_score` | NUMERIC(10,6) | 0–1, relative money-flow strength |
| `trend_score` | NUMERIC(10,6) | 0–1, buy/sell pressure direction |
| `persistence_score` | NUMERIC(10,6) | 0–1, consistency over recent days |
| `crowding_score` | NUMERIC(10,6) | 0–1, money-in vs price-stagnation |
| `derived_json` | JSONB | source-specific extras (dc_rank, elg_rate, etc.) |
| `computed_at` | TIMESTAMPTZ | |

**Unique:** `(trade_date, sector_code, sector_source)`.

---

**`sector_state_daily`** — Role + cycle phase per sector × date, written by
`write_sector_states()` after merging `role.py` + `cycle.py` outputs.

| Column | Type | Notes |
|---|---|---|
| `trade_date` | DATE | |
| `sector_code` | TEXT | |
| `sector_source` | TEXT | |
| `sector_name` | TEXT | |
| `role` | TEXT CHECK | `主线`, `中军`, `轮动`, `防守`, `催化`, `退潮`, `未识别` |
| `cycle_phase` | TEXT CHECK | `冷`, `点火`, `确认`, `扩散`, `高潮`, `分歧`, `退潮`, `未识别` |
| `role_confidence` | TEXT CHECK | `high`, `medium`, `low` |
| `phase_confidence` | TEXT CHECK | `high`, `medium`, `low` |
| `evidence_json` | JSONB | rule traces for role + phase (NaN sanitized before insert) |
| `computed_at` | TIMESTAMPTZ | |

**Unique:** `(trade_date, sector_code, sector_source)`.

---

**`market_state_daily`** — One row per trade date. Overall market water-level.

| Column | Type | Notes |
|---|---|---|
| `trade_date` | DATE PK | |
| `market_state` | TEXT CHECK | `进攻`, `中性`, `防守`, `退潮` |
| `total_amount` | NUMERIC | Total A-share turnover (亿元) |
| `amount_percentile_60d` | NUMERIC | 0–1, percentile of today's volume vs 60d window |
| `up_count` | INT | Advancing stocks |
| `down_count` | INT | Declining stocks |
| `limit_up_count` | INT | |
| `limit_down_count` | INT | |
| `broken_limit_rate` | NUMERIC | 炸板率 |
| `max_consecutive_limit` | INT | Max consecutive limit-up count today |
| `snapshot_json` | JSONB | Full snapshot for reference |
| `computed_at` | TIMESTAMPTZ | |

---

**`stock_signals_daily`** — Per-stock signals from leader + candidate engines.

| Column | Type | Notes |
|---|---|---|
| `signal_id` | UUID PK | |
| `trade_date` | DATE | |
| `ts_code` | TEXT | |
| `ts_name` | TEXT | |
| `role` | TEXT CHECK | `龙头`, `中军`, `情绪先锋`, `补涨`, `趋势`, `风险` |
| `score` | NUMERIC | composite score for ranking |
| `sector_code` | TEXT | parent sector |
| `sector_source` | TEXT | |
| `theme` | TEXT | KPL concept or sector name |
| `signal_json` | JSONB | full scoring evidence |
| `computed_at` | TIMESTAMPTZ | |

---

**`param_versions`** — Versioned parameter sets for the factor/ML pipeline.

| Column | Type | Notes |
|---|---|---|
| `version_id` | UUID PK | |
| `version_name` | TEXT UNIQUE | e.g. `v2026_05`, `default` |
| `params_json` | JSONB | full params dict |
| `frozen_at` | TIMESTAMPTZ | |
| `frozen_from_backtest_run_id` | UUID | FK → `backtest_runs` (nullable) |
| `status` | TEXT CHECK | `active`, `archived`, `draft` |
| `notes` | TEXT | |

---

**`backtest_runs`** — One row per backtest invocation.

| Column | Type | Notes |
|---|---|---|
| `backtest_run_id` | UUID PK | |
| `started_at` | TIMESTAMPTZ | |
| `completed_at` | TIMESTAMPTZ | |
| `start_date` | DATE | backtest window start |
| `end_date` | DATE | backtest window end |
| `params_json` | JSONB | params used |
| `param_version_used` | TEXT | named version if from DB |
| `status` | TEXT CHECK | `running`, `succeeded`, `failed`, `partial` |
| `notes` | TEXT | |

---

**`backtest_metrics`** — Factor and ML metrics from one backtest run.

| Column | Type | Notes |
|---|---|---|
| `backtest_run_id` | UUID FK | |
| `factor_name` | TEXT | `heat_score`, `trend_score`, `persistence_score`, `crowding_score`, `ml_logistic`, etc. |
| `metric_name` | TEXT | `ic`, `ic_std`, `ic_ir`, `ic_positive_rate`, `rank_ic`, `rank_ic_std`, `rank_ic_ir`, `topn_hit`, `group_return`, `auc_mean`, `auc_std` |
| `window_days` | INT | forward return window (1 or 5) |
| `group_label` | TEXT | `Q1`..`Q5` for group return rows, empty otherwise |
| `metric_value` | NUMERIC(14,6) | |
| `n_samples` | INT | |

**PK:** `(backtest_run_id, factor_name, metric_name, window_days, group_label)`.

---

**`etl_watermarks`** — Tracks the last successful ETL per table.

| Column | Type | Notes |
|---|---|---|
| `table_name` | TEXT PK | |
| `last_trade_date` | DATE | |
| `last_run_at` | TIMESTAMPTZ | |
| `last_run_mode` | TEXT | |
| `rows_loaded_total` | BIGINT | cumulative |

---

**`predictions_daily`** — ML model predictions per sector × date (optional, populated by backtest walk-forward or daily inference).

| Column | Type | Notes |
|---|---|---|
| `trade_date` | DATE | |
| `sector_code` | TEXT | |
| `sector_source` | TEXT | |
| `model_name` | TEXT | |
| `model_version` | TEXT | |
| `prob_up` | NUMERIC(10,6) | P(next day up) |
| `label_scheme` | TEXT | `binary_up`, `binary_up5d` |
| `predicted_at` | TIMESTAMPTZ | |

## Part 3 — SME schema (`sme.*`)

SME is an independent family introduced in V2.2.2. It reads local `smartmoney.*` tables only in read-only mode and writes all new derived data to `sme.*`.

### Core derived tables

| Table | Purpose | Key |
|---|---|---|
| `sme_sw_member_daily` | PIT SW L2 daily membership materialized from SW history/monthly membership | `(trade_date, l2_code, ts_code)` |
| `sme_stock_orderflow_daily` | Per-stock official moneyflow plus bucket-level buy/sell audit, normalized to yuan | `(trade_date, ts_code)` |
| `sme_sector_orderflow_daily` | SW L2 aggregate 主力/超大单/大单/中小单 flow, concentration, member returns | `(trade_date, l2_code)` |
| `sme_sector_diffusion_daily` | 1/3/5/10 trading-day flow diffusion and return diffusion by PIT member universe | `(trade_date, l2_code)` |
| `sme_sector_state_daily` | Sector state machine: accumulation, distribution, crowded, rebound, retail chase, etc. | `(trade_date, l2_code)` |
| `sme_labels_daily` | 1/3/5/10/20 trading-day forward labels for tuning/backtest | `(trade_date, l2_code, horizon_days)` |
| `sme_market_structure_daily` | Persistent daily market-structure strategy snapshot used by reports and tuning | `(trade_date, profile_name)` |
| `sme_strategy_eval_daily` | Join of market-structure buckets to forward labels, used for OOS/OOC review | `(trade_date, profile_name, bucket, l2_code, horizon_days)` |

### Governance / audit tables

| Table | Purpose |
|---|---|
| `sme_unit_registry` | Authoritative unit conversion registry; `_yuan` columns are stored in yuan |
| `sme_data_contracts` | Table-level data contracts, source expectations, and quality rules |
| `sme_source_audit_daily` | Per-date source coverage and quality audit |
| `sme_etl_runs` | Incremental/backfill run audit with status, row counts, storage deltas, and errors |
| `sme_storage_audit` | Schema/table/index storage monitoring against the 10GB MVP1 budget |
| `sme_param_runs` | Parameter search / promotion artifacts and gating state |
| `sme_report_runs` | SME report output registry for production/manual runs |

### Temporal contract

All SME "previous", "next", "recent N", and forward-label horizons are trading-day based. Use `smartmoney.trade_cal` / canonical trading-date row numbers, not calendar-day arithmetic.

---

## Part 4 — Research schema (`research.*`)

Research owns its own product memory because single-stock financial statements are sparse and reusable. A report generated in manual mode can satisfy a later production request when the stock / statement lens / tier / latest filing period match.

| Table | Purpose |
|---|---|
| `company_identity` | Resolver lookup for `ts_code`, name, exchange, list status, and SW identity hints |
| `api_cache` | TTL'd Tushare endpoint responses |
| `computed_cache` | Deterministic / LLM computed payload cache keyed by input hash |
| `factor_value` | Persisted rule-layer factor results used for peer rank and industry scans |
| `period_factor_decomposition` | Canonical period-level fundamental memory for profitability / growth / cash quality / balance / governance |
| `pdf_extract_cache` | Analyst-report PDF extraction cache, including key points and text hash |
| `report_runs` | Research report asset registry with `output_html_path`, `output_pdf_path`, `scope_json` (`analysis_type`, `tier`, `latest_period`, `md_path`) |
| `report_sections` | Section JSON for each registered Research report asset |
| `company_event_memory` | LLM-extracted company events from announcements / IRM / reports |
| `scan_run` | SW L2 peer-scan audit trail |

Primary reuse key for report assets: `ts_code + scope_json.analysis_type + report_type + scope_json.latest_period`. `run_mode` is audit metadata, not a forced-rerun boundary.

---

## Enum reference

### Core schema enums

| Column | Values |
|---|---|
| `report_runs.market` | `china_a`, `hk`, `us`, `cross_asset` |
| `report_runs.report_family` | `macro`, `asset`, `tech`, `main`, `smartmoney`, `weekend`, `briefing`, `adhoc` |
| `report_runs.slot` | `morning`, `midday`, `evening`, `weekend_sat`, `weekend_sun`, `adhoc`, … |
| `report_runs.status` | `running`, `succeeded`, `failed`, `partial`, `superseded` |
| `report_runs.run_mode` | `test`, `manual`, `production` |
| `report_judgments.review_status` | `pending`, `validated`, `partial`, `failed`, `not_applicable` |
| `report_model_outputs.status` | `parsed`, `parse_failed`, `fallback_used`, `error` |

### SmartMoney schema enums

| Column | Values |
|---|---|
| `sector_state_daily.role` | `主线`, `中军`, `轮动`, `防守`, `催化`, `退潮`, `未识别` |
| `sector_state_daily.cycle_phase` | `冷`, `点火`, `确认`, `扩散`, `高潮`, `分歧`, `退潮`, `未识别` |
| `stock_signals_daily.role` | `龙头`, `中军`, `情绪先锋`, `补涨`, `趋势`, `风险` |
| `market_state_daily.market_state` | `进攻`, `中性`, `防守`, `退潮` |
| `backtest_runs.status` | `running`, `succeeded`, `failed`, `partial` |
| `param_versions.status` | `active`, `archived`, `draft` |
