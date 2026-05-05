# Family Reference

One section per report family. For data-flow detail see [`architecture.md`](architecture.md). For SW-specific tables see [`sw-migration.md`](sw-migration.md).

---

## Market — A 股总指挥 (main)

**Purpose.** The customer-visible daily A-share report. Three slots tell the day's story end-to-end: morning briefing (set hypotheses), noon update (mid-day check), evening recap (verdict + next-day watchlist).

**Slots.** `morning` · `noon` · `evening`

**Sections (evening, 14).**

| # | Name | Notes |
|---|---|---|
| S1 | commentary | Headline narrative |
| S2 | index_panel | 上证 / 深成 / 创业 / 科创 / 北证 / 沪深300 / 中证500 / 中证1000 |
| S3 | category_strength | SW L1 strength heatmap (28 行业) |
| S4 | sentiment_grid | 涨停 / 跌停 / 炸板率 / 涨家数 / 上证成交 |
| S5 | dragon_tiger | 龙虎榜机构席位 |
| S6 | three_aux_summary | SmartMoney + Tech + Asset 三辅验证 (单行 verdict each) |
| S7 | review_table (morning) | 早盘假设回看 |
| S8 | review_table (noon) | 午盘假设回看 |
| S9 | focus_deep | 10-stock deep focus |
| S10 | focus_brief | 20-stock brief watch |
| S11 | attribution | 涨幅归因 |
| S12 | hypotheses_list | 明日假设资产 (sinks to `report_judgments`) |
| S13 | watchlist | 明日观察清单 |
| S14 | disclaimer | Lindenwood + 中文风险提示 |

**Key data sources.**

- SW L1 (28 sectors) for S3 category strength
- **Dynamic SW L2 主线** for S6 / S11: top-N from `smartmoney.sector_moneyflow_sw_daily` ordered by `net_amount`, with member-stock `pct_change` aggregated via `sw_member_monthly`
- `raw_top_list`, `raw_top_inst` for 龙虎榜
- `raw_limit_list_d` for 涨停/跌停 sentiment

**CLI.**

```bash
ifa generate market --slot morning --report-date 2026-04-30 --user default
ifa generate market --slot noon    --report-date 2026-04-30
ifa generate market --slot evening --report-date 2026-04-30 --user default --generate-pdf
```

---

## Macro — Policy & Liquidity Overlay (aux)

**Purpose.** Macro context that shapes A-share regime: GDP / CPI / PPI prints, central-bank liquidity, FX, HSGT 北向, margin balance, policy events. No sector axis — Macro is regime-level, not stock-level.

**Slots.** `morning` · `evening`

**Sections (evening, 11).**

S1 commentary · S2 review_table · S3 news_list · S4 data_panel · S5 liquidity_grid · S6 cross_asset_grid · S7 attribution · S8 watchlist · S9 hypotheses_list · S10 indicator_capture_table · S11 disclaimer

**Data sources.**

- TuShare structured: `cn_gdp`, `cn_cpi`, `cn_ppi`, `cn_pmi`, `shibor`, `moneyflow_hsgt`, `margin`
- `ifa job text-capture` — LLM-extracts structured macro indicators from news (incremental, watermark-based) → `indicator_capture` table
- `ifa job policy-memory` — curates active policy events into `policy_memory` (14-day lookback)

**CLI.**

```bash
ifa job text-capture --lookback-days 90
ifa job policy-memory --lookback-days 14
ifa generate macro --slot morning --report-date 2026-04-30
ifa generate macro --slot evening --report-date 2026-04-30 --generate-pdf
```

---

## Asset — Cross-Asset Transmission (aux)

**Purpose.** Track commodity / futures markets and their transmission into A-share sector groups. The story is "原油涨 → 石化 / 油服板块 → 哪些股动了".

**Slots.** `morning` · `evening`

**Sections (evening, 10).**

S1 commentary · S2 commodity_dashboard · S3 category_strength · S4 review_table · S5 transmission_review · S6 chain_review · S7 news_list · S8 watchlist · S9 hypotheses_list · S10 disclaimer

**Data sources.**

