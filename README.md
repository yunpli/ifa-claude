# ifa-claude — iFA China Market Report System

**Version 2.2.0** · AI-native, structured, source-anchored intelligence reports for China A-share investors. Customer-facing reports are 中文; engineering documentation is bilingual.

---

## What's New in V2.2

V2.2 ships **three new families** plus a **complete report UI overhaul** and **production-grade data correctness** across the existing 1+3 reports.

### Three new families
- **Research** — single-stock financial-statement reports. 28 factors × 5 dimensions, SW L2 peer percentile, four lenses (quarterly/annual × quick/deep), durable Postgres fundamental memory, analyst-PDF extraction cache. See [`docs/research-deep-dive.md`](docs/research-deep-dive.md).
- **TA** — 晚盘技术面 evening report. 9-regime classifier, 19 candidate setups across 7 families, T+N outcome tracking, regime gating + decay-based suspension, 11 deterministic + 3 LLM-augmented sections. See [`docs/ta-strategy-deep-dive.md`](docs/ta-strategy-deep-dive.md).
- **Stock Edge** — single-stock 5d/10d/20d quantitative trade plan. 85-strategy 决策矩阵 + per-horizon decision layer + auto-promotion gates. Tuning playbook still maturing (current alpha 不稳，see "Roadmap to V2.2.1" below). See [`docs/stock-edge-deep-dive.md`](docs/stock-edge-deep-dive.md).

### Complete UI overhaul (1 main + 3 aux)
- **§01 headline cards** standardised across all 9 family×slot combinations: `headline ≤28字 + top3[3] ≤22字 + summary ≤80字`. The "三件事" promise on every card front is now actually three forward-looking actions, not a recap of §02.
- **Premium card components** replace wall-of-text everywhere: `_review_hooks` (numbered question cards), `_scenario_plans` (3-col color-coded), `_chain_review` (商品端/A股端 row cards), `_chain_transmission` (上游 ▶ 中游 ▶ 下游 chevron pipeline), `_layer_map` (5-layer cake with plain-language intro), `_risk_list` (severity-colored card grid), `_hypotheses_list` (numbered cards with confidence pills), `_mapping_table` (sector pills with up/down tone).
- **Schema-validation retry** in LLM layer — 3 attempts with 2s/4s/8s exponential backoff if `top3` missing; only fallback after retries exhausted.
- **System-wide infrastructure** — 7 robust Jinja format filters (no raw float reaches templates), 37-term plain-language glossary tooltip system, mobile-responsive (cards collapse <768px), print-CSS optimised, A股 红涨/绿跌 enforced, banner staleness warning, sticky TOC pills + 回到顶部 floating button.
- See [`docs/ui-overhaul-v2.2.md`](docs/ui-overhaul-v2.2.md) for the full design reference.

