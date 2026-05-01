# ifa-claude — iFA China Market Report System

AI-native, structured, auditable daily market intelligence for China A-share investors.
Customer-facing reports are **Chinese**; this engineering spec is **English**.

---

## What this repo is

A report-generation system that turns market data, news, policy, macro variables,
commodity prices, and sector money-flow behavior into **structured, source-anchored,
reviewable investment judgments** — rendered as professional-grade HTML reports.

It is **not** a chatbot. It is **not** a market data terminal. Every report is a
deterministic *report run* with persisted facts, signals, judgments, prompts,
LLM I/O, and source references. Every LLM judgment generates tomorrow's *hypotheses*,
which are automatically reviewed the next morning against actual market outcomes.

---

## Report families

| Family | CLI | Slots | Sections | Focus |
|---|---|---|---|---|
| **SmartMoney** | `ifa smartmoney` | evening | 14 | Institutional money flow, sector roles, cycle phases, ML factor backtest |
| **Market** | `ifa generate market` | morning / noon / evening | 14 | A-share main report — 总指挥型, index/breadth/sentiment/龙虎 |
| **Tech** | `ifa generate tech` | morning / evening | 12 | AI Five-Layer Cake — 算力/模型/应用/终端/生态链 |
| **Asset** | `ifa generate asset` | morning / evening | 10 | Cross-asset transmission — 商品/期货/A股板块传导 |
| **Macro** | `ifa generate macro` | morning / evening | 11–12 | Macro overlay — policy, liquidity, cross-asset, FX |

The reporting database (`ifavr`) is shared across all families:
`report_runs`, `report_sections`, `report_judgments`, `model_outputs` serve every report.

---

## SmartMoney — Flow Intelligence

The most complete module. Tracks **institutional money flow** through A-share sectors
(申万 SW / 东财 DC / 同花顺 THS / 开盘啦 KPL) and synthesises it into a daily
evening report with LLM analysis and verifiable next-day hypotheses.

### Pipeline

```
TuShare API
    │
    ▼
ETL (20 raw_* tables / day)
    │
    ▼
Factor Engine                        ML Layer
  liquidity.py → market_state_daily    features.py      31-feature matrix
  flow.py      → factor_daily          logistic.py      SmartMoneyLogistic
  role.py      → sector role           random_forest.py SmartMoneyRandomForest
  cycle.py     → cycle phase           xgboost_model.py SmartMoneyXGBoost
  leader.py    → stock_signals_daily   news_catalyst.py sector catalyst scoring
  candidate.py → 补涨候选              persistence.py   model versioning
    │
    ▼
Backtest Engine
  metrics.py  IC / RankIC / TopN / Q1–Q5 group returns (pure math)
  engine.py   backtest loop + walk-forward ML AUC evaluation
  runner.py   DB persistence → backtest_runs + backtest_metrics
    │
    ▼
Param Store
  default.yaml  → baseline params
  param_versions table  → freeze best backtest result as active version
    │
    ▼
Evening Report (14 sections, Jinja2 HTML)
  E1  sm_market_pulse      市场水位 / 涨停跌停 / 炸板率
  E2  sm_sector_flow (in)  净流入 Top — 板块角色 + LLM 评语
  E3  sm_sector_flow (out) 净流出 Top — 退潮信号
  E4  sm_quality_flow      优质流入 vs 拥挤板块
  E5  crowding risk        拥挤风险提示
  E6  sm_cycle_grid        情绪周期格（点火/确认/扩散/高潮/分歧/退潮）
  E7  market state         市场水位卡
  E8  candidate_pool       补涨候选池
  E9  review_table         昨日假设 Review（自动对账）
  E10 sm_tomorrow_targets  明日观察清单 + 验证点
  E11 sm_sector_structure  龙头 / 中军 / 情绪先锋结构
  E12 hypotheses_list      今日判断资产（沉淀到 report_judgments）
  E13 sm_strategy_view     策略视角（主线延续/分歧修复/高低切/防守切换）
  E14 disclaimer
```

### Factor scores

| Score | What it measures |
|---|---|
| `heat_score` | Relative money-flow strength vs cross-section |
| `trend_score` | Directional momentum (buy/sell pressure) |
| `persistence_score` | Consistency of flow over recent days |
| `crowding_score` | Money piled in but price not moving — crowding risk |