- TuShare commodity futures (原油 / 黄金 / 铜 / 铝 / 镍 / 螺纹钢 / 焦炭 / 玉米 / 豆粕 / 棉花 / 白糖 / 橡胶 ...)
- 18 SW L2 sectors flagged as commodity-relevant: 有色金属, 钢铁, 煤炭开采, 石油加工, 油气开采, 基础化工, 化学制品, 化学纤维, 化学原料, 建材, 农业种植, 养殖业, 食品加工, 纺织, 造纸, 化学农药, 化学肥料, 橡胶塑料

**CLI.**

```bash
ifa generate asset --slot morning --report-date 2026-04-30
ifa generate asset --slot evening --report-date 2026-04-30 --generate-pdf
```

---

## Tech — AI 五层蛋糕 (aux)

**Purpose.** Track the AI / 科技 ecosystem as a five-layer stack: 算力 (energy + chips + infra) → 模型 → 应用. Each layer maps to a curated set of SW L2 sectors so flow / strength / leaders can be measured per layer.

**Slots.** `morning` · `evening`

**Sections (evening, 12).**

S1 commentary · S2 layer_map (五层复盘) · S3 category_strength · S4 review_table · S5 leader_table · S6 candidate_pool · S7 focus_deep · S8 focus_brief · S9 news_list · S10 watchlist · S11 hypotheses_list · S12 disclaimer

**AI five-layer SW L2 mapping.**

| Layer | SW L2 codes | Names |
|---|---|---|
| **energy** (算力·能源) | 801738, 801737, 801735, 801736, 801733, 801731 | 电网设备 / 电池 / 光伏设备 / 风电设备 / 电源设备 / 电机 |
| **chips** (算力·芯片) | 801081, 801083, 801086, 801082 | 半导体 / 元件 / 电子化学品 / 其他电子 |
| **infra** (算力·基础设施) | 801102, 801223, 801101 | 通信设备 / 通信服务 / 计算机设备 |
| **models** (模型层) | 801104, 801103 | 软件开发 / IT服务 |
| **apps** (应用·终端) | 801085, 801084, 801767, 801764, 801093, 801095 | 消费电子 / 光学光电子 / 数字媒体 / 游戏 / 汽车零部件 / 乘用车 |

This mapping is the canonical reference; if any consumer code drifts from it, that consumer is wrong. See [`sw-migration.md`](sw-migration.md).

**CLI.**

```bash
ifa generate tech --slot morning --report-date 2026-04-30 --user default
ifa generate tech --slot evening --report-date 2026-04-30 --user default --generate-pdf
```

---

## SmartMoney — 板块资金流 (separate)

**Purpose.** Track institutional money flow through SW L2 sectors and convert it into a daily evening report with LLM analysis, ML-derived stock signals, and verifiable next-day hypotheses. Owns its own ETL / compute / backtest / training pipeline.

**Slots.** `evening` only.

**Sections (14).**

| # | Name | Description |
|---|---|---|
| E1 | sm_market_pulse | 市场水位 / 涨停跌停 / 炸板率 |
| E2 | sm_sector_flow (in) | 净流入 Top — 板块角色 + LLM 评语 |
| E3 | sm_sector_flow (out) | 净流出 Top — 退潮信号 |
| E4 | sm_quality_flow | 优质流入 vs 拥挤板块 |
| E5 | crowding_risk | 拥挤风险卡 |
| E6 | sm_cycle_grid | 情绪周期格 (点火/确认/扩散/高潮/分歧/退潮) |
| E7 | market_state | 市场水位卡 |
| E8 | candidate_pool | 补涨候选池 |
| E9 | review_table | 昨日假设回顾 (自动对账) |
| E10 | sm_tomorrow_targets | 明日观察清单 + 验证点 |
| E11 | sm_sector_structure | 龙头 / 中军 / 情绪先锋结构 |
| E12 | hypotheses_list | 今日判断资产 |
| E13 | sm_strategy_view | 策略视角 |
| E14 | disclaimer | |

**ETL pipeline.**

