# ifa-claude — iFA China Market Report System

**Version 2.1.2** · AI-native, structured, source-anchored daily intelligence reports for China A-share investors. Customer-facing reports are 中文; engineering documentation is bilingual.

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

All families share the same reporting tables (`report_runs`, `report_sections`, `report_judgments`, `model_outputs`) and the same `ReportRun` lifecycle.

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

- [`docs/architecture.md`](docs/architecture.md) — the 1+3+smartmoney design, data flow, lifecycle, LLM usage
- [`docs/family-reference.md`](docs/family-reference.md) — per-family slots, sections, data sources, sample CLI
- [`docs/sw-migration.md`](docs/sw-migration.md) — why we unified on 申万, phase plan, key tables, the 千元 bug
- [`docs/pdf-tool.md`](docs/pdf-tool.md) — PDF export internals and troubleshooting
- [`docs/database-schema.md`](docs/database-schema.md) — full schema reference (`public.*` + `smartmoney.*`)
- [`docs/run-modes.md`](docs/run-modes.md) — test / manual / production semantics
- `docs/audit-pre-b8.md` — historical audit record (kept for provenance, do not edit)

The root `CLAUDE.md` is the engineering checklist for the in-progress SmartMoney B/C work and is the source of truth for migration phase status.

---

## Project Status & Roadmap

| Track | Status |
|---|---|
| V2.1 release (SW unification, PDF, units fix) | Done |
| V2.1.1 patch (SW L2 daily price ETL)          | Done |
| V2.1.2 patch (SmartMoney L2 pct_change in factors; recompute+retrain required) | Done |
| SmartMoney A 阶段 (raw backfill 2021-01 → 2026) | Done |
| SmartMoney B 阶段 (factor refactor onto SW) | In progress |
| SmartMoney C 阶段 (compute / train / OOS validation) | Pending B |
| V2.2 (planned) | Transition matrix LLM nudge; persistent param store v2026_05; drop legacy DC fallback in `factors/flow.py` |

See `CLAUDE.md` for the live B1–B9 / C1–C6 task list.

---

## License & Disclaimer

This repository contains internal research tooling. All generated reports include the Lindenwood Management LLC disclaimer (English + 中文). Reports are **informational and research only — not investment advice**. No LLM prompt instructs the model to give buy/sell recommendations; all LLM outputs use 观察 / 假设 / 验证点 framing.
