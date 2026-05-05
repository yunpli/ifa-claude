# Stock Edge v2.2 调参体系专业 Review

## 真实结论

本轮 review 前，当前代码的真实状态是：

1. global preset JSON **不会自动进入报告运行路径**。
2. single-stock overlay **不是基于 global preset**，而是直接从 YAML baseline 出发。
3. `_runtime_tuning` 只记录一个 artifact path，不能审计 global + single 两层参数来源。
4. artifact 只有 `base_param_hash`，没有明确 objective version。
5. optimizer 主 objective 仍使用 40d 命名和 40d 路径，和 v2.2 的 5/10/20 三周期产品口径不一致。

## 新治理口径

### Global tuning

global tuning 是全市场、Top liquidity universe、regime/sector 级参数校准。它有两种状态：

- 实验态：生成 JSON artifact，不改 YAML，用于 dry-run、review、对比、回滚。
- 晋升态：生成可审计 YAML patch，人工 review 后才更新 baseline 或生成新的 YAML variant。

脚本默认不静默改 YAML。生产晋升必须走 `stock_edge_promote_global_preset.py`。

### Single-stock overlay

single-stock overlay 是报告运行前的局部适配：

- 永远不写回 YAML。
- 从当前生产 baseline 出发。
- 如果可用，先叠加 reviewed global preset artifact。
- 再生成/复用单股 overlay。
- 单股 overlay 只能微调连续参数，不能改变模型结构和目标定义。

## 新运行路径

```text
YAML baseline
  -> compatible global_preset JSON artifact
  -> compatible/generated single-stock pre_report_overlay JSON artifact
  -> runtime params
```

## 已实现修改

- `prepare_report_params()` 先查 `global_preset/__GLOBAL__`，hash/TTL 兼容才使用。
- single overlay 的 `base_param_hash` 改为 global overlay 之后的 params hash。
- single overlay 覆盖 global overlay。
- `_runtime_tuning` 同时记录 global 与 single artifact path、score、candidate count。
- incompatible/stale global artifact 不复用。
- disabled tuning 仍保持 no-op。

## 生产风险

- global preset 仍需人工 review 后晋升 YAML，不能只依赖 runtime overlay 长期生产。
- 当前 global preset 搜索仍是 deterministic random search，不是完整 Bayesian/Optuna 调参。
- 当前 ML/DL 多为策略矩阵证据与轻量 report-time 模型，不是全量持久化模型训练。
- 仍需建立 weekly/monthly/quarterly 的验证与 promotion cadence。

