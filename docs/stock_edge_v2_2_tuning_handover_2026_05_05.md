# Stock Edge v2.2 调参 Handover — 2026-05-05

> 目的：给下一位 agent/reviewer 快速接手 Stock Edge v2.2 的数据补足、全局 preset、单股 pre-report overlay、报告运行时参数叠加逻辑，重点 review “调参是否正确服务 5/10/20 交易日 decision layer”。

## 1. 先回答核心问题

这轮“全局调参 / 单股调参”**没有自动修改 YAML 文件**。

当前机制是：

1. `ifa/families/stock/params/stock_edge_v2.2.yaml` 是 baseline 参数、搜索边界、TTL、阈值和权重配置。
2. 调参脚本读取 YAML，生成连续参数候选 overlay。
3. optimizer 在历史日线样本上评估候选 overlay。
4. 最优 overlay 写入 `/Users/neoclaw/claude/ifaenv/models/stock/tuning/**.json`。
5. 报告运行时通过 `apply_param_overlay()` 把 JSON overlay 叠加到 YAML 加载出的 params 上。
6. YAML 本身不会被调参脚本原地改写。

所以目前不是“训练后改 YAML”，而是“YAML baseline + JSON tuning artifact overlay”。这更适合审计、回滚和多版本并存，但 reviewer 需要重点检查 artifact 选择和叠加顺序。

## 2. 已 commit/push 状态

当前实现主提交已经推到 `origin/main`：

- commit: `5a578a6`
- message: `Implement Stock Edge v2.2 decision layer`

该提交包含：

- Stock Edge v2.2 5/10/20 三周期 decision layer。
- 85 个策略/模型/分析器的策略矩阵框架。
- 三周期报告 section、落库、HTML/MD 最小展示。
- 数据补足脚本。
- 全局 preset 与单股 overlay 调参脚本。
- Stock Edge 测试集。

本 handover 文档是后续补充提交。

## 3. 数据补足结果

执行过的受控 intraday backfill：

```bash
uv run python scripts/stock_edge_data_backfill.py \
  --universe top-liquidity \
  --limit 500 \
  --family intraday \
  --intraday-days 180 \
  --execute \
  --resume \
  --run-id stockedge_top500_180d_20260505
```

结果：

| 数据 | 行数 | 股票数 | 日期范围 | 重复键 |
|---|---:|---:|---|---:|
| intraday_5min | 5,059,512 | 500 | 2025-08-25 → 2026-04-30 | 0 |
| intraday_30min | 722,946 | 500 | 2025-08-25 → 2026-04-30 | 0 |
| intraday_60min | 402,160 | 500 | 2025-08-25 → 2026-04-30 | 0 |

位置：

- Parquet: `/Users/neoclaw/claude/ifaenv/duckdb/parquet`
- Manifest: `/Users/neoclaw/claude/ifaenv/manifests/stock_edge_data_backfill/stockedge_top500_180d_20260505/`
- Log: `/Users/neoclaw/claude/ifaenv/logs/stock_edge_data_backfill/stockedge_top500_180d_20260505.log`

空间约 `271M`，低于 10GB 预算。

## 4. 调参脚本与产物

### 4.1 全局 preset

执行命令：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_global_preset.py \
  --as-of 2026-04-30 \
  --limit 500 \
  --max-candidates 96 \
  --no-backfill-short-history
```

说明：

- `--max-candidates 96` 可理解为本次候选 iteration 数，包含 baseline `{}` 和 95 个随机连续 overlay。
- 随机搜索使用 deterministic seed：`{universe}:{as_of}:global`。
- `--no-backfill-short-history` 是本轮新增，避免 Top500 中少量短历史新股触发 TuShare 回补后拖住 overnight preset。
- 本次跳过 11 只短历史股票，但 `fit_global_preset()` 的 metrics 仍显示 `stock_count=500`，reviewer 应检查这里是否应改为有效样本股票数。

产物：

```text
/Users/neoclaw/claude/ifaenv/models/stock/tuning/global_preset/__GLOBAL__/20260430.json
```

结果摘要：

| 字段 | 值 |
|---|---:|
| objective_score | 0.207596 |
| candidate_count | 96 |
| history_rows | 435,684 |
| sample_count | 24,287 |
| fill_rate_5d | 0.665829 |
| clean_fill_rate_5d | 0.325195 |
| stop_first_rate | 0.340635 |
| overlay 参数数 | 59 |

### 4.2 单股 pre-report overlay

执行命令：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_pre_report_overlay.py \
  300042.SZ \
  --as-of 2026-04-30 \
  --max-candidates 64
```

