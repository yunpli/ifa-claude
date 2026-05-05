# Stock Edge v2.2 三周期决策层设计

> 版本口径：Stock Edge v2.2  
> 决策周期：5 / 10 / 20 个交易日  
> 本文只定义设计，不改变当前代码。

## 1. 产品目标

Stock Edge v2.2 面向高净值个人投资者、专业交易员、投顾和内部研究/交易人员。用户在收盘后分析某一只 A 股时，不需要先看到系统堆叠了多少模型，而是要得到一张可执行、可复盘、可解释的个股交易决策卡。

三周期系统要回答的是：

- 5 个交易日：这只股票在一周内是否具备短线可交易性，今天能不能买，追高风险有多大，买入区间、止损和第一止盈在哪里。
- 10 个交易日：这只股票是否值得做两周短波段，资金和板块是否能持续，是否只是单日脉冲，目标/止损谁更可能先到。
- 20 个交易日：这只股票是否值得做一个月内波段，趋势质量、板块顺风、资金持续性、同行强弱、MFE/MAE 与仓位是否匹配。

报告应优先回答“该不该交易、怎么交易、错了怎么办、为什么这么判断”。不应优先展示模型数量、原始策略列表或未校准概率。未校准的 0-1 输出只能称为模型分、排序分或未校准估计，不能表达成确定性上涨概率。

本轮不做 40 个交易日和长期持有，因为 v2.2 当前交易目标已经收敛到 5/10/20 个交易日。40d 右尾目标和长期配置容易把短线执行、波段交易、基本面研究混在一起，导致阈值、回测标签、止损和用户理解全部失真。已有 40d/长期相关模型可以降级为 20d 辅助证据或暂时禁用，但不应成为主决策轴。

Research / Fundamental 深度能力当前只作为 20d 辅助背景和风险提示。原因是财报/研报频率低，不能解释 5d/10d 的主要交易噪声；它适合回答“这家公司有没有中期基本面支撑/矛盾”，不适合作为短线买卖点主因。三周期 decision layer 是当前最重要的功能核心，因为它把现有 80+ 策略、数据、报告和调参能力转化成用户真正能执行和复盘的交易对象。

## 2. 当前代码基线

| 模块 | 当前真实状态 | 对三周期的含义 |
|---|---|---|
| 入口 | `ifa stock report` in `ifa/cli/stock.py` | 支持单股报告；未显式输出 5d/10d/20d decision objects |
| 分析入口 | `run_rule_baseline_analysis()` in `ifa/families/stock/analysis.py` | 构建 snapshot 后生成单一 `TradePlan` |
| 当前 plan | `TradePlan` / `ProbabilityBlock` in `ifa/families/stock/plan.py` | `ProbabilityBlock` 字段仍是 `*_40d` 为主 |
| 预测面 | `build_prediction_surface()` | 当前机会为 15d/20%、25d/30%、40d/50%，不是 5/10/20 |
| 策略矩阵 | `compute_strategy_matrix()` | 已有多模型信号和 cluster score，可作为三周期输入 |
| 回测标签 | `compute_forward_labels()` | 支持 horizons=(5,10,20,40) 收益，但最大回撤/target/stop 当前偏 40d |
| 报告层 | `build_report_model()` | 现在有今日决策、未来 5 日买入路线、20-40d 目标；缺三周期并列决策 |
| 落库 | `stock.analysis_record` / `stock.report_sections` | 可扩展存三周期 JSON；当前仅 section 01/02/03 和 forecast |
| 参数 | `stock_edge_v2.2.yaml` | 有很多连续参数，但目标函数、risk、prediction_surface 仍偏 40d |

## 3. Decision Object Schema

三个对象应独立生成、独立入库、独立展示：

- `decision_5d`
- `decision_10d`
- `decision_20d`

建议 JSON Schema 结构如下，三个周期共享字段，只是 horizon-specific 参数和模型贡献不同。