```
TuShare endpoints
  ├─ daily, daily_basic, moneyflow, top_inst, top_list, limit_list_d
  ├─ block_trade, kpl_list, kpl_concept, kpl_concept_cons
  ├─ moneyflow_hsgt, ths_hot, dc_hot, dc_index
  ├─ sw_daily (L1), index_daily (8 indices)
  └─ index_member_all (for SW L1/L2/L3 membership)
        │
        ▼
smartmoney.raw_* (~20 tables, daily, idempotent ON CONFLICT)
        │
        ▼
sw_member_monthly  ← derived from raw_sw_member, monthly snapshot
        │
        ▼
sector_moneyflow_sw_daily  ← raw_moneyflow ⨝ sw_member_monthly, GROUP BY l2_code
```

**Factor / state layer.** `factor_daily`, `market_state_daily`, `sector_state_daily`, `stock_signals_daily`.

**ML models.**

| Model | File | Use |
|---|---|---|
| `SmartMoneyLogistic` | `ml/logistic.py` | StandardScaler + class_weight=balanced baseline |
| `SmartMoneyRandomForest` | `ml/random_forest.py` | n_estimators=100, short-horizon (1–3d) signal |
| `SmartMoneyXGBoost` | `ml/xgboost_model.py` | tree_method='hist', nthread=2 (M1-safe), mid-horizon signal |
| News catalyst scorer | `ml/news_catalyst.py` | LLM-based per-sector catalyst score |

Models persist to `~/claude/ifaenv/models/smartmoney/` with an atomic `manifest.json` written via the `persistence.py` helper.

**Backtest metrics.** IC / IC-IR (Pearson) · RankIC / RankIC-IR (Spearman) · TopN hit rate · Q1–Q5 group returns. Walk-forward ML evaluates AUC over rolling 60d-train / 20d-step windows.

**CLI tree.**

```
ifa smartmoney
├── etl       --report-date YYYY-MM-DD
├── backfill  --start YYYYMMDD --end YYYYMMDD
├── compute   --report-date YYYY-MM-DD
│              [--start YYYY-MM-DD --end YYYY-MM-DD]
├── evening   --report-date YYYY-MM-DD [--generate-pdf]
├── backtest  --start ... --end ... [--no-ml] [--windows 1,5]
├── bt
│   ├── list
│   └── show <run-id>
└── params
    ├── list
    ├── freeze --name vYYYY_MM --from-backtest <run-id>
    └── archive vYYYY_MM
```

---

## Ningbo — 宁波派短线策略 (separate)

**Purpose.** A 股短线流派（神枪手/聚宝盆/半年翻倍）的算法化实现 + ML 增强。每天从 ~310 候选股中选 top-5 推荐，目标 +20% 累计止盈 / 跌破 24 日均线止损 / 满 15 日到期。

**Slots.** `evening` only

**Sections (evening, 5).**

| # | Name | Notes |
|---|---|---|
| S1 | market_brief | 上证 / 中证 1000 / 创业板 + 6 步曲漏斗扫描 |
| S2 | **consensus_matrix** ★1-★5 | 三轨（启发式 / ML 激进 / ML 稳健）排名加权打星，前 5 高亮 |
| S3 | alerts | 今日触发的 stop_loss / take_profit |
| S4 | tracking (by-date click-to-expand) | 过去 15 交易日每日 top-5 共识 picks 的 sparkline 趋势 |
| S5 | disclaimer | 中英对照，含宁波派特化风险提示 |

**Architecture.** Champion-Challenger 双 slot：
- **aggressive slot** — 优化 Top5_AvgReturn（当前 active: ensemble_meanrank, +2.18% T5_Mean）
- **conservative slot** — 优化 Sharpe（当前 active: xgb_ndcg, Sharpe 0.27, MaxDD -57%）
- **heuristic** — 永远在线，作为 baseline

每周日 22:00 BJT 自动重训 + Champion-Challenger 晋升判断。

**Data.** Phase 1-3.D 实现，~233k 历史候选 + 219k 标签（自 2024-01-02）。

**CLI tree.**

