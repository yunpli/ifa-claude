# IFAVR — Reporting Database Schema (proposal v0.1)

This document is the **design proposal** for the iFA reporting database. No
migrations have been generated yet — please review and confirm before we lock in
the alembic baseline.

The schema serves the **whole** P0→P2 roadmap, not only Macro morning/evening.
Macro is the first family to populate it; Asset, Tech, Main A-share, weekend,
intraday briefings, ad-hoc research, and reviews all share the same `report_*`
tables, distinguished by `report_family` / `report_type` / `slot`.

---

## 1. Design principles

1. **One database, two physical instances.** `ifavr` (production) and `ifavr_test`
   (test) are physically separate databases on the same local cluster. Run-mode
   selection happens at the `database_url` level, so test runs can never touch
   production data.
2. **Run-mode column on `report_runs`.** Even within a single DB, every run
   records whether it was a `manual` or `production` invocation, so dashboards
   and review queries can include / exclude as needed.
3. **UUID primary keys via `gen_random_uuid()`** (built-in in PostgreSQL 13+ via
   `pgcrypto`, enabled by migration). Time-ordered v7 is a future option; v4 is
   sufficient now.
4. **JSONB everywhere flexible content lives** (`value_json`, `content_json`,
   `parsed_json`, `affected_areas`). Strict columns where the field is a stable
   business attribute (e.g. `slot`, `data_timing_label`).
5. **All timestamps are `TIMESTAMPTZ`.** The application stores in UTC; rendering
   converts to `Asia/Shanghai`.
6. **No on-delete cascades on report_runs → children.** A run is the immutable
   unit of work; you delete *whole runs* (rare, only test/cleanup), and we want
   that to be an explicit operation.
7. **Foreign keys are `ON UPDATE CASCADE ON DELETE RESTRICT`** by default.
8. **Soft enums via `TEXT CHECK(...)`** rather than native PostgreSQL enums —
   PRD vocabulary is still evolving, and `ALTER TYPE … ADD VALUE` cannot run in
   a transaction. CHECK constraints are easier to evolve.

---

## 2. Enumerations (as CHECK constraints)

| Column                     | Allowed values |
|----------------------------|----------------|
| `report_runs.market`       | `china_a`, `hk`, `us`, `cross_asset` |
| `report_runs.report_family`| `macro`, `asset`, `tech`, `main`, `weekend`, `briefing`, `adhoc` |
| `report_runs.report_type`  | `morning_long`, `midday_long`, `evening_long`, `weekend_review`, `weekend_outlook`, `briefing`, `adhoc_*` (free below) |
| `report_runs.slot`         | `morning`, `midday`, `evening`, `0929`, `1000`, `1030`, `1100`, `1130`, `1305`, `1330`, `1400`, `1430`, `1459`, `weekend_sat`, `weekend_sun`, `adhoc` |
| `report_runs.status`       | `running`, `succeeded`, `failed`, `partial`, `superseded` |
| `report_runs.run_mode`     | `test`, `manual`, `production` |
| `report_inputs.data_timing_label` | `latest_available`, `previous_trading_day_confirmed`, `overnight_to_cutoff`, `text_derived_capture`, `to_be_validated_today`, `historical_memory` |
| `report_inputs.freshness_status`  | `fresh`, `stale`, `missing`, `partial` |
| `report_facts.confidence`         | `high`, `medium`, `low` |
| `report_signals.direction`        | `up`, `down`, `flat`, `mixed`, `unknown` |
| `report_signals.strength`         | `strong`, `medium`, `weak` |
| `report_judgments.review_status`  | `pending`, `validated`, `partial`, `failed`, `not_applicable` |
| `report_model_outputs.status`     | `parsed`, `parse_failed`, `fallback_used`, `error` |
| `macro_text_derived_indicators.release_type` | `official_release`, `media_report_citing_official_data`, `forecast_or_expectation`, `market_commentary`, `unrelated_or_false_positive`, `unknown` |
| `macro_text_derived_indicators.status` | `extracted`, `confirmed`, `revised`, `rejected` |
| `macro_policy_event_memory.policy_signal` | `升温`, `平稳`, `降温`, `延续既有框架`, `无新增信号` |
| `macro_policy_event_memory.status` | `active`, `expired`, `superseded` |

