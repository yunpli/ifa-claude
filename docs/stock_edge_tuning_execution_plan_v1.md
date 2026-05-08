# Stock Edge 调参执行计划 v1

## 当前状态与未提交变更含义

截至本计划编写时，仓库已有未提交变更：

- `ifa/cli/stock.py`
- `scripts/stock_edge_panel_tune.py`

这些变更不是本次计划文件引入的代码改动；本次执行只允许新增本文档，并运行一次最小可行 dry-run。现有变更的含义按当前代码行为理解为：

- `scripts/stock_edge_panel_tune.py` 是当前生产对齐的 Stock Edge panel tuner：基于真实 `compute_strategy_matrix` 构建 PIT replay panel，缓存 panel 后在 `decision_layer.horizons.*` 权重/阈值空间搜索。
- 该脚本已支持 `--dry-run`、`--k-fold`、`--successive-halving`、`--search-algo tpe`、`--bootstrap-iterations`、`--liquidity-offset`、`--include-llm`、`--auto-promote`、`--apply-to-baseline` 等执行开关。
- `ifa/cli/stock.py` 中已有 Stock Edge tuning CLI 包装逻辑，但本计划的首跑使用脚本直连命令，避免 Typer 默认值或包装参数改变首跑语义。
- 当前 baseline YAML 是 `ifa/families/stock/params/stock_edge_v2.2.yaml`。本次 dry-run 不传 `--auto-promote`，不传 `--apply-to-baseline`，因此不应写 artifact，也不应写 YAML variant 或覆盖 baseline。

## 专家默认决策表

| 决策项 | v1 默认 | 理由 | 首跑设置 |
|---|---:|---|---|
| `as_of` | 自动推断 `smartmoney.raw_daily` 最大 `trade_date` | 避免手写日期与本地数据不一致 | 不传 `--as-of` |
| Top N | 50 | 首跑控制面板规模与运行时间 | `--top 50` |
| PIT samples | 10 | 足够形成 3-fold 小样本验证，同时仍是最小可行 | `--pit-samples 10` |
| K-fold | 3 | 比单 split 更能暴露过拟合，成本仍可控 | `--k-fold 3` |
| Val dates per fold | 2 | 每折验证 2 个 PIT 日期，配合 10 samples 可运行 | `--val-dates-per-fold 2` |
| Min train dates | 3 | 首折训练样本不至于过小 | `--min-train-dates 3` |
| OOS | 首跑不用单独 `--oos` | K-fold 已承担 OOS 角色，避免双重模式混淆 | 不传 `--oos` |
| OOC | 首跑不用 OOC | OOC 是扩展验证，不进入首个 dry-run | `--liquidity-offset 0` 隐式默认 |
| Liquidity offset | 0 | 训练生产 top-liquidity cohort | 不传 `--liquidity-offset` |
| Max candidates | 96 | 最小可行搜索预算，配合 successive halving | `--max-candidates 96` |
| Workers | 4 | 控制本地 DB 与 CPU 压力 | `--workers 4` |
| LLM | 关闭 | 首跑验证调参管线与结构，不引入 LLM 延迟/不稳定性 | 不传 `--include-llm` |
| Negative weights | 开启 | 当前脚本默认允许反向信号权重，保留专家搜索空间 | 不传 `--no-negative-weights` |
| Search algo | TPE | 在小预算下优于纯 random 的搜索效率 | `--search-algo tpe` |
| Successive halving | 开启 | 先粗后细，降低无效候选成本 | `--successive-halving` |
| Bootstrap | 300 | 首跑低成本统计检查；正式扩展再升至 1000+ | `--bootstrap-iterations 300` |
| Apply baseline | 禁止 | baseline 只在人工复核后通过 promotion path 改动 | 不传 `--apply-to-baseline` |
| Promotion policy | 首跑只观察，不晋升 | dry-run 不写 artifact；promotion 需独立审查 | 不传 `--auto-promote` |

