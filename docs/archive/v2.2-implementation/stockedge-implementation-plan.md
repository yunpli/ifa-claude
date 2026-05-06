# Stock Edge Implementation Plan

> **Status**: Phases A-E functional path implemented; continuing strategy breadth, calibration, and report polish  
> **Date**: 2026-05-05  
> **Product name**: Stock Edge（个股作战室）  
> **Code family**: reuse existing `ifa/families/stock/`; do not create a parallel `stockedge` package.  
> **DB schema**: reuse existing PostgreSQL `stock` schema. `Stock Edge` is the product name, not a new schema name.  
> **Design source**: [`stock-edge-deep-dive.md`](stock-edge-deep-dive.md)

---

## 0. Current Decision Log

This plan reflects the latest implementation constraints:

1. **Fast functional delivery first**
   - Default scoring lookback is **7 trading days**.
   - Target-stock intraday sweep defaults to **5min 30 days + 30min 60 days + 60min 90 days** because storage is tiny and it materially improves the prediction execution card.
   - The goal is to make the full product loop work quickly, not to optimize every parameter upfront.

2. **Two-layer parameter system**
   - Stock Edge should have a **global preset** trained on the full market or, more practically for V2.2, the top-liquidity ~500 A-share universe.
   - A scheduled weekend job refreshes this global preset so the first report is not forced to spend too long searching parameters.
   - When a user triggers one stock, the report runner checks whether that stock has a fresh per-stock tuning artifact. If missing or older than the TTL, it runs a **pre-report single-stock overlay tune** from that stock's own history, then generates the report.
   - Offline walk-forward/OOS is still required for governance and promotion, but it is **not** the report-time tuning mechanism.

3. **Local-first data policy**
   - Every data request checks local storage first.
   - If missing, check whether Tushare Pro has the endpoint and whether the local environment has token access.
   - If available and allowed, backfill into the right local store before computing.
   - No report should silently proceed with missing data unless it records a degraded state.

4. **Storage choice must be deliberate**
   - PostgreSQL is for authoritative, relational, audited, PIT-correct data and report memories.
   - DuckDB + Parquet is for large time-series scans, 5min bars, embeddings, analog search, and backtest snapshots.

5. **Prediction execution card is the product core**
   - Stock Edge is judged by prediction quality, not analysis volume.
   - The first actionable output must answer: can we buy today, at what price, under what future-5-day condition, where to sell, and when the thesis is invalid.
   - Strategy, TA, SmartMoney, Research, Kronos, ML, and LLM are evidence suppliers. They are not the product by themselves.

6. **Production-grade architecture**
   - First version can be simple, but the boundaries must be right.
   - Algorithms can begin with deterministic rules and calibrated heuristics, but they must produce executable forecasts: entry timing, entry price, stop, target, holding window, and probability state.

## 0.1 Implementation Snapshot

As of 2026-05-05, the fast functional Stock Edge path is implemented:

- `ifa stock report` / `ifa stock quick` / `ifa stock today` / `ifa stock data-check` are wired into the global CLI.
- `stock_edge_v2.2.yaml` loads with deterministic param hashing, 7-day scoring defaults, optional intraday sweep defaults, default-enabled pre-report overlay tuning, and versioned prediction-surface models.
- Daily technical context reads up to 360 local bars for S/R, trend background, and model evidence. The visual K-line chart still renders a compact recent window for readability.
- The as-of router implements the 15:00 Beijing cutoff: trading day before 15:00 uses T-1, at/after 15:00 uses T, non-trading day uses the latest completed trading day.
- The data snapshot checks local PostgreSQL first, optionally reads DuckDB 5min data, and uses Tushare target-stock backfill when mandatory local data is missing and backfill is allowed.
- Optional intraday data is demand-driven: if enabled strategies need 5min/30min/60min and local DuckDB has no target-stock bars, the system may backfill the configured target-stock sweep from TuShare into ifaenv, then reload local data. It should not crawl data that no active strategy consumes.
- Existing PostgreSQL `stock` schema is reused for `analysis_record`, `report_sections`, and `analysis_lock`; no new schema was created.
- The first rule-baseline trade plan produces action, confidence, entry zone, stop, targets, probability block, position sizing, T+0 plan with base-position gating, vetoes, and auditable evidence.
- The report model now builds a **买卖时机预测 / prediction execution card**: today's buy/no-buy decision, today's executable entry band, next-5-trading-day conditional entry scenarios, 20% / 30% / 50% sell targets, stop, vetoes, and 20-40 trading-day holding window.
- The strategy matrix is grouped into cluster plans; each cluster has its own continuous score plus entry range, target, stop, and 20-40 trading-day forecast surface.
- Single-stock replay now includes both similar-state right-tail replay and target/stop first-event replay, so the prediction execution card can show whether the target or stop historically fired first and roughly how many trading days it took.
- Existing SmartMoney RF/XGB sector models, Ningbo active aggressive/conservative models, and Ningbo Kronos cached embeddings are reused as real evidence sources when available; Stock Edge does not create placeholder model interfaces.
- Before building the final trade plan, Stock Edge ensures prerequisite Research deep reports exist for the target and visible same-SW-L2 leaders. It checks reusable annual deep and quarterly deep reports first; missing reports are generated through the project Research service and then the Stock Edge snapshot is reloaded. Target reports may use the project LLM with a bounded timeout; peer reports default to rules-only deep factor generation to avoid external narrative latency blocking the trade plan.
- `ifa stock intraday-sweep` can backfill target-stock 5min / 30min / 60min bars into `/Users/neoclaw/claude/ifaenv/duckdb/parquet/intraday_5min/`, with `--estimate-only` storage sizing. Default single-stock estimate for 5min 30d + 30min 60d + 60min 90d is ~2,280 rows, ~0.365 MB uncompressed, ~0.073 MB Parquet before small-file overhead.
- Default reports run through `prepare_report_params()`: if a compatible fresh overlay exists it is reused; otherwise the runner performs bounded single-stock overlay tuning before report generation. If history is too short, it attempts a local-first TuShare core backfill, reloads local bars, and then tunes.
- Standalone `ifa stock tune-overlay` and `scripts/stock_edge_pre_report_overlay.py` follow the same short-history backfill policy, so report generation, manual tuning, and production jobs do not diverge.
- The overlay objective is execution-first: a candidate signal must define a continuous entry zone, pass a future-5-trading-day fill check, and then evaluate 40-trading-day target/stop path from the filled entry date.
- Manual reports render Chinese HTML + Markdown under `/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/stock_edge/`, with disclaimer. Data freshness remains internal audit metadata and is not displayed as a user-facing section.
- Repeated same request reuses a prior report unless `--fresh` is passed.
- Optional `intraday_5min` absence remains visible in freshness but does not by itself mark the persisted report as `partial`.

Current validation:

```bash
uv run pytest tests/stock
# 58 passed

uv run python -m compileall -q ifa/families/stock ifa/cli/stock.py tests/stock
```

---

## 1. Scope For The First Working Version

### 1.1 What V2.2 Must Deliver

Given one A-share `ts_code` and an optional timestamp:

1. Resolve `as_of_trade_date` correctly:
   - trading day before 15:00: use T-1
   - trading day after 15:00: use T
   - non-trading day: use latest completed trading day
2. Build a single-stock data snapshot from local DB first.
3. Backfill missing local data from Tushare Pro when available.
4. Consume existing Research annual deep + quarterly deep output if available.
5. Consume existing TA candidates / setup / warnings / metrics if available.
6. Produce a Stock Edge trade plan:
   - today buy / watch / avoid decision
   - today's executable entry zone
   - next-5-trading-day conditional entry scenarios
   - stop / invalidation
   - sell targets at 20%, 30%, and right-tail 50% where applicable
   - 20-40 trading day thesis
   - T+0 plan only if user has a base position
   - data freshness and degraded-state notes
7. Render manual-mode HTML + MD report.
8. Persist report metadata and structured plan for future reuse.

### 1.2 What V2.2 Should Not Block On

These are important, but they should not block the first working version:

- full-market 2-year 5min backfill
- mandatory all-stock pretraining before any report can run
- new production ML model training inside Stock Edge
- A/B switching
- full new Kronos embedding index; V2.2 should reuse existing Ningbo Kronos cache first
- portfolio-level optimizer
- broker QR auto-fill integration
- user quota / HTTP / Telegram entrypoint
- long-window parameter tuning

### 1.3 Default Development Profile

```yaml
stock_edge:
  default_lookback_days: 7
  intraday:
    enabled: optional
    default_window_days: 30
    backfill_on_missing: true
    sweep:
      5min_days: 30
      30min_days: 60
      60min_days: 90
    full_market_backfill: false
  tuning:
    enabled: true
    mode: global_preset_plus_pre_report_overlay
    global_preset:
      universe: top_liquidity_500
      refresh_schedule: weekly_weekend
    pre_report_overlay:
      ttl_days: 10
      search_space: continuous
      backfill_on_short_history: true
  model:
    mode: rule_baseline
    ab_switching: false
```

---

## 2. Target Architecture

### 2.1 Module Layout

Use the existing `ifa/families/stock/` family as the implementation home:

```text
ifa/families/stock/
  __init__.py
  api.py                         # public orchestration entrypoints
  context.py                     # StockEdgeContext, as_of, run mode, params
  params/
    stock_edge_v2.2.yaml
    loader.py
  data/
    gateway.py                   # local-first data resolver
    availability.py              # freshness and missing-data checks
    tushare_backfill.py          # endpoint-specific backfill adapters
    daily.py
    intraday.py
    events.py
    fundamentals.py
    smartmoney.py
    ta.py
  db/
    duckdb_client.py             # existing
    lock.py                      # existing
    postgres.py                  # thin helpers if needed
    memory.py                    # report/plan cache helpers
  features/
    technical.py
    support_resistance.py
    moneyflow.py
    event_risk.py
    fundamental_lineup.py
    intraday_profile.py
  strategies/
    rules.py
    statistical.py
    t0.py
    scoring.py
    veto.py
  models/
    baseline.py                  # heuristic probability baseline
    registry.py                  # version metadata, no A/B in V2.2
  report/
    builder.py
    renderer.py
    markdown.py
    templates/
      stock_edge_report.html
      styles.css
  cli.py                         # if not wired directly into global CLI
```

