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

- There was no normalized historical weekly theme/news heat feature table.  Added `stock.theme_heat_weekly` as the cache target; initial script writes explicit `quality_flag='stub'` rows only.

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

- Weekly top-5 themes with category, affected sectors, representative stocks, confidence, URLs, generated_at, valid_week.
- Backtests must use cached weekly rows only.  No per-row LLM calls.
- Current proxy sets `theme_heat_score` missing until real historical theme rows are mapped to SW sectors.

## Objective and Gates

Primary horizons are 5/10/20 trading days.  Diagnostics must report rank IC, top bucket average return, top bucket win rate, top-vs-bottom spread, month stability, March 2026 behavior, and sector slices where available.

Promotion gate remains unchanged: no production YAML changes from proxy-only evidence.  Required path is 6-month 60-PIT/top200 proxy, then 60-PIT full replay on a cross-section subset, then multi-window and final walk-forward validation.
