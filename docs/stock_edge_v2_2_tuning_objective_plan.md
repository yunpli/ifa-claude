# Stock Edge v2.2 三周期调参目标函数设计

> 本轮不正式调参，只定义后续调参目标、标签、指标和治理规则。

## 1. 当前目标函数问题

当前 `ifa/families/stock/backtest/objectives.py` 的 `PredictionObjectiveInputs` 和 `stock_edge_v2.2.yaml` 的 `tuning.objectives/objective` 主要围绕 40d 右尾：

- `hit_50pct_40d`
- `avg_return_40d`
- `expected_return_40d`
- `expected_drawdown`
- `stop_first_rate`
- `entry_fill_quality`

这套目标曾适合“20-40d / 50% 右尾”版本，但不适合当前 5/10/20 三周期决策。下一轮必须拆成 horizon-specific objective，并保留统一 governance。

## 2. 三周期目标函数表

| Horizon | Primary objective | Secondary objective | Risk penalty | Label 需求 | Metric 需求 | 推荐优化方式 |
|---|---|---|---|---|---|---|
| 5d | 短线可执行风险调整收益：`fill_quality * expected_5d_return - drawdown_penalty - gap_penalty` | T+3/T+5 正收益、entry fill、VWAP 承接、资金确认 | gap/open risk、stop-first、5d max drawdown、滑点、追高失败 | return_3d/5d、entry_fill_5d、target_first_5d、stop_first_5d、max_drawdown_5d、gap_adverse_next_open | 正收益率、平均/中位收益、fill rate、MAE、滑点成本、止损先到率 | 连续参数搜索；先全市场/Top500 preset，再单股 overlay 小幅微调 |
| 10d | 两周目标先到 + 风险调整收益：`target_first_10d_quality + persistence - stop_first - overheat` | 资金连续性、板块扩散、同行强弱、TA validation | 10d max drawdown、筹码松动、过热、一日游、板块退潮 | return_10d、target_first_10d、stop_first_10d、mfe_10d、mae_10d、moneyflow_persistence_label | 目标先到率、收益/回撤比、资金持续胜率、sector diffusion hit rate | horizon-specific objective；按 market/sector regime 分桶验证 |
| 20d | 一个月波段风险调整收益：`expected_20d_return + target_first + MFE/MAE - drawdown - decay` | 趋势质量、板块顺风、同行强弱、仓位适配 | 20d max drawdown、stop-first、strategy decay、liquidity、基本面矛盾提示 | return_20d、target_first_20d、stop_first_20d、mfe_20d、mae_20d、drawdown_20d、position_loss_budget | 平均/中位收益、p10/p50/p90、MFE/MAE、target/stop 用时、收益回撤比 | 全市场 preset 为主，单股 overlay 仅微调阈值和权重 |

## 3. 目标函数建议公式

### 5d objective

```text
objective_5d =
  + w1 * positive_return_quality_5d
  + w2 * median_return_5d
  + w3 * entry_fill_quality_5d
  + w4 * intraday_support_quality
  + w5 * moneyflow_confirmation
  - w6 * max_drawdown_5d
  - w7 * stop_first_rate_5d
  - w8 * adverse_gap_open_rate
  - w9 * slippage_cost
  - w10 * chase_failure_rate
```

重点不是“5d 涨幅最大”，而是“可成交、亏损受控、非追高、短线能执行”。

### 10d objective

```text
objective_10d =
  + w1 * target_first_10d_quality
  + w2 * risk_adjusted_return_10d
  + w3 * moneyflow_persistence_quality
  + w4 * sector_diffusion_quality
  + w5 * peer_relative_strength_quality
  + w6 * strategy_validation_score
  - w7 * stop_first_rate_10d
  - w8 * max_drawdown_10d
  - w9 * overheat_penalty
  - w10 * one_day_wonder_penalty
```

10d 是“短波段持续性”，不能被单日强势信号主导。

### 20d objective

```text
objective_20d =
  + w1 * expected_return_20d
  + w2 * target_first_20d_quality
  + w3 * mfe_mae_reward_risk
  + w4 * trend_quality
  + w5 * sector_tailwind
  + w6 * moneyflow_persistence
  + w7 * peer_relative_strength
  + w8 * position_sizing_efficiency
  - w9 * max_drawdown_20d
  - w10 * stop_first_rate_20d
  - w11 * strategy_decay
  - w12 * liquidity_slippage_penalty
  - w13 * fundamental_contradiction_penalty
```

20d 可以吃 Research/Fundamental 作为低频辅助风险，但不能让 Research 阻塞交易性判断。

## 4. 参数调节节奏

| 参数类型 | 调节频率 | 原因 |
|---|---|---|
| score thresholds: buy/watch/wait/avoid | 每周小调 | 市场风险偏好和策略触发密度变化快 |
| signal/cluster weights | 每周小调，月度大审 | 需要随 regime 调整，但不能每日过拟合 |
| 5d entry/gap/intraday 权重 | 每周小调 | 短线执行环境变化快 |
| target/stop pct、max drawdown 约束 | 月度或季度 | 过于频繁会破坏可复盘性 |
| model hyperparameters | 月度/季度 | 训练成本和过拟合风险较高 |
| probability calibration | 周度监控，月度更新 | 需要足够样本 |
| Research/Fundamental 辅助权重 | 季度 | 低频数据，不应频繁调 |
| liquidity/slippage 成本参数 | 月度或市场冲击后 | 交易成本结构变化较慢 |

## 5. 全市场 / 板块 / Regime / 单股 overlay 边界

| 参数 | 允许范围 |
|---|---|
| 全市场 preset | 基础 score 权重、阈值、risk penalty、calibration |
| 板块/regime 调整 | cluster weight multiplier、sector gate、risk-on/risk-off gate |
| 单股 overlay | 小幅调整 entry/support/moneyflow/target-stop 权重，不允许重写模型结构 |
| 不允许单股频繁调 | 模型 hyperparameter、target pct 大幅变化、Research 权重 |

## 6. 防止单股过拟合

- 单股 overlay 必须从最新 global preset 出发，只允许连续小范围微调。
- 每次 overlay 必须记录样本数、候选数、objective、参数 hash、as-of trade date。
- 单股 overlay TTL 仍建议 10 个交易日附近，不能每天无约束重调。
- 单股 overlay 不允许改变 label 定义，只能改变权重和阈值。
- 必须保留 out-of-sample 或时间后半段验证，不允许用全历史同时训练和评估。
- 5/10/20 三周期 objective 应分别评估，不能用一个强周期掩盖其他周期失效。