Rationale:

- `data/` owns all local-vs-Tushare decisions.
- `features/` computes raw evidence.
- `strategies/` turns evidence into decisions.
- `models/` is a stable seam for later ML/DL without infecting rule code.
- `report/` only formats already-computed decisions and evidence.

### 2.2 Data Flow

```text
CLI / internal call
  → resolve_as_of_trade_date()
  → acquire analysis lock
  → check result cache
  → DataGateway.build_snapshot()
      → local PostgreSQL
      → local DuckDB / Parquet
      → Tushare availability check
      → backfill missing mandatory data
  → FeatureBuilder
  → VetoEngine
  → StrategyEngine
  → TradePlanSynthesizer
  → ReportBuilder
  → persist plan + render assets
  → return paths + structured JSON
```

### 2.3 Core Dataclasses

The first implementation should use typed dataclasses or Pydantic-style models before writing SQL-heavy logic:

```python
StockEdgeRequest:
    ts_code: str
    requested_at: datetime
    mode: Literal["quick", "deep", "update"]
    run_mode: Literal["manual", "production", "test"]
    has_base_position: bool = False
    base_position_shares: int | None = None
    fresh: bool = False

StockEdgeSnapshot:
    ts_code: str
    as_of_trade_date: date
    data_cutoff_at: datetime
    daily_bars: DataFrame
    daily_basic: DataFrame | None
    moneyflow: DataFrame | None
    intraday_5min: DataFrame | None
    research_lineup: dict | None
    ta_context: dict | None
    event_context: dict | None
    freshness: list[DataFreshness]
    degraded_reasons: list[str]

TradePlan:
    action: str
    confidence: str
    entry_zone: PriceZone | None
    add_zone: PriceZone | None
    stop: PriceLevel | None
    targets: list[PriceTarget]
    holding_window_days: tuple[int, int]
    probability: ProbabilityBlock
    position_size: PositionSize
    t0_plan: T0Plan | None
    vetoes: list[Veto]
    evidence: list[EvidenceItem]
    model_version_used: dict
    param_hash: str
```

---

## 3. Data Gateway Design

### 3.1 Local-First Resolution Contract

Every loader returns both data and freshness metadata:

```python
LoadResult:
    data: Any
    source: Literal["postgres", "duckdb", "parquet", "tushare_backfill", "missing"]
    as_of: date | datetime | None
    rows: int
    status: Literal["ok", "partial", "missing", "stale"]
    message: str | None
```

Rules:

1. Never call Tushare first.
2. Check local PostgreSQL / DuckDB first.
3. If local data is missing and Tushare supports the data:
   - backfill only the minimum window needed for the current request in V2.2 functional mode.
   - persist the result.
   - reload from local storage after persistence.
4. If Tushare does not support the data or token/API fails:
   - continue only if the data is optional.
   - mark snapshot degraded.
   - mandatory data failure stops the report.

### 3.2 Mandatory vs Optional Data

For the 7-day functional version:

| Data | Store | Mandatory | Functional Window | Backfill Rule |
|---|---|---:|---:|---|
| trading calendar | PostgreSQL | yes | enough to resolve T/T-1 | backfill if missing |
| daily OHLCV | PostgreSQL | yes | 60 trading days minimum | backfill target stock |
| adj factor / adjusted close | PostgreSQL | yes | 60 trading days minimum | backfill target stock |
| daily_basic | PostgreSQL | yes | 20 trading days | backfill target stock |
| moneyflow | PostgreSQL | recommended | 20 trading days | backfill target stock if endpoint available |
| limit up/down | PostgreSQL | recommended | 20 trading days | backfill latest market dates |
| top list / top inst | PostgreSQL | optional | 20 trading days | degraded if missing |
| SW sector membership | PostgreSQL | recommended | as_of month | use existing SmartMoney tables |
| Research lineup | PostgreSQL | recommended | latest annual + quarterly deep | trigger Research deep via service; service reuses existing reports first |
| TA context | PostgreSQL | recommended | latest as_of date | degraded if missing |
| 5min bars | DuckDB/Parquet | optional for first pass | 7 trading days | target stock only |
| events: share_float / margin / hk_hold / holdertrade / pledge | PostgreSQL | optional first pass, P0 later | 7-60 days by type | local first, endpoint-specific |

### 3.3 Storage Decision Matrix

| Data Shape | Preferred Store | Why |
|---|---|---|
| one row per stock-date, audited | PostgreSQL | joins, PIT, constraints, migrations |
| report metadata / plan JSON | PostgreSQL | reuse, audit, query by stock/date |
| 5min bars | DuckDB + Parquet | large scans, cheap partitioning, fast analytics |
| Kronos embeddings / analog vectors | DuckDB + Parquet | vector-like batch scans, appendable artifacts |
| backtest/tuning snapshots | DuckDB + Parquet | large result sets, not OLTP |
| model artifact files | `ifaenv/models/stock/` | not repo data, versioned by metadata |

### 3.4 Backfill Boundaries

Functional mode:

```text
daily bars: target stock, enough for indicators, normally 60 trading days
5min bars: target stock, 7 trading days only when needed
events: minimum endpoint-specific window
research: trigger target annual/quarterly deep and visible peer annual/quarterly deep through Stock Edge prefetch; the Research service checks reusable reports first
```

Tuning mode:

```text
daily bars: full universe / long history
5min bars: full market 2 years
TA metrics: 60d / 180d / 360d windows
ML labels: 20d / 40d / 60d forward windows
```

---

## 4. Database Plan

### 4.1 PostgreSQL Schema

Use the existing PostgreSQL schema name `stock`. The current repo already has
`stock.analysis_record`, `stock.report_sections`, `stock.support_resistance`,
`stock.tracking_log`, `stock.user_watchlist`, `stock.user_context`, and
`stock.analysis_lock`.

Minimum V2.2 tables already present:

```text
stock.analysis_record
stock.report_sections
stock.support_resistance
stock.tracking_log
stock.user_watchlist
stock.user_context
stock.analysis_lock
```

Do not add a new migration until the existing columns are mapped. If later
phases need cache, freshness, param audit, or model registry, prefer minimal
additive tables under the same `stock` schema.

Current column mapping:

```text
analysis_record:
  record_id
  ts_code
  analysis_type                  # existing values fast/deep/update; Stock Edge "quick" maps to fast if needed
  base_record_id
  triggered_at
  data_cutoff                    # TIMESTAMPTZ; maps to Stock Edge data_cutoff_at
  status
  conclusion_label
  conclusion_text
  key_levels_json
  setup_match_json
  validation_json
  invalidation_json
  next_watch_json
  forecast_json
  output_html_path
  output_pdf_path
  error_summary

report_sections:
  record_id
  section_key
  section_order
  content_json
  status
  skip_reason

support_resistance:
  ts_code
  trade_date
  price
  sr_type
  sources
  strength
  distance_pct
  confidence
```

Potential additive tables, deferred until the feature that truly needs them:

```text
stock.universe_history
stock.calibration_metrics
stock.fill_log
stock.result_cache
stock.param_change_log
stock.model_registry
stock.analog_cases
stock.prediction_snapshot
```

### 4.2 DuckDB / Parquet Layout

```text
~/claude/ifaenv/duckdb/stock.duckdb
~/claude/ifaenv/duckdb/parquet/
  intraday_5min/
    trade_month=YYYY-MM/
      part-*.parquet
  tuning_runs/
    run_id=.../
  kronos/
    model_version=.../
```

Functional mode writes only target-stock 5min windows if needed. The full market 2-year backfill is a later tuning/data-completeness phase.

---

## 5. Algorithm Framework

### 5.1 First Version Algorithm Stack

V2.2 functional version should start with:

1. **Rule-based veto**
   - ST / delisting risk when detectable
   - suspended / no recent trading
   - liquidity too low
   - entry too far above support
   - stop too wide vs reward
   - recent limit-up one-line board not fillable

2. **Technical structure**
   - trend alignment: 5d / 20d / 60d
   - breakout / pullback / reversal classification
   - volatility: ATR and normalized range
   - relative strength vs SW L2 sector when available
   - volume confirmation: 5d volume vs 20d volume

3. **Support / resistance**
   - recent swing high/low
   - MA20 / MA60
   - gap boundaries
   - limit-up / limit-down levels
   - optional 5min VWAP / volume profile for recent 7 days

4. **Money and sector**
   - 5d / 7d main net inflow trend
   - large/super-large order direction
   - SmartMoney sector phase / strength if available

5. **Fundamental lineup**
   - Research annual deep + quarterly deep summary if present
   - five-dimension factor scores
   - red flags and watchpoints
   - do not recalculate financial statements in Stock Edge

6. **Heuristic probability baseline**
   - no production ML model yet
   - map score bands to conservative probability ranges
   - record as `model_version_used = {"right_tail": "heuristic_v0"}`

7. **LLM explanation**
   - explain evidence, scenario tree, and risks
   - never invent prices, probabilities, or financial numbers

### 5.2 Price Generation

Entry zone:

```text
support_anchor = strongest support near current price
entry_low = support_anchor * (1 - entry_buffer)
entry_high = min(current_price, support_anchor + 0.3 * ATR)
```

Breakout setup:

```text
entry = breakout retest zone
stop = failed breakout level or MA20 invalidation
target_1 = prior resistance or +1.5R
target_2 = +2.5R / major resistance
right_tail = entry * 1.50, displayed only with probability and caveat
```

