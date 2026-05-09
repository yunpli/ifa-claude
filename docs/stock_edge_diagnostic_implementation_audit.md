# Stock Edge Diagnostic Implementation Audit

Date: 2026-05-08  
Baseline commit: `ff10e2d Add Stock Edge diagnostic MVP`  
Latest implementation update: P0 client-usability/artifact pass after `24093b0 Optimize Stock Edge proxy validation`

## Executive Read

The diagnostic MVP has the right product shape: a read-only single-stock report with separate perspectives and conflict-preserving synthesis.  The latest P0 pass adds structured run manifests, multi-stock directory output with index summary, per-perspective freshness quality (`fresh/stale/unavailable`), a compact institutional HTML layout, theme-cache hit surfacing, and a batch JSON writer for weekly theme heat.  The main remaining gap is that most perspectives are still thin evidence collectors, not yet full adapters to their native family outputs.  There is still no persisted diagnostic run DB table or Telegram delivery contract.

## Current Implementation By Perspective

| Perspective | Current code | Data / tables read | Usable fields today | Stub / unavailable / slow | Key gaps |
|---|---|---|---|---|---|
| Stock Edge / sector-cycle | `ifa/families/stock/diagnostic/service.py::_stock_edge_perspective`; full matrix optional via `--full-stock-edge` | `smartmoney.sw_member_monthly`, `sme.sme_sector_orderflow_daily`, `sme.sme_sector_diffusion_daily`, `sme.sme_sector_state_daily`, `sme.sme_stock_orderflow_daily`, `stock.analysis_record` | SW L2, sector state/diffusion, sector main/retail ratios, main/retail divergence, risk/crowding flags when available, sector leader, target main/retail flow, latest persisted report summary, optional 5d/10d/20d decisions | Full strategy matrix/decision layer skipped by default for latency; `sector_cycle_leader` replay/proxy rank is not stock-specific/persisted yet and is explicitly marked missing | Need first-class sector-cycle adapter with normalized fields, latest report reuse, latency budget, and explicit leader-within-sector rank |
| TA | `_ta_perspective`; light loader `_load_light_ta_context` | `ta.candidates_daily`, `ta.warnings_daily`, `ta.regime_daily` | setup name/label, rank, final score, stars, entry, stop, target, RR, warnings, market regime | `setup_metrics_daily` not loaded; no family-level historical edge in diagnostic | Add TA family rollup: setup family, tier, 60/180d edge, sector role, and trigger/invalidation normalization |
| Ningbo | `_ningbo_perspective` | `ningbo.recommendations_daily`, fallback `ningbo.candidates_daily` | rec date, strategy, scoring mode, confidence, rec price, signal meta raw | No recent hit becomes unavailable; no explanation of why not selected; Kronos/ML context only indirectly available | Add recency window policy, top-N rank context, reason fields, and optional Ningbo tracking outcome |
| Research / news / theme | `_research_perspective`; `_load_light_research_lineup`; `_load_light_event_context`; `ifa/families/stock/theme_heat.py` | `research.period_factor_decomposition`, `research.report_runs`, `research.company_event_memory`, `ta.catalyst_event_memory`, `stock.theme_heat_weekly` | annual/quarterly factor counts, recent research reports, event title/polarity/importance, weekly top-5 theme rows, stock/sector theme-hit marker when cache rows contain mappings | `stock.theme_heat_weekly` currently allows explicit `quality_flag='stub'`; no fundamental factor scoring | Need concise fundamental scorecard fields, event freshness/severity normalization, real weekly theme/news backfill coverage |
| Risk | `_risk_perspective` | `ta.blacklist_daily`, `ta.suspend_daily`, `ta.stk_limit_daily`, `smartmoney.raw_daily`, `smartmoney.raw_daily_basic` | blacklist/suspension/limit events, avg amount 7d, ATR14 pct, turnover | No ST/delist/pledge/reduction/margin-specific veto table; daily risk only, no minute execution risk by default | Add hard-veto registry, board limit rules, gap/liquidity capacity fields, and optional intraday execution risk adapter |
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
- Existing full report path remains `uv run python -m ifa.cli stock report|quick`

Missing:

- No persisted `stock.diagnostic_runs` / `stock.diagnostic_perspective_evidence` tables; P0 uses lightweight manifest JSON with the future DB shape documented in the artifact.
- No Telegram-specific short summary/delivery contract.
- No latency SLO measurement in CLI output.

## Data / Table Gaps

P0 persisted surface now available:

- Per-run manifest JSON: `artifact_type=stock_edge_diagnostic_run`, stock code/name, requested/generated timestamps, perspective status/freshness, conclusion, confidence, output paths, evidence freshness, and DB schema plan.
- Multi-stock index JSON: `artifact_type=stock_edge_diagnostic_index`, one summary row per stock.