## Step-by-step execution plan

### Step 0：仓库与接口确认

- Purpose：确认当前工作区、未提交变更、脚本参数与 baseline 位置。
- Proposed command：

```bash
pwd
git status --short
rg -n "ArgumentParser|add_argument|auto-promote|apply-to-baseline|dry-run|k-fold|bootstrap" scripts/stock_edge_panel_tune.py
```

- Inputs：本地 repo 文件、Git index。
- Outputs：dirty file 列表、脚本支持的参数清单。
- Acceptance criteria：确认脚本存在并支持首跑命令所需 flags；明确已有 dirty files 与本次允许变更边界。
- Stop condition：脚本缺失、参数不兼容、baseline 文件不存在。
- Rollback：无写操作，无需 rollback。

### Step 1：写入执行计划

- Purpose：把调参策略、首跑命令、扩展与审查标准固化为可复核 Markdown。
- Proposed command：使用 patch 新增 `docs/stock_edge_tuning_execution_plan_v1.md`。
- Inputs：当前脚本参数、现有 tuning 文档、用户约束。
- Outputs：本文档。
- Acceptance criteria：只新增一个 Markdown 文件；不改代码；不改 baseline YAML。
- Stop condition：工作区不允许写入，或目标文件已存在且需要覆盖。
- Rollback：删除本次新增计划文件即可；不涉及数据或 baseline。

### Step 2：首个最小可行 dry-run

- Purpose：验证 Stock Edge panel 构建、cache 复用/写入、K-fold 搜索与 dry-run 安全边界。
- Proposed command：

```bash
uv run python scripts/stock_edge_panel_tune.py --top 50 --pit-samples 10 --max-candidates 96 --workers 4 --k-fold 3 --val-dates-per-fold 2 --min-train-dates 3 --bootstrap-iterations 300 --search-algo tpe --successive-halving --dry-run
```

- Inputs：本地 DB `smartmoney.raw_daily`、`smartmoney.trade_cal`、Stock Edge baseline YAML、生产 strategy matrix、panel cache root。
- Outputs：stdout tuning summary；可能创建或复用 replay panel cache：
  - `/Users/neoclaw/claude/ifaenv/data/stock/replay_panels/*.parquet`
  - `/Users/neoclaw/claude/ifaenv/data/stock/replay_panels/*.manifest.json`
- Acceptance criteria：
  - 命令 exit code 为 0。
  - 输出显示 `dry_run: True`。
  - 输出显示 `[dry-run] artifact NOT written`。
  - 未出现 `Auto-Promotion Gates`。
  - `ifa/families/stock/params/stock_edge_v2.2.yaml` 无 diff。
- Stop condition：
  - 本地 DB 缺数据导致无法推断 `as_of` 或 universe 为空。
  - K-fold 数据不足并退化到单 split 时，需要在总结中标记首跑验证强度不足。
  - 命令意外尝试 promotion、variant、baseline 写入时立即停止后续操作。
- Rollback：
  - dry-run 不写 tuning artifact，不写 baseline。
  - 如需清理 cache，仅可在后续用户明确授权后删除本次新建 panel cache；本轮不执行删除。

### Step 3：结果记录与自审

- Purpose：把首跑输出转化为下一轮可决策信息。
- Proposed command：

```bash
git status --short
git diff -- ifa/families/stock/params/stock_edge_v2.2.yaml
find /Users/neoclaw/claude/ifaenv/data/stock/replay_panels -maxdepth 1 -type f -mtime -1
```

- Inputs：Git 状态、baseline YAML、panel cache 目录。
- Outputs：baseline untouched 证明、cache/artifact 列表、dry-run 指标摘要。
- Acceptance criteria：报告包含 exact command、baseline 状态、artifacts/cache、tuning summary、自审与下一步建议。
- Stop condition：发现 baseline 或代码发生非预期改动。
- Rollback：若只新增计划文件，则无需 rollback；若发现非预期改动，先报告，不自行 revert 用户已有变更。