Pullback setup:

```text
entry = MA20 / VWAP cluster / support band
stop = support break + next_open_stop rule
target_1 = recent swing high
target_2 = channel top or +2R
```

Avoid single-point recommendations. Always output ranges and invalidation conditions.

### 5.3 T+0 Logic

T+0 is output only when:

```text
request.has_base_position == true
base_position_shares > 0
recent liquidity is sufficient
stock is not one-line limit-up / limit-down
```

Functional first version:

- Use 7-day daily + optional 5min VWAP.
- Suggest high-sell / low-buy ranges as a plan, not as guaranteed execution.
- Limit T+0 size to a fraction of base position.
- Explicitly show do-not-T+0 conditions.

Example fields:

```text
t0_plan:
  eligible: true
  max_size_pct_of_base: 20%
  sell_zone: [x, y]
  buyback_zone: [a, b]
  do_not_t0_if:
    - gap_up_above_6pct
    - one_line_limit_up
    - volume_below_50pct_normal
```

### 5.4 Model Strategy

V2.2:

- no A/B switching
- no claim of trained ML edge before validation
- reuse existing SmartMoney / Ningbo / Kronos assets before adding any new Stock Edge model
- use named heuristic baseline versions only where no trained adjacent model exists
- record all predictions as uncalibrated unless calibration exists
- keep model registry table ready

Current reuse map:

| Existing Asset | Stock Edge Use | Direction |
|---|---|---|
| SmartMoney `random_forest` | score the target stock's SW L2 sector for short-horizon sector strength | `smartmoney_sector_ml` signal |
| SmartMoney `xgboost` | score the target stock's SW L2 sector for 1-2 month sector strength | `smartmoney_sector_ml` signal |
| Ningbo active aggressive model | score target if it is in `ningbo.candidates_daily` | `ningbo_active_ml` signal |
| Ningbo active conservative model | same target-stock candidate scoring with different risk profile | `ningbo_active_ml` signal |
| Ningbo Kronos cache | reuse cached 128-bar OHLCV embedding as K-line representation evidence | `kronos_pattern` signal |

Kronos principle:

- Kronos is not shown as a decorative model label.
- Its value is K-line representation learning: compressing hard-to-hand-code OHLCV pattern structure into continuous embeddings.
- Stock Edge should add directional value through a labelled downstream model or analog/backtest layer. Until that calibration is present, the report records Kronos availability as model evidence and lets Ningbo active models consume it when their artifacts were trained with Kronos features.

Later tuning phase:

- right-tail classifier for `hit_50pct_40d`
- quantile return forecaster for 20/40/60d
- stop-first model
- entry-fill model using 5min
- T+0 improvement classifier
- Kronos analog embedding

### 5.5 Prediction Execution Card

The prediction execution card is the first-class product surface. It must be
generated before narrative sections and must be persisted with the report.

It answers:

| Question | Required Output |
|---|---|
| Can we buy today? | buy / watch / avoid decision with evidence score |
| At what price? | executable entry low/high, not a single magic price |
| What if we do not buy today? | next-5-trading-day conditional buy scenarios |
| Where is the risk invalidated? | stop / invalidation price and condition |
| Where do we sell? | 20%, 30%, and right-tail 50% target prices when applicable |
| How long do we hold? | default 20-40 trading days |
| Why not buy? | vetoes and cancellation rules |

Important implementation rule:

- Analysis sections exist only to support this prediction.
- A strategy that cannot move entry timing, entry price, target, stop, or probability is not a production Stock Edge strategy.

### 5.6 Strategy Taxonomy And Display

Stock Edge should treat “strategy” as a matrix of auditable evidence feeding the
prediction execution card, not as a single black-box score. The current
functional implementation uses `heuristic_v0` plus reused adjacent model
signals; each row has `family`, `algorithm`, `direction`, `score`, `weight`,
`status`, `evidence`, and `data_source`.

Current V2.2 signal count:

- **85 implemented strategy/report-layer components** in one report.
- **84 scoring signals** feed the numeric strategy matrix.
- **1 report-layer LLM component** (`scenario_tree_llm`) converts structured prices, probabilities, vetoes, and cluster evidence into a falsifiable execution scenario tree. It uses deterministic structured values as the source of truth; any future LLM rewrite must use `ifa.core.llm.LLMClient` and must not change numbers.
- **73 base/reused scoring signals**: trend following, support pullback, breakout pressure, 5d momentum, volume confirmation, volatility structure, 60d range position, volatility contraction, drawdown recovery, gap risk, 开盘跳空风险模型, 开盘/竞价失衡代理, trend-quality R2, candle reversal, volume-price divergence, 7d moneyflow, order-flow mix, 北向资金体制, 两融杠杆脉冲, 大宗交易压力, 龙虎榜机构/游资分歧, LLM 事件催化, moneyflow persistence/decay, 涨停微结构, 涨停事件路径模型, SmartMoney SW L2 flow/phase, SW L2 diffusion breadth, same-sector leadership, peer relative momentum, peer leader fundamental spread, 同行财务 Alpha 模型, 同行 Research 自动触发, 基本面矛盾审计 LLM, 行业层级收缩, daily-basic style, Research fundamental lineup, 财报-价格错配模型, historical analog replay, target/stop replay, entry-fill replay, 入场成交概率分类器, 收益分位预测, 保序置信收益带, 先止损概率, 单调概率校准, 右尾收益 GBM, 多周期序列排序, 目标/止损生存模型, 止损危险率模型, 多目标周期分类器, 目标阶梯概率模型, 路径形态混合模型, MFE/MAE 收益风险面, 未来5日择时模型, 买入价格面模型, 连续仓位模型, 回踩反弹分类器, 收敛突破分类器, 多模型概率融合器, 体制自适应权重模型, strategy-validation decay, SmartMoney RF/XGB sector ML, Ningbo active ML, Kronos pattern evidence, Kronos 相似形态近邻, Kronos 路径簇转移, SmartMoney cached LLM regime, SmartMoney cached LLM counterfactual, intraday VWAP profile, volume-profile support, VWAP reclaim execution, T+0 uplift, liquidity/slippage.
- **11 TA family signals**: T/P/R/F/V/S/C/O/Z/E/D, reused from `ifa.families.ta`.
- Tuning phase can add/remove weights, but production reporting must keep every active signal auditable.

Production display:

1. **交易计划总览**: final action, confidence, position, holding window.
2. **买卖时机预测**: today's entry decision, today's entry band, next-5d conditional entries, 20%/30%/50% sell targets.
3. **预测执行场景树**: today execute / today wait / next-5d branches / invalidate, each with trigger, action, entry band, target, stop, and watch signal.
4. **关键价位结构**: nearest support/resistance, 20d/60d high-low, S/R table.
5. **技术图谱**: inline SVG daily candlestick + MA5/MA20/MA60, S/R overlay, MACD, 5d momentum.
6. **多策略矩阵**: every active rule/statistical/TA/fundamental/flow/ML/Kronos signal plus cluster plans.
7. **同板块财务对照**: SW L2 peers are compared primarily through Research annual/quarterly deep financial factors: ROE, revenue growth, CFO/NI, leverage, valuation. Size and 5/10/15d returns are secondary market-position visuals, not the main ranking.
8. **买入区间与失效条件**: executable range, stop/invalidation, no single-point “magic price”.
9. **目标价格与概率**: 20%/30%/right-tail objective with conservative uncalibrated probability.
10. **T+0 底仓计划**: shown only when user has a base position.
11. **风控否决与证据**.
12. **策略验证摘要**: rolling TA setup metrics; never presented as single-stock guaranteed backtest.
13. **免责声明**. Data freshness remains internal audit metadata and is not displayed in the user-facing report.

### 5.7 Strategy Families

#### Rule-Based / Price Structure

| Family | Current V2.2 Use | Data | Purpose |
|---|---|---|---|
| Trend following | MA5/MA20/MA60 alignment | `smartmoney.raw_daily` | decide trend continuation vs weak structure |
| Pullback to support | nearest support distance + ATR | daily bars + S/R engine | determine entry range and invalidation |
| Breakout pressure | nearest resistance distance | S/R engine | identify breakout confirmation zone |
| Momentum | 5d return bands | daily bars | distinguish healthy momentum from overheated chase |
| Range position | 60d high/low percentile | daily bars | favor strong-but-not-fully-extended structures |
| Volatility contraction | 5d/30d true range ratio | daily bars | identify quiet-coil / pre-breakout compression |
| Drawdown recovery | distance to 20d high + recovery from 20d low | daily bars | separate constructive repair from falling knife |
| Gap risk | recent open gap size | daily bars | penalize poor fill quality and event-distorted bars |
| Trend quality R2 | log-price regression slope and R2 | daily bars | distinguish durable trend from noisy drift |
| Candle reversal | close location and upper/lower shadows | daily bars | detect constructive lower-shadow reversal or upper-shadow distribution |
| Volume-price divergence | 10d price trend vs amount trend | daily bars | penalize shrink-volume chase and down-with-volume damage |
| Liquidity veto | 7d amount floor | daily bars | prevent untradeable names |
| T+0 execution | ATR high-sell / low-buy zones | daily + optional 5min | only for base-position users |

#### Statistical / Cross-Sectional Signals

| Signal | Source | Use |
|---|---|---|
| Peer relative momentum | same SW L2 peers, 5/10/15d returns | detects whether the target is leading or lagging comparable leaders |
| Daily-basic style | turnover, volume ratio, PE/PB | liquidity/valuation/crowding sanity check |
| Moneyflow persistence | 7d `raw_moneyflow` | target-stock accumulation / distribution |
| Order-flow mix | super-large and large-order imbalance | institutional participation proxy |

#### TA Setup Families Reused From `ifa.families.ta`

Stock Edge should consume TA outputs; it should not duplicate or fork TA scanners.

