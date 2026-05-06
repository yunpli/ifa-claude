# Changelog

All notable changes to iFA China Market Report System.

格式约定：[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)

---

## [2.2.0] — 2026-05-06 — UI overhaul + production-grade data correctness

### Added — Reports UI v2.2 (4 family × 9 slots — premium card system)

**Cross-family `§01` headline overhaul** (kills wall-of-text 流水账)
- New schema: `{headline ≤28字, top3[3] ≤22字, summary ≤80-100字}` enforced
  across all 8 morning/noon/evening tone+headline prompts
- LLM schema-validation retry: 3 attempts with 2s/4s/8s exponential backoff
  if `top3` missing; only fallback after retries exhausted
- New premium components: `.ifa-headline-card`, `.ifa-tone__top3`,
  `.ifa-client-brief__top3` — numbered serif tile grid (01/02/03 衬线大数字)

**Section type upgrades**
- `_review_hooks.html` (NEW) — 中报 §11 review hooks render as numbered card
  grid with question / 为什么重要 / 验证阈值 / 关联板块 pills (was wall of text)
- `_scenario_plans.html` — premium 3-col color-coded grid (看多/震荡/看空)
- `_chain_review.html` — 商品端/A股端 row card layout
- `_chain_transmission.html` — 上游 ▶ 中游 ▶ 下游/A股 chevron pipeline
- `_layer_map.html` — AI Five-Layer Cake gets plain-language intro + 今日 highlight
- `_risk_list.html` — color-coded card grid with severity badges (was flat list)
- `_hypotheses_list.html` — 01/02 numbered cards with chip metadata
- `_leader_table.html` — column rename: 所在层→AI 产业链层 / 龙头类型→属性·标签
  / 失效→退场信号
- `_mapping_table.html` — sector tags as colored ifa-pills (was concatenated text)

**System-wide UI infrastructure**
- New `ifa/core/render/glossary.py` — 37 plain-language financial term tooltips,
  exposed via `ifa_term` Jinja filter
- New `ifa/core/render/templates/styles.css` premium components:
  `.ifa-pill`, `.ifa-headline-card`, `.ifa-section--elevated/--appendix`,
  `.ifa-toc-pills`, `.ifa-back-top`, `.ifa-chain-flow`, etc.
- 7 robust Jinja filters in HtmlRenderer: `fmt_pct`, `fmt_pct_signed`,
  `fmt_amt_yi`, `fmt_num`, `fmt_int`, `fmt_price`, `fmt_dir` (no raw float
  ever reaches templates)
- Cross-family TOC pill nav + section anchors (`id="s{order}"`) + 回到顶部
  floating button
- Mobile responsive: `@media(max-width:768px)` table → card collapse;
  6/8-col tables get desktop+mobile dual layouts
- Print CSS: `page-break-inside: avoid` + repeating table headers + auto-expand
  collapsed `<details>` on print; PDF-friendly typography hierarchy
- A股 红涨/绿跌 convention enforced via `--up/--down` CSS variables
- Banner staleness warning component (red-bordered alert at top of report
  when any data point's `trade_date < report_date`)

### Fixed — Data correctness bugs (issues #1 — #19, all closed)

**P0 data-correctness bugs (production-blocking)**
- #1 Staleness defense across 4 families — fields stay None when
  `row.trade_date != on_date` (no more T-1 prints labeled as today)
- #2 Noon breadth EOD empty — rt_k whole-A snapshot + stk_limit join
  computes total amount / up-down / limit-up locally; fail-closed
- #3 LLM hallucination — morning/noon/evening hyps are now in prompts
  (no more "早报假设未提供" filler)
- #4 Banner staleness warning across 4 main families
- #5 DB ETL freshness pre-flight check (per-slot, trading-day-aware)
- #10 Watchlist empty placeholder fields hidden
- #11 Sparkline timeframe + slot cutoff (noon=11:30 / evening=15:00,
  production AND historical replay both honor the cutoff)
- #12 Calendar-day vs trading-day audit — fixed 8 sites across all
  families (morning's `prev = report_date - 1 day` was breaking every
  Monday + post-holiday open)
- #13 Noon report drops sections that have no data at this slot (vs
  rendering empty placeholders)
- #15 Cross-asset HK / 期货 strict staleness gate
- #19 Noon prefetch + freshness check no longer pulls/validates EOD-only
  tables noon doesn't display

**Engineering bugs found during UI overhaul**
- M5: noon `_build_n11_review_hooks` now `insert_judgment(...)` so evening
  §08 中报判断 Review can load them (was always empty)
- MC4: macro §07 sector tags use `.ifa-pill` chips with up/down tone
  (was concatenated text "资源品工业金属化工")
- A2: `_chain_review.html` strip_prefix macro handles nested + slash-variant
  duplicates (`A股端A股端...` / `下游/A 股下游/A股...`)
- T2: tech "潜在蓄势待发标的池" section removed entirely (low-value empty
  placeholder)
- T4: tech morning news lookback 24h → 36h + keyword expansion (was missing
  prev-trade-day evening news)