说明：

- `--max-candidates 64` 是单股候选 iteration 数，包含 baseline `{}` 和 63 个随机连续 overlay。
- 随机搜索使用 deterministic seed：`{ts_code}:{as_of}:overlay`。
- 样本只用目标股历史日线，最多 900 根。

产物：

```text
/Users/neoclaw/claude/ifaenv/models/stock/tuning/pre_report_overlay/300042_SZ/20260430.json
```

结果摘要：

| 字段 | 值 |
|---|---:|
| objective_score | 0.302342 |
| candidate_count | 64 |
| history_rows | 900 |
| sample_count | 39 |
| fill_rate_5d | 0.820513 |
| clean_fill_rate_5d | 0.358974 |
| stop_first_rate | 0.461538 |
| overlay 参数数 | 59 |

## 5. 参数 overlay 如何进入报告

关键代码：

- `ifa/families/stock/backtest/report_runtime.py`
- `ifa/families/stock/params/overlay.py`
- `ifa/families/stock/backtest/tuning_artifact.py`

运行时路径：

1. `run_stock_edge_report()` 调用 `prepare_report_params(request, engine=engine)`。
2. `prepare_report_params()` 加载 YAML baseline。
3. 查找同股票最新 `pre_report_overlay` artifact。
4. 如果 artifact 与当前 YAML hash 兼容且 TTL 未过期，则复用。
5. 否则报告生成前跑 `fit_pre_report_overlay()` 并写 JSON。
6. `_with_artifact()` 调用 `apply_param_overlay()`，把 JSON overlay 叠加到 params。
7. `attach_tuning_runtime()` 在 params 中写 `_runtime_tuning`，用于审计。

重要细节：

- overlay key 是 dotted key，例如 `aggregate.buy_threshold`、`cluster_weights.trend_breakout`、`risk.right_tail_target_pct`。
- `risk/t0/model/runtime/data/intraday/cache/report/tuning` 等 prefix 会写到 YAML 顶层对应路径。
- 其他 key 默认写到 `strategy_matrix.*` 下。
- artifact 不进入 git，属于本地运行输出。

## 6. 需要重点 review 的问题

### 6.1 全局 preset artifact 当前没有自动进入 pre-report overlay 的起点

`pre_report_tuning.py` docstring 写着“单股 overlay starts from latest global preset”，但当前 `prepare_report_params()` 实际只查目标股 `pre_report_overlay`，没有读取 `global_preset/__GLOBAL__` 并先叠加。

这意味着：

- 全局 preset 已生成，但当前报告运行时不一定自动用它。
- 单股 overlay 仍是从 YAML baseline 搜索。
- reviewer 应决定：是否实现 `latest global preset -> apply -> then per-stock overlay` 的两层叠加。

建议修复方向：

1. 在 `prepare_report_params()` 中先查 `kind="global_preset", ts_code="__GLOBAL__"`。
2. 若 hash 兼容、TTL 合法，先 apply global overlay。
3. 再查/生成单股 overlay。
4. `_runtime_tuning` 记录 global artifact 与 single-stock artifact 两个路径。

### 6.2 optimizer 目标函数仍带 40d 遗留口径

虽然用户主报告已经切到 5/10/20 交易日 decision layer，但当前调参 optimizer 的 objective 仍有以下字段：

- `hit_target_40d_quality`
- `expected_return_40d`
- `hit_40d_rate`
- `avg_return_40d`
- `avg_drawdown_40d`