### Data correctness (production-blocking bugs fixed)
- **Slot cutoff** — noon=11:30, evening=15:00 honored in BOTH live runs and historical replay. Production today uses `rt_min_daily` cut at slot; historical noon uses `stk_mins` 09:30→11:30 last bar.
- **Realtime breadth** — `rt_k` whole-A snapshot + `stk_limit` join computes today's breadth locally when EOD `daily` isn't published yet; fail-closed on partial wildcard failure.
- **SW realtime aggregation** — new `market/_sw_realtime.py` synthesizes SW L1/L2 sector pct from member stocks (TuShare's `rt_min_daily` rejects SW codes); MV-weighted by T-1 `daily_basic.total_mv`.
- **Trade-day-aware everywhere** — every "previous day" computation now goes through `ifa.core.calendar.prev_trading_day` (smartmoney.trade_cal-backed). 8 sites previously used calendar-day stepping which broke on Mondays + post-holiday opens.
- **Pre-flight DB freshness check** per slot — only validates tables that slot actually loads (noon doesn't read raw_moneyflow → not flagged).
- **Banner staleness warning** when any `trade_date < report_date` — red-bordered alert tells the reader why "—" appears.
- See [`docs/v2.2-release-notes.md`](docs/v2.2-release-notes.md) for the bug list (#1—#19, all closed) and migration guide.

### Roadmap to V2.2.1
- **Stock Edge tuning** — T3.2 ML 跨日期复用 + T3.3 扩 panel 100×24 终验。Currently 5d/10d val rank IC unstable across folds (2/4 positive); 20d at +0.034 K-fold median (target ≥+0.05). Production YAML works but is suboptimal. See [`docs/tuning-playbook.md`](docs/tuning-playbook.md) for the cross-family tuning surface.

---

## What's New in V2.1

- **SW (申万) unification** — All sector-aware logic (Market 主线, Tech 五层, Asset 商品传导, SmartMoney 板块流) now reads from a single 申万 source. DC (东财概念) and THS (同花顺) are no longer on the primary path. Rationale: SW is the only source with full PIT history (in_date / out_date since 1993).
- **千元 / 万元 unit fix** — `raw_daily.amount` was historically read as 万元 in some aggregations and 千元 in others, producing 10× inflated 净流入 numbers. All sector-aggregation SQL has been audited and normalised to 万元 at the source, then scaled to 亿 at the render layer.
- **PDF export** — Every report can now be printed to PDF. Use the `--generate-pdf` flag on any `ifa generate ...` command, or run `scripts/html_to_pdf.py` standalone (with `--all-today` batch mode).
- **Version bumped to 2.1.0** in both `pyproject.toml` and `ifa/__init__.py`.

### V2.1.1 patch

- **SW L2 daily price ETL** — `raw_sw_daily` now covers all ~131 SW L2 indices (in addition to 31 L1). `market.fetch_main_lines` queries L2 OHLC directly, with member-stock aggregation as fallback. Backfill: `scripts/backfill_sw_l2_daily.py` (~3 min for 2021-today; supports `--recent-days N` for incremental top-up). Bumped to `2.1.1`.

### V2.1.2 patch

- **SmartMoney factors now use L2 pct_change** — Previously every L2 sector inherited its parent L1's pct_change as a proxy (e.g. 半导体 / 元件 / 消费电子 all read 电子's daily change). Now each L2 reads its own value from `raw_sw_daily` (V2.1.1 backfill) with L1 fallback for ~6 deprecated L2 codes. Restores L2-internal divergence to factor signal — sample 2026-04-30: 电子 L1 had spread of **5.73 percentage points** across its 6 L2 children that the old model couldn't see.
- **Action required**: re-compute `factor_daily` / `sector_state_daily` and retrain RF + XGB before generating fresh SmartMoney reports. See `scripts/recompute_smartmoney_required.sh` (renamed from `_optional`). Bumped to `2.1.2`.

---

## Architecture at a Glance

```
            ┌──────────────────┐
            │   TuShare Pro    │
            └────────┬─────────┘
                     │
                     ▼
            ┌──────────────────┐
            │   ETL (raw_*)    │   smartmoney.raw_daily, raw_moneyflow,
            │                  │   raw_sw_daily, raw_sw_member,
            └────────┬─────────┘   sw_member_monthly, raw_index_daily, ...
                     │
                     ▼
            ┌──────────────────┐
            │  PostgreSQL 16   │   ifavr (prod) | ifavr_test (test)
            │   port 55432     │   public.* (reporting) + smartmoney.* (flow)
            └────────┬─────────┘
                     │
       ┌─────────────┼─────────────┬─────────────────────┐
       ▼             ▼             ▼                     ▼
  ┌─────────┐  ┌──────────┐  ┌──────────┐         ┌──────────────┐
  │ Market  │  │  Macro   │  │  Asset   │  ...    │  SmartMoney  │
  │ (main)  │  │  (aux)   │  │  (aux)   │         │  (separate)  │
  └────┬────┘  └────┬─────┘  └────┬─────┘         └──────┬───────┘
       │            │             │                       │
       │       ┌────┴─────────────┴────┐                  │
       │       │  Tech (aux)           │                  │
       │       └────────────┬──────────┘                  │
       │                    │                             │
       └────────────┬───────┴─────────────────────────────┘
                    ▼
            ┌──────────────────┐
            │  HTML + PDF Out  │   ~/claude/ifaenv/out/{test,manual,production}/
            └──────────────────┘
```

Composition: **1 main + 3 auxiliary + smartmoney (separate)**.
The main `market` family is 总指挥型 — it summarises the day. The three auxiliaries (`macro`, `asset`, `tech`) feed it. `smartmoney` runs its own ETL → factor → backtest → train → report pipeline and is consumed by `market` as one of the three 辅助 inputs at evening.

---

## Report Families

| Family | Command | Slots | Sections | SW-based? | Focus |
|---|---|---|---|---|---|
| **Market** (main) | `ifa generate market` | morning / noon / evening | 14 | Yes (SW L1 + dynamic SW L2 main lines) | A 股总指挥 — index/breadth/sentiment/龙虎/三辅验证 |
| **Macro** (aux) | `ifa generate macro` | morning / evening | 11–12 | No (no sector axis) | Policy / liquidity / FX / cross-asset |
| **Asset** (aux) | `ifa generate asset` | morning / evening | 10 | Yes (18 SW commodity-relevant L2) | Commodity futures → A 股板块传导 |
| **Tech** (aux) | `ifa generate tech` | morning / evening | 12 | Yes (5-layer SW L2 mapping) | AI 五层蛋糕 — 算力/模型/应用/终端/生态 |
| **SmartMoney** (separate) | `ifa smartmoney evening` | evening | 14 | Yes (SW L2 sector flow) | Institutional 板块资金流, ML 信号, 假设回顾 |
| **Ningbo** (separate) | `ifa ningbo evening` | evening | 5 | Yes (SW L2 member lookup) | 短线策略三轨 — 启发式 / ML 激进 / ML 稳健，★1-★5 共识矩阵，15 日追踪 |
| **Research** (V2.2, separate) | `ifa research report` | quarterly/annual × quick/standard/deep | 18 | Yes (SW L2 peer rank) | 个股财报分析 — 四类报告 / 5 维度 / 研报 PDF 摘要 / Postgres 基本面记忆 / 报告资产复用 |
| **TA** (V2.2, separate) | `ifa ta evening` | evening | 11 + 3 LLM | Yes (SW L1/L2 sector) | 晚盘技术面 — 9 体制 + 19 setup + T+N 追踪 + 衰减门控 |
| **Stock Edge** (V2.2, separate) | `ifa stock edge` | on-demand | – | Yes (SW peer scan) | 个股 5d/10d/20d 量化交易计划 — 85 策略矩阵 + 决策层 + 自动晋升门 (V2.2.1 调参中) |

Most families share the core reporting tables (`report_runs`, `report_sections`, `report_judgments`, `model_outputs`) and the same `ReportRun` lifecycle. Research additionally owns `research.report_runs` / `research.report_sections` as a single-stock report asset registry plus `research.period_factor_decomposition` and `research.pdf_extract_cache` for reusable fundamental memory.

---

## Quick Start

### Prerequisites

- macOS or Linux
- Python 3.12 with `uv` package manager
- PostgreSQL 16 (binary or Homebrew install)
- TuShare Pro token (register at tushare.pro)
- Google Chrome (for `--generate-pdf`)
- LLM relay endpoint (OpenAI-compatible)

### Install

```bash
cd /Users/neoclaw/claude/ifa-claude
uv venv --python 3.12
uv sync
```

### Configure secrets

Create `~/claude/ifaenv/secrets/.env` (chmod 600) with:

```
IFA_TUSHARE_TOKEN=...
IFA_LLM_BASE_URL=...
IFA_LLM_API_KEY=...
IFA_LLM_PRIMARY_MODEL=gpt-5.4
IFA_LLM_FALLBACK_MODEL=gpt-5.5
IFA_DB_PASSWORD=...
```

### Database setup

```bash
./scripts/postgres-bootstrap.sh    # one-time: install PG16, initdb cluster on 127.0.0.1:55432
./scripts/postgres-start.sh
uv run alembic upgrade head
ifa healthcheck                     # ping LLM, TuShare, DB
```

### First report

```bash
ifa generate market --slot evening --report-date 2026-04-30 --user default --generate-pdf
```

Output:

```
~/claude/ifaenv/out/manual/20260430/<run-id>/
    CN_Market_Evening_20260430_<...>.html
    CN_Market_Evening_20260430_<...>.pdf
```

---

## CLI Reference

### Generate (1 main + 3 aux)

| Command | Purpose |
|---|---|
| `ifa generate market --slot {morning,noon,evening} --report-date YYYY-MM-DD --user default [--generate-pdf]` | A-share main report |
| `ifa generate macro --slot {morning,evening} --report-date YYYY-MM-DD [--generate-pdf]` | Macro overlay |
| `ifa generate asset --slot {morning,evening} --report-date YYYY-MM-DD [--generate-pdf]` | Cross-asset transmission |
| `ifa generate tech --slot {morning,evening} --report-date YYYY-MM-DD --user default [--generate-pdf]` | AI 五层蛋糕 |

All accept `--mode {test,manual,production}`, `--cutoff-time HH:MM`, `--triggered-by NAME`.

### SmartMoney (separate pipeline)

| Command | Purpose |
|---|---|
| `ifa smartmoney etl --report-date YYYY-MM-DD` | Daily raw ETL |
| `ifa smartmoney backfill --start YYYYMMDD --end YYYYMMDD` | Historical raw backfill |
| `ifa smartmoney compute --report-date YYYY-MM-DD` | Factor / role / cycle / leader / candidate compute |
| `ifa smartmoney evening --report-date YYYY-MM-DD [--generate-pdf]` | Render the 14-section evening report |
| `ifa smartmoney backtest --start ... --end ... [--no-ml]` | Run a backtest, persist `backtest_runs` + `backtest_metrics` |
| `ifa smartmoney bt list` / `bt show <run-id>` | Inspect past backtest runs |
| `ifa smartmoney params freeze --name vYYYY_MM --from-backtest <run-id>` | Freeze a param version |
| `ifa smartmoney params list` / `params archive vYYYY_MM` | Manage param versions |

### Pre-jobs (for Macro)

```bash
ifa job text-capture --lookback-days 90 --mode test
ifa job policy-memory --lookback-days 14 --mode test
```

### Health & utilities

```bash
ifa healthcheck                                          # LLM + TuShare + DB
uv run python scripts/html_to_pdf.py FILE [FILE ...]     # Standalone HTML → PDF
uv run python scripts/html_to_pdf.py --all-today         # Batch all of today's reports
```

---

## Output Paths

```
~/claude/ifaenv/
├── secrets/.env              # API keys (chmod 600), gitignored
├── pgdata/                   # PostgreSQL 16 data dir
├── models/smartmoney/        # pickled ML models + manifest.json
└── out/
    ├── test/<date>/<run-id>/         # ifavr_test DB
    ├── manual/<date>/<run-id>/       # ifavr DB, operator re-run
    └── production/<date>/<run-id>/   # ifavr DB, scheduled
```

Each run directory contains the rendered HTML and (optionally) the matching PDF.

---

## PDF Generation

Two equivalent paths:

**Inline** — append `--generate-pdf` to any `ifa generate ...` or `ifa smartmoney evening` command. The PDF is written next to the HTML.

**Standalone** — for backfilling PDFs from existing HTML:

```bash
uv run python scripts/html_to_pdf.py path/to/CN_Market_Evening_*.html
uv run python scripts/html_to_pdf.py -o /tmp/pdfs file1.html file2.html
uv run python scripts/html_to_pdf.py --all-today                    # everything generated today
uv run python scripts/html_to_pdf.py --all-today --out-root /custom/root
```

Implementation uses headless Chrome with print-CSS injection that opens all `<details>` blocks (so the Top-5 individual-stock drill-downs are visible in print). See [`docs/pdf-tool.md`](docs/pdf-tool.md).

---

## Documentation Index

### V2.2 release pack
- [`docs/v2.2-release-notes.md`](docs/v2.2-release-notes.md) — V2.2 deliverables, migration v2.1.3 → v2.2, breaking changes, ops checklist, gap analysis
- [`docs/ui-overhaul-v2.2.md`](docs/ui-overhaul-v2.2.md) — premium card design system reference (components, tokens, anti-patterns)
- [`docs/tuning-playbook.md`](docs/tuning-playbook.md) — cross-family tuning surface (TA / SmartMoney / Stock Edge), how to extend, V2.2.1 plan

### Family deep-dives
- [`docs/research-deep-dive.md`](docs/research-deep-dive.md) — Research family (V2.2 NEW)
- [`docs/ta-strategy-deep-dive.md`](docs/ta-strategy-deep-dive.md) — TA family (V2.2 NEW)
- [`docs/stock-edge-deep-dive.md`](docs/stock-edge-deep-dive.md) — Stock Edge family (V2.2 NEW)
- [`docs/ningbo-deep-dive.md`](docs/ningbo-deep-dive.md) — 宁波派完整架构：Phase 1-3.D，三轨，Champion-Challenger
- [`docs/smartmoney-deep-dive.md`](docs/smartmoney-deep-dive.md) — SmartMoney V2.1.2 修复背景 + 两层 recompute 体系
- [`docs/main-three-aux-deep-dive.md`](docs/main-three-aux-deep-dive.md) — 一主三辅协作时序与数据流

### Engineering reference
- [`docs/architecture.md`](docs/architecture.md) — the 1+3+smartmoney design, data flow, lifecycle, LLM usage
- [`docs/family-reference.md`](docs/family-reference.md) — per-family slots, sections, data sources, sample CLI
- [`docs/database-schema.md`](docs/database-schema.md) — full schema reference (`public.*` + `smartmoney.*` + `research.*` + `ta.*`)
- [`docs/data-accuracy-guidelines.md`](docs/data-accuracy-guidelines.md) — 15 rules every data fetcher must follow (PIT, staleness gate, slot cutoff, etc.)
- [`docs/tushare-units-reference.md`](docs/tushare-units-reference.md) — TuShare API units cheatsheet (千元 / 万元 / 元 / 万股 / 万亿）
- [`docs/sw-migration.md`](docs/sw-migration.md) — why we unified on 申万, phase plan, key tables, the 千元 bug

### Operations
- [`docs/OPERATIONS.md`](docs/OPERATIONS.md) — 运维手册：日/周/月/季节奏，所有 family 的操作检查清单
- [`docs/run-modes.md`](docs/run-modes.md) — test / manual / production semantics
- [`docs/pdf-tool.md`](docs/pdf-tool.md) — PDF export internals and troubleshooting
- [`docs/multi-agent-deployment.md`](docs/multi-agent-deployment.md) — 多 agent 平台部署指南（generic prompt + watcher 配置）

The root `CLAUDE.md` is the engineering checklist for the in-progress SmartMoney B/C work and is the source of truth for migration phase status.

---

## Project Status & Roadmap

| Track | Status |
|---|---|
| V2.1 release (SW unification, PDF, units fix) | Done |
| V2.1.1 patch (SW L2 daily price ETL)          | Done |
| V2.1.2 patch (SmartMoney L2 pct_change in factors; recompute+retrain required) | Done |
| V2.1.3 patch (Ningbo Phase 1-3.D 全闭环；Champion-Challenger；★ 共识矩阵；运维文档) | Done |
| **V2.2 release** — UI overhaul + Research + TA + Stock Edge + production data correctness | **Done** |
| V2.2.1 (planned) — Stock Edge tuning T3.2 + T3.3 → variant YAML auto-promote | In progress |
| V2.3 (planned) — Research HTTP API + Telegram + quota + dashboard | Backlog |

See [`docs/v2.2-release-notes.md`](docs/v2.2-release-notes.md) for the full V2.2 deliverables list and migration guide.
See [`docs/tuning-playbook.md`](docs/tuning-playbook.md) for the cross-family tuning surface and V2.2.1 plan.

---

## License & Disclaimer

This repository contains internal research tooling. All generated reports include the Lindenwood Management LLC disclaimer (English + 中文). Reports are **informational and research only — not investment advice**. No LLM prompt instructs the model to give buy/sell recommendations; all LLM outputs use 观察 / 假设 / 验证点 framing.