**Slot cutoff / SW realtime aggregation**
- New `ifa/families/market/_sw_realtime.py` — synthesizes SW L1/L2 sector
  pct from member rt_k snapshots (TuShare's `rt_min_daily` + `stk_mins`
  reject SW codes); MV-weighted by T-1 `daily_basic.total_mv`
- Slot cutoff coverage extended to macro / asset / tech `fetch_*` (4
  families now uniform: today+noon|evening → realtime, morning/historical
  → EOD with strict staleness gate)

### Added — Research core complete; TA in progress; Stock Intel deferred

**Research family — single-stock financial-statement reports**
- 28 financial factors across 5 families (profitability / growth / cash quality / balance / governance)
- SW L2 peer percentile via `sw_member_monthly` PIT JOIN
- Four delivered report lenses: quarterly quick, annual quick, quarterly deep, annual deep
- Deep reports compare up to 12 quarters / 3 annual reports with YoY and QoQ / prior-year analysis
- LLM watchpoints + cross-cutting tensions (§09) + analyst coverage (§10) + investor concerns (§11)
- Analyst-report PDF extraction cache with key points
- Durable Postgres fundamental memory: `research.period_factor_decomposition`
- Durable PDF memory: `research.pdf_extract_cache`
- Report asset registry and reuse via `research.report_runs` / `research.report_sections`; manual and production share the same reuse pool unless `--fresh` is passed
- Stock Intel / TA integration boundary: `ifa.families.research.memory.load_fundamental_lineup(...)`
- Output: `Stock-Analysis-{ts_code}-{YYYYMMDD}-{analysis_type}-{tier}.html`
- V2.2 defers HTTP API, Telegram, quota, dashboard, and manual golden-set gates to V2.3 / later productionization

**TA family — 晚盘技术面 evening report**
- 9-regime classifier (trend_continuation / early_risk_on / weak_rebound / range_bound / sector_rotation / emotional_climax / distribution_risk / cooldown / high_difficulty) with Laplace-smoothed transition matrix
- 19 candidate setups across 7 families (T/P/R/F/V/S/C)
- T+1/T+3/T+5/T+10/T+30 outcome tracking → `candidate_tracking`
- Rolling 60d/250d `setup_metrics_daily` with decay score + suitable_regimes
- M5.3 regime gating + decay-based suspension (OBSERVATION_ONLY / SUSPENDED)
- Falsifiable next-day hypotheses → `report_judgments` with auto-evaluation
- 11 deterministic + 3 LLM-augmented sections (regime explainer / candidate narrator / strategy review)
- 25-day golden set scaffold + regression script
- Output: `ifa_TA_{slot}_{YYYYMMDD}_{HHMM BJT}.{html,md}`

**Stock Intel family — deferred to V2.3.**

### Added — Cross-cutting

- Auto trade-calendar enforcement (`ifa.core.calendar` + `smartmoney.trade_cal`); all trade-day arithmetic goes through this layer (handles 调休 + multi-day holidays correctly)
- Persistent BJT timezone discipline at every report boundary
- Three new schemas: `research.*`, `ta.*` (+ shared `catalyst_event_memory`)
- `ifa ta` CLI: classify-regime / scan-candidates / track-candidates / compute-metrics / evening-report / backtest / evaluate-judgments / backfill-regime
- `ifa research` CLI: report / peer-scan / batch / scan-* family

### Fixed (research)

- Stock-analysis report tables: dropped factor-code column; only Chinese name shown
- IRM 减持次数 fetcher: now recognizes Tushare `stk_holdertrade in_de='DE'` correctly
- Pledge_stat: distinguishes fetched-empty from not-fetched cases
- IRM Q&A field name (`a` not `reply`); 5194 stocks recomputed: 5154 GREEN / 17 YELLOW / 23 RED

---

## [2.1.3] — 2026-05-03

### Added — Ningbo Phase 1 → 3.D 完整闭环

- **Phase 1**：神枪手 / 聚宝盆 / 半年翻倍三策略实现 + 启发式打分 + 单一 evening 报告 + 15 日追踪
- **Phase 2**：历史回填（2024-01 → 2026-04，~2.8k top-5 推荐 + 41k 追踪行）+ 向量化指标计算（10x 提速）
- **Phase 3.A-C**：ML 训练管线（LR + RF + XGB + LightGBM + CatBoost + 3 rankers + ensemble）on 全候选池（174k → 233k 行），解决样本选择偏差。Kronos 预训练 OHLCV embedding 已实验：8/10 模型加 Kronos 后变差，**已禁用**（季度 refresh 自动重审）
- **Phase 3.D**：Champion-Challenger 双 slot 自动晋升机制
  - `aggressive` slot 优化 Top5_AvgReturn（当前 active: ensemble_meanrank, +2.18% T5_Mean）
  - `conservative` slot 优化 Sharpe（当前 active: xgb_ndcg, Sharpe 0.27, MaxDD -57%）
  - `heuristic` 永远在线作为 baseline
