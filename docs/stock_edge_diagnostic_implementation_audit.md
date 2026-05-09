# Stock Edge Diagnostic Implementation Audit

Date: 2026-05-08  
Baseline commit: `ff10e2d Add Stock Edge diagnostic MVP`  
Latest implementation update: P2 delivery dry-run contract + weekly theme heat source-policy builder; daily theme heat curve cache added

## Executive Read

The diagnostic MVP has the right product shape: a read-only single-stock report with separate perspectives and conflict-preserving synthesis.  P0/P1 added structured run manifests, DB persistence, multi-stock directory output with index summary, per-perspective freshness quality (`fresh/stale/unavailable`), latency/source contracts, a compact institutional HTML layout, adapter modules, theme-cache hit surfacing, TA/Ningbo/risk rollup improvements, and a persisted `stock.sector_cycle_leader_daily` rank surface for future backfills.  P2 now adds a Telegram dry-run delivery payload contract, a weekly theme heat builder that can use existing cached local event memories, and `stock.theme_heat_daily` for queryable daily heat curves.  The theme heat product direction is rolling and multi-resolution rather than weekly-only: raw event ingestion, optional intraday/hourly snapshots, daily heat curves, and weekly report summaries.  No Telegram/external send, cron mutation, production YAML mutation, auto-promote, or apply-to-baseline behavior has changed.

## Current Implementation By Perspective

| Perspective | Current code | Data / tables read | Usable fields today | Stub / unavailable / slow | Key gaps |
|---|---|---|---|---|---|
| Stock Edge / sector-cycle | `ifa/families/stock/diagnostic/service.py::_stock_edge_perspective`; full matrix optional via `--full-stock-edge` | `smartmoney.sw_member_monthly`, `sme.sme_sector_orderflow_daily`, `sme.sme_sector_diffusion_daily`, `sme.sme_sector_state_daily`, `sme.sme_stock_orderflow_daily`, `stock.sector_cycle_leader_daily`, `stock.analysis_record` | SW L2, sector state/diffusion, sector main/retail ratios, main/retail divergence, risk/crowding flags when available, sector leader, optional target rank/score surface, target main/retail flow, latest persisted report summary, optional 5d/10d/20d decisions | Full strategy matrix/decision layer skipped by default for latency; `stock.sector_cycle_leader_daily` is schema-ready but needs population/backfill | Need first-class sector-cycle adapter module and table builder/backfill |
| TA | `_ta_perspective`; light loader `_load_light_ta_context` | `ta.candidates_daily`, `ta.warnings_daily`, `ta.regime_daily`, `ta.setup_metrics_daily` | setup rollup, setup name/label, rank, final score, stars, entry, stop, target, RR, warnings, market regime, latest 60d setup edge metrics | 180d/family tier rollup not yet shown | Add trigger/invalidation normalization and broader family/tier edge fields |
| Ningbo | `_ningbo_perspective` | `ningbo.recommendations_daily`, fallback `ningbo.candidates_daily` | rec date, strategy, scoring mode, confidence, rec price, recency days, same-day rank context when available, signal meta raw | No recent hit becomes unavailable; no explanation of why not selected; Kronos/ML context only indirectly available | Add reason fields and optional Ningbo tracking outcome |
| Research / news / theme | `_research_perspective`; `_load_light_research_lineup`; `_load_light_event_context`; `ifa/families/stock/theme_heat.py` | `research.period_factor_decomposition`, `research.report_runs`, `research.company_event_memory`, `ta.catalyst_event_memory`, `stock.theme_heat_weekly` | annual/quarterly factor counts, recent research reports, event title/polarity/importance, weekly top-5 theme rows, stock/sector theme-hit marker when cache rows contain mappings | `stock.theme_heat_weekly` currently allows explicit `quality_flag='stub'`; no raw event table, hourly snapshots, or daily heat curve yet; no fundamental factor scoring | Need concise fundamental scorecard fields, event freshness/severity normalization, rolling theme heat event/snapshot/daily backfill coverage |
| Risk | `_risk_perspective` | `ta.blacklist_daily`, `ta.suspend_daily`, `ta.stk_limit_daily`, `smartmoney.raw_daily`, `smartmoney.raw_daily_basic` | blacklist/suspension/limit events, normalized hard/soft veto categories, avg amount 7d, ATR14 pct, turnover | No ST/delist/pledge/reduction/margin-specific veto table; daily risk only, no minute execution risk by default | Add persisted `stock.risk_veto_daily`, board limit rules, gap/liquidity capacity fields, and optional intraday execution risk adapter |
| Advisor synthesis | `synthesize_diagnostic`; `render_markdown` | Perspective objects only | conclusion, confidence, horizon suitability, trigger, invalidation, time window, position risk, conflict notes | Rule logic is intentionally simple; no persisted rationale version; trigger/invalidation often generic | Add versioned synthesis policy, stronger conflict taxonomy, and deterministic client wording templates |

