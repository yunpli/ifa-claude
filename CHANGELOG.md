# Changelog

All notable changes to iFA China Market Report System.

格式约定：[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)

---

## [2.2.0] — 2026-05-04 (in development)

### Added — Three new families: Research / TA / Stock Intel (planned)

**Research family — single-stock equity research reports**
- 28 financial factors across 5 families (profitability / growth / cash quality / balance / governance)
- SW L2 peer percentile via `sw_member_monthly` PIT JOIN
- Three tiers: quick / standard / deep
- LLM watchpoints + cross-cutting tensions (§09) + analyst coverage (§10) + investor concerns (§11)
- 30-stock golden set + regression script (4 metrics, all gating thresholds passing)
- Output: `Stock-Analysis-{ts_code}-{YYYYMMDD}[-{tier}].html`

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