```json
{
  "horizon": "5d",
  "horizon_label": "一周内短线",
  "decision": "buy | wait | watch | hold | reduce | sell | avoid | no_action",
  "user_facing_label": "短线值得关注",
  "decision_summary": "一句话说明当前最重要的交易结论。",
  "confidence_level": "high | medium | low",
  "risk_level": "low | medium | high | extreme",
  "score": 0.0,
  "score_type": "signal_score | ranking_score | execution_score | raw_probability | calibrated_probability",
  "score_explanation": "解释 score 的含义和不可误读之处。",
  "probability_estimates": {
    "up_probability": {"value": 0.0, "calibrated": false, "label": "未校准上涨估计"},
    "target_first_probability": {"value": 0.0, "calibrated": false},
    "stop_first_probability": {"value": 0.0, "calibrated": false},
    "entry_fill_probability": {"value": 0.0, "calibrated": false}
  },
  "probability_display_warning": "当前概率未经过正式校准，不能当作确定性预测。",
  "buy_zone": {"low": 0.0, "high": 0.0, "basis": "support/vwap/pullback/breakout"},
  "chase_warning_price": 0.0,
  "stop_loss": {"price": 0.0, "basis": "support/atr/path invalidation"},
  "first_take_profit": {"low": 0.0, "high": 0.0, "basis": "resistance/risk_reward"},
  "target_zone": {"low": 0.0, "high": 0.0, "basis": "horizon target"},
  "invalidation_condition": ["跌破止损价且无法收回", "策略矩阵转负"],
  "suggested_action": "用户下一步具体动作。",
  "if_already_holding": "已有仓位处理方式。",
  "if_not_holding": "未持仓处理方式。",
  "key_supporting_signals": [
    {"key": "moneyflow_7d", "label": "7日主力净流", "score": 0.0, "evidence": "证据"}
  ],
  "key_risk_signals": [
    {"key": "gap_risk_open_model", "label": "跳空风险", "score": 0.0, "evidence": "证据"}
  ],
  "model_contributors": [
    {"key": "entry_price_surface_model", "contribution": 0.0, "role": "entry"}
  ],
  "opposing_models": [
    {"key": "liquidity_slippage", "contribution": -0.0, "reason": "流动性不足"}
  ],
  "conflict_notes": "模型冲突说明。",
  "data_quality": {
    "status": "ok | partial | degraded | missing",
    "required_sources_ok": true,
    "optional_sources_ok": false
  },
  "missing_data_notes": ["5min 数据不足时说明影响。"],
  "as_of_trade_date": "2026-04-30",
  "data_cutoff": "2026-04-30T15:00:00+08:00",
  "generated_at": "2026-05-05T00:00:00+08:00"
}
```

## 4. 字段归属

| 字段 | 含义 | 必须入库 | 必须进报告 | Debug/Audit | 用于复盘调参 |
|---|---|---|---|---|---|
| horizon / horizon_label | 决策周期 | 是 | 是 | 是 | 是 |
| decision / user_facing_label | 操作结论 | 是 | 是 | 是 | 是 |
| decision_summary | 用户可读摘要 | 是 | 是 | 否 | 是 |
| confidence_level | 证据一致性与数据质量 | 是 | 是 | 是 | 是 |
| risk_level | 风险等级 | 是 | 是 | 是 | 是 |
| score / score_type / score_explanation | 分数及解释 | 是 | 是 | 是 | 是 |
| probability_estimates | 概率块 | 是 | 条件展示 | 是 | 是 |
| probability_display_warning | 未校准提示 | 是 | 是 | 否 | 否 |
| buy_zone / stop_loss / target_zone | 执行价格 | 是 | 是 | 是 | 是 |
| chase_warning_price | 追高警戒 | 是 | 是 | 是 | 是 |
| first_take_profit | 第一止盈 | 是 | 是 | 是 | 是 |
| invalidation_condition | 失效条件 | 是 | 是 | 是 | 是 |
| suggested_action | 下一步动作 | 是 | 是 | 否 | 是 |
| if_already_holding / if_not_holding | 持仓/未持仓分支 | 是 | 是 | 否 | 是 |
| supporting/risk signals | 关键证据 | 是 | 是，限制 3-5 条 | 是 | 是 |
| model_contributors/opposing_models | 模型贡献与反对 | 是 | 摘要展示 | 是 | 是 |
| conflict_notes | 冲突解释 | 是 | 是 | 是 | 是 |
| data_quality/missing_data_notes | 数据质量 | 是 | 是 | 是 | 是 |
| as_of/data_cutoff/generated_at | 时间追溯 | 是 | 是 | 是 | 是 |

## 5. 三周期 Score 定义