### Sector roles & cycle phases

**Roles:** 主线 / 中军 / 轮动 / 防守 / 催化 / 退潮 / 未识别

**Cycle phases:** 冷 / 点火 / 确认 / 扩散 / 高潮 / 分歧 / 退潮 / 未识别

### Backtest metrics (per factor × forward window)

- **IC / IC-IR** — Pearson information coefficient + information ratio
- **RankIC / RankIC-IR** — Spearman rank IC
- **TopN hit rate** — % of top-5 factor-ranked sectors up next day
- **Q1–Q5 group returns** — equal-count quintile mean forward return

Walk-forward ML: rolling 60d train → 20d step, AUC for 3 model types.

### SmartMoney CLI

```bash
# ETL
ifa smartmoney etl      --report-date 2026-04-30
ifa smartmoney backfill --start 20251101 --end 20260430

# Factor compute (run after ETL)
ifa smartmoney compute  --report-date 2026-04-30
ifa smartmoney compute  --start 2025-11-01 --end 2026-04-30

# Report
ifa smartmoney evening  --report-date 2026-04-30

# Backtest
ifa smartmoney backtest --start 2025-11-01 --end 2026-04-30 --windows 1,5
ifa smartmoney bt list
ifa smartmoney bt show <run-id>

# Param versioning
ifa smartmoney params list
ifa smartmoney params freeze --name v2026_05 --from-backtest <run-id>
ifa smartmoney params archive v2026_04
```

---

## Market — A-share Main (总指挥型)

The centrepiece daily report. Three slots: morning briefing, intraday noon update, evening recap.

**Evening sections (14):**
S1 commentary · S2 index_panel · S3 category_strength · S4 sentiment_grid ·
S5 dragon_tiger (龙虎榜) · S6 three_aux_summary (SmartMoney/Tech/Asset 三辅验证) ·
S7 review_table (morning hypotheses) · S8 review_table (noon hypotheses) ·
S9 focus_deep (10 stocks) · S10 focus_brief (20 stocks) ·
S11 attribution · S12 hypotheses_list · S13 watchlist · S14 disclaimer

```bash
ifa generate market --slot morning  --report-date 2026-04-30 --user default
ifa generate market --slot noon     --report-date 2026-04-30
ifa generate market --slot evening  --report-date 2026-04-30
```

---

## Tech — AI Five-Layer Cake

Tracks the tech/AI ecosystem across five structural layers:
**算力层 → 模型层 → 应用层 → 终端层 → 生态链**

**Evening sections (12):**
S1 commentary · S2 layer_map (五层复盘) · S3 category_strength ·
S4 review_table · S5 leader_table (科技龙头) · S6 candidate_pool ·
S7 focus_deep · S8 focus_brief · S9 news_list · S10 watchlist ·
S11 hypotheses_list · S12 disclaimer

```bash
ifa generate tech --slot morning --report-date 2026-04-30 --user default
ifa generate tech --slot evening --report-date 2026-04-30 --user default
```

---

## Asset — Cross-Asset Transmission

Tracks commodity/futures markets and their transmission effects into A-share sectors:
**原油 / 贵金属 / 有色 / 黑色 / 化工 / 农产品** → 申万行业当日表现

**Evening sections (10):**
S1 commentary · S2 commodity_dashboard · S3 category_strength ·
S4 review_table · S5 transmission_review (Asset→A股传导复盘) ·
S6 chain_review (分链复盘) · S7 news_list · S8 watchlist ·
S9 hypotheses_list · S10 disclaimer

```bash
ifa generate asset --slot morning --report-date 2026-04-30
ifa generate asset --slot evening --report-date 2026-04-30
```

---

## Macro — Policy & Liquidity Overlay

Macro overlay report covering policy events, liquidity, FX, and cross-asset context.
Feeds indicator data from TuShare structured endpoints + LLM-extracted text signals.

**Evening sections (11):**
S1 commentary · S2 review_table · S3 news_list · S4 data_panel ·
S5 liquidity_grid · S6 cross_asset_grid · S7 attribution · S8 watchlist ·
S9 hypotheses_list · S10 indicator_capture_table · S11 disclaimer

