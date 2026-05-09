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
- Every written diagnostic also gets a lightweight manifest JSON containing stock code/name, requested/generated timestamps, perspective status/freshness, latency/source tables, conclusion, confidence, and output paths.
- Telegram delivery is contract/dry-run only in this phase. `scripts/stock_edge_diagnostic_delivery.py --manifest <manifest.json>` consumes the diagnostic manifest and writes `artifact_type=stock_edge_diagnostic_telegram_delivery_payload` with title, 3-5 line short text, attachment paths, recipient placeholder, latency summary, and failure context. It never sends externally; later direct-send integration should consume this payload and keep iFA's direct-send preference.
- `--persist-db/--no-persist-db` controls best-effort persistence of the same run/evidence contract to `stock.diagnostic_runs` and `stock.diagnostic_perspective_evidence`; default is best-effort persistence, and schema/DB failures fall back to artifact-only output. This is audit-only and must not feed production YAML promotion or crons.
- `tests/stock/test_diagnostic.py` verifies conflict-preserving synthesis and unavailable-perspective rendering.

The MVP deliberately uses a light snapshot.  It does not run the expensive full Stock Edge strategy matrix unless `--full-stock-edge` is passed, and it skips optional intraday/model-context loaders in the default path.

## Persistence Contract

P0 writes structured JSON artifacts and persists DB audit rows once migrations are applied:

- `stock.diagnostic_runs(run_id, ts_code, name, requested_at, generated_at, as_of_trade_date, run_mode, status, conclusion, confidence, logic_version, output_paths_json, perspective_status_json, evidence_freshness_json, synthesis_json, manifest_json)`.
- `stock.diagnostic_perspective_evidence(run_id, perspective_key, title, status, view, freshness_status, latency_ms, source_tables_json, missing_evidence_json, missing_required_json, source_as_of, summary, evidence_json, raw_json)`.
- `stock.sector_cycle_leader_daily(trade_date, ts_code, l2_code, rank_in_sector, sector_rank_count, leader_score, sector_score, stock_score, quality_flag, logic_version, evidence_json)` is the new P1 PIT rank/score surface for sector-first leader evidence. The diagnostic reads it when populated.
- `stock.theme_heat_weekly` is the weekly theme heat cache. Non-stub rows can come from approved JSON ingestion (`--from-json`) or the local-source builder when existing `research.company_event_memory` / `ta.catalyst_event_memory` rows are sufficient. The builder does not call external LLM/news APIs and returns a source-policy blocker when local evidence is too thin.

Theme heat examples:

```bash
uv run python scripts/stock_edge_theme_heat_builder.py --week 2026-05-04 --build-local --dry-run --json
uv run python scripts/stock_edge_theme_heat_builder.py --week 2026-05-04 --from-json /path/to/approved_theme_cache.json --dry-run --json
```

Freshness is shown per perspective as `fresh`, `stale`, or `unavailable`; synthesis confidence is lowered when key perspectives are stale or unavailable.

## Non-Goals

- Do not mutate `ifa/families/stock/params/stock_edge_v2.2.yaml`.
- Do not auto-promote, apply-to-baseline or change report/delivery crons from diagnostic evidence.
- Do not use `sector_cycle_leader` proxy-only results as production YAML evidence.
- Do not call LLM per stock-date inside backtests; theme/news heat must be cached in tables first.
