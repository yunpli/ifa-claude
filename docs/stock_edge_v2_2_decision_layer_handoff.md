# Stock Edge v2.2 三周期决策层 Handoff

> 当前状态：三周期 decision layer 已接入代码主路径，等待正式调参、校准和真实数据回测。

## 已完成

1. 新增 `ifa/families/stock/decision_layer.py`。
2. `StockEdgeAnalysis.to_dict()` 输出 `decision_layer`。
3. 报告 builder 可读取三周期对象。
4. DB runner 将三周期 JSON 写入 `forecast_json`，并新增：
   - `01_decision_layer`
   - `02_data_freshness`
   - `03_model_conflicts`
   - `04_scenario_tree`
   - `05_legacy_trade_plan_audit`
5. HTML / Markdown 最小展示三周期 card。
6. forward labels 扩展到 5d / 10d / 20d。
7. YAML 参数新增 `decision_layer`。
8. `tests/stock` 全量通过。

## 后续正式调参前必须做

| 工作 | 说明 |
|---|---|
| Intraday 数据补足 | 按已定边界只补 5min；30min/60min 从 5min 派生；默认 Top500 × 180 trading days，不超过 10GB |
| 三周期 label 落库 | 将 `compute_forward_labels()` 的三周期字段写入 tuning/backtest 样本表 |
| 目标函数实现 | 按 `stock_edge_v2_2_tuning_objective_plan.md` 拆 5d/10d/20d objective |
| 参数 overlay | 周末全局 preset + 单股 pre-report overlay，均从 YAML 输出版本化参数 |
| 概率校准 | 未校准前报告必须继续显示 warning，不能写成确定性上涨概率 |
| 回测验证 | 分市场 regime、SW L2、流动性分层、样本内/OOS 检查 |
| 模型贡献审计 | 对每个 horizon 保存模型贡献、反对模型、冲突解释，供复盘 |

## 注意事项

- 不要把旧 `prob_hit_50_40d`、`expected_return_40d`、`expected_drawdown_40d` 放回主报告。
- Research/Fundamental 只能作为 20d 辅助风险，不阻塞 5d/10d/20d。
- LLM 只能解释结构化结果，不能改数字、补证据、编造结论。
- 所有关键阈值保持 YAML 化，调参通过配置版本推进。
- 当前 5d 在无 `intraday_5min` 时会降级为 `partial`，这是预期行为。
- 当前 `moneyflow_persistence_10d`、`sector_persistence_10d`、`strategy_decay_bucket` 标签字段已预留，但需要后续样本构建时填充。

## 推荐下一步顺序

1. 跑 intraday dry-run，确认 Top500 × 180d 预计数据量。
2. 小 universe 执行 5min backfill validation，不要直接全量。
3. 落三周期 labels 样本表。
4. 实现三周期 objective 与 evaluation report。
5. 做小样本调参 smoke。
6. 再进入 overnight 全局 preset 和单股 overlay。