| Family | Meaning | Examples |
|---|---|---|
| T 趋势 | trend/breakout/acceleration | `T1_BREAKOUT`, `T2_PULLBACK_RESUME`, `T3_ACCELERATION` |
| P 回踩 | pullback/gap/tight base | `P1_MA20_PULLBACK`, `P2_GAP_FILL`, `P3_TIGHT_CONSOLIDATION` |
| R 反转 | bottom/reversal/support bounce | `R1_DOUBLE_BOTTOM`, `R2_HS_BOTTOM`, `R3_HAMMER`, `R4_SUPPORT_BOUNCE` |
| F 形态 | chart patterns | `F1_FLAG`, `F2_TRIANGLE`, `F3_RECTANGLE` |
| V 量价 | volume-price behavior | `V1_VOL_PRICE_UP`, `V2_QUIET_COIL` |
| S 板块 | sector resonance inside SW L2 | `S1_SECTOR_RESONANCE`, `S2_LEADER_FOLLOWTHROUGH`, `S3_LAGGARD_CATCHUP` |
| C 筹码 | chip concentration/looseness | `C1_CHIP_CONCENTRATED`, `C2_CHIP_LOOSE` |
| O 订单流 | order-flow / institution clues | `O1_INST_PERSISTENT_BUY`, `O2_LHB_INST_BUY`, `O3_LIMIT_SEAL_STRENGTH` |
| Z 统计 | statistical extremes/reversion | `Z1_ZSCORE_EXTREME`, `Z2_OVERSOLD_REBOUND`, `Z3_RANGE_FADE` |
| E 事件 | event catalyst | `E1_EVENT_CATALYST` |
| D 顶部预警 | bearish top patterns | `D1_DOUBLE_TOP`, `D2_HS_TOP`, `D3_SHOOTING_STAR` |

V2.2 implementation rule:

- read `ta.candidates_daily`, `ta.warnings_daily`, `ta.setup_metrics_daily`;
- group by setup family;
- add score for active long setups;
- subtract score for warning setups and decaying/weak rolling metrics;
- display the exact triggered setup labels and rolling edge.

#### SmartMoney / SW L2 Flow

All sector context must be SW L2:

| Signal | Source | Use |
|---|---|---|
| SW L2 membership | `smartmoney.sw_member_monthly` | PIT-correct sector ownership |
| Sector net flow | `smartmoney.sector_moneyflow_sw_daily` | 7-day sector moneyflow background |
| Sector role/phase | `smartmoney.sector_state_daily` | avoid fighting retreat/cooling phases |
| Sector factor state | `smartmoney.factor_daily` | heat/trend/persistence/crowding |
| Sector diffusion breadth | sector 7d flow + persistence/crowding + leader overlap | detect broadening mainline vs crowded retreat |
| Same-sector leaders | SW L2 peers + daily/basic/moneyflow/TA | fundamental and tactical comparison |

Leader definition must be multi-dimensional:

- **市值龙头**: top total market value / circulating market value.
- **动量龙头**: top 5d return among same SW L2 peers.
- **资金龙头**: top recent main net inflow among same SW L2 peers.
- **TA 形态龙头**: highest recent TA candidate score.

These are context, not automatic buy recommendations. They answer whether the
target stock is leading, following, lagging, or diverging from its SW L2 group.

#### ML / DL / LLM Reused Signals

These are implemented as reuse adapters. Stock Edge should not retrain or
silently fork these model families during V2.2.

Reuse boundary: Stock Edge may copy small reusable feature logic or read
existing cache/artifact tables, but the report path must not launch TA, Ningbo,
or SmartMoney scripts as side effects. That keeps the family independently
operable for IFA integration while still benefiting from adjacent systems.

| Signal | Type | Source | Current Role |
|---|---|---|---|
| SmartMoney sector RF | ML | `ifa.families.smartmoney.ml.random_forest` artifact | short-horizon SW L2 sector strength |
| SmartMoney sector XGB | ML | `ifa.families.smartmoney.ml.xgboost_model` artifact | 1-2 month SW L2 sector strength |
| Ningbo active aggressive | ML ensemble | `ifa.families.ningbo.ml.dual_scorer` | target-stock candidate score if present in Ningbo pool |
| Ningbo active conservative | ML ensemble | same | risk-adjusted target-stock candidate score |
| Kronos pattern evidence | DL representation | `ifa.families.ningbo.ml.kronos_features` cache | 128-bar OHLCV embedding availability and downstream model input |
| SmartMoney LLM regime | LLM cache | `smartmoney.llm_regime_states` via `ifa.core.llm.LLMClient` | market-regime narrative tilt, bounded as contextual score |
| SmartMoney LLM counterfactual | LLM cache | `smartmoney.llm_counterfactuals` via `ifa.core.llm.LLMClient` | robustness / invalidation narrative, bounded as contextual score |

LLM signals are only accepted from project-managed cache tables or from calls
made through `ifa.core.llm.LLMClient`; they cannot be generated by the coding
assistant or by report template prose.

### 5.7 Backtest Design

Backtesting should be staged; do not optimize before the functional product loop
is stable.

#### Stage 1: Deterministic Replay

Purpose: verify no lookahead and basic mechanics.

- Input: historical `as_of_trade_date`, target stock universe, `heuristic_v0` params.
- For each date: build snapshot only with data available up to that date.
- Output: generated action, entry zone, stop, targets, strategy matrix rows.
- Labels:
  - T+5/T+10/T+20/T+40 forward return;
  - stop-first event;
  - target hit event;
  - hit +50% within 40 trading days;
  - max drawdown before target or horizon.

#### Stage 2: Setup-Family Attribution

Purpose: understand which families work in which regimes.

- Group by TA family, SW L2 sector phase, market regime, liquidity bucket, volatility bucket.
- Metrics:
  - sample count;
  - win rate;
  - average / median return;
  - payoff ratio;
  - max drawdown;
  - stop-first rate;
  - decay score: recent edge minus long-window edge.

#### Stage 3: Entry/Exit Execution Replay

Purpose: turn analysis into executable price levels.

- Daily-only first: fill if next-day low/high reaches entry zone.
- Optional 5min replay later:
  - entry fill quality;
  - VWAP slippage;
  - T+0 high-sell/low-buy feasibility;
  - intraday stop and buyback sequence.

#### Stage 4: Model Training

Only after Stage 1-3 produce stable labels:

- right-tail classifier: `hit_50pct_40d`;
- quantile return model: 20d/40d return distribution;
- stop-first classifier;
- entry-fill model;
- T+0 improvement classifier;
- Kronos analog embedding for pattern similarity.

### 5.8 Parameter Tuning

Stock Edge uses a two-layer tuning system:

1. **Global preset tuning**
   - Runs offline, preferably during the weekend.
   - Universe starts with the top-liquidity ~500 A-shares; it can expand to the full market after runtime and data cost are proven acceptable.
   - Produces the default parameter preset used when a stock has no fresh local overlay.
   - This keeps first-run latency acceptable and avoids relying on mediocre hand-picked defaults.

2. **Pre-report per-stock overlay tuning**
   - Runs only when a user triggers a report for one stock and the stock has no fresh tuning artifact, or the artifact is older than the configured TTL, currently 10 days.
   - Uses that stock's local history, and backfills only the data required by active strategies.
   - Searches a continuous overlay on top of the latest global preset: strategy weights, curve centers/scales, entry/stop buffers, target probabilities, cluster gates, and T+0 zone parameters.
   - After the overlay is selected, the report is generated from that fixed overlay. This is not rolling adaptive tuning during the report.

3. **Offline validation and promotion**
   - Walk-forward/OOS remains mandatory for deciding whether a global preset or algorithm family should be promoted.
   - It is a governance layer, not the report-time tuning path.

Parameters to tune:

- score weights by strategy family;
- buy/watch/avoid decision cutoffs at the report layer only;
- smooth support-distance curve center/scale/amplitude;
- ATR entry/stop buffers;
- smooth momentum curve center/width/overheat penalty;
- continuous moneyflow scale parameters;
- SW L2 sector score multipliers;
- TA family winrate/decay continuous curve centers/scales;
- T+0 size and zone parameters.

Global preset optimization protocol:

1. Freeze a candidate param set with hash.
2. Run purged validation windows, e.g. 90d train / 60d validation / rolling OOS, for promotion only.
3. Load local high-liquidity universe first; for selected stocks with short
   local history, optionally backfill from TuShare with a capped stock count
   (`global_preset.max_backfill_stocks`) and reload local PostgreSQL before
   scoring.
4. Optimize multi-objective score:
   - 40d hit +50% rate;
   - average 20/40d return;
   - max drawdown control;
   - stop-first rate;
   - turnover and liquidity penalty;
   - sample-size penalty.
5. Reject params that improve in-sample but fail OOS.
6. Save artifacts under `/Users/neoclaw/claude/ifaenv/`, not the repo:
   - DuckDB/Parquet replay outputs;
   - model artifacts;
   - calibration metrics;
   - parameter manifests.

No A/B model switching is needed in V2.2. A/B becomes relevant only after there
are at least two validated model families with stable OOS metrics.

Pre-report overlay protocol:

1. Resolve the as-of date and load the latest global preset.
2. Check local tuning memory for `(ts_code, preset_hash)` and TTL.
3. If fresh, reuse the overlay and generate the report.
4. If stale or missing, build labels/features from the target stock's history, run a bounded continuous search, persist the overlay artifact, then generate the report.
5. If local history is insufficient, automatically attempt target-stock TuShare backfill for the configured history window, reload local PostgreSQL, and re-run the decision. Only if history is still insufficient should the report fall back to the global preset and record the tuning state as degraded.

Standalone run requirement:

- Global preset and single-stock overlay must be runnable without generating a
  report because IFA will be integrated into other systems.
