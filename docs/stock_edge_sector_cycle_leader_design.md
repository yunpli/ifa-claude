# Stock Edge sector_cycle_leader research design

Date: 2026-05-08

## Thesis

`sector_cycle_leader` reconstructs Stock Edge selection as sector-first, then leader-within-sector.  The strategy is not a production baseline yet.  It is a research/proxy family whose job is to test whether "main-money accumulation before retail crowding" predicts 5/10/20 trading-day forward returns better than current cheap momentum/liquidity families.

## Current Data Audit

Available local PIT inputs:

- `sme.sme_stock_orderflow_daily`: stock-level small/medium/large/extra-large buckets, `main_net_yuan`, `retail_net_yuan`, amount, turnover, market cap, 2021-01-04 through 2026-05-08.
- `sme.sme_sector_orderflow_daily`: SW L2 sector amount, return fallback, main/retail net flow, breadth, top-5 concentration, leader stock, 2021-01-04 through 2026-05-08.
- `sme.sme_sector_diffusion_daily`: leader-vs-median returns, flow breadth windows, diffusion phase/score, top members.
- `sme.sme_sector_state_daily`: persisted sector state, transition hint, risk flags such as retail chase/crowding.
- `sme.sme_labels_daily`: sector forward labels for 1/3/5/10/20 trading days through 2026-04-30.
- `smartmoney.sw_member_monthly` and `sme.sme_sw_member_daily`: PIT SW membership / stock-sector mapping.
- `smartmoney.raw_daily`, `raw_daily_basic`, `raw_moneyflow`: existing Stock Edge proxy/replay market, liquidity, and raw moneyflow inputs.
- `public.report_runs` and `public.report_model_outputs`: generic report/LLM audit logs, useful for provenance but not suitable as a direct weekly theme feature table.

Gap:

- There was no normalized historical theme/news heat feature surface.  Added `stock.theme_heat_weekly` as the first compatibility cache target; initial script writes explicit `quality_flag='stub'` rows only.
- Weekly top-5 rows are not the final product shape.  Theme heat needs to become a multi-resolution rolling curve: raw events are incrementally ingested by source/endpoint watermark, intraday snapshots are computed every 1h/2h/4h when source frequency supports it, daily rows persist level/change/acceleration/persistence, and weekly rows summarize top themes plus cached LLM narrative for reports and historical features.

## Feature Model

Sector-stage features:

- Main accumulation: 5-day sector main net / sector amount, 5-day positive-main persistence, sector diffusion score.
- Retail crowding: 5-day retail net / amount, retail positive persistence, SME sector risk flags.
- Heat confirmation: 5-day sector return, price-positive breadth, flow breadth/diffusion.
- Exhaustion/crowding: top-5 main-flow concentration, `retail_chase`, `leader_crowded`, and crowding risk flags.

Leader features:

- Stock main-flow rank inside sector, stock 5-day main persistence, stock retail chase.
- Within-sector 5-day relative strength and raw moneyflow rank.
- Liquidity/tradability: mid-liquidity preference plus lower volatility.
- Drawdown resilience remains for full replay; proxy only has volatility/left-tail proxies.

Theme/news feature:

- Raw event layer: one row per news/announcement/research/event item with `source`, `endpoint`, source event id or fingerprint, event timestamp, observed timestamp, related stocks/sectors when available, source URL, raw payload hash, and ingestion watermark metadata.  Ingestion is incremental per source/endpoint; reprocessing must be idempotent and PIT-safe.
- Intraday/hourly layer: `stock.theme_heat_snapshots`-style rows keyed by `snapshot_ts`, `window_hours`, `theme_id/theme_label`, and optional SW sector mapping.  Windows such as 1h, 2h, and 4h should emit `heat_level`, `heat_delta`, `heat_acceleration`, breadth, source-count quality, and source-lag quality when source frequency supports sub-day updates.
- Daily layer: `stock.theme_heat_daily`-style rows keyed by trade date and theme.  Required features include `heat_level`, `heat_delta`, `heat_acceleration`, `persistence_days`, sector/theme breadth, representative stocks, and evidence counts.  Daily rows are the preferred feature input for 5/10/20 trading-day stock selection.
- Weekly layer: `stock.theme_heat_weekly` remains a report/cache summary: top themes, representative sectors/stocks, evidence URLs, and cached batch LLM analysis.  It should be derived from raw/daily evidence, not maintained as a standalone static top-5 opinion table.
- Flow alignment layer: theme curves must join SME sector/stock main-money and retail-chase curves.  Core features are main-money lead vs retail chase alignment, crowding/distribution risk, sector breadth, leader breadth, and whether heat is expanding, peaking, exhausting, or fading.
- LLM policy: repo `ifa.core.llm.LLMClient` is the high-level weekly/daily theme strategist, not a conservative stub and not a per-row tagger.  It asks which A-share themes actually changed capital behavior, which are one-day hype versus persistent, and maps them to sectors, representative stocks/leaders, risks, and validation signals.  Inputs should be cached Tushare/local news, announcements, events, existing Research rows, and TA catalyst rows.  If cached facts are thin, LLM-prior rows are allowed only with `quality_flag='llm_prior_only'` or `needs_local_evidence`; those rows are preliminary context, not strong alpha evidence.  Backtests and reports consume persisted rows only.
- Current proxy sets `theme_heat_score` missing until real historical theme rows are mapped to SW sectors.  The next implementation should prefer daily rolling features; weekly rows are enough only for report narrative and coarse historical diagnostics.

Current daily/weekly LLM interface:

```bash
uv run python scripts/stock_edge_theme_heat_llm.py \
  --date 2026-05-08 \
  --window 7d \
  --cadence daily \
  --dry-run \
  --allow-llm-prior \
  --json

uv run python scripts/stock_edge_theme_heat_llm.py \
  --date 2026-05-04 \
  --cadence weekly \
  --persist \
  --allow-llm-prior \
  --source all-cache \
  --json

uv run python scripts/stock_edge_theme_heat_stub.py \
  --week 2026-05-04 \
  --build-llm \
  --llm-dry-run \
  --allow-llm-prior \
  --source all-cache \
  --json
```

`scripts/stock_edge_theme_heat_llm.py` is the preferred MVP entrypoint.  `--dry-run` emits the prompt, response schema, and compact fact pack with no external LLM call or DB/artifact write.  Daily `--persist` writes a local JSON artifact under `/Users/neoclaw/claude/ifaenv/data/stock/theme_heat/llm/`; weekly `--persist` writes into the existing `stock.theme_heat_weekly` columns.  `--from-json` ingests reviewed model output without a live LLM call.  The legacy `stock_edge_theme_heat_stub.py --build-llm` path remains weekly-compatible.  Parsed weekly rows store `persistence_score`, `freshness`, `leader_candidates`, `one_day_wonder_risk`, flow/crowding judgements, `validation_signals`, horizon validation signals, and evidence refs in `evidence_json`.

Proposed table progression:

1. `stock.theme_raw_events`: raw normalized event rows plus source/endpoint watermark audit.
2. `stock.theme_heat_snapshots`: intraday/hourly rolling snapshots, optional when source frequency is good enough.
3. `stock.theme_heat_daily`: daily theme curve and flow-alignment features for stock selection.
4. `stock.theme_heat_weekly`: weekly top themes and cached LLM summary for reporting/history.

## Objective and Gates

Primary horizons are 5/10/20 trading days.  Diagnostics must report rank IC, top bucket average return, top bucket win rate, top-vs-bottom spread, month stability, March 2026 behavior, and sector slices where available.

Promotion gate remains unchanged: no production YAML changes from proxy-only evidence.  Required path is 6-month 60-PIT/top200 proxy, then 60-PIT full replay on a cross-section subset, then multi-window and final walk-forward validation.