```
ifa ningbo
├── evening              --scoring dual --mode production --generate-pdf
├── backfill             --start YYYY-MM-DD --end YYYY-MM-DD
├── backfill-candidates  --start ... --end ... [--skip-outcomes]
├── candidate-outcomes   --start ... --end ...
├── backfill-dual        --days N
├── tracking             --start ... --end ...
├── train                --in-sample-end YYYY-MM-DD --activate
├── train-v2             (legacy, prefer refresh weekly)
├── refresh
│   ├── weekly           # 每周日 22:00：训练 + 晋升判断
│   ├── monthly          # 每月 1 号：walk-forward 健康体检
│   └── quarterly        # 每季 1 号：架构评审
├── registry
│   ├── status           # 看当前 active + 晋升历史
│   ├── promote <slot> <version>     # 手动晋升
│   └── rollback <slot>              # 紧急回退
├── stats
└── params
    ├── list
    └── freeze
```

详见 [`ningbo-deep-dive.md`](./ningbo-deep-dive.md)。

---

## Research — 个股深度研究 (separate)

**Purpose.** Single-stock financial-statement research reports built from Tushare fundamentals, disclosures, IRM Q&A, and analyst research reports. V2.2 delivers the core财报 analysis matrix: `quarterly quick`, `annual quick`, `quarterly deep`, and `annual deep`. Cross-stock comparison is out of scope for this module.

**Sections (deep).** §01 overview · §02-§07 5-family factor tables (profitability / growth / cash quality / balance / governance) · §08 timeline · §09 cross-cutting tensions · §10 analyst coverage + themes · §11 investor concerns · §12 trend grid · §13 red flags · §14 5-dim radar · §15 watchpoints · §16 next-disclosure · §17 data completeness · §18 disclaimer.

**Key data sources.** Tushare `stock_basic`, `fina_indicator`, `forecast`, `express`, `top10_holders`, `pledge_stat`, `stk_holdertrade`, `irm_qa_sh/sz`, `report_rc`, `anns_*`. SW L2 peer ranking via `sw_member_monthly`. Derived period factors persist to `research.period_factor_decomposition`; analyst PDF extracts persist to `research.pdf_extract_cache`.

**CLI.**

```
ifa research
├── report <name-or-code> [--analysis-type quarterly/annual] [--tier quick/standard/deep] [--fresh] [--llm] [--output tmp/]
├── peer-scan <name-or-code> [--max-peers N] [--full]
├── peer-rank-refresh
├── batch <code1> <code2> ...
└── scan-* (industry-view / cleanup / status)
```

**Filename schema:** `Stock-Analysis-{ts_code}-{YYYYMMDD}-{analysis_type}-{tier}.html`

**Reuse / quality gate.** Reports are reusable assets registered in `research.report_runs` / `research.report_sections`. Manual and production runs share the same reuse pool; run mode only changes output location. Current core gate: `tests/research/` green, manual matrix run, DB memory verification, and desktop/mobile layout check. The 30-stock golden set remains available for later scoring/LLM tuning but is deferred from the V2.2 core completion gate.

详见 [`research-deep-dive.md`](./research-deep-dive.md)。

---

## TA — 晚盘技术面体制+候选 (separate)

**Purpose.** Daily evening report covering market regime classification, 28 candidate setups across 11 families (T/P/R/F/V/S/C/O/D/Z/E), candidate ranking with regime gating + decay-based suspension, T+1/T+3/T+5/T+10/T+15/T+30 outcome tracking, falsifiable next-day hypotheses.

**Sections (evening, 11 + 3 LLM).** §01 overview · §02 market state · §02-N regime narrative (LLM) · §03 5★ candidates · §04 4★ candidates · §04-N candidate narrative (LLM) · §07 candidates by family · §08 verification (T+1) · §10 setup metrics · §11 attribution · §13 risk scan · §13-N strategy review (LLM) · §14 falsifiable hypotheses · §16 disclaimer.

**Setup families.**

| Family | Setups |
|---|---|
| T 趋势 | T1 突破 · T2 回踩 · T3 加速 |
| P 回踩 | P1 MA20 / P2 缺口 / P3 紧缩 |
| R 反转 | R1 双底 · R2 头肩底 · R3 锤子线 |
| F 形态 | F1 旗形 · F2 三角形 · F3 矩形 |
| V 量价 | V1 量价齐升 · V2 缩量整理 |
| S 板块 | S1 共振 · S2 跟风 · S3 补涨 |
| C 筹码 | C1 集中 · C2 松动 |
| O 主力资金 (M10) | O1 机构连续抢筹 · O2 龙虎榜机构净买入 · O3 涨停封单结构 |
| D 顶部反转 (M10, 警示) | D1 双顶 · D2 头肩顶 · D3 流星线 |
| Z 统计 (M10) | Z1 极端 z-score · Z2 超卖反弹 |
| E 事件 (M10) | E1 业绩预告/快报/披露窗口催化 |

