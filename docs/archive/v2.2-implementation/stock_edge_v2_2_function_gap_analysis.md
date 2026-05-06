# Stock Edge v2.2 三周期功能缺口分析

> 本文基于当前 repo 真实实现：`analysis.py`、`plan.py`、`prediction_surface.py`、`backtest/labels.py`、`report/builder.py`、`stock_edge_v2.2.yaml`、`strategies/catalog.py`、`strategies/matrix.py`。

## 1. 总体判断

Stock Edge v2.2 当前已经具备：

- 单股报告入口；
- local-first snapshot；
- Tushare 小窗口 backfill；
- DuckDB/Parquet intraday loader/backfill；
- 85 个 implemented strategy catalog item；
- 策略矩阵、cluster score、signal score；
- 单股即时 ML / replay / path / target-stop / entry-fill / gap-risk / position-sizing 等模型；
- 报告 HTML/Markdown 渲染与 stock schema 落库；
- 报告复用逻辑；
- tuning artifact 机制和 continuous search bounds。

但正式三周期 decision layer 仍缺一等对象和三周期标签/参数/报告结构。当前系统偏“单一 TradePlan + 20-40d 右尾/机会目标”，而不是 5d/10d/20d 并列决策卡。

## 2. 功能缺口表

| 功能缺口 | 所属周期 | 当前状态 | 缺什么 | 当前是否必须补 | 是否需要补数据 | 是否需要调参 | 建议实现方式 | 相关文件/模块 |
|---|---|---|---|---|---|---|---|---|
| `decision_5d` object | 5d | 无一等对象 | 独立 JSON、落库、报告 section | 是 | 否 | 是 | 新增 decision builder，兼容旧 `TradePlan` | `plan.py`, `analysis.py`, `report/builder.py` |
| `decision_10d` object | 10d | 无一等对象 | 独立 JSON、短波段 score | 是 | 否 | 是 | 同上 | 同上 |
| `decision_20d` object | 20d | 无一等对象 | 独立 JSON、20d score/target/stop | 是 | 否 | 是 | 同上 | 同上 |
| 5d return label | 5d | `compute_forward_labels` 可算 horizon returns | 未落地到三周期 tuning objective | 是 | 否 | 是 | labels 扩展输出 `return_5d_pct` | `backtest/labels.py` |
| 10d return label | 10d | 可算 | 未被 objective 正式使用 | 是 | 否 | 是 | 输出 `return_10d_pct` | `backtest/labels.py` |
| 20d return label | 20d | 可算 | 未与 20d target/stop/MFE 绑定 | 是 | 否 | 是 | 输出 `return_20d_pct` | `backtest/labels.py` |
| 5d max drawdown label | 5d | 当前 max drawdown 偏 40d | 缺 5d MAE/max drawdown | 是 | 否 | 是 | 按 horizon 计算 low-path drawdown | `backtest/labels.py` |
| 10d max drawdown label | 10d | 同上 | 缺 10d MAE/max drawdown | 是 | 否 | 是 | 同上 | `backtest/labels.py` |
| 20d max drawdown label | 20d | 同上 | 缺 20d MAE/max drawdown | 是 | 否 | 是 | 同上 | `backtest/labels.py` |
| 5d target/stop-first | 5d | 当前 `stop_first` 对 40d target | 缺 5d target-first/stop-first | 是 | 否 | 是 | horizon-specific event scan | `labels.py`, `target_stop_replay.py` |
| 10d target/stop-first | 10d | 当前缺明确 10d | 缺 10d target/stop path | 是 | 否 | 是 | 同上 | 同上 |
| 20d target/stop-first | 20d | 现有模型可支持但参数偏 20/25/40 | 缺 20d 固化目标 | 是 | 否 | 是 | 统一 target ladder scenarios | `prediction_surface.py`, `meta_models.py` |
| 5d entry fill label | 5d | `entry_fill_replay` 和 classifier 已有 | 缺正式 5d execution label 入 objective | 是 | 分钟线增强可选 | 是 | 日线可先做，分钟线补后增强 | `entry_fill_replay.py`, `meta_models.py` |
| gap/open risk | 5d | 已有 rule + RF | 缺 5d decision veto 阈值 | 是 | 否 | 是 | 写入 5d risk veto | `matrix.py`, params |
| 5min intraday loader | 5d | 已有，数据仅少量股票 | 广泛 universe 数据不足 | 是 | 是 | 是 | 只补 intraday family | `data/intraday.py`, backfill script |
| VWAP / volume profile | 5d/10d | 已有策略 | 缺广泛数据和 5d 报告权重 | 是 | 是 | 是 | 5min 补足后启用 | `intraday_profile.py`, `vwap_execution.py` |
| T+0 约束 | 5d | `requires_base_position` 已有 | 报告必须明确“仅底仓可用” | 是 | 是 | 是 | decision object 中按持仓分支展示 | `plan.py`, `report/builder.py` |
| 5d buy zone | 5d | 报告有 `next_5d` route | 不是正式 `decision_5d.buy_zone` | 是 | 分钟线增强可选 | 是 | 统一 `buy_zone` 字段 | `report/builder.py`, `support_resistance.py` |
| 10d buy zone | 10d | 无独立对象 | 缺短波段 buy zone | 是 | 否 | 是 | 支撑/回踩/突破 + target/stop | `plan.py`, `prediction_surface.py` |
| 20d buy zone | 20d | 当前 plan entry zone 可用 | 未 horizon-specific | 是 | 否 | 是 | 20d entry zone 独立 | 同上 |
| chase warning | 5d/10d | 报告有高开不追 veto | 缺价格字段 | 是 | 分钟线增强可选 | 是 | 增加 `chase_warning_price` | `report/builder.py` |
| first target | 5d/10d/20d | 当前 sell_targets 来自 15/25/40 | 缺 5/10/20 target | 是 | 否 | 是 | target ladder 改三周期 | `prediction_surface.py` |
| risk level | 5d/10d/20d | 仅 confidence/action，缺 risk_level | 缺统一映射 | 是 | 否 | 是 | YAML risk mapping | params, `plan.py` |
| confidence level | 5d/10d/20d | `TradePlan.confidence` 单一 | 缺三周期 confidence | 是 | 否 | 是 | 按数据质量/一致性/样本量 | `plan.py` |
| conflict explanation | 5d/10d/20d | 策略矩阵有 signals，但缺冲突摘要 | 缺 supporting/opposing 模型摘要 | 是 | 否 | 是 | 贡献度排序 + LLM scenario tree 摘要 | `matrix.py`, `scenario_tree.py` |
| 10d moneyflow persistence | 10d | 已有 `flow_persistence_decay` | 未成为 10d 核心决策字段 | 是 | 否 | 是 | 10d score 权重提升 | `matrix.py`, params |
| 10d sector persistence | 10d | SW L2 状态/资金已接 | 缺 horizon 权重 | 是 | 否 | 是 | 10d sector cluster | params |
| 10d peer relative strength | 10d | 已有 `peer_relative_momentum` | 报告 UI/决策没有强绑定 | 是 | 否 | 是 | 10d contributor | `gateway.py`, `report/charts.py` |
| 10d strategy validation | 10d | 已有 `strategy_validation_decay` | 缺三周期 target validation | 是 | 否 | 是 | TA setup metrics + 10d labels | `validation_decay.py` |
| 20d position sizing | 20d | 已有模型和 `position_sizing` params | 缺三周期仓位输出 | 是 | 否 | 是 | 分 horizon 仓位建议 | `position_sizing.py`, `plan.py` |
| 20d Research/Fundamental 辅助 | 20d | 已有 prefetch/lineup/peer factors | Research cache 不全，不能阻塞 | 评估但不阻塞 | Research deferred | 低频参数 | 仅风险/背景 section | `research_prefetch.py`, gateway |
| 20d strategy decay | 20d | 已有 | 缺 20d objective 绑定 | 是 | 否 | 是 | decay penalty | `objectives.py` |
| 三周期 score-to-label | 全部 | 当前 action 是 buy/watch/avoid 单一 | 缺 score label mapping | 是 | 否 | 是 | YAML 化 | params |
| 三周期 probability warning | 全部 | `calibrated=false` 已有 | 报告需强提示 | 是 | 否 | 否 | 固定 display rule | `report/builder.py` |
| 三周期落库 | 全部 | `forecast_json` 可存旧 ProbabilityBlock | 缺 `decision_layer_json` 或 forecast 新结构 | 是 | 否 | 否 | 先存 forecast_json 内嵌三对象 | `db/memory.py` |
| 报告 section | 全部 | 现有 section 01/02/03 | 缺三周期决策 section | 是 | 否 | 是 | 新 section `01_decision_layer` | `report/runner.py` |