- CLI entrypoints:
  - `uv run python -m ifa.cli stock tune-global-preset --as-of YYYY-MM-DD --limit 500`
  - `uv run python -m ifa.cli stock tune-overlay 300042.SZ --as-of YYYY-MM-DD`
- Script entrypoints for cron/external orchestrators:
  - `uv run python scripts/stock_edge_global_preset.py --as-of YYYY-MM-DD --limit 500`
  - `uv run python scripts/stock_edge_pre_report_overlay.py 300042.SZ --as-of YYYY-MM-DD`
- Artifacts are written under `/Users/neoclaw/claude/ifaenv/models/stock/tuning/`.

Report integration:

- `run_stock_edge_report()` now goes through `prepare_report_params()`.
- With `tuning.enabled: true` by default, the runner loads or creates the target stock's
  pre-report overlay, merges it into the parameter surface, and uses the merged
  hash for cache lookup and audit metadata.
- With `tuning.enabled: false`, this is a strict no-op and preserves the base
  parameter hash. This mode is mainly for debugging or benchmark comparisons.

Parameter rule:

- All production-relevant tuning parameters live in
  `ifa/families/stock/params/stock_edge_v2.2.yaml`.
- This includes candidate counts, TTLs, objective weights, continuous search
  bounds, strategy weights, cluster weights, score curves, risk targets, and
  T+0 sizing.
- Python may keep conservative fallback defaults for safety, but the YAML is
  the source of truth for normal runs.

### 5.9 Institutional Ensemble Design

This is the core quant layer. The goal is not to “average a pile of signals”;
the goal is to build a regime-aware, style-aware, evidence-calibrated ensemble
where every strategy can be isolated, clustered, stress-tested, tuned, and
retired.

#### 5.9.1 Three-Level Scoring Hierarchy

Use a three-level hierarchy:

```text
single strategy score
  → cluster score
      → final trade-plan score / probability / action
```

Single strategy score:

```text
s_i = f_i(features, params_i, as_of_context)
```

Each single strategy outputs:

- raw signal direction: positive / neutral / negative;
- raw score in `[-1, +1]`;
- confidence / data quality;
- expected holding period;
- expected failure mode;
- feature snapshot;
- explainable evidence;
- parameter hash.

Cluster score:

```text
C_k = gated_weight_k(context) × robust_aggregate({s_i in cluster k})
```

Final score:

```text
FinalEdge =
    Σ cluster_weight_k(context) × calibrated_cluster_score_k
    - risk_penalty
    - liquidity_penalty
    - crowding_penalty
```

The final score should map to:

- action: buy / watch / avoid / exit;
- entry quality;
- stop distance;
- expected return distribution;
- probability of `hit_50pct_40d`;
- stop-first probability;
- max drawdown estimate;
- position sizing.

#### 5.9.2 Strategy Clusters

Clusters are not cosmetic. They allow the system to learn which families work
under which market/stock regimes.

| Cluster | Strategy Members | Best Context Hypothesis | Main Risk |
|---|---|---|---|
| Trend / Breakout | T, F, V, pressure breakout, MACD trend | bull market, strong SW L2 phase, high breadth | late chase / exhaustion |
| Pullback / Continuation | P, support pullback, MA20/MA60 bounce | healthy uptrend, sector still strong, volatility normal | catching a trend break |
| Reversal / Mean Reversion | R, Z, oversold, range fade | choppy/range market, oversold relief, broad stabilization | value trap / falling knife |
| Order Flow / Smart Money | O, raw moneyflow, LHB, super-large orders | accumulation, institutional participation | one-day noise / event distortion |
| SW L2 Sector Leadership | S, sector flow, sector role/phase, same-sector leaders | mainline sector, diffusion/acceleration phase | sector crowding / phase rollover |
| Fundamentals / Quality | Research five dimensions, analyst reports, red flags | 20-40d swing with business support | stale financials / report lag |
| Intraday / T+0 Execution | 5min VWAP/profile, ATR zones, fill quality | base-position management, liquid stocks | slippage / impossible execution |
| Risk / Warning | D, liquidity veto, ST/suspend/event warnings, crowding | all regimes | false negatives if too loose |

Each cluster should have:

- independent score;
- cluster-specific parameters;
- cluster-specific backtest metrics;
- cluster-specific suitability map;
- cluster-specific decay monitor.

#### 5.9.3 Context Gating

Weights must be conditional, not static. Gating dimensions:

Market regime:

- bull / risk-on: boost Trend, Breakout, Sector Leadership.
- range-bound: boost Pullback, Reversal, Statistical.
- bear / risk-off: boost Risk, reduce long clusters, require stronger flow.
- post-crash rebound: boost Oversold/Reversal but cap position.

Stock style:

- large cap: favor sector leadership, fundamentals, lower-vol trend.
- small/micro cap: favor liquidity filters, event/order-flow, stricter drawdown.
- high beta: favor momentum but raise stop-first penalty.
- low liquidity: veto or heavily penalize regardless of score.

Sector state, always SW L2:

- mainline / acceleration: boost S/T/V/O clusters.
- diffusion: allow laggard catch-up but watch crowding.
- climax: reduce new buys, require pullback or T+0 only.
- retreat/cooling: suppress long signals unless reversal cluster is very strong.

Signal freshness:

- fresh local data gets full weight.
- partial/stale data weight decays.
- optional missing data should not block, but should lower confidence.

Implementation status:

- Strategy-matrix weights now apply YAML `context_gates`.
- Market regime is normalized from TA regime first, then local SmartMoney LLM
  regime cache if available.
- SW L2 phase is normalized from `sector_state_daily.cycle_phase`.
- The gates adjust cluster weights before final aggregation, so risk-off /
  retreat regimes can suppress trend-breakout signals and emphasize risk
  warnings without changing individual strategy code.

#### 5.9.4 Single-Strategy Joint Tuning

A single strategy still has many parameters and must be tuned jointly, not one
threshold at a time. Examples:

Trend breakout:

- lookback high window;
- breakout buffer;
- volume confirmation;
- MA slope requirement;
- entry retest band;
- stop anchor;
- overheat cap.

Support pullback:

- support source weights;
- max distance to support;
- ATR buffer;
- stop ATR multiplier;
- rebound confirmation;
- invalidation delay.

Order-flow:

- net inflow window;
- large/super-large order mix;
- flow scaled by circulating market cap;
- persistence requirement;
- sector-flow confirmation.

T+0:

- sell-zone ATR multiple;
- buyback-zone ATR multiple;
- max size as percent of base;
- do-not-T+0 gap threshold;
- 5min VWAP/liquidity filter.

Joint tuning principle:

- tune parameters as a vector;
- prefer continuous curves (`sigmoid`, `tanh`, Gaussian-like decay) over discrete step functions;
- evaluate fill feasibility and stop-first rate together;
- never optimize only for average return;
- penalize unstable parameter sets with high local sensitivity.

#### 5.9.4.1 Pre-Report Overlay Search Space

The report-triggered overlay optimizer must only search continuous parameters.
It should not switch strategies on/off with discrete flags and it should not
change code. Initial bounds are defined in
`ifa/families/stock/backtest/objectives.py`:

- aggregate score scale and buy/watch decision curves;
- support-distance, breakout-distance, momentum, and moneyflow curve
  centers/scales;
- cluster weights for trend, pullback, reversal, order flow, SW L2 leadership,
  fundamentals, model ensemble, T+0 execution, and risk warning;
- strategy weights for newly implemented price-action, SW L2 diffusion,
  strategy-validation decay, and intraday VWAP execution signals;
- risk parameters including max stop distance and right-tail target;
- T+0 base-position size.

Single-stock overlay tuning starts from the latest global preset and searches
inside a bounded neighborhood. If a stock has too little history, the runner
must first try target-stock TuShare backfill, then shrink back to the global
preset only if the source still cannot provide enough local history.

#### 5.9.5 Cluster-Level Tuning

After single strategies are stable, tune cluster weights and gates:

```text
cluster_weight_k = base_weight_k × gate_market × gate_sector × gate_style × gate_freshness
```

Tune by slice:

- market regime;
- SW L2 sector role/phase;
- market-cap bucket;
- liquidity bucket;
- volatility bucket;
- recent return bucket;
- stock exchange / board;
- event window.

Cluster metrics:

- hit +50% within 40d;
- average T+20/T+40 return;
- median return;
- tail loss;
- max drawdown;
- stop-first rate;
- fill rate;
- turnover;
- sample size;
- decay score.

Only promote cluster weights that improve OOS stability across multiple slices,
not just aggregate returns.

#### 5.9.6 Ensemble Calibration

The final ensemble must be calibrated into probabilities. Do not let raw score
masquerade as probability.

Calibration targets:

- `P(hit_50pct_40d)`;
- `P(stop_first)`;
- expected return quantiles: p10/p50/p90;
- expected max drawdown;
- entry fill probability;
- T+0 positive improvement probability.

Calibration methods, in order:

1. monotonic binning / isotonic calibration for rule baseline;
2. logistic calibration by regime/style bucket;
3. gradient boosting or random forest meta-model;
4. conformal prediction bands for uncertainty;
5. later: Kronos/sequence analog features as additional inputs.

Use purged walk-forward validation with embargo to avoid leakage.

#### 5.9.6.1 Prediction Objective

The optimizer must maximize prediction-execution quality, not raw return.

Current objective contract:

```text
objective = 0.30 * hit_target_40d_quality
          + 0.20 * expected_return_40d
          + 0.15 * entry_fill_quality
          + 0.15 * reward_risk
          + 0.10 * calibration_quality
          - 0.15 * expected_drawdown
          - 0.10 * stop_first_rate
          - 0.05 * turnover_liquidity_penalty
```

This is implemented as a shared contract in
`ifa/families/stock/backtest/objectives.py` so the weekend global preset job and
the pre-report single-stock overlay optimize the same thing.