**Key data sources.** `smartmoney.raw_daily` (60d OHLCV) · `smartmoney.market_state_daily` (breadth + 涨跌停 + 连板) · `smartmoney.raw_moneyflow_hsgt` (北向) · `smartmoney.raw_sw_daily` (SW L1/L2 pct_change) · `ta.factor_pro_daily` (Tushare 80 fields incl. MACD/RSI) · `ta.cyq_perf_daily` (chip distribution) · `smartmoney.raw_top_inst` / `raw_top_list` (LHB) · `smartmoney.raw_kpl_list` + `raw_limit_list_d` (涨停池) · `smartmoney.raw_moneyflow` (5d super-large net flow) · `ta.event_signal_daily` (M10 — populated by `ifa.families.ta.etl.event_etl` from Tushare `forecast`/`express`/`disclosure_date`) · `ta.warnings_daily` (M10 P0.1 — D-family bearish-pattern hits on full liquid universe) · `ta.position_events_daily` (M10 P1.2 — fill / stop / target / T+5/10/15 outcomes) · `ta.blacklist_daily` (M10 P1.6 — anns_d 立案 + 重组 + 业绩雷 + 减持) · `ta.fina_indicator_quarterly` (M10 P1.7 — ROE / EPS / margins, scaffolding pending Tushare backfill).

**Key engines (M10 P1).** `ifa.families.ta.setups.position_tracker` — institutional position state machine (T+1 fill check via low ≤ entry, walk forward with stop/target/time-exit; outputs realized_return + max_drawdown + horizon-fixed return_t5/t10/t15). `ifa.families.ta.backtest.runner` — walk-forward backtest (90d-IS / 252d-OOS / 12 rolls per ta_v2.2 walk_forward params; combined objective = 0.7 × T+15 + 0.2 × T+5 + 0.1 × T+10). `ifa.families.ta.metrics_v2.compute_setup_metrics_v2` — daily setup_metrics from position_events_daily (filled-only, real fill prices, replaces legacy candidate_tracking).

**CLI (M10).** `ta walk-forward --start ... --end ...` (backtest engine); `ta daily-etl --date ...` (unified TA ETL: factor_pro / cyq / suspend / events / blacklist); `ta coverage --date ... --lookback 30` (per-setup hit-count monitor; flags starved/low_coverage setups).

**Governance (M5.3).**
- Regime gating: setup gets +0.1 score boost when current regime ∈ historical `suitable_regimes` (from setup_metrics_daily).
- Decay-based suspension: `decay_score < -15pp` → SUSPENDED (dropped); `-15 ≤ decay < -10` → OBSERVATION_ONLY (kept but excluded from top_watchlist).

**CLI.**

```
ifa ta
├── classify-regime --date YYYY-MM-DD [--transitions]
├── scan-candidates / scan --date YYYY-MM-DD [--top-n N]
├── track-candidates --start YYYY-MM-DD --horizon 1 3 5 10 30
├── compute-metrics --date YYYY-MM-DD
├── evening-report / evening --date YYYY-MM-DD [--llm] [--slot evening]
├── backtest --start ... --end ... [--horizon N] [--top-only]
├── evaluate-judgments --judgment-date YYYY-MM-DD
└── backfill-regime --start ... --end ...
```

**Filename schema:** `ifa_TA_{slot}_{YYYYMMDD}_{HHMM BJT}.{html,md}`

**Quality gate.** 25-day golden set (`tests/golden_set/ta_v22.json`), 3 metrics: regime accuracy ≥80%, top-pick intersection ≥60%, rejected-setups recall ≥80%. Run `uv run python scripts/ta_regression.py`.

详见 [`ta-strategy-deep-dive.md`](./ta-strategy-deep-dive.md)。