位置：

- `ifa/families/stock/backtest/optimizer.py`
- `ifa/families/stock/backtest/objectives.py`

这不等于报告主路径继续展示 40d；报告主 section 已修正为不含 legacy 40d。但调参目标还没有完全重构为 5d/10d/20d horizon-specific objective。

这是下一轮调参 review 的最重要问题。

建议：

- 保留旧 40d metrics 作为 audit 或删除。
- 新增 `objective_5d` / `objective_10d` / `objective_20d`。
- 全局 preset 可以优化三周期综合目标。
- 单股 overlay 应输出三周期 metrics，不要只对 40d path 做搜索。

### 6.3 候选搜索无候选级进度

Top500 × 96 本次耗时约 50+ 分钟，期间 CPU 正常，但 stdout 没有候选级进度。

建议：

- 给 `_search_overlay()` 增加可选 `on_progress(candidate_idx, total, best_score)`。
- CLI 每 N 个候选 flush 一行。
- artifact 写入前后输出 best overlay top changes。

### 6.4 当前不是完整 ML 模型训练

本轮所谓“训练/调参”是参数 overlay 搜索，不是训练持久化 RandomForest/XGB/深度模型。

已有 ML/DL 相关策略模块和 SmartMoney/Ningbo/Kronos 复用入口，但 Stock Edge 自身还没有独立持久化 ML model artifact，例如 `.pkl` / `.json params` / calibrated probability model。

下一位 agent review 时不要误以为全局 preset JSON 是完整模型训练产物。

## 7. 验证命令与结果

测试：

```bash
uv run pytest tests/stock -q
```

结果：

```text
62 passed
```

报告 smoke：

```bash
PYTHONUNBUFFERED=1 uv run python -m ifa.cli stock report \
  300042.SZ \
  --mode quick \
  --run-mode manual \
  --requested-at 2026-04-30T15:30:00 \
  --fresh
```

输出：

```text
/Users/neoclaw/claude/ifaenv/out/manual/20260430/stock_edge/CN_stock_edge_300042_SZ_20260430_031725.html
/Users/neoclaw/claude/ifaenv/out/manual/20260430/stock_edge/CN_stock_edge_300042_SZ_20260430_031725.md
```

落库校验：

- `stock.analysis_record.forecast_json.decision_layer` 包含 `decision_5d` / `decision_10d` / `decision_20d`。
- `stock.report_sections.01_decision_layer` 只含三周期主决策，不含 `legacy_40d_audit`。
- `stock.report_sections.05_legacy_trade_plan_audit` 保留 legacy 40d audit。

## 8. 下一位 agent 建议工作顺序

1. 先 review `prepare_report_params()`，确认是否要启用 global preset -> single overlay 两层叠加。
2. 重构 optimizer/objectives，把 40d legacy objective 改为 5/10/20 horizon-specific objective。
3. 给全局 preset 增加进度输出和有效股票数 metrics。
4. 增加 artifact schema/version 字段，区分 `global_preset_v1`、`pre_report_overlay_v1`。
5. 增加测试覆盖：
   - global preset artifact 被读取并叠加；
   - single overlay 覆盖 global overlay；
   - base YAML hash 不兼容时 artifact 不复用；
   - 5/10/20 objective metrics 写入 artifact。
6. 再跑小规模 smoke：Top100 × 12 dry-run。
7. 再跑 Top500 × 96 正式 preset。
8. 最后再生成 fresh report，检查 `_runtime_tuning` 同时记录 global + single artifact。

## 9. 不要做的事

- 不要让调参脚本直接改写 `stock_edge_v2.2.yaml`。
- 不要把 tuning artifact commit 到 repo。
- 不要把 40d 重新放进用户主决策 section。
- 不要绕过 `ifa.core.tushare` / 现有 data gateway 新写 token/client。
- 不要在日志或文档里打印 token。
- 不要用外部大模型直接解释/改数值；需要 LLM 时使用项目内 `LLMClient`。

