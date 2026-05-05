# Stock Edge v2.2 调参治理 Handover — 2026-05-05

> 接手对象：继续 review / 调参 / 训练 / promotion 的下一个 agent。  
> 当前代码提交：`9b4b4a0` (`Refactor Stock Edge tuning governance`) 已推送 `origin/main`。  
> 本文是当前最新 handover，旧版 `docs/stock_edge_v2_2_tuning_handover_2026_05_05.md` 中关于 “global preset 未进入 runtime / objective 仍由 40d 主导” 的风险已被本次提交修正。

## 1. 当前任务结论

这轮完成的是 Stock Edge v2.2 **调参体系治理重构**，不是继续做报告 UI，也不是正式大规模调参。

核心结论：

1. YAML 仍是 baseline 和搜索边界。
2. Global tuning 可以继续先产出 JSON artifact 做实验审计。
3. 经验证的 global preset 现在有明确晋升路径：生成可 review YAML patch，人工确认后 apply，支持 backup / rollback。
4. Single-stock pre-report overlay 仍是 runtime-only 局部适配，永远不写回 YAML baseline。
5. 报告运行参数路径已修正为：

```text
stock_edge_v2.2.yaml baseline
  -> compatible fresh global_preset artifact
  -> compatible fresh / newly fitted single-stock pre_report_overlay
  -> runtime params
```

6. 主 objective 已从 legacy 40d 口径切换为 `stock_edge_5_10_20_v1`。
7. 40d 只允许作为 `legacy_40d_audit`，不进入主 composite objective，也不应进入用户主决策。

## 2. 接手前必读文件

| 文件 | 作用 |
|---|---|
| `docs/stock_edge_v2_2_tuning_architecture_review.md` | 调参架构 review，总结 global / single overlay 治理边界 |
| `docs/stock_edge_v2_2_5_10_20_objective_refactor.md` | 5d / 10d / 20d objective 输入、输出、metrics、artifact 结构 |
| `docs/stock_edge_v2_2_global_preset_promotion.md` | global preset 到 YAML baseline 的 promotion 机制 |
| `docs/stock_edge_v2_2_strategy_tuning_coverage.md` | 85 个策略/模型的 tuning coverage 表 |
| `docs/stock_edge_v2_2_tuning_runtime_handoff.md` | 本轮 runtime 改动的短摘要 |
| `docs/stock_edge_v2_2_decision_layer_handoff.md` | 三周期 decision layer 的实现 handoff |
| `docs/stock_edge_v2_2_backfill_execution_recommendation.md` | intraday backfill 边界与推荐命令 |

## 3. 关键代码位置

| 模块 | 文件 | 接手重点 |
|---|---|---|
| Objective | `ifa/families/stock/backtest/objectives.py` | `OBJECTIVE_VERSION = stock_edge_5_10_20_v1`；5/10/20 horizon scoring；40d 只 audit |
| Optimizer | `ifa/families/stock/backtest/optimizer.py` | `_evaluate_overlay()`、`_search_overlay()`、progress callback、artifact metrics |
| Runtime params | `ifa/families/stock/backtest/report_runtime.py` | YAML -> global -> single 的叠加顺序；hash / TTL 兼容检查 |
| Artifact schema | `ifa/families/stock/backtest/tuning_artifact.py` | `objective_version`；旧 artifact 兼容读取 |
| Overlay apply | `ifa/families/stock/params/overlay.py` | `_runtime_tuning` 同时记录 global / single artifact |
| Promotion | `ifa/families/stock/backtest/promotion.py` | build / emit / apply promotion patch；拒绝 single overlay 晋升 |
| Baseline config | `ifa/families/stock/params/stock_edge_v2.2.yaml` | tuning objective / search bounds / promotion 允许参数基线 |
| Global CLI | `scripts/stock_edge_global_preset.py` | global dry-run /正式 artifact；progress；日志 |
| Single CLI | `scripts/stock_edge_pre_report_overlay.py` | 单股 overlay；progress；日志 |
| Promote CLI | `scripts/stock_edge_promote_global_preset.py` | emit/apply YAML patch |
| Tests | `tests/stock/test_pre_report_tuning.py` | runtime overlay、objective、promotion、TTL/hash、progress 覆盖 |
| Tests | `tests/stock/test_strategy_matrix.py` | YAML objective 结构校验 |

