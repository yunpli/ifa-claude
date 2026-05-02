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