The report target is not fixed to `40d +50%`. The prediction surface evaluates
multiple executable opportunities from YAML:

- tactical target: 15 trading days / +20%;
- swing target: 25 trading days / +30%;
- right-tail target: 40 trading days / +50%.

Each opportunity has probability, target price, expected value, and a minimum
probability floor. The report highlights the best positive expected-value
opportunity; `40d +50%` is shown only as the right-tail path, not as a forced
recommendation.

The overlay replay evaluates execution, not just signal quality:

1. A candidate signal first creates a continuous entry zone from close, ATR,
   support-distance, and risk-distance parameters.
2. The replay checks whether the next 5 trading days actually touch that entry
   zone.
3. Only after a fill does the replay evaluate the next 40 trading days for
   target-first versus stop-first path quality.
4. The objective receives `fill_rate_5d`, `clean_fill_rate_5d`, target-hit
   quality, stop-first risk, drawdown, reward/risk, and calibration metrics.

This keeps the optimizer aligned with the user-facing prediction execution
card: "Can we buy, at what price, and what happens after that filled entry?"

Useful statistical-learning methods to add in order:

1. isotonic / monotonic binning for rule-score probability calibration;
2. regime-bucket logistic calibration for bull/range/bear and SW L2 phases;
3. quantile regression or conformal prediction for 20/40-day p10/p50/p90 paths;
4. survival / hazard modeling for target-first vs stop-first timing;
5. Bayesian hierarchical shrinkage from stock → SW L2 sector → style bucket → market preset;
6. gradient boosting / random forest meta-model over rule, TA, SmartMoney,
   Research, Kronos, and intraday execution features;
7. Kronos nearest-neighbor analog distribution for historical path similarity.

Implemented statistical-learning signal:

- `historical_replay_edge` performs single-stock analog replay from local daily
  bars.
- It finds historical states similar to the current feature vector
  (`ret_5`, `ret_20`, range position, drawdown, amount ratio), then measures
  realized forward paths for 15d/+20%, 25d/+30%, and 40d/+50%.
- It outputs hit rate, stop-first rate, average return, expected value, analog
  count, and similarity. The strategy matrix consumes it as a `model_ensemble`
  signal.
- The prediction surface also blends its target-specific hit rates into the
  15d/+20%, 25d/+30%, and 40d/+50% probabilities with bounded shrinkage. This
  keeps the report anchored to the general scoring model while allowing the
  target stock's own history to personalize probabilities.

Implemented target/stop path signal:

- `target_stop_replay` replays target-first versus stop-first events on the
  target stock's own daily history.
- It evaluates the same execution targets used by the report: 15d/+20%,
  25d/+30%, and 40d/+50%, with a YAML-controlled stop distance and recency
  weighting.
- It outputs target-first probability, stop-first probability, neither-event
  rate, average days to target, average days to stop, average forward return,
  expected value, and sample count.
- The strategy matrix consumes it as a `model_ensemble` signal; the prediction
  surface blends its target-specific probabilities and stop-first rate with
  bounded shrinkage, and attaches path timing fields to each opportunity row.

Implemented entry execution signal:

- `entry_fill_replay` replays whether a support/ATR entry zone would have been
  filled within the next 5 trading days in the target stock's own history.
- It measures fill rate, clean-fill rate, stop-before-fill rate, and average
  days to fill.
- The strategy matrix consumes it as an `intraday_t0_execution` signal, and
  the prediction surface blends its clean-fill rate into the report's entry
  fill probability with bounded shrinkage.

Implemented path-distribution signals:

- `quantile_return_forecaster` builds weighted historical forward-path
  distributions for the same 15d/+20%, 25d/+30%, and 40d/+50% opportunities.
  It emits P10/P50/P90, expected return, right-tail touch probability, and
  average drawdown, then feeds the `model_ensemble` cluster.
- `conformal_return_band` uses the same weighted path distribution as a
  conformal-style uncertainty band. Wide bands or heavy left tails feed the
  `risk_warning` cluster even when the upside tail is attractive.
- `stop_first_classifier` estimates whether the configured stop is historically
  likely to fire before the target. It is currently a recency-weighted path
  classifier from local daily bars and can later be replaced by a trained
  survival/hazard model without changing the report contract.
- `isotonic_score_calibrator` builds a single-stock monotonic bin calibration:
  recent continuous raw scores are labelled by whether the next 40 trading days
  touched the configured right-tail target. The current score is mapped to a
  monotonic calibrated hit-rate bin and consumed as `model_ensemble` evidence.
  It degrades when history lacks enough positive and negative labels.
- `right_tail_meta_gbm` trains a per-trigger
  `sklearn.HistGradientBoostingClassifier` from the target stock's own PIT
  daily features and 40-trading-day right-tail labels. It reports current
  right-tail probability, historical positive rate, OOS high-score hit rate,
  and an AUC proxy.
- `temporal_fusion_sequence_ranker` is the first sequence-model implementation
  path. V2.2 uses a compact `sklearn.MLPClassifier` on multi-horizon return,
  volatility, range-position, amount-ratio, acceleration, and volatility-slope
  features. It is intentionally per-stock/on-demand for report generation; a
  weekend global sequence ranker can later replace it with a persisted artifact.
- `target_ladder_probability_model` trains per-trigger target ladder classifiers
  for the executable 15d/+20%, 25d/+30%, and 40d/+50% contracts. It reports
  each ladder's probability, base rate, stop-before-target rate, expected return
  proxy, and historical time-to-target, so the decision layer can choose the
  realistic target rather than hard-code one horizon.
- `path_shape_mixture_model` fits a single-stock Gaussian mixture over PIT path
  features and maps the latest state into continuous cluster posterior weights.
  The weighted cluster distribution emits target hit probability, expected
  forward return, stop-first rate, and dominant path-shape context.
- `mfe_mae_surface_model` trains paired gradient-boosting regressors for future
  maximum favorable excursion and maximum adverse excursion. It feeds the
  model ensemble with predicted upside, adverse path risk, and reward/risk ratio
  for the execution card's target and stop logic.
- `stop_loss_hazard_model` is a standalone random-forest hazard model for
  stop-before-target risk. It feeds `risk_warning` as a veto-capable signal
  instead of burying stop risk inside bullish right-tail classifiers.
- `entry_price_surface_model` labels each historical state by the best realized
  route among buy-now, wait-for-pullback, breakout-confirmation, and avoid. The
  classifier outputs route probabilities plus suggested current, pullback, and
  breakout prices for the next-5-trading-day execution window.
- `gap_risk_open_model` trains a next-open adverse-gap classifier from local
  daily bars. It estimates the probability of an execution-damaging open gap
  and feeds `risk_warning` rather than letting a right-tail model overrule
  near-term gap risk.
- `regime_adaptive_weight_model` exposes the market-regime and SW L2 phase
  gates already used by the matrix as an auditable ML-style signal. It reports
  continuous cluster multipliers for offensive, defensive, and execution
  clusters so later tuning can learn regime-specific weights.
- `peer_financial_alpha_model` deepens the same-sector comparison by turning
  Research statement factors, valuation discount, and price lag versus peers
  into an expected peer alpha signal.
- `limit_up_event_path_model` converts recent涨停/炸板/开板 records into a
  continuation-versus-fade path estimate, using seal quality, event density,
  open-count pressure, and limit reference return as continuous inputs.
- `position_sizing_model` turns the current signal stack into a continuous
  recommended fraction using aggregate edge, mean model probability, risk
  pressure, stop hazard, and liquidity gate. It is a scoring signal only here;
  the final report position card remains responsible for presentation.

Implemented intraday execution signal:

- `intraday_profile` now builds a real VWAP / volume-profile structure when
  local DuckDB minute bars are available.
- It measures close-vs-VWAP, lower cost-support volume share, upper pressure
  volume share, volume concentration, and produces an execution score.
- This replaces the earlier shallow availability-only signal while still
  degrading gracefully when minute data is absent.

Implemented VWAP / cost-zone execution signals:

- `volume_profile_support` reads local DuckDB minute bars and scores whether
  the latest cost distribution sits below price as support or above price as
  pressure.
- `vwap_reclaim_execution` measures recent VWAP reclaim behavior and the latest
  close-vs-VWAP condition. It feeds the `intraday_t0_execution` cluster and is
  useful for deciding whether today's entry should chase, wait for reclaim, or
  stand down.

Implemented price-action statistics:

- `trend_quality_r2` fits a log-price regression on recent daily bars and uses
  continuous slope/R2 scoring to separate clean trend from noisy drift.
- `candle_reversal_structure` scores lower-shadow reversal versus upper-shadow
  distribution using the latest candle and recent drawdown/bounce context.
- `volume_price_divergence` compares 10-day price trend with成交额 trend to
  detect confirmation or divergence.

Implemented validation meta-signal:

- `strategy_validation_decay` converts existing TA rolling validation metrics
  (`winrate_60d`, `combined_score_60d`, `decay_score`) into one bounded
  statistical-learning signal. This makes historical setup quality affect the
  prediction score instead of remaining a passive report table.

Implemented SW L2 diffusion signal:

- `sector_diffusion_breadth` uses sector 7-day moneyflow, factor persistence,
  crowding, and overlap among visible sector leaders to score whether the SW L2
  sector is broadening or exhausting.

Implemented event-driven signals:

- `lhb_institution_hotmoney_divergence` reads `raw_top_list` and
  `raw_top_inst` for the target stock's recent 龙虎榜/机构席位 activity. It scores
  whether榜单资金和机构净买 are aligned or divergent, and feeds the
  `order_flow_smart_money` cluster.
- `limit_up_microstructure` reads `raw_kpl_list` and `raw_limit_list_d` for
  recent涨停/开板/封单 structure. It treats no recent limit-up event as neutral,
  while strong seal ratio and low open count add trend/breakout evidence.