## 4. 本轮具体实现

### 4.1 Global + single 两层 overlay

`prepare_report_params()` 现在会：

1. 加载 `stock_edge_v2.2.yaml`。
2. 查找 `global_preset/__GLOBAL__` 最新 artifact。
3. 检查：
   - `kind == "global_preset"`
   - `base_param_hash == params_hash(YAML baseline)`
   - TTL 未过期
   - tuning enabled
4. 如果兼容，先 apply global overlay。
5. 再用 global-overlaid params 作为 base，查找/生成目标股 `pre_report_overlay`。
6. 检查 single overlay 的 hash / TTL。
7. single overlay 覆盖 global overlay。
8. `_runtime_tuning` 同时记录 global 与 single。

重要口径：

- single overlay 不允许晋升 YAML。
- single overlay 的 hash 应基于 `params_after_global`，不是裸 YAML。
- 如果 YAML 改动导致 hash 变化，旧 global / single artifacts 都会自然失效，需要重跑。

### 4.2 Objective 重构

主 objective 输出结构：

```json
{
  "objective_version": "stock_edge_5_10_20_v1",
  "objective_5d": {},
  "objective_10d": {},
  "objective_20d": {},
  "composite_objective": {},
  "legacy_40d_audit": {}
}
```

5d 重点：

- T+5 正收益
- entry fill quality
- gap/open risk
- max drawdown
- stop-first
- slippage/liquidity
- short-term risk-adjusted return

10d 重点：

- 10d return
- target-first / stop-first
- moneyflow persistence
- sector persistence
- peer relative strength
- one-day-wonder / overheat penalty

20d 重点：

- 20d return
- MFE/MAE
- drawdown
- trend quality
- sector tailwind
- position sizing efficiency
- strategy decay
- Research/Fundamental 只作为辅助风险，不作为主 alpha 轴

`score_prediction_objective()` 不再读取 40d 作为主评分，40d 只留在 `legacy_40d_audit`。

### 4.3 Global promotion

新增命令：

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /path/to/global_preset.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --emit-patch
```

这会生成：

- `yaml_patch_candidate.yaml`
- `yaml_patch_candidate.md`

默认输出到：

```text
/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/<timestamp>/
```

人工 review 后才允许：

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /path/to/global_preset.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --apply \
  --backup
```

安全规则：

- 默认不改 YAML。
- 只接受 `kind=global_preset`。
- 拒绝 `pre_report_overlay`。
- patch 包含 old / new / delta / source artifact / objective improvement / validation metrics。
- apply 会生成 backup。
- 允许输出 YAML variant，不一定直接覆盖原 baseline。

### 4.4 Progress / observability

`_search_overlay()` 支持 progress callback。CLI 每 N 个候选输出：

- candidate index
- total
- score
- best_score
- elapsed
- ETA

Global / single CLI 都支持：

```bash
--progress-every N
```

日志路径：

```text
/Users/neoclaw/claude/ifaenv/logs/stock_edge_tuning/
```

## 5. Smoke 与测试结果

### 5.1 单元测试

命令：

```bash
uv run pytest tests/stock -q
```

结果：

```text
68 passed
```

### 5.2 编译检查

命令：

```bash
uv run python -m py_compile \
  ifa/families/stock/backtest/objectives.py \
  ifa/families/stock/backtest/optimizer.py \
  ifa/families/stock/backtest/report_runtime.py \
  ifa/families/stock/backtest/promotion.py \
  scripts/stock_edge_global_preset.py \
  scripts/stock_edge_pre_report_overlay.py \
  scripts/stock_edge_promote_global_preset.py
```

结果：通过。

### 5.3 Global dry-run smoke

命令：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_global_preset.py \
  --as-of 2026-04-30 \
  --limit 10 \
  --max-candidates 3 \
  --progress-every 1 \
  --dry-run \
  --no-backfill-short-history