Future DB promotion plan:

- `stock.diagnostic_runs`: one row per diagnostic request, with `ts_code`, `as_of_trade_date`, `generated_at_bjt`, `run_mode`, `status`, `synthesis_json`, `output_markdown_path`, `output_html_path`, `logic_version`.
- `stock.diagnostic_perspective_evidence`: normalized perspective evidence rows keyed by `run_id`, `perspective_key`, `status`, `view`, `source_table`, `source_as_of`, `payload_json`.
- Real `stock.theme_heat_weekly` rows with `quality_flag != 'stub'`, mapped to SW L1/L2 and representative stocks.

P1/P2 candidate surfaces:

- `stock.sector_cycle_leader_daily`: persisted leader-within-sector ranks and scores from SME/orderflow proxy, separate from production YAML.
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

Still P0/P1:

1. Promote diagnostic runs to DB once manifest contract stabilizes.
   - Files: new Alembic migration, `ifa/families/stock/diagnostic/persistence.py`, `ifa/families/stock/diagnostic/service.py`, `ifa/cli/stock.py`.
   - Tables: `stock.diagnostic_runs`, `stock.diagnostic_perspective_evidence`.
   - Verify: unit test persistence with transaction rollback; smoke `stock diagnose 300042.SZ --format json` and confirm rows.

2. Normalize perspective adapters.
   - Files: split `service.py` into `perspectives/{stock_edge,ta,ningbo,research,risk}.py`.
   - Contract: each adapter returns `PerspectiveEvidence` plus `latency_ms`, `source_tables`, `missing_required`.
   - Verify: unit tests for each adapter with mocked query rows.

3. Define synthesis version.
   - Files: `models.py`, `service.py`, docs.
   - Fields: `logic_version='stock_diagnostic_synthesis_v1'`, conflict taxonomy, hard-veto precedence.
   - Verify: tests for hard risk, sector positive/TA neutral, Ningbo positive/risk negative, all unavailable.

### P1 - Improve Evidence Quality

1. Sector-cycle leader adapter.
   - Files: `ifa/families/stock/diagnostic/perspectives/sector_cycle.py`, `ifa/families/stock/backtest/outcome_proxy.py`.
   - Tables: optionally `stock.sector_cycle_leader_daily`; continue reading SME source tables.
   - Verify: static PIT check that all previous/next windows use trading calendar, not calendar dates; 60-PIT proxy comparison remains no-YAML.

2. TA rollup.
   - Files: TA context loader and diagnostic TA adapter.
   - Tables: `ta.setup_metrics_daily`, `ta.candidates_daily`, `ta.warnings_daily`.
   - Verify: sample stocks with candidate only, warning only, both, neither.

3. Research/news scorecard.
   - Files: Research diagnostic adapter; maybe `ifa/families/research/memory.py` helper reuse.
   - Tables: `research.period_factor_decomposition`, `research.report_runs`, `research.company_event_memory`, `ta.catalyst_event_memory`.
   - Verify: annual/quarterly report reuse for 朗科科技; negative event polarity displays without LLM rewriting numbers.

4. Risk veto registry.
   - Files: risk adapter plus config/enum.
   - Tables: `ta.blacklist_daily`, `ta.suspend_daily`, `ta.stk_limit_daily`, future `stock.risk_veto_daily`.
   - Verify: synthetic hard-risk row forces `avoid`; limit-up event alone does not become a hard veto unless rules say so.

### P2 - Production Delivery And Monitoring

1. Telegram delivery contract.
   - Files: update Telegram skill/runbook docs, no cron mutation.
   - Output: 3-5 line summary plus HTML attachment path.
   - Verify: dry-run message payload from a generated diagnostic artifact.

2. Real weekly theme heat.
   - Files: replace `scripts/stock_edge_theme_heat_stub.py` with a cache builder that writes non-stub rows from approved inputs.
   - Tables: `stock.theme_heat_weekly`.
   - Verify: no per-row LLM calls in backtests; every row has source URLs/evidence and sector mapping.

3. Latency/availability monitoring.
   - Files: CLI timing wrapper or `stock.diagnostic_latency_log`.
   - Verify: default diagnostic remains fast without `--full-stock-edge`; full mode reports why it is slow.

## Verification Commands

```bash
uv run python scripts/stock_edge_diagnostic_audit.py
uv run pytest tests/stock/test_diagnostic.py tests/stock/test_theme_heat.py -q
uv run python -m compileall -q ifa/families/stock/diagnostic ifa/families/stock/theme_heat.py ifa/cli/stock.py scripts/stock_edge_diagnostic_audit.py
```