---

## 3. Core tables

### 3.1 `report_runs`
Every report generation = one row. Children join on `report_run_id`.

| Column                | Type           | Constraints / notes |
|-----------------------|----------------|---------------------|
| `report_run_id`       | UUID           | PK, default `gen_random_uuid()` |
| `market`              | TEXT           | NOT NULL, CHECK |
| `report_family`       | TEXT           | NOT NULL, CHECK |
| `report_type`         | TEXT           | NOT NULL, CHECK |
| `report_date`         | DATE           | NOT NULL — the *trading date* the report covers |
| `slot`                | TEXT           | NOT NULL, CHECK |
| `timezone`            | TEXT           | NOT NULL, default `'Asia/Shanghai'` |
| `data_cutoff_at`      | TIMESTAMPTZ    | NOT NULL — no input later than this is allowed |
| `status`              | TEXT           | NOT NULL, default `'running'`, CHECK |
| `run_mode`            | TEXT           | NOT NULL, CHECK — `test`/`manual`/`production` |
| `triggered_by`        | TEXT           | nullable — operator id, `cron`, etc. |
| `template_version`    | TEXT           | NOT NULL — e.g. `macro_morning_v0.4` |
| `prompt_version`      | TEXT           | NOT NULL — overall prompt bundle version (per-section also tracked) |
| `output_html_path`    | TEXT           | nullable until rendered |
| `output_json_path`    | TEXT           | nullable until rendered |
| `output_md_path`      | TEXT           | nullable; reserved for future MD KB |
| `started_at`          | TIMESTAMPTZ    | NOT NULL, default `now()` |
| `completed_at`        | TIMESTAMPTZ    | nullable |
| `duration_seconds`    | NUMERIC(10,3)  | nullable |
| `fallback_used`       | BOOLEAN        | NOT NULL, default `false` — true if any section degraded |
| `error_summary`       | TEXT           | nullable — short reason if `status='failed'` |
| `created_at`          | TIMESTAMPTZ    | NOT NULL, default `now()` |

**Indexes:**
- `(market, report_family, report_type, report_date, slot)` — find a run by identity
- `(run_mode, status, created_at DESC)` — ops dashboard
- `(report_date DESC, slot)` — daily timelines

**Uniqueness:** *not* unique on identity columns — we may run multiple times
(e.g. v1 superseded by v2). The latest non-superseded run is "the" report; a
view `current_report_runs` will surface that.

---

### 3.2 `report_inputs`
Records every source-side fetch a run consumed.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `input_id`            | UUID PK     | |
| `report_run_id`       | UUID FK     | → `report_runs` |
| `input_type`          | TEXT        | e.g. `tushare.daily`, `tushare.cn_cpi`, `npr`, `macro_text_derived` |
| `source_name`         | TEXT        | e.g. `tushare`, `npr`, `local_table` |
| `source_table_or_api` | TEXT        | e.g. `daily`, `macro_policy_event_memory` |
| `data_window_start`   | TIMESTAMPTZ | nullable |
| `data_window_end`     | TIMESTAMPTZ | nullable |
| `data_timing_label`   | TEXT CHECK  | see enums |
| `row_count`           | INT         | how many rows came back |
| `freshness_status`    | TEXT CHECK  | |
| `raw_snapshot_path`   | TEXT        | nullable — path to JSON/Parquet of the raw fetch |
| `created_at`          | TIMESTAMPTZ | default `now()` |

---

### 3.3 `report_facts`
Objective, source-anchored facts derived from inputs.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `fact_id`             | UUID PK     | |
| `report_run_id`       | UUID FK     | |
| `section_key`         | TEXT        | e.g. `macro_morning.s2_core_panel` |
| `fact_type`           | TEXT        | e.g. `macro_indicator`, `flow_summary`, `policy_event` |
| `subject`             | TEXT        | what the fact is about (`CPI`, `北向资金`, …) |
| `fact_text`           | TEXT        | human-readable Chinese sentence |
| `value_json`          | JSONB       | structured payload (numbers, dates, sub-fields) |
| `data_timing_label`   | TEXT CHECK  | |
| `source_reference_ids`| UUID[]      | array of `report_references.reference_id` |
| `confidence`          | TEXT CHECK  | |
| `created_at`          | TIMESTAMPTZ | default `now()` |