```

结果：

- 10 stocks
- 3 candidates
- candidate progress 正常输出
- best score: `0.2731`
- dry-run 未写 artifact

### 5.4 Promotion emit smoke

命令：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /Users/neoclaw/claude/ifaenv/models/stock/tuning/global_preset/__GLOBAL__/20260430.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --emit-patch \
  --output-dir /Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/smoke_20260505
```

输出：

```text
/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/smoke_20260505/yaml_patch_candidate.yaml
/Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/smoke_20260505/yaml_patch_candidate.md
```

注意：这个 smoke 用的是旧 global artifact 验证 promotion 工具，不代表推荐晋升旧 artifact。

## 6. 当前 artifact 兼容性口径

本轮修改了 YAML 和 objective，因此旧 artifact 多半会因为 hash / objective_version 不兼容而不进入 runtime。

这是预期行为，不是 bug。

接下来如果要让 global preset 真正进入报告运行，需要重新跑新 objective 下的 global tuning：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_global_preset.py \
  --as-of 2026-04-30 \
  --limit 500 \
  --max-candidates 256 \
  --progress-every 8 \
  --no-backfill-short-history
```

建议先跑小规模：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_global_preset.py \
  --as-of 2026-04-30 \
  --limit 100 \
  --max-candidates 32 \
  --progress-every 4 \
  --dry-run \
  --no-backfill-short-history
```

## 7. 下一位 agent 的推荐工作顺序

### Step 1：确认 repo 状态

```bash
git pull --ff-only origin main
git log --oneline -5
uv run pytest tests/stock -q
```

确认包含：

```text
9b4b4a0 Refactor Stock Edge tuning governance
```

### Step 2：跑小规模 objective smoke

先不要 Top500 大跑。用 Top100 × 32 dry-run 看分布：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_global_preset.py \
  --as-of 2026-04-30 \
  --limit 100 \
  --max-candidates 32 \
  --progress-every 4 \
  --dry-run \
  --no-backfill-short-history
```

检查：

- progress 是否正常
- objective_5d / 10d / 20d 是否都有非空 metrics
- score 是否不是全挤在一个窄区间
- top changed params 是否合理

### Step 3：正式 global coarse search

建议第一轮：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_global_preset.py \
  --as-of 2026-04-30 \
  --limit 500 \
  --max-candidates 256 \
  --progress-every 8 \
  --no-backfill-short-history
```

不要一上来就做极大搜索。先看 objective 稳定性、runtime、artifact 内容。

### Step 4：生成 promotion patch，不直接 apply

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /Users/neoclaw/claude/ifaenv/models/stock/tuning/global_preset/__GLOBAL__/20260430.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --emit-patch
```

人工 review：

- 是否只是允许晋升的参数路径
- 是否 objective improvement 足够
- 5/10/20 是否没有一个周期被牺牲
- risk 参数是否过于激进
- right-tail / 40d legacy 是否没有进入主评价

### Step 5：通过后再 apply 或生成 variant

推荐先生成 variant，而不是直接覆盖 baseline：

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /path/to/new_global_preset.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --apply \
  --variant-output /Users/neoclaw/claude/ifaenv/manifests/stock_edge_global_promotion/stock_edge_v2.2.202605_global.yaml
```

如果决定覆盖 repo baseline：

```bash
uv run python scripts/stock_edge_promote_global_preset.py \
  --artifact /path/to/new_global_preset.json \
  --base-yaml ifa/families/stock/params/stock_edge_v2.2.yaml \
  --apply \
  --backup
```

覆盖后必须：

```bash
uv run pytest tests/stock -q
```

### Step 6：单股 overlay smoke

以朗科科技为例：

```bash
PYTHONUNBUFFERED=1 uv run python scripts/stock_edge_pre_report_overlay.py \
  300042.SZ \
  --as-of 2026-04-30 \
  --max-candidates 64 \
  --progress-every 8
```

确认 single overlay 的 base hash 是 global-overlaid params，而不是裸 YAML。

## 8. 需要继续 review 的风险点

