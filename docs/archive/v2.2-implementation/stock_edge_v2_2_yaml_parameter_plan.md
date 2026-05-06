# Stock Edge v2.2 三周期 YAML 参数规划

> 当前参数文件：`ifa/families/stock/params/stock_edge_v2.2.yaml`。  
> 现状：大量参数已 YAML 化且为连续范围，但目标、prediction surface、risk、holding window 仍带 40d/20-40d 遗留。

## 1. 建议 YAML 结构

```yaml
decision_layer:
  horizons:
    5d:
      enabled: true
      score_type: execution_score
      thresholds: {}
      weights: {}
      risk: {}
      price_rules: {}
      probability_display: {}
    10d:
      enabled: true
      score_type: signal_score
      thresholds: {}
      weights: {}
      risk: {}
      price_rules: {}
      probability_display: {}
    20d:
      enabled: true
      score_type: signal_score
      thresholds: {}
      weights: {}
      risk: {}
      price_rules: {}
      probability_display: {}
  common:
    score_to_label: {}
    risk_level_mapping: {}
    conflict_thresholds: {}
    degraded_rules: {}
```

## 2. 参数清单

| 参数名 | 所属周期 | 当前是否存在 | 当前位置 | 是否 hardcoded | 建议 YAML 位置 | 是否需要调参 | 备注 |
|---|---|---|---|---|---|---|---|
| `buy_threshold` | 5d/10d/20d | 部分 | `strategy_matrix.aggregate.buy_threshold` | 否 | `decision_layer.horizons.*.thresholds.buy` | 是 | 需按 horizon 拆分 |
| `watch_threshold` | 5d/10d/20d | 部分 | `strategy_matrix.aggregate.watch_threshold` | 否 | `decision_layer.horizons.*.thresholds.watch` | 是 | 同上 |
| `wait_threshold` | 5d/10d/20d | 否 | 无 | 是 | `decision_layer.horizons.*.thresholds.wait` | 是 | 新增 |
| `avoid_threshold` | 5d/10d/20d | 否 | 无 | 是 | `decision_layer.horizons.*.thresholds.avoid` | 是 | 新增 |
| `reduce_threshold` | 5d/10d/20d | 否 | 无 | 是 | `decision_layer.horizons.*.thresholds.reduce` | 是 | 已持仓场景 |
| `sell_threshold` | 5d/10d/20d | 否 | 无 | 是 | `decision_layer.horizons.*.thresholds.sell` | 是 | 已持仓强退出 |
| `score_to_label` | 全部 | 否 | report 规则分散 | 是 | `decision_layer.common.score_to_label` | 是 | 用户标签统一 |
| `probability_display_rule` | 全部 | 部分 | `prediction_surface.calibrated` | 部分 | `decision_layer.common.probability_display` | 否 | 未校准必须提示 |
| `risk_level_mapping` | 全部 | 否 | 分散于 risk/veto | 是 | `decision_layer.common.risk_level_mapping` | 是 | low/medium/high/extreme |
| `confidence_mapping` | 全部 | 部分 | `TradePlan.confidence` | 部分 | `decision_layer.horizons.*.confidence_mapping` | 是 | 按数据/冲突/样本 |
| `model_conflict_threshold` | 全部 | 否 | 无 | 是 | `decision_layer.common.conflict_thresholds` | 是 | supporting/opposing 差异 |
| `liquidity_veto` | 5d/10d/20d | 部分 | `risk.min_avg_amount_yuan`, `liquidity_slippage` | 否 | `decision_layer.common.vetoes.liquidity` | 是 | 5d 权重最高 |
| `slippage_rule` | 5d/10d | 是 | `liquidity_slippage` | 否 | `decision_layer.horizons.5d.risk.slippage` | 是 | 需按周期拆 |
| `market_regime_gate` | 全部 | 是 | `strategy_matrix.context_gates.market_regime` | 否 | `decision_layer.common.regime_gates.market` | 是 | 保留并 horizon-aware |
| `sector_regime_gate` | 10d/20d | 是 | `strategy_matrix.context_gates.sw_l2_phase` | 否 | `decision_layer.common.regime_gates.sector` | 是 | 10/20d 重点 |
| `strategy_decay_threshold` | 10d/20d | 部分 | `strategy_validation_decay` | 否 | `decision_layer.horizons.10d/20d.risk.strategy_decay` | 是 | TA validation |
| `fallback_degraded_rules` | 全部 | 部分 | `LoadResult.degraded` | 是 | `decision_layer.common.degraded_rules` | 否 | 数据缺失时降级 |
| `entry_fill_weight` | 5d | 是 | `signal_weights.entry_fill_*` | 否 | `decision_layer.horizons.5d.weights.entry_fill` | 是 | 5d 核心 |
| `gap_risk_weight` | 5d | 是 | `signal_weights.gap_risk_open_model` | 否 | `decision_layer.horizons.5d.weights.gap_risk` | 是 | 风险 veto |
| `intraday_vwap_weight` | 5d | 是 | `signal_weights.intraday_profile/vwap` | 否 | `decision_layer.horizons.5d.weights.intraday_vwap` | 是 | 需分钟线 |
| `moneyflow_weight_5d` | 5d | 是 | `moneyflow_7d/orderflow_mix` | 否 | `decision_layer.horizons.5d.weights.moneyflow` | 是 | 短线资金确认 |
| `lhb_limit_weight` | 5d | 是 | `lhb/limit_up` signal weights | 否 | `decision_layer.horizons.5d.weights.event_flow` | 是 | 事件条件化 |
| `stop_loss_5d` | 5d | 部分 | ATR/support in builder | 部分 | `decision_layer.horizons.5d.price_rules.stop_loss` | 是 | 新增正式规则 |
| `first_target_5d` | 5d | 否 | 无 | 是 | `decision_layer.horizons.5d.price_rules.first_target` | 是 | 支撑/压力/ATR |
| `chase_warning_5d` | 5d | 部分 | report veto 文本 | 是 | `decision_layer.horizons.5d.price_rules.chase_warning` | 是 | 必须出价格 |
| `t0_max_size_pct` | 5d | 是 | `t0.max_size_pct_of_base` | 否 | `decision_layer.horizons.5d.t0.max_size_pct_of_base` | 是 | 只在底仓 |
| `risk_veto_5d` | 5d | 部分 | 分散 | 是 | `decision_layer.horizons.5d.risk.vetoes` | 是 | gap/滑点/炸板 |
| `momentum_weight_10d` | 10d | 是 | `trend/momentum/volume` weights | 否 | `decision_layer.horizons.10d.weights.momentum` | 是 | 两周持续 |
| `moneyflow_persistence_weight` | 10d | 是 | `flow_persistence` | 否 | `decision_layer.horizons.10d.weights.moneyflow_persistence` | 是 | 10d 核心 |
| `sector_persistence_weight` | 10d | 是 | `smartmoney_sw_l2`, `sector_diffusion` | 否 | `decision_layer.horizons.10d.weights.sector_persistence` | 是 | 10d 核心 |
| `peer_relative_strength_weight` | 10d | 是 | `peer_relative_momentum` | 否 | `decision_layer.horizons.10d.weights.peer_relative_strength` | 是 | 同行业强弱 |
| `overheat_chip_risk_weight` | 10d | 部分 | momentum/range/TA C | 部分 | `decision_layer.horizons.10d.risk.overheat_chip` | 是 | 防一日游 |
| `stop_loss_10d` | 10d | 部分 | risk/ATR | 部分 | `decision_layer.horizons.10d.price_rules.stop_loss` | 是 | 新增 |
| `first_target_10d` | 10d | 否 | 无 | 是 | `decision_layer.horizons.10d.price_rules.first_target` | 是 | 新增 |
| `max_drawdown_10d` | 10d | 否 | 40d 口径 | 是 | `decision_layer.horizons.10d.risk.max_drawdown` | 是 | 新增 |
| `target_stop_10d` | 10d | 部分 | target_stop params | 部分 | `decision_layer.horizons.10d.target_stop` | 是 | horizon-specific |
| `trend_weight_20d` | 20d | 是 | trend/r2 weights | 否 | `decision_layer.horizons.20d.weights.trend` | 是 | 20d 核心 |
| `sector_weight_20d` | 20d | 是 | sw_l2 cluster | 否 | `decision_layer.horizons.20d.weights.sector` | 是 | 板块顺风 |
| `moneyflow_weight_20d` | 20d | 部分 | moneyflow/flow persistence | 否 | `decision_layer.horizons.20d.weights.moneyflow` | 是 | 持续性 |
| `peer_weight_20d` | 20d | 是 | peer signals | 否 | `decision_layer.horizons.20d.weights.peer` | 是 | 相对强弱 |
| `target_stop_weight_20d` | 20d | 是 | target/stop models | 否 | `decision_layer.horizons.20d.weights.target_stop` | 是 | 20d 核心 |
| `mfe_mae_weight_20d` | 20d | 是 | `mfe_mae_surface_model` | 否 | `decision_layer.horizons.20d.weights.mfe_mae` | 是 | 风险收益面 |
| `position_sizing_weight_20d` | 20d | 是 | position_sizing params | 否 | `decision_layer.horizons.20d.weights.position_sizing` | 是 | 仓位建议 |
| `research_aux_weight_20d` | 20d | 是 | fundamentals cluster | 否 | `decision_layer.horizons.20d.weights.research_aux` | 低频 | 只辅助 |
| `stop_loss_20d` | 20d | 部分 | risk max_stop_distance | 否 | `decision_layer.horizons.20d.price_rules.stop_loss` | 是 | 新增 horizon |
| `first_target_20d` | 20d | 部分 | opportunities 15/25/40 | 部分 | `decision_layer.horizons.20d.price_rules.first_target` | 是 | 去 40d |
| `target_zone_20d` | 20d | 部分 | sell_targets | 部分 | `decision_layer.horizons.20d.price_rules.target_zone` | 是 | 新增 |
| `max_drawdown_20d` | 20d | 否 | 40d 口径 | 是 | `decision_layer.horizons.20d.risk.max_drawdown` | 是 | 新增 |

## 3. 需要移除或降级的 40d 参数

| 当前参数 | 建议 |
|---|---|
| `risk.holding_window_days: [20, 40]` | 改为 decision horizons `[5, 10, 20]`；保留旧字段仅兼容 |
| `risk.right_tail_target_pct: 50.0` | 降级为 debug/legacy；20d 不默认 50% |
| `prediction_surface.hit_50` | 不作为主展示；可保留为 20d 右尾辅助 |
| `prediction_surface.opportunities.right_tail_40d_50` | 当前三周期不展示 |
| `tuning.objectives.hit_50pct_40d` | 改为 horizon objectives |
| `expected_return_40d`, `expected_drawdown_40d` | 改为 `expected_return_5d/10d/20d` 和 drawdown 同步 |

## 4. 连续化原则

- 阈值、权重、目标收益、止损距离、风险惩罚均保持连续参数。
- 不引入“开/关式”的离散策略选择，除非数据源不可用或模型被显式 disabled。
- Regime 调整应使用连续 multiplier，不用硬切换。
- 单股 overlay 只微调连续参数，不改变模型结构。