| Horizon | Score 名称 | Score 含义 | 是否概率 | 核心用途 | 推荐阈值 | 风险阈值 | 未校准时如何展示 |
|---|---|---|---|---|---|---|---|
| 5d | `decision_5d.execution_score` | 短线可执行性：入场可成交、追高风险、分时承接、止损空间、短线事件风险 | 否 | 判断今天/未来 5 日能否交易 | buy >= 0.72, watch >= 0.58, wait >= 0.48 | avoid < 0.42 或 risk high | 展示为“短线执行分”，不写“上涨概率” |
| 10d | `decision_10d.swing_score` | 两周持续性：动量、资金连续性、板块扩散、同行强弱、target/stop 路径 | 否，除非校准 | 判断短波段是否值得做 | buy >= 0.70, watch >= 0.56, wait >= 0.46 | avoid < 0.40 或 stop-first 高 | 展示为“短波段综合分” |
| 20d | `decision_20d.position_score` | 一个月波段质量：趋势、支撑压力、资金/板块/同行、MFE/MAE、仓位适配 | 否，除非校准 | 判断 20d 波段持有价值 | buy >= 0.68, watch >= 0.54, wait >= 0.44 | avoid < 0.38 或 drawdown extreme | 展示为“20日波段评分” |

## 6. 周期级阈值与标签

### 5d

- `buy_threshold`: 0.72，且 entry_fill 支持、gap/open 风险不高、stop-first 不高。
- `watch_threshold`: 0.58，可观察但不追高。
- `wait_threshold`: 0.48，等待回踩或确认。
- `avoid_threshold`: 0.42 以下，或出现流动性/跳空/涨停炸板 veto。
- `reduce/sell`: 已持有且 5d risk high、跌破止损或短线资金转负。
- `confidence`: 高 = 数据完整且正反模型一致；中 = 有核心信号但冲突；低 = 数据缺失或模型分散。
- `risk_level`: 由 gap risk、stop-first、滑点、最大回撤、事件风险取最大约束。

### 10d

- `buy_threshold`: 0.70，且 moneyflow persistence、sector diffusion、peer relative momentum 至少两项支持。
- `watch_threshold`: 0.56。
- `wait_threshold`: 0.46。
- `avoid_threshold`: 0.40 以下，或“一日游/过热/筹码松动”风险高。
- `reduce/sell`: 已持有且资金连续性断裂、板块衰退、target/stop 模型转负。
- `confidence`: 看 10d label 覆盖、TA validation、target/stop 一致性。
- `risk_level`: 由 10d MAE/max drawdown、过热、stop-first、板块退潮决定。

### 20d

- `buy_threshold`: 0.68，且趋势质量、板块顺风、资金持续性、MFE/MAE 结构可接受。
- `watch_threshold`: 0.54。
- `wait_threshold`: 0.44。
- `avoid_threshold`: 0.38 以下，或 20d drawdown / stop-first / liquidity veto。
- `reduce/sell`: 已持有且 20d 趋势破坏、止损危险率升高、板块顺风消失。
- `confidence`: 看模型一致性、样本量、校准状态和 Research/Fundamental 是否冲突。
- `risk_level`: 由 expected drawdown、stop-first、MFE/MAE、仓位模型和策略衰减决定。

## 7. 决策冲突处理

| 场景 | 决策规则 |
|---|---|
| 5d buy, 10d/20d weak | 允许短线交易，但报告必须写“只做短线，不转波段”。 |
| 5d avoid, 10d/20d positive | 不追当日，等待回踩或 VWAP/支撑确认。 |
| 10d positive, 20d weak | 定义为短波段，止盈更快，不允许上调持仓周期。 |
| 20d positive, 5d execution weak | 中期可观察，但短线买点不好，等待价格进入 buy zone。 |
| 模型概率高但风险 high | 降级为 watch/wait，风险 veto 优先。 |
| 未校准概率高 | 展示为“未校准模型估计”，不能直接写确定性概率。 |

## 8. 当前实现需要收敛的点

当前 v2.2 已有强大的策略矩阵和报告基础，但三周期决策层还没有成为一等对象：

- `TradePlan` 是单一 plan，不是 5d/10d/20d 三对象。
- `ProbabilityBlock` 仍使用 `prob_hit_50_40d`、`expected_return_40d`、`expected_drawdown_40d`。
- `prediction_surface.opportunities` 仍是 15d/20%、25d/30%、40d/50%。
- `risk.holding_window_days` 是 `[20,40]`。
- 报告中 `holding_window` 写作 20-40 个交易日。
- `compute_forward_labels` 已有 5/10/20 return 能力，但 drawdown/target/stop 标签需按三周期拆分。

因此下一轮实现应把当前单一 plan 改为三周期 `decision_layer`，并保持旧 plan 字段作为兼容输出，直到报告和落库全部迁移完成。