## Current CLI / Delivery

Available:

- `uv run python -m ifa.cli stock diagnose <query> --format markdown|json`
- `uv run python -m ifa.cli stock diagnose <query> --format html`
- `uv run python -m ifa.cli stock diagnose <query> --format markdown|json|html --output <file-or-dir>`
- Multiple stocks are accepted as positional arguments; when `--output` is used with multiple stocks, it must be a directory.
- Directory output writes one report file plus one manifest JSON per stock; multi-stock runs also write `CN_stock_edge_diagnostic_index_*.json`.
- `--requested-at` for reproducible BJT as-of routing
- `--run-mode` for settings-compatible routing
- `--full-stock-edge` to run the expensive strategy matrix and decision layer
- `--persist-db/--no-persist-db` for best-effort audit persistence; default tries DB and falls back to artifact/terminal output when unavailable
- Existing full report path remains `uv run python -m ifa.cli stock report|quick`
- Dry-run Telegram payload path:
  `uv run python scripts/stock_edge_diagnostic_delivery.py --manifest <CN_stock_edge_diagnostic_*_manifest.json> --json`
  This writes a `stock_edge_diagnostic_telegram_delivery_payload` JSON containing title, short text, attachment paths, recipient placeholder, dry-run flag, latency, and failure context. It never sends externally.

Missing:

- No aggregate latency SLO monitor table; per-perspective `latency_ms` is already in manifest/DB evidence.

## Data / Table Gaps

P0 persisted surface now available:

- Per-run manifest JSON: `artifact_type=stock_edge_diagnostic_run`, stock code/name, requested/generated timestamps, perspective status/freshness, latency/source tables, conclusion, confidence, output paths, and evidence freshness.
- Multi-stock index JSON: `artifact_type=stock_edge_diagnostic_index`, one summary row per stock.

DB promotion implemented as best-effort persistence:

- `stock.diagnostic_runs`: one row per diagnostic request, with `ts_code`, `as_of_trade_date`, `generated_at`, `run_mode`, `status`, `synthesis_json`, `manifest_json`, `output_paths_json`, `logic_version`.
- `stock.diagnostic_perspective_evidence`: normalized perspective evidence rows keyed by `run_id`, `perspective_key`, `status`, `view`, `source_tables_json`, `source_as_of`, `evidence_json`, `raw_json`.
- CLI: `uv run python -m ifa.cli stock diagnose 300042.SZ --format json` attempts best-effort DB rows without mutating production YAML or crons; `--no-persist-db` disables DB writes.
- Real `stock.theme_heat_weekly` rows with `quality_flag != 'stub'`, mapped to SW L1/L2 and representative stocks. Implemented sources are approved JSON ingestion and local cached event memories; broad raw news/report source policy is still the blocker for reliable production backfill.
- Rolling theme heat is partially implemented.  `stock.theme_heat_daily` now stores daily `heat_level`, `heat_delta`, `heat_acceleration`, `persistence_days`, breadth, main-money-vs-retail alignment, and crowding/distribution risk from reviewed/batch daily LLM artifacts.  Remaining planned surfaces are `stock.theme_raw_events` for source/endpoint-watermarked raw news/announcement/research/event rows and `stock.theme_heat_snapshots` for 1h/2h/4h rolling snapshots when source frequency supports it.  `stock.theme_heat_weekly` should become a weekly summary derived from those lower-level tables.

P1/P2 candidate surfaces:

- `stock.sector_cycle_leader_daily`: schema implemented for persisted leader-within-sector ranks and scores from SME/orderflow proxy, separate from production YAML; still needs builder/backfill.
- `stock.risk_veto_daily`: normalized hard-veto facts from ST/delist/suspension/blacklist/limit/pledge/reduction sources.
- `stock.diagnostic_latency_log`: optional if CLI logs are not enough.

## Completion Plan

### P0 - Make Diagnostic Product Shippable

