# Stock Edge Diagnostic Product Definition

Date: 2026-05-08

## Product Contract

Stock Edge diagnostic serves the real customer workflow:

> User provides one A-share name or code and asks for a single-stock diagnosis.

The product is not a single recommendation model and not a weekly stock-picking list.  It is a multi-perspective single-stock diagnostic report that answers whether the stock is worth further trading/research work, where the opportunity and risk sit, and what would change the conclusion over the next 5/10/20 trading days.

`sector_cycle_leader` is an important perspective and may become the weekly sector-first stock-picking briefing engine.  It is not the whole Stock Edge product.  A single-stock diagnostic must be able to say: "sector-cycle is positive, but TA is not ready", or "Ningbo fires, but hard risk vetoes the trade", without forcing consensus.

## Required Perspectives

| Perspective | Role | Current product expectation |
|---|---|---|
| Stock Edge / sector-cycle | Sector-first, leader-within-sector, orderflow and strategy-matrix evidence | Must explain sector state, target stock flow, leader status, latest Stock Edge decision if available |
| TA | Independent technical setup and warning family | Must surface recent setup/warning/regime evidence, not just price momentum |
| Ningbo | Independent short-term strategy family | Must show whether Ningbo recently selected the stock; absence is signal insufficiency, not bearish proof |
| Research / news / theme | Fundamentals, events, sell-side/PDF memory, weekly theme heat | Must cite stored factors/events/theme cache; LLM/theme rows must be cached before use |
| Risk | Hard veto and execution risk | Must surface blacklist, suspension, limit-event, liquidity, volatility and turnover evidence |
| Advisor synthesis | Client-facing conclusion | Must explain conflicts and map them to 5d/10d/20d suitability, trigger, invalidation and position risk |

## Conflict Policy

Perspectives are allowed to conflict.  The synthesis layer must preserve conflicts rather than smoothing them away:

- Hard risk wins over positive evidence.
- Two independent positive perspectives can make a short-term trade watchable only if hard risk is clear.
- Sector-cycle strength without TA/execution confirmation should usually be "watch" or "wait for pullback", not an unconditional buy.
- Research/news can support or challenge a trade, but must not invent numbers or override structured risk.
- Stub or unavailable evidence must be labeled explicitly and excluded from alpha claims.

## Current MVP Surface

Primary CLI:

```bash
uv run python -m ifa.cli stock diagnose 300042.SZ --format markdown
uv run python -m ifa.cli stock diagnose 朗科科技 --format json
uv run python -m ifa.cli stock diagnose 300042.SZ --full-stock-edge
uv run python -m ifa.cli stock diagnose 300042.SZ 朗科科技 --format html --output /Users/neoclaw/claude/ifaenv/out/manual/diagnostic_batch
```

Implementation:

- `ifa/families/stock/diagnostic/models.py` defines the typed evidence schema.
- `ifa/families/stock/diagnostic/service.py` builds a read-only diagnostic report.
- `ifa/cli/stock.py diagnose` exposes markdown/json/html output, one file per stock when `--output` is a directory, plus a multi-stock JSON index.
- Every written diagnostic also gets a lightweight manifest JSON containing stock code/name, requested/generated timestamps, perspective status/freshness, conclusion, confidence, output paths, and DB schema plan.
- `tests/stock/test_diagnostic.py` verifies conflict-preserving synthesis and unavailable-perspective rendering.

The MVP deliberately uses a light snapshot.  It does not run the expensive full Stock Edge strategy matrix unless `--full-stock-edge` is passed, and it skips optional intraday/model-context loaders in the default path.

## Persistence Contract

P0 uses structured JSON artifacts instead of a DB migration because the diagnostic report shape is still being hardened and this path does not affect report/delivery crons.  If artifacts become insufficient, promote the same manifest shape into:

- `stock.diagnostic_runs(run_id, ts_code, name, requested_at, generated_at, as_of_trade_date, conclusion, confidence, output_paths_json, perspective_status_json, evidence_freshness_json)`.
- `stock.diagnostic_evidence(run_id, perspective_key, source_table, as_of, freshness_status, payload_json)`.

Freshness is shown per perspective as `fresh`, `stale`, or `unavailable`; synthesis confidence is lowered when key perspectives are stale or unavailable.

## Non-Goals

- Do not mutate `ifa/families/stock/params/stock_edge_v2.2.yaml`.
- Do not auto-promote, apply-to-baseline or change report/delivery crons from diagnostic evidence.
- Do not use `sector_cycle_leader` proxy-only results as production YAML evidence.
- Do not call LLM per stock-date inside backtests; theme/news heat must be cached in tables first.