| 风险 | 当前状态 | 建议 |
|---|---|---|
| Objective 未经过 OOS 验证 | 已重构但未正式验证 | Top100 smoke -> Top500 coarse -> OOS |
| 搜索仍是随机搜索 | 支持 progress，但搜索器还不够机构级 | 后续引入 Optuna / successive halving / coarse-to-fine |
| 85 策略不是都训练内部超参 | 当前多数是权重/阈值 overlay | 把 ML/DL 类单独做校准/训练计划 |
| 旧 artifact hash 失效 | 预期行为 | 用新 objective 重跑 global preset |
| YAML promotion 需人工审查 | 已实现，不自动静默 apply | 保持人工 gate |
| 单股 overlay 过拟合 | TTL + small continuous overlay，但仍有风险 | 限幅、样本数门槛、OOS/bootstrapping |
| 概率未校准 | 报告必须提示 | 后续做 Platt / isotonic / reliability |
| intraday 数据只 Top500 × 180d | 足够当前 v2.2 smoke | 扩 universe 前仍控制预算 |

## 9. 不要做的事

1. 不要把 single-stock overlay 写回 YAML。
2. 不要静默自动改 `stock_edge_v2.2.yaml`。
3. 不要把旧 40d 重新放入主 objective 或用户主报告。
4. 不要把未校准 score 写成确定性上涨概率。
5. 不要把 tuning artifact commit 到 repo。
6. 不要打印 token。
7. 不要绕过现有数据入口另写 Tushare client。
8. 不要在没跑 smoke 的情况下直接 Top500 × 大候选数长跑。

## 10. 当前推荐的生产参数治理流程

```text
1. YAML baseline
   |
2. global tuning experiment
   -> JSON artifact
   -> metrics / OOS / review
   |
3. promotion emit
   -> yaml_patch_candidate.md
   -> yaml_patch_candidate.yaml
   |
4. human review
   |
5a. apply to baseline + backup
   or
5b. generate explicit YAML variant
   |
6. tests + report smoke
   |
7. runtime:
   YAML baseline / variant
     -> optional global artifact if not yet promoted
     -> single-stock overlay
```

这个流程符合当前用户口径：global tuning 不能永远只是 JSON overlay；实验可以 overlay，验证通过后必须有可审计 YAML promotion。

## 11. 本轮文件变更摘要

新增：

- `ifa/families/stock/backtest/promotion.py`
- `scripts/stock_edge_promote_global_preset.py`
- `docs/stock_edge_v2_2_tuning_architecture_review.md`
- `docs/stock_edge_v2_2_5_10_20_objective_refactor.md`
- `docs/stock_edge_v2_2_global_preset_promotion.md`
- `docs/stock_edge_v2_2_strategy_tuning_coverage.md`
- `docs/stock_edge_v2_2_tuning_runtime_handoff.md`
- `docs/stock_edge_v2_2_tuning_governance_handover_2026_05_05.md`

修改：

- `AGENTS.md`
- `ifa/families/stock/backtest/__init__.py`
- `ifa/families/stock/backtest/objectives.py`
- `ifa/families/stock/backtest/optimizer.py`
- `ifa/families/stock/backtest/report_runtime.py`
- `ifa/families/stock/backtest/tuning_artifact.py`
- `ifa/families/stock/params/overlay.py`
- `ifa/families/stock/params/stock_edge_v2.2.yaml`
- `scripts/stock_edge_global_preset.py`
- `scripts/stock_edge_pre_report_overlay.py`
- `tests/stock/test_pre_report_tuning.py`
- `tests/stock/test_strategy_matrix.py`

## 12. 最短接手路径

如果下一位 agent 只有 15 分钟，请按这个顺序：

1. 读本文。
2. 跑 `uv run pytest tests/stock -q`。
3. 读 `ifa/families/stock/backtest/report_runtime.py` 的 `prepare_report_params()`。
4. 读 `ifa/families/stock/backtest/objectives.py`。
5. 跑 Top100 × 32 dry-run。
6. 检查 artifact metrics 中的 `objective_5d/10d/20d/composite_objective`。
7. 再决定是否进入 Top500 coarse search。