- `auction_imbalance_proxy` uses the latest local daily open, previous close,
  close, high, and low as a conservative auction/opening imbalance proxy. It
  rewards gap retention and intraday close location while penalizing large
  chase gaps, feeding the `intraday_t0_execution` cluster.
- `northbound_regime` reads `raw_moneyflow_hsgt` and turns recent northbound
  flow level, acceleration, and positive-day share into a market risk-appetite
  prior under `order_flow_smart_money`.
- `market_margin_impulse` reads aggregated `raw_margin` and scores whether
  financing balance and net financing purchases are expanding sanely,
  shrinking, or overheating. It feeds `risk_warning`.
- `block_trade_pressure` reads target-stock `raw_block_trade` and compares
  recent block-trade weighted price with the latest close, scaled by liquidity,
  so large discount transactions can suppress the buy plan and premium
  transactions can support承接 evidence.
- `event_catalyst_llm` consumes cached project-LLM event memory from
  `research.company_event_memory` and `ta.catalyst_event_memory`. It does not
  ask the coding assistant for narrative or numbers; upstream Research/TA jobs
  populate those tables through `ifa.core.llm.LLMClient`, and Stock Edge scores
  polarity, importance, and recency as a bounded catalyst/risk signal.
- `entry_fill_classifier` converts the support/ATR replay labels into a
  continuous next-5-trading-day fill probability. It feeds the
  `intraday_t0_execution` cluster and directly affects the prediction execution
  card's “can we actually buy this zone?” question.

Implemented Kronos analog signal:

- `analog_kronos_nearest_neighbors` consumes the existing Ningbo
  `kronos_small_v1` 256-dimensional embedding cache from `ifaenv`; it does not
  rerun the Kronos script during report generation.
- It finds cosine-similar historical K-line embeddings under a no-lookahead
  rule: only samples whose 20/40-trading-day future path is already observable
  before the report `as_of_trade_date` are eligible.
- The labelled analog distribution emits average similarity, expected 20/40-day
  return, average 40-day max return/drawdown, 20%/30%/50% right-tail hit rates,
  and 12% stop-first rate. The matrix consumes it in the `model_ensemble`
  cluster so Kronos becomes directional prediction evidence rather than a
  passive availability flag.
- `kronos_path_cluster_transition` reuses the same labelled analog set and
  classifies future paths into right-tail breakout, swing-up, grind-up,
  pop-and-fade, range-chop, and stop-first clusters. This gives the report a
  path-shape forecast, not only a mean-return estimate.

Implemented hierarchical prior signal:

- `hierarchical_sector_shrinkage` uses SW L2 factor heat/persistence and the
  target's visible peer momentum percentile as a sector/style prior. When the
  target stock has limited historical samples, the signal increases shrinkage
  toward this sector prior; when history is rich, the stock's own evidence
  dominates.

Implemented T+0 uplift signal:

- `t0_uplift` estimates whether an A-share base-position T+0 process can add
  value through high-sell/low-buy execution.
- It prefers local DuckDB 5min bars and falls back to a daily range proxy when
  minute data is unavailable.
- It measures average intraday range, estimated reversal capture, success rate,
  round-trip-cost-adjusted uplift, and produces an `intraday_t0_execution`
  score.
- This does not allow naked T+0. The report plan still requires a base position
  before presenting an executable T+0 plan.

Implemented dynamic sizing module:

- `position_sizing.py` converts the selected opportunity, stop distance,
  stop-first probability, expected drawdown, and confidence into a continuous
  budget fraction.
- Sizing parameters are YAML-driven: min/max buy fraction, loss budget,
  expected-value scale, stop-probability penalty, drawdown penalty, and
  confidence multipliers.
- This replaces the earlier fixed 25% test position with a risk-budget-aware
  position recommendation.

Implemented liquidity/slippage module:

- `liquidity_slippage.py` estimates capacity and executable slippage from
  recent daily amount, turnover, and volatility.
- The strategy matrix consumes it as a `risk_warning` signal so thin liquidity,
  excessive turnover, or unstable ranges can suppress otherwise attractive
  setups.
- Parameters are YAML-driven: amount window, good/min amount, participation
  rate, base/scale slippage bps, turnover limits, and volatility penalty scale.

Implemented flow persistence module:

- `flow_persistence_decay` measures whether single-stock main money flow is
  persistent, decaying, or reversing.
- It uses recent `raw_moneyflow.net_mf_amount`, recency weighting, positive-day
  share, same-sign streak, and latest-3-day versus prior-period decay.
- The strategy matrix consumes it under `order_flow_smart_money`, complementing
  the simpler `moneyflow_7d` sum and `orderflow_mix` large-order imbalance.

Implemented peer leader fundamental spread module:

- `peer_leader_fundamental_spread` compares the target against visible SW L2
  leaders using market cap percentile, 5/10/15-day composite momentum
  percentile, PE/PB relative discount, and Research memory coverage.
- It is intentionally conservative: if Research annual/quarterly deep factors
  are missing, it lowers coverage rather than inventing profitability or
  governance conclusions.
- The strategy matrix consumes it under `fundamentals_quality`, complementing
  the visual sector-leader context already rendered in the report.

Implemented Research deep prefetch orchestration:

- `ifa.families.research.report.service.ensure_research_report()` exposes the
  Research report pipeline as a reusable Python service. It preserves the CLI
  semantics: analyze/fetch local data, check `research.report_runs` for a
  succeeded reusable asset with the same latest filing period, and generate
  HTML/MD only when needed.
- Stock Edge calls `ensure_stock_edge_research_prefetch()` before final plan
  generation. By default it covers the target stock plus up to four visible SW
  L2 leaders, for both `annual deep` and `quarterly deep`.
- The prefetch policy is asymmetric by design: target deep reports may use the
  project `LLMClient` with `llm_timeout_seconds`, while sector-peer deep reports
  default to rules-only generation. Stock Edge needs peer factor memory and
  comparable reports; it should not let multiple peer narrative calls block the
  execution card.
- After any Research report is generated or reused, Stock Edge reloads its
  local snapshot so `research.memory.load_fundamental_lineup()` can consume the
  newly persisted factor decomposition and PDF/research-report cache.
- Failures are fail-soft by default and appear in the Stock Edge freshness
  table as `research_prefetch` degradation; this keeps the trade plan available
  while making missing fundamental work auditable.
- The strategy matrix also exposes this orchestration as
  `peer_research_auto_trigger`, a `fundamentals_quality` signal. It scores
  whether target/peer annual and quarterly deep factor coverage is available or
  was successfully generated/reused before the plan was built.
- `fundamental_contradiction_llm` audits whether Research deep factor coverage,
  recent price/flow behavior, and cached SmartMoney LLM counterfactual evidence
  are aligned or contradictory. It remains cache-first: the report path reads
  `research.memory` and `smartmoney.llm_counterfactuals` instead of calling the
  coding model.

#### 5.9.7 LLM Role

LLM should not invent numbers, prices, or probabilities. Its correct roles:

- summarize structured evidence into a scenario tree;
- detect contradiction between signals;
- explain why a cluster is down-weighted in the current regime;
- extract event/fundamental risks from local Research memory;
- produce falsifiable next-watch conditions;
- generate human-readable “what would change my mind” statements.

LLM output should be persisted separately as narrative, never as raw numeric
signal unless backed by structured fields.

Implementation constraint:

- Stock Edge code must use the project LLM tool: `ifa.core.llm.LLMClient`.
- It must not use the Codex/chat model that is writing the code as a runtime analysis engine.
- Any LLM narrative used in a report should follow existing project patterns (`report_model_outputs` or family-specific LLM tables) so model name, prompt version, raw response, and latency remain auditable.

#### 5.9.8 Tuning Infrastructure

All tuning artifacts live under `/Users/neoclaw/claude/ifaenv/`.

Recommended layout:

```text
/Users/neoclaw/claude/ifaenv/
  duckdb/stock.duckdb
  duckdb/parquet/stock_edge/
    replays/
    labels/
    strategy_features/
    tuning_runs/
  models/stock/
    heuristic/
    meta_model/
    calibration/
  out/tuning/stock_edge/
```

Tuning run contract:

- immutable param manifest;
- dataset cutoff;
- train/validation/OOS date windows;
- strategy/cluster metrics;
- aggregate metrics;
- failure slices;
- chosen params only promoted after review.

Promotion rule:

```text
candidate params → replay → OOS validation → slice diagnostics → human review → freeze
```

No automatic production promotion in V2.2.

---

## 6. Implementation Milestones

### Phase A: Foundation First

Goal: create the smallest reliable Stock Edge skeleton and remove ambiguity around dates, params, and handover.

Scope:

- SE-I0 documentation alignment
- SE-I1 params + skeleton
- SE-I2 as-of date router

Why first:

- `as_of_trade_date` is the root of every no-lookahead guarantee.
- Params and hashes must exist before report cache and model version metadata can be meaningful.
- This phase does not depend on 5min, TA internals, Research internals, or model training.

Exit criteria:

- `stock_edge_v2.2.yaml` loads.
- param hash is deterministic.
- `resolve_as_of_trade_date()` passes before-15:00 / after-15:00 / non-trading-day tests.
- no TA files are touched.

### Phase B: Persistence And Local-First Data Gateway

Goal: make Stock Edge able to know what is already local, what is missing, and how to backfill the minimum needed window.

Scope:

- SE-I3 storage and migration
- SE-I4 data gateway

Implementation order:

1. Inspect existing `stock.*` schema before adding new tables.
2. Reuse existing `stock` tables by default.
3. Add only the minimum missing tables through Alembic, and only after mapping existing columns.
4. Implement local-first loaders for PostgreSQL.
5. Implement DuckDB loader for optional 7-day 5min target-stock data.
6. Add Tushare backfill adapters only after local miss is proven.

Exit criteria:

- local PostgreSQL read path works for trading calendar, daily, daily_basic, moneyflow when present.
- local DuckDB path works for existing tables/views and degrades cleanly when 5min parquet is absent.
- missing mandatory target-stock daily data can be backfilled from Tushare and then reread locally.
- optional data absence is recorded in freshness/degraded metadata.

### Phase C: Evidence And Rule Baseline

Goal: produce a production-grade first trade plan without pretending we already have trained models.

Scope:

- SE-I5 feature builders
- SE-I6 rule baseline strategy engine

First-pass evidence:

- 7-day short-term behavior.
- 20/60-day daily bars only where needed for trend, ATR, support/resistance.
- Research annual deep + quarterly deep lineup if already available.
- TA candidates/setup/regime if already available.
- SmartMoney sector and moneyflow if already available.
- optional 7-day 5min VWAP/volume profile only for entry/T+0 refinement.

First-pass algorithms:

- hard veto rules.
- trend / pullback / breakout / reversal setup classification.
- support/resistance from swing levels, MA20/MA60, gaps, ATR.
- heuristic probability baseline, explicitly versioned as untrained.
- T+0 plan only with declared base position.

Exit criteria:

- every entry/stop/target has evidence.
- veto dominates the decision.
- no T+0 plan appears without base position.
- no LLM-generated numbers enter the plan.

### Phase D: Report, Cache, And Manual E2E

Goal: make the user-facing loop work in manual mode and make reruns reusable.

Scope:

- SE-I7 report builder and renderer
- SE-I8 end-to-end manual mode

Implementation order:

1. Render structured plan to HTML + MD.
2. Persist `analysis_record`, `trade_plan`, internal data freshness metadata, and report paths.
3. Add result cache lookup by stock + as_of date + mode + param hash + model versions.
4. Add `--fresh` behavior to bypass cache.
5. Run one known stock end-to-end in manual mode.
6. Do desktop/mobile layout QA as test artifacts only.

Exit criteria:

- first manual run computes and writes report assets.
- second same run reuses cache.
- `--fresh` recomputes.
- report visibly shows data cutoff, key risk/degraded notes when material, and disclaimer; data freshness stays internal-only.
- screenshot PNGs are not production artifacts.

Output rule:

- Report HTML/MD must use `ifa.core.report.output.output_dir_for_family(settings, "stock_edge", as_of_trade_date)`.
- Manual/production layout is `/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/stock_edge/`.
- Test layout is `/Users/neoclaw/claude/ifaenv/out/test/`.
- Desktop/mobile screenshots, if generated, are QA artifacts under the same `ifaenv/out/.../stock_edge/screenshots/` tree and must never be committed.
- DuckDB, Parquet, logs, and model files also stay under `/Users/neoclaw/claude/ifaenv/`, never inside the repo.

### Phase E: Overnight Data And Parameter Tuning

Goal: improve signal quality after the functional product works.

Scope:

- SE-I9 overnight tuning and backfill

Deferred work:

- optional full-market 2-year 5min backfill after the target-stock sweep and
  high-liquidity universe preset prove incremental value.
- 60d / 180d / 360d robustness validation.
- ML labels for 20/40/60-day returns and `hit_50pct_40d`.
- right-tail classifier / quantile forecaster / stop-first model.
- Kronos analog embedding index.
- parameter tuning and calibration.

Exit criteria:

- overnight jobs write to DuckDB/Parquet and model artifact directories.
- tuned params are changed only through audited param updates.
- metrics improve out-of-sample without breaking the functional pipeline.

### Work Rule

At any point, if a later phase reveals a missing foundation, return to the earlier phase and fix the boundary. Do not patch around missing data contracts inside report rendering or LLM prompts.

### SE-I0: Documentation And Handover Alignment ✅

Deliverables:

- this plan
- update V2.2 todo naming from Stock Intel to Stock Edge when safe
- note dirty workspace files before code work

Do not touch TA files unless explicitly assigned.

### SE-I1: Skeleton And Params ✅

Deliverables:

- `ifa/families/stock/params/stock_edge_v2.2.yaml`
- params loader with defaults:
  - `default_lookback_days: 7`
  - intraday optional
  - tuning enabled by default with pre-report overlay TTL
  - model mode `rule_baseline`
- public `build_stock_edge_report(request)` placeholder

Tests:

- params load test
- param hash stable test

### SE-I2: As-Of Date Router ✅

Deliverables:

- trading calendar resolver
- 15:00 cutoff logic
- data cutoff metadata
- manual run reproducibility

Tests:

- trading day before 15:00 uses T-1
- trading day after 15:00 uses T
- non-trading day uses latest completed trade date
- missing today data degrades or falls back consistently

### SE-I3: Storage And Migration ✅

Deliverables:

- existing `stock` schema mapping
- Alembic migration only if a required field/table is truly missing
- Postgres memory helpers
- result cache and analysis lock helpers

Tests:

- migration applies
- insert/read analysis record
- duplicate cache read works
- lock prevents duplicate compute

### SE-I4: Data Gateway ✅

Deliverables:

- local-first loaders
- mandatory/optional data classification
- Tushare backfill adapters for target-stock daily data
- DuckDB 5min target-window loader, optional

Tests:

- local hit does not call Tushare
- missing mandatory data triggers backfill
- missing optional data marks degraded
- backfilled data is persisted then reloaded locally

### SE-I5: Feature Builders ✅

Deliverables:

- daily technical features
- S/R levels
- moneyflow features
- sector context
- Research lineup adapter
- TA context adapter
- optional 5min intraday profile

Tests:

- deterministic feature output on fixture data
- no future data beyond `as_of_trade_date`
- 5min feature works when data exists and degrades when absent

### SE-I6: Rule Baseline Strategy Engine ✅

Deliverables:

- veto engine
- setup classifier
- entry/stop/target generator
- heuristic probability baseline
- T+0 plan generator
- trade plan synthesizer

Tests:

- veto dominates buy signal
- no T+0 output without base position
- entry/stop/target are auditable from evidence
- China color convention stored for renderer: up red, down green

### SE-I7: Report Builder And Renderer ✅

Deliverables:

- HTML + MD manual output
- first-screen decision card
- evidence sections
- data freshness section
- disclaimer
- persisted report asset paths

Tests:

- report renders for a known stock
- MD/HTML paths go to ifaenv output area
- no production screenshots are generated by report itself
- screenshot/layout checks are separate test artifacts only

User-facing design worklist:

- Audience: high-net-worth users and active A-share traders who need fast
  decisions but may not know every quant/TA term. The report must be beautiful,
  information-dense, and focused on the few facts that change action.
- First screen must make action, entry band, invalidation, target/probability,
  and position sizing obvious without explaining the whole system.
- Technical terms should be compressed into plain Chinese labels with hover or
  short inline definitions only where needed; avoid long instructional prose.
- Peer charts must always show the target stock, even when it is not a top
  market-cap/momentum/moneyflow/TA leader. The target should use a distinct
  marker and be readable on desktop and mobile.
- Visual hierarchy should favor decision cards, compact tables, and annotated
  charts over long paragraphs; UI polishing belongs after strategy correctness
  and parameter loops are stable.

### SE-I8: End-To-End Manual Mode ✅

Deliverables:

- CLI command or existing CLI integration
- run one target stock end-to-end
- cache reuse on second run
- `--fresh` bypass

Tests:

- first run computes
- second run reuses
- fresh run recomputes
- degraded data visible in report

### SE-I9: Overnight Tuning And Backfill Phase ⏭️ Deferred

This is explicitly after functional delivery.

Deliverables:

- full-market 5min backfill job
- long-window feature generation
- 60d / 180d / 360d validation
- ML labels and training jobs
- parameter search
- calibration metrics

Runtime expectation:

- designed for overnight or weekend runs
- outputs to DuckDB/Parquet and model artifact directory
- does not block normal manual report generation

---

## 7. Test Strategy

### 7.1 Unit Tests

Minimum:

- as-of date routing
- param loading and hashing
- local-first data gateway
- S/R feature generation
- veto engine
- trade plan synthesis
- T+0 eligibility
- report rendering smoke test

### 7.2 Integration Tests

Use manual mode and one small stock first. The goal is to prove the pipeline, not optimize signal quality.

Test matrix:

| Case | Expected |
---|---|
| local data complete | no Tushare call |
| local daily missing | Tushare backfill then local reload |
| optional 5min missing | report degrades but completes |
| no base position | no T+0 plan |
| base position present | T+0 section appears |
| repeated same request | cache reused |
| `--fresh` | recompute |

### 7.3 Visual QA

Desktop and mobile layout checks are required for report UI, but screenshots are test artifacts only:

```text
/Users/neoclaw/claude/ifaenv/out/manual/<YYYYMMDD>/stock_edge/screenshots/
```

Production report generation should not create screenshot PNGs unless explicit visual QA mode is enabled.

---

## 8. Risk Register

| Risk | Mitigation |
|---|---|
| trying to optimize before product loop works | force 7-day functional mode first |
| Tushare API gaps | local-first gateway + degraded-state reporting |
| 5min scope explosion | target-stock 7-day optional first; full market later |
| LLM hallucinated numbers | LLM only explains structured evidence |
| TA concurrent changes | consume via stable adapters; do not edit TA files during Stock Edge work |
| Research report missing | reuse if present; mark degraded or explicitly trigger later |
| too many tables too early | minimum schema first, defer calibration/fill/analog tables |
| overclaiming +50% | show probability and evidence, never deterministic recommendation |

---

## 9. Immediate Next Step When Coding Starts

Start with **SE-I1 + SE-I2**:

1. Add params file and loader.
2. Add `StockEdgeRequest` / context dataclasses.
3. Implement `resolve_as_of_trade_date`.
4. Add tests for cutoff logic.

Only after this foundation is green should the work move into schema and data gateway.

This keeps the implementation small, testable, and easy for another developer to pick up midstream.