**Pre-jobs** (feed the Macro report with low-frequency text signals):

```bash
# Extract structured macro indicators from news (incremental, watermark-based)
ifa job text-capture --lookback-days 90 --mode test

# Curate active policy events into memory table
ifa job policy-memory --lookback-days 14 --mode test
```

```bash
ifa generate macro --slot morning --report-date 2026-04-30
ifa generate macro --slot evening --report-date 2026-04-30
```

---

## Repo layout

```
ifa-claude/
├── README.md
├── pyproject.toml                       # uv-managed, Python 3.12
├── alembic/
│   └── versions/
│       ├── *_core_schema.py             # report_runs/sections/judgments/model_outputs
│       └── *_smartmoney_schema.py       # smartmoney.raw_* + business tables
├── ifa/
│   ├── config.py                        # Pydantic Settings → ifaenv/secrets/.env
│   ├── core/
│   │   ├── llm/                         # OpenAI-compatible relay, primary→fallback
│   │   ├── tushare/                     # TuShare token-aware wrapper
│   │   ├── db/                          # SQLAlchemy engine (port 55432)
│   │   ├── render/
│   │   │   └── templates/
│   │   │       ├── report.html          # section dispatcher
│   │   │       ├── _tone_card.html      # shared section partials (20+)
│   │   │       ├── _sm_market_pulse.html
│   │   │       ├── _sm_sector_flow.html
│   │   │       ├── _sm_quality_flow.html
│   │   │       ├── _sm_cycle_grid.html
│   │   │       ├── _sm_tomorrow_targets.html
│   │   │       ├── _sm_sector_structure.html
│   │   │       └── _sm_strategy_view.html
│   │   └── report/                      # ReportRun lifecycle, insert_section, finalize
│   ├── families/
│   │   ├── _shared/
│   │   │   └── news.py                  # shared news loader
│   │   ├── smartmoney/
│   │   │   ├── etl/
│   │   │   │   ├── raw_fetchers.py      # 20 TuShare fetchers (one per raw_* table)
│   │   │   │   └── runner.py            # run_etl_for_date, run_backfill
│   │   │   ├── factors/
│   │   │   │   ├── flow.py              # heat/trend/persistence/crowding → factor_daily
│   │   │   │   ├── liquidity.py         # market water-level → market_state_daily
│   │   │   │   ├── role.py              # sector role classification (7 roles)
│   │   │   │   ├── cycle.py             # cycle phase state machine + write_sector_states
│   │   │   │   ├── leader.py            # 龙头股 scoring → stock_signals_daily
│   │   │   │   └── candidate.py         # 补涨候选 scoring
│   │   │   ├── ml/
│   │   │   │   ├── features.py          # 31-feature matrix (F1 raw → F7 DC extras)
│   │   │   │   ├── dataset.py           # MLDataset, time-based split, label schemes
│   │   │   │   ├── logistic.py          # SmartMoneyLogistic (StandardScaler+balanced)
│   │   │   │   ├── random_forest.py     # SmartMoneyRandomForest (n_est=100)
│   │   │   │   ├── xgboost_model.py     # SmartMoneyXGBoost (hist, nthread=2)
│   │   │   │   ├── news_catalyst.py     # LLM sector catalyst scoring
│   │   │   │   └── persistence.py       # save/load/list models + manifest.json
│   │   │   ├── backtest/
│   │   │   │   ├── metrics.py           # IC/RankIC/TopN/group returns (pure math)
│   │   │   │   ├── engine.py            # backtest loop, walk-forward ML eval
│   │   │   │   └── runner.py            # DB persistence, list/show helpers
│   │   │   ├── params/
│   │   │   │   ├── default.yaml         # baseline param set
│   │   │   │   └── store.py             # get_active_params, freeze_params
│   │   │   ├── data.py                  # pure DB loaders for report sections
│   │   │   ├── prompts.py               # 7 LLM prompt bundles + SYSTEM_PERSONA
│   │   │   ├── evening.py               # 14-section orchestrator
│   │   │   └── universe.py
│   │   ├── market/
│   │   │   ├── morning.py / noon.py / evening.py
│   │   │   ├── data.py / prompts.py / universe.py / _common.py
│   │   ├── tech/
│   │   │   ├── morning.py / evening.py / focus.py
│   │   │   ├── data.py / prompts.py / universe.py
│   │   ├── asset/
│   │   │   ├── morning.py / evening.py
│   │   │   ├── data.py / prompts.py / universe.py
│   │   └── macro/
│   │       ├── morning.py / evening.py
│   │       └── data.py / prompts.py
│   └── cli/
│       ├── __main__.py                  # root: job / generate / smartmoney
│       ├── smartmoney.py                # etl/compute/backfill/backtest/evening/params/bt
│       ├── generate.py                  # macro/tech/market/asset
│       ├── jobs.py                      # text-capture / policy-memory
│       └── healthcheck.py
└── scripts/
    ├── postgres-bootstrap.sh
    ├── postgres-start.sh
    └── postgres-stop.sh
```