- **★1-★5 共识矩阵**：三轨排名加权打星，前 5 高亮
- **三轨追踪 section**：按日期 click-to-expand（HTML5 `<details>`），每日 top-5 共识 picks 的 sparkline 趋势

### Added — Refresh 自动化框架

- `ifa ningbo refresh weekly`（每周日 22:00 BJT）：训练所有候选模型 + 应用晋升规则
- `ifa ningbo refresh monthly`（每月 1 号）：walk-forward 3 × 60 天 bucket 健康体检
- `ifa ningbo refresh quarterly`（每季 1 号）：重新评估先前被拒的模型族（如 Kronos）
- `ifa ningbo registry status / promote / rollback` — 模型注册表管理
- `ifa ningbo backfill-dual --days N` — 用最新 active 模型重打分历史 N 天的 ml_aggressive / ml_conservative 推荐

### Added — 文档

- `docs/OPERATIONS.md` — iFA 整体运维手册（7 章 + 3 附录），覆盖所有 family 的日/周/月/季节奏
- `docs/ningbo-deep-dive.md` — 宁波派完整架构（Phase 1-3.D 全细节）
- `docs/smartmoney-deep-dive.md` — V2.1.2 SW L2 修复的来龙去脉 + 两层 recompute 体系
- `docs/main-three-aux-deep-dive.md` — 一主三辅协作时序与数据流
- `docs/multi-agent-deployment.md` — 多 agent 平台部署建议（generic prompt + 配置）
- `LICENSE` — MIT License
- `CHANGELOG.md` — 本文件
- `.env.example` — 环境变量模板

### Added — Schema

- `ningbo.candidates_daily` — 全候选池（不限 top-N）
- `ningbo.candidate_outcomes` — 15 日前向标签
- `ningbo.model_registry` — 模型注册表（slot-aware）
- `ningbo.promotion_log` — 晋升日志
- `recommendations_daily.scoring_mode` CHECK 扩展支持 `ml_aggressive` / `ml_conservative`

### Added — Scripts

- `scripts/ningbo_backfill_parallel.py` — 多年并行回填编排
- `scripts/run_v3_training.py` — 9 模型矩阵训练
- `scripts/compare_v3_kronos.py` — Kronos vs 无 Kronos 对比
- `scripts/walk_forward_eval.py` — Walk-forward CV
- `scripts/run_kronos_precompute.py` — Kronos embedding 预计算（已禁用）
- `scripts/rebuild_ensemble_artifact.py` — 紧急修复 ensemble 序列化格式
- `scripts/run_april_last5_reports_timed.sh` — 批量历史报告生成（带 timing）
- `scripts/install_macos.sh` — 一键 macOS 部署脚本

### Fixed

- `add_kdj` 用向量化 EWM 替代 Python 行循环（5,500 stocks × 200 行迭代 → groupby.transform）
- ML scores 入库时归一化到 [0, 1]，避免 PostgreSQL NUMERIC 溢出
- Ningbo evening 的 `disclaimer` section 之前用错 schema（`items` vs `paragraphs_zh`）导致空白，改用共享 disclaimer 模块
- `is_trading_day.py` 的 exit code 语义文档化（0=trading, 1=non-trading, 2=infra error）

### Dependencies

- 新增：`lightgbm`, `catboost`, `torch` (with MPS), `pytorch-tabnet`, `transformers`, `huggingface-hub`, `einops`, `pyarrow`

---

## [2.1.2] — 2026-04 (date approximate)

### Fixed

- SmartMoney factor SQL JOINs 全部从 `l1_code` 改为 L2-with-L1-fallback。修复电子 L1 下 6 个 L2 子板块 pct_change 实际离散度 5.73% 但 ML 模型看到 0% 的严重信号失真问题。
- **必须重跑** `bash scripts/recompute_smartmoney_required.sh` 才能用 V2.1.2+

---

## [2.1.1] — 2026-04 (date approximate)

### Added

- SmartMoney SW L2 daily price ETL（`raw_sw_daily` + `raw_index_daily`）
- 优化 raw backfill：按 code 批量拉（31+8 次 API 而非 39×N_days）

---

## [2.1.0] — 2026-04 (initial SW unification)

### Changed

- 所有板块逻辑（Market 主线、Tech 五层、Asset 商品传导、SmartMoney 板块流）切换到申万（SW）作为主源
- DC（东财概念）和 THS（同花顺）退出主路径（仅作 fallback）
- 修复 `raw_daily.amount` 单位（千元 vs 万元）混用导致的 10× 净流入数字膨胀

### Added

- PDF 输出工具（headless Chrome + print-CSS）
- Run modes: `test` / `manual` / `production`

---

## 版本号约定

- **Major** (X.0.0)：架构级变更（如 SW unification）
- **Minor** (2.X.0)：family 级新功能（如新增 Ningbo）
- **Patch** (2.1.X)：bugfix / 单 family 增强 / 文档更新

每次 release 都打 git tag `vX.Y.Z` 并在 GitHub 创建 release notes。