**Index:** `(report_run_id, section_key)`.

---

### 3.4 `report_signals`
Signals derived from one or more facts.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `signal_id`           | UUID PK     | |
| `report_run_id`       | UUID FK     | |
| `section_key`         | TEXT        | |
| `signal_type`         | TEXT        | e.g. `liquidity_tone`, `policy_signal`, `cross_asset_transmission` |
| `signal_text`         | TEXT        | |
| `based_on_fact_ids`   | UUID[]      | provenance chain |
| `direction`           | TEXT CHECK  | |
| `strength`            | TEXT CHECK  | |
| `confidence`          | TEXT CHECK  | |
| `created_at`          | TIMESTAMPTZ | |

---

### 3.5 `report_judgments`
**The most important table.** These are the reviewable claims.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `judgment_id`         | UUID PK     | |
| `report_run_id`       | UUID FK     | |
| `section_key`         | TEXT        | |
| `judgment_type`       | TEXT        | e.g. `macro_tone`, `liquidity_tone`, `risk`, `hypothesis`, `mapping` |
| `judgment_text`       | TEXT        | |
| `target`              | TEXT        | what the judgment is about (`A-share risk appetite`, `半导体板块`, …) |
| `horizon`             | TEXT        | e.g. `today_morning_session`, `today_full_day`, `next_week`, `multi_day` |
| `confidence`          | TEXT CHECK  | |
| `validation_method`   | TEXT        | how a later report should validate it |
| `review_status`       | TEXT CHECK  | starts `pending` |
| `superseded_by`       | UUID        | nullable, FK self-ref — if a later run revises this judgment |
| `created_at`          | TIMESTAMPTZ | |

---

### 3.6 `report_sections`
The final per-section content (what the renderer reads).

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `section_id`          | UUID PK     | |
| `report_run_id`       | UUID FK     | |
| `section_key`         | TEXT        | |
| `section_title`       | TEXT        | Chinese display title |
| `section_order`       | INT         | render order |
| `content_markdown`    | TEXT        | optional MD form |
| `content_json`        | JSONB       | structured payload — what the Jinja templates consume |
| `input_fact_ids`      | UUID[]      | |
| `input_signal_ids`    | UUID[]      | |
| `input_judgment_ids`  | UUID[]      | |
| `prompt_name`         | TEXT        | e.g. `macro_morning.s1_tone` |
| `prompt_version`      | TEXT        | |
| `model_output_id`     | UUID        | FK → `report_model_outputs` (nullable for rule-only sections) |
| `fallback_used`       | BOOLEAN     | default `false` |
| `created_at`          | TIMESTAMPTZ | |

**Unique:** `(report_run_id, section_key)`.

---

### 3.7 `report_references`
Source citations actually used in a run.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `reference_id`        | UUID PK     | |
| `report_run_id`       | UUID FK     | |
| `source_type`         | TEXT        | `news`, `major_news`, `npr`, `tushare_table`, `irm_qa`, `research_report` |
| `source_name`         | TEXT        | publisher / table name |
| `source_table_or_api` | TEXT        | |
| `title`               | TEXT        | |
| `url`                 | TEXT        | |
| `url_hash`            | BYTEA       | SHA256 of `url` for dedup; index |
| `publish_time`        | TIMESTAMPTZ | |
| `data_time`           | TIMESTAMPTZ | for data-source references |
| `evidence_sentence`   | TEXT        | quote (≤15 words rule for displayed text) |
| `used_in_section_key` | TEXT        | |
| `created_at`          | TIMESTAMPTZ | |

**Index:** `(report_run_id, source_type)`, `(url_hash)`.

---

### 3.8 `report_model_outputs`
Audit trail for every LLM call.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `model_output_id`     | UUID PK     | |
| `report_run_id`       | UUID FK     | |
| `section_key`         | TEXT        | |
| `prompt_name`         | TEXT        | |
| `prompt_version`      | TEXT        | |
| `model_name`          | TEXT        | actual model id returned |
| `endpoint`            | TEXT        | `primary` \| `fallback` |
| `input_json_path`     | TEXT        | path to dumped input on disk |
| `output_json_path`    | TEXT        | path to dumped raw output |
| `parsed_json`         | JSONB       | parsed structured output |
| `status`              | TEXT CHECK  | |
| `prompt_tokens`       | INT         | |
| `completion_tokens`   | INT         | |
| `latency_seconds`     | NUMERIC(10,3) | |
| `created_at`          | TIMESTAMPTZ | |