Done in latest P0 pass:

- Top summary block: conclusion, confidence, horizon suitability, trigger, invalidation, key conflict.
- Per-perspective contract aliases in JSON: `status`, `stance`, `evidence`, `missing_evidence`, `freshness`.
- Standalone HTML renderer and markdown/json/html CLI artifact writing.
- Better SME/sector-cycle evidence labels for stage, main/retail divergence, crowding/risk flags, and explicit missing note for non-persisted stock-specific sector-cycle leader rank.
- Minimal multi-stock CLI support for cheap batch diagnostics.

Current P0/P1/P2 checklist:

1. Promote diagnostic runs to DB once manifest contract stabilizes.
   - Files: new Alembic migration, `ifa/families/stock/diagnostic/persistence.py`, `ifa/families/stock/diagnostic/service.py`, `ifa/cli/stock.py`.
   - Tables: `stock.diagnostic_runs`, `stock.diagnostic_perspective_evidence`.
   - Verify: unit test persistence with transaction rollback; smoke `stock diagnose 300042.SZ --format json` and confirm rows.
   - Status: implemented. Diagnostic run/evidence migration `alembic/versions/p0q1r2s3t4u5_stock_diagnostic_runs.py`; follow-up sector leader surface migration `alembic/versions/p4q5r6s7t8u9_stock_diagnostic_persistence.py`; CLI switch `--persist-db/--no-persist-db`; unit test uses a fake transaction to verify run + evidence inserts. DB smoke requires `uv run alembic upgrade head` before expecting rows.

2. Normalize perspective adapters.
   - Files: split `service.py` into `perspectives/{stock_edge,ta,ningbo,research,risk}.py`.
   - Contract: each adapter returns `PerspectiveEvidence` plus `latency_ms`, `source_tables`, `missing_required`.
   - Verify: unit tests for each adapter with mocked query rows.
   - Status: implemented for contract fields, deferred for directory split. `PerspectiveEvidence` now carries `latency_ms`, `source_tables`, `missing_required`; `_safe()` and `_with_quality()` populate them for all current adapters. Directory split is a later refactor because current service remains small enough and changing imports is unnecessary risk for this pass.

3. Define synthesis version.
   - Files: `models.py`, `service.py`, docs.
   - Fields: `logic_version='stock_diagnostic_synthesis_v1'`, conflict taxonomy, hard-veto precedence.
   - Verify: tests for hard risk, sector positive/TA neutral, Ningbo positive/risk negative, all unavailable.
   - Status: implemented. `DiagnosticSynthesis.logic_version` defaults to `stock_diagnostic_synthesis_v1`; `_conflict_taxonomy()` tags hard-risk precedence and cross-perspective conflicts; manifest includes `logic_version` and full synthesis JSON. Current tests cover hard risk and sector-positive/TA-unconfirmed taxonomy; Ningbo/risk and all-unavailable should be added with adapter split.

### P1 - Improve Evidence Quality

1. Sector-cycle leader adapter.
   - Files: `ifa/families/stock/diagnostic/perspectives/sector_cycle.py`, `ifa/families/stock/backtest/outcome_proxy.py`.
   - Tables: optionally `stock.sector_cycle_leader_daily`; continue reading SME source tables.
   - Verify: static PIT check that all previous/next windows use trading calendar, not calendar dates; 60-PIT proxy comparison remains no-YAML.
   - Status: schema + optional diagnostic read path implemented, deferred for table builder/backfill. The Stock Edge perspective now attempts `stock.sector_cycle_leader_daily` and labels it missing when absent, without changing YAML or proxy promotion.

2. TA rollup.
   - Files: TA context loader and diagnostic TA adapter.
   - Tables: `ta.setup_metrics_daily`, `ta.candidates_daily`, `ta.warnings_daily`.
   - Verify: sample stocks with candidate only, warning only, both, neither.
   - Status: implemented. `_load_light_ta_context()` now fetches latest `ta.setup_metrics_daily` rows for setups found in candidate/warning evidence; `_ta_perspective()` surfaces 60d edge metrics. Verification is unit/static plus live sample smoke after DB is available.

2b. Ningbo recency/rank context.
   - Files: `ifa/families/stock/diagnostic/service.py`.
   - Tables: `ningbo.recommendations_daily`, `ningbo.candidates_daily`.
   - Verify: sample stock with recommendation/candidate row shows `recency_days` and same-day rank context when available.
   - Status: implemented. Rank context is read-only and only enriches diagnostic evidence.

