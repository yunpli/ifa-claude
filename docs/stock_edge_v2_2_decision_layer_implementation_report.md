# Stock Edge v2.2 三周期决策层实施报告

> 日期：2026-05-05  
> 范围：5 / 10 / 20 交易日 decision layer 代码实现  
> 明确未做：正式调参、正式训练、大规模 intraday backfill、报告美工重构、40d/长期主决策

## 实现摘要

本轮在现有 Stock Edge v2.2 上新增三周期交易决策层，主路径从旧的单一 `TradePlan + 40d probability surface` 切换为：

- `decision_5d`：一周内短线，`execution_score`
- `decision_10d`：两周短波段，`swing_score`
- `decision_20d`：一个月波段，`position_score`

三类 score 都是信号/执行/波段质量评分，不等同于上涨概率。旧 `ProbabilityBlock` 保留兼容和审计，但不进入用户主决策 section。

## 主要新增/修改文件

| 文件 | 变化 |
|---|---|
| `ifa/families/stock/decision_layer.py` | 新增三周期决策对象、评分、价格规则、冲突解释、数据质量降级和旧 40d 审计 |
| `ifa/families/stock/analysis.py` | `StockEdgeAnalysis` 增加 `decision_layer`，`to_dict()` 输出三周期对象 |
| `ifa/families/stock/backtest/labels.py` | 扩展 5d/10d/20d forward labels，保留旧 40d 字段 |
| `ifa/families/stock/report/builder.py` | 报告模型接入 `decision_layer` |
| `ifa/families/stock/report/runner.py` | 落库 section 改为 `01_decision_layer`、`02_data_freshness`、`03_model_conflicts`，forecast 保存三周期 JSON |
| `ifa/families/stock/report/templates/stock_edge_report.html` | 最小 HTML 改动，顶部展示三周期 decision cards |
| `ifa/families/stock/report/markdown.py` | Markdown 改为三周期主决策，旧概率只作审计 |
| `ifa/families/stock/report/templates/styles.css` | 增加三周期卡片样式 |
| `ifa/families/stock/params/stock_edge_v2.2.yaml` | 新增 `decision_layer` YAML 参数 |
| `tests/stock/test_forward_labels.py` | 新增三周期 labels 测试 |
| `tests/stock/test_analysis.py` | 增加 decision layer JSON serialization 检查 |
| `tests/stock/test_report.py` | 增加三周期报告模型/渲染检查 |

## Decision Object

每个 horizon 对象包含：

- `horizon` / `horizon_label`
- `decision`
- `user_facing_label`
- `decision_summary`
- `confidence_level`
- `risk_level`
- `score`
- `score_type`
- `score_explanation`
- `probability_estimates`
- `probability_display_warning`
- `buy_zone`
- `chase_warning_price`
- `stop_loss`
- `first_take_profit`
- `target_zone`
- `invalidation_condition`
- `suggested_action`
- `if_already_holding`
- `if_not_holding`
- `key_supporting_signals`
- `key_risk_signals`
- `model_contributors`
- `opposing_models`
- `conflict_notes`
- `data_quality`
- `missing_data_notes`
- `as_of_trade_date`
- `data_cutoff`
- `generated_at`

对象通过 dataclass 生成，并在输出时转为 plain dict，可 JSON serialize，可进入 `StockEdgeAnalysis.to_dict()`、`forecast_json` 和 report builder。

## 三周期评分

| Horizon | Score | 含义 | 主输入 |
|---|---|---|---|
| 5d | `execution_score` | 短线可执行性，不是概率 | entry fill、gap/open risk、VWAP/volume profile、支撑回踩、5d momentum、moneyflow、LHB/涨停事件、流动性、stop-first |
| 10d | `swing_score` | 两周短波段持续性，不是确定概率 | trend、support/breakout、资金持续、板块扩散、同行强弱、target/stop replay、路径形态、TA decay |
| 20d | `position_score` | 一个月波段质量，不是长期投资评级 | 趋势质量、区间位置、SmartMoney SW L2、同行动量、target/stop survival、分位收益、MFE/MAE、仓位、Research 辅助风险 |

## 价格规则

每个周期输出：

- `buy_zone`
- `chase_warning_price`
- `stop_loss`
- `first_take_profit`
- `target_zone`
- `invalidation_condition`

价格规则使用支撑/压力、ATR 和 YAML 参数生成。所有核心阈值与倍数已经参数化，避免把买点、止损、追高线写死在代码中。

## Labels 扩展

`compute_forward_labels()` 保留旧调用，同时新增：

- 5d：`return_5d_pct`、`positive_5d`、`target_first_5d`、`stop_first_5d`、`max_drawdown_5d_pct`、`mfe_5d_pct`、`mae_5d_pct`、`entry_fill_5d`、`adverse_gap_next_open`、`slippage_bucket`
- 10d：`return_10d_pct`、`positive_10d`、`target_first_10d`、`stop_first_10d`、`max_drawdown_10d_pct`、`mfe_10d_pct`、`mae_10d_pct`、`moneyflow_persistence_10d`、`sector_persistence_10d`
- 20d：`return_20d_pct`、`positive_20d`、`target_first_20d`、`stop_first_20d`、`max_drawdown_20d_pct`、`mfe_20d_pct`、`mae_20d_pct`、`position_loss_budget_hit`、`strategy_decay_bucket`

`moneyflow_persistence_10d`、`sector_persistence_10d`、`strategy_decay_bucket` 目前保留字段位，后续调参/回测时由对应数据族补充。

## YAML 参数

新增 `decision_layer`：

- `common.probability_display`
- `common.score_to_label`
- `common.risk_level_mapping`
- `common.confidence_mapping`
- `common.conflict_thresholds`
- `common.vetoes`
- `common.regime_gates`
- `common.degraded_rules`
- `horizons.5d/10d/20d.thresholds`
- `horizons.5d/10d/20d.weights`
- `horizons.5d/10d/20d.risk`
- `horizons.5d/10d/20d.price_rules`

所有分数阈值、风险压力、权重、ATR multiplier、target pct、追高线参数均使用连续值，便于后续优化。

## 40d 遗留处理

保留：

- `TradePlan`
- `ProbabilityBlock`
- prediction surface 的旧字段
- `legacy_40d_audit`

降级：

- 旧 40d probability 不进入用户主决策 section；
- HTML / Markdown 第一屏和主决策改为 5/10/20；
- DB `forecast_json` 保存 `decision_layer` 为主，旧 probability 只作 `legacy_probability_audit`；
- 旧目标价格 section 标记为兼容审计。

## 已知限制

- 本轮未做正式参数调优，score 阈值为 production-ready 初始参数，不是经过 OOS 校准的最终参数。
- 5min intraday 缺失时，5d 会 `partial` 降级，但不阻塞报告。
- Research/Fundamental 缺失时，只影响 20d 辅助背景，不阻塞 20d 决策。
- LLM 未用于改写数字或生成交易结论；后续只应解释结构化结果。
- 旧策略矩阵仍可能在审计/策略矩阵里出现 40d 模型名称，这是兼容层，不属于用户主 decision section。