---

### 3.9 `report_reviews`
Every later judgment re-evaluation.

| Column                | Type        | Notes |
|-----------------------|-------------|-------|
| `review_id`           | UUID PK     | |
| `judgment_id`         | UUID FK     | the judgment being reviewed |
| `review_report_run_id`| UUID FK     | the run that performed the review (e.g. evening reviewing morning) |
| `review_result`       | TEXT        | `validated` / `partial` / `failed` / `not_applicable` |
| `evidence_text`       | TEXT        | |
| `evidence_json`       | JSONB       | |
| `lesson`              | TEXT        | optional — distilled lesson worth carrying forward |
| `should_update_rule`  | BOOLEAN     | default false |
| `created_at`          | TIMESTAMPTZ | |

---

## 4. Macro-specific memory tables

These are produced by the **pre-jobs**, *not* by report runs. Reports read them.

### 4.1 `macro_text_derived_indicators`
M2, social financing, new RMB loans extracted from `news` / `major_news` / `npr`
by `macro_text_derived_capture_job`. Schema follows the PRD `ifa-macro-v1.txt`
§2.1 verbatim, with these additions / clarifications:

- `id UUID PK default gen_random_uuid()`
- all enum-style fields enforced via CHECK
- `(indicator_name, reported_period, status)` index
- `(source_publish_time DESC)` index for "latest captures" queries
- `unique (source_url, indicator_name, reported_period)` to avoid duplicates

### 4.2 `macro_policy_event_memory`
PRD §2.2 verbatim, with:

- `id UUID PK`, `event_id` made UNIQUE (the PRD's existing identifier; we keep
  it as a stable cross-run handle, distinct from the surrogate PK)
- `affected_areas JSONB` (array)
- `(status, carry_forward_until)` index for active-memory queries
- `(policy_dimension, status)` index

### 4.3 *(reserved)* `macro_news_candidates`
PRD lists this as optional. **Recommendation: skip in v0.1**, add only if a
candidate-then-extract two-stage flow turns out to be needed. Initial extraction
will write straight to `macro_text_derived_indicators`.

---

## 5. Auxiliary

### 5.1 `report_render_manifests` (PRD §1.3)
Lightweight: which output paths belong to which run, with render config snapshot.
Could also be folded into `report_runs.output_*` columns — **recommendation:
skip a separate table for v0.1**, keep the three path columns on `report_runs`.

### 5.2 `report_prompts_catalog` (NEW, recommended addition)
A small lookup of `(prompt_name, prompt_version) → text` so we can reproduce a
historical run even if the file content changed in the repo. Optional for v0.1
if we keep prompt YAML under git-tagged commits, but valuable later.

---

## 6. Open questions / decisions needed

| # | Question                                                                                  | Default I'd pick                |
|---|-------------------------------------------------------------------------------------------|---------------------------------|
| 1 | Use `pgcrypto.gen_random_uuid()` or app-side UUID v7?                                     | `gen_random_uuid()` for v0.1    |
| 2 | Single DB with `run_mode` column vs separate `ifavr_test`?                                | **Both** — separate test DB *and* the column, since test runs against test DB plus the column lets us mark "what if I rerun production manually" inside prod DB |
| 3 | Track prompt content in DB (`report_prompts_catalog`)?                                    | Defer — git-tag prompts for v0.1|
| 4 | Add `macro_news_candidates`?                                                              | Defer                           |
| 5 | Foreign key `ON DELETE`: RESTRICT or CASCADE for child rows of `report_runs`?             | `RESTRICT` (deletion is a deliberate ops action) |
| 6 | Should `report_judgments.target` and `affected_areas` move to a normalized taxonomy table?| Defer; keep as TEXT / TEXT[] for v0.1 |
| 7 | Index strategy for `report_facts.value_json` / `content_json` — GIN now?                  | GIN on `value_json` & `content_json` only after first slow query — defer |

Please mark each of the above with **agree / change / discuss** and I will lock
the alembic baseline migration on the next pass.