## 3. 5d 专项判断

当前已有足够多 5d 输入：日线、资金、事件、支撑压力、entry fill、gap risk、intraday/VWAP/T+0 模块。但正式 5d decision 缺：

- `decision_5d.execution_score`；
- 5d max drawdown / stop-first / entry-fill labels；
- 5d buy zone、chase warning、stop、first target；
- 5d 风险 veto；
- T+0 只在 `has_base_position=true` 时展示为可执行；
- 5min 数据对 Top universe 不足。

## 4. 10d 专项判断

当前 10d 所需的 moneyflow、SW L2、peer relative、TA validation、target/stop replay 大多已有。缺口主要是三周期对象化和目标函数化：

- `decision_10d.swing_score`；
- 10d target-first / stop-first / path replay label；
- 10d moneyflow persistence、sector diffusion、peer strength 的固定权重；
- 10d buy zone / stop / first target；
- 10d 风险调整收益目标。

## 5. 20d 专项判断

当前 20d 与旧 20-40d/右尾模型有重叠，但必须去掉 40d 主口径：

- `decision_20d.position_score`；
- 20d target/stop、MFE/MAE、max drawdown、position sizing；
- 20d target ladder scenarios；
- 20d Research/Fundamental 只作为辅助；
- 20d strategy decay 和 regime gate。

## 6. 不应混入当前主路径的内容

| 内容 | 处理 |
|---|---|
| 40d decision | 当前不做，只保留历史兼容字段或 debug |
| 长期持有/配置 | Deferred |
| Research/Fundamental 深度加强 | Deferred；现有缓存可辅助 20d |
| 完整研报/公告全文抽取 | Deferred |
| 个性化真实账户/税务/组合配置 | Deferred |