3. Research/news scorecard.
   - Files: Research diagnostic adapter; maybe `ifa/families/research/memory.py` helper reuse.
   - Tables: `research.period_factor_decomposition`, `research.report_runs`, `research.company_event_memory`, `ta.catalyst_event_memory`.
   - Verify: annual/quarterly report reuse for 朗科科技; negative event polarity displays without LLM rewriting numbers.

4. Risk veto registry.
   - Files: risk adapter plus config/enum.
   - Tables: `ta.blacklist_daily`, `ta.suspend_daily`, `ta.stk_limit_daily`, future `stock.risk_veto_daily`.
   - Verify: synthetic hard-risk row forces `avoid`; limit-up event alone does not become a hard veto unless rules say so.
   - Status: implemented as in-memory normalized registry; deferred for `stock.risk_veto_daily` table. Current hard veto remains suspension or hard blacklist; limit events are soft risk only.

### P2 - Production Delivery And Monitoring

1. Telegram delivery contract.
   - Files: update Telegram skill/runbook docs, no cron mutation.
   - Output: 3-5 line summary plus HTML attachment path.
   - Verify: dry-run message payload from a generated diagnostic artifact.
   - Status: implemented as no-send dry-run formatter. Files: `ifa/families/stock/diagnostic/delivery.py`, `scripts/stock_edge_diagnostic_delivery.py`, tests in `tests/stock/test_diagnostic.py`. It consumes manifest JSON only and does not call Telegram.

2. Rolling multi-resolution theme heat.
   - Files: extend `ifa/families/stock/theme_heat.py` and replace `scripts/stock_edge_theme_heat_stub.py` with builders for raw ingestion, snapshot aggregation, daily aggregation, and weekly summary derivation.
   - Tables: current `stock.theme_heat_weekly` and `stock.theme_heat_daily`; planned `stock.theme_raw_events`, `stock.theme_heat_snapshots`, with weekly derived from daily/raw evidence.
   - Verify: source/endpoint watermarks are idempotent; no per-row/per-stock/realtime LLM calls in backtests; every aggregate row has source URLs/evidence, source lag/coverage quality, and sector/theme mapping; daily rows expose `heat_level`, `heat_delta`, `heat_acceleration`, `persistence_days`, breadth, main-money-vs-retail alignment, and crowding/distribution risk.
   - Status: partially implemented at the weekly and daily cache layers. `scripts/stock_edge_theme_heat_builder.py` / `scripts/stock_edge_theme_heat_stub.py --build-local` can write `quality_flag=local_source_cache` weekly rows from existing `research.company_event_memory` and `ta.catalyst_event_memory` without external LLM calls. `scripts/stock_edge_theme_heat_llm.py --cadence daily --persist` now writes both JSON artifacts and `stock.theme_heat_daily`; `--from-json --cadence daily --persist` supports reviewed offline ingestion. Weekly `--build-llm` uses repo `ifa.core.llm.LLMClient` for one weekly batch theme strategist call over cached facts; daily LLM does the same at day/window level. Dry-run modes emit prompt/schema/facts without external calls; insufficient-fact runs require `--allow-llm-prior` and must write `llm_prior_only` / `needs_local_evidence` quality flags. Remaining blocker: no broad raw `news` / `major_news` local cache table exists yet, so market-wide rolling heat is limited to company announcements/research-report cache plus derived event memories until a raw event cache table is added.

3. Latency/availability monitoring.
   - Files: CLI timing wrapper or `stock.diagnostic_latency_log`.
   - Verify: default diagnostic remains fast without `--full-stock-edge`; full mode reports why it is slow.
   - Status: implemented at adapter-contract level, deferred for DB log table. Each perspective now records `latency_ms` in manifest/DB evidence payload; SLO aggregation can be promoted to `stock.diagnostic_latency_log` later without changing report output.

## Verification Commands

```bash
uv run python scripts/stock_edge_diagnostic_audit.py
uv run pytest tests/stock/test_diagnostic.py tests/stock/test_theme_heat.py -q
uv run python -m compileall -q ifa/families/stock/diagnostic ifa/families/stock/theme_heat.py ifa/cli/stock.py scripts/stock_edge_diagnostic_audit.py scripts/stock_edge_diagnostic_delivery.py scripts/stock_edge_theme_heat_stub.py scripts/stock_edge_theme_heat_builder.py
```