External (gitignored):

```
/Users/neoclaw/claude/ifaenv/
├── secrets/.env                         # all API keys & DB password (chmod 600)
├── pgdata/                              # PostgreSQL 16 data dir, port 55432
├── models/smartmoney/                   # pickled ML models + manifest.json
└── out/{test,manual,production}/        # rendered HTML reports by date/run-id
```

---

## Run modes

| Mode | DB | Trigger | Output path |
|---|---|---|---|
| `test` | `ifavr_test` | developer / CI | `ifaenv/out/test/<date>/<run-id>/` |
| `manual` | `ifavr` | operator re-run | `ifaenv/out/manual/<date>/<run-id>/` |
| `production` | `ifavr` | cron scheduled | `ifaenv/out/production/<date>/<run-id>/` |

Set via `--mode` flag or `IFA_RUN_MODE` env var.
`report_runs.run_mode` is persisted so test runs can be excluded from any review query.

---

## Setup

### 1. Python environment
```bash
cd /Users/neoclaw/claude/ifa-claude
uv venv --python 3.12
uv sync
```

### 2. PostgreSQL (one-time)
```bash
./scripts/postgres-bootstrap.sh    # brew install postgresql@16, initdb
./scripts/postgres-start.sh        # starts cluster on 127.0.0.1:55432
```

### 3. Run migrations
```bash
alembic upgrade head
```

### 4. Verify
```bash
ifa healthcheck                    # pings LLM, TuShare, DB
```

### 5. SmartMoney full pipeline (initial backfill)
```bash
# Raw data backfill (~90 min for 120 trading days)
ifa smartmoney backfill --start 20251101 --end 20260430

# Factor + role + cycle + leader + candidate compute
ifa smartmoney compute --start 2025-11-01 --end 2026-04-30

# Backtest and freeze params
ifa smartmoney backtest --start 2025-11-01 --end 2026-04-30 --no-ml
ifa smartmoney params freeze --name v2026_05 --from-backtest <run-id>

# Generate latest evening report
ifa smartmoney evening --report-date 2026-04-30
```

### 6. Other family reports
```bash
ifa generate market --slot evening --report-date 2026-04-30 --user default
ifa generate tech   --slot evening --report-date 2026-04-30 --user default
ifa generate asset  --slot evening --report-date 2026-04-30
ifa generate macro  --slot evening --report-date 2026-04-30
```

---

## Tech stack

| Layer | Choice |
|---|---|
| Python | 3.12, uv |
| Database | PostgreSQL 16, port 55432, SQLAlchemy 2.0 (raw SQL via `text()`, no ORM) |
| LLM | OpenAI-compatible relay — primary `gpt-5.4`, fallback `gpt-5.5` |
| Market data | TuShare Pro API |
| Templates | Jinja2, inline CSS, self-contained HTML (no external CDN) |
| ML | scikit-learn 1.8, XGBoost 3.2 (M1-safe: `tree_method='hist'`, `nthread=2`) |
| Model storage | Pickle + atomic `manifest.json` → `ifaenv/models/smartmoney/` |

---

## Secrets

All API keys and DB credentials live in `ifaenv/secrets/.env` (chmod 600),
never in this repo. `.env.example` documents variable names only.

---

## Compliance

All reports include the Lindenwood Management LLC disclaimer (English + Chinese).
Reports are **informational and research only — not investment advice**.
No LLM prompt instructs the model to give buy/sell recommendations.
All LLM outputs use 观察 / 假设 / 验证点 framing only.