## First minimal viable dry-run command

```bash
uv run python scripts/stock_edge_panel_tune.py --top 50 --pit-samples 10 --max-candidates 96 --workers 4 --k-fold 3 --val-dates-per-fold 2 --min-train-dates 3 --bootstrap-iterations 300 --search-algo tpe --successive-halving --dry-run
```

不调整该命令，除非实际仓库兼容性要求必须调整。任何调整都必须在执行总结中说明原因。

## Expansion logic

首跑通过后，扩展必须逐级推进，且每一级都保持先 dry-run、后审查、再决定是否进入下一步：

1. **Cache reuse rerun**：同参数重跑一次，确认 panel cache 命中、搜索结果结构稳定。仍不 promotion。
2. **统计增强**：保持 `top 50`，把 `--bootstrap-iterations` 提到 1000 或 2000，确认 G5/CI 稳定性。
3. **样本增强**：扩大到 `--pit-samples 16~24`、`--k-fold 4`、`--val-dates-per-fold 2~3`，观察 fold 一致性。
4. **Universe 扩展**：逐步到 top 100、top 200、top 500；每步保留 K-fold 与 dry-run。
5. **OOC 验证**：使用 `--liquidity-offset 100` 或更高 offset 跑非训练 liquidity cohort，只做验证，不用于直接晋升。
6. **Promotion 候选生成**：只有在 top liquidity 与 OOC 都稳定、主助手二审通过后，才考虑独立 promotion run。promotion run 也应优先生成 variant，不直接 `--apply-to-baseline`。
7. **Baseline 覆盖**：只有在人工明确授权、variant diff 审查通过、回归/报告 smoke 通过后，才允许覆盖 baseline。

## Codex per-run self-review template

每次调参后 Codex 必须记录：

- Run ID / 时间 / 当前 commit 或 dirty 状态。
- Exact command。
- 是否使用 dry-run；是否出现 forbidden flags。
- `as_of`、universe size、liquidity offset、PIT dates。
- Panel rows：期望行数、实际行数、失败数、cache path、是否 cache hit。
- K-fold 拆分：每折 train/val 日期与行数。
- 每折 5d/10d/20d baseline rank IC、tuned rank IC、lift。
- Aggregate：median lift、positive folds、IC range。
- 搜索预算：max candidates、实际 candidate count、search algo、successive halving、negative weights 状态。
- Artifact/YAML：是否写 artifact、是否写 variant、baseline 是否 untouched。
- 异常：warnings、fallback、数据缺口、DB/权限问题。
- 自评：
  - 是否满足用户边界？
  - 是否有数据泄漏风险？
  - 是否有过拟合迹象？
  - 哪个 horizon 值得扩展？
  - 哪个 horizon 应保留 baseline？
  - 下一步是否应扩样、OOC、或停止？

## Main assistant second-opinion checklist

主助手二审时至少检查：

- 命令是否严格等于授权命令，或调整理由是否充分。
- 是否未传 `--auto-promote`、`--apply-to-baseline`。
- `stock_edge_v2.2.yaml` 是否无 diff。
- dry-run 是否未写 tuning artifact；若有 cache，是否仅为 replay panel cache。
- K-fold 是否真的执行；若 fallback 到 single split，首跑结论是否降级。
- 每个 horizon 是否看 validation lift，不用 train lift 做结论。
- Positive folds 是否足够；单个 fold 大幅胜出但其他 fold 为负时不得推广。
- TPE + successive halving 在小预算下是否只是烟测，不应作为 promotion 证据。
- 是否存在样本数不足、regime bucket 不足、bootstrap 过低导致的统计不确定性。
- OOC 尚未执行前，不允许推荐 baseline promotion。
- 后续扩展是否仍保持 dry-run 与人工审查优先。
