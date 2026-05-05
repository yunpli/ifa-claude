# Stock Edge v2.2 5/10/20 Objective Refactor

## 改造目标

Stock Edge v2.2 主产品只做 5/10/20 个交易日 decision。调参 objective 必须服务：

- 5d：短线可执行性和买点质量。
- 10d：两周短波段持续性。
- 20d：一个月波段质量和风险调整收益。

40d/right-tail 只保留 legacy audit，不进入主 objective。

## 新 artifact metrics schema

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

## 每个 horizon 的主要字段

| 字段 | 含义 |
|---|---|
| positive_return_rate | 该 horizon 内最终收益为正的比例 |
| target_first_rate | 目标价先于止损触发比例 |
| stop_first_rate | 止损先触发比例 |
| avg_return / median_return | 平均/中位收益 |
| avg_drawdown | 入场后最大不利回撤 |
| avg_mfe / avg_mae | 最大有利/最大不利路径 |
| mfe_mae_ratio | 路径收益风险比 |
| entry_fill_quality | 未来 5 日买入带成交质量 |
| reward_risk | MFE/MAE 与 stop width 归一化质量 |
| risk_adjusted_return | horizon-specific 风险调整收益质量 |
| drawdown_penalty | 回撤惩罚 |
| stop_first_penalty | 止损先到惩罚 |
| liquidity_penalty | 交易频率/流动性惩罚 |
| chase_failure_penalty | 不成交/追高失败惩罚 |

## Composite objective

默认权重在 YAML：

```yaml
tuning:
  objective:
    composite_weights:
      horizon_5d: 0.34
      horizon_10d: 0.33
      horizon_20d: 0.33
      calibration_quality: 0.08
      turnover_liquidity_penalty: -0.05
      strategy_decay_penalty: -0.04
```

主 score 是 signal-quality score，不是概率。

## Legacy 40d

`legacy_40d_audit` 可记录：

- `hit_target_40d_rate`
- `stop_first_40d_rate`
- `avg_return_40d`
- `avg_drawdown_40d`

但 `score_prediction_objective()` 明确忽略 legacy 40d。

