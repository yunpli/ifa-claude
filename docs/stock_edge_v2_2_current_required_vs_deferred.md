# Stock Edge v2.2 当前必须评估规划 vs Deferred

> 本文不使用优先级分层。  
> 当前必须 = 直接服务 5/10/20 交易日 decision layer。  
> Deferred = 不阻塞三周期交易决策的中长期/深度能力。

## 1. 当前必须评估并规划

| 类别 | 任务 | 当前是否必须 | 是否 deferred | 原因 | 依赖 |
|---|---|---|---|---|---|
| Decision object | `decision_5d` | 是 | 否 | 5d 是短线执行核心 | 策略矩阵、intraday、labels |
| Decision object | `decision_10d` | 是 | 否 | 10d 是短波段核心 | 策略矩阵、moneyflow、SW L2 |
| Decision object | `decision_20d` | 是 | 否 | 20d 是一个月波段核心 | 趋势、target/stop、MFE/MAE |
| Score | 5d execution score | 是 | 否 | 用户必须知道今天/未来一周能否交易 | entry/gap/VWAP/滑点 |
| Score | 10d swing score | 是 | 否 | 用户必须知道两周波段是否持续 | moneyflow/sector/peer |
| Score | 20d position score | 是 | 否 | 用户必须知道一个月波段是否值得拿 | trend/target/stop/position |
| Labels | 5d return/drawdown/target/stop/fill | 是 | 否 | 没有标签无法调参验证 | 日线、5min 增强 |
| Labels | 10d return/drawdown/target/stop/path | 是 | 否 | 10d 持续性需要验证 | 日线、资金、板块 |
| Labels | 20d return/drawdown/MFE/MAE/target/stop | 是 | 否 | 20d 波段必须有风险收益标签 | 日线 |
| Price rules | 三周期 buy zone | 是 | 否 | 报告必须给可执行价格区间 | 支撑压力、VWAP、ATR |
| Price rules | 三周期 stop loss | 是 | 否 | 错了怎么办是核心产品问题 | 支撑压力、ATR、path |
| Price rules | 三周期 first target/target zone | 是 | 否 | 用户需要止盈路径 | 压力位、target ladder |
| Risk | 三周期 risk level | 是 | 否 | 不能只给买卖结论 | gap/MAE/stop/slippage |
| Confidence | 三周期 confidence | 是 | 否 | 分数可靠性必须解释 | 数据质量、模型一致性 |
| Conflict | supporting/opposing model explanation | 是 | 否 | 高净值用户需要知道分歧来自哪里 | strategy matrix |
| YAML | 三周期 thresholds/weights/risk rules | 是 | 否 | 可调参和可交接必须 YAML 化 | params loader |
| Tuning | 三周期 objective | 是 | 否 | 不能继续用 40d 目标调当前三周期 | labels |
| Backfill | intraday 5min Top universe | 是 | 否 | 5d 执行/VWAP/T+0 数据不足 | backfill script |
| Derived data | 30min/60min derived view | 是 | 否 | 多周期执行结构需要，不应重复拉源 | 5min parquet |
| Validation | post-run intraday validation | 是 | 否 | 防止补数表面成功实际不可用 | DuckDB |
| Reporting | 三周期报告 section | 是 | 否 | 当前报告仍是单一 plan 和 20-40d | report builder |
| Persistence | 三周期 forecast JSON 落库 | 是 | 否 | 后续复盘/调参需要结构化输出 | stock.analysis_record |
| Probability display | 校准/未校准展示规则 | 是 | 否 | 防止把模型分误写成确定性概率 | prediction_surface |
| T+0 | 只在有底仓时启用 | 是 | 否 | A 股 T+0 必须合规表达 | request.has_base_position |
| Cache/reuse | 同 cutoff/param_hash 复用 | 是 | 否 | 报告不应重复生成 | current stock cache |

## 2. Deferred

| 类别 | 任务 | 当前是否必须 | 是否 deferred | 原因 | 依赖 |
|---|---|---|---|---|---|
| Research | 财报 Research 深度加强 | 否 | 是 | 只辅助 20d，不阻塞交易性判断 | research cache |
| Fundamental | 更完整行业竞争格局 | 否 | 是 | 对 5d/10d 主决策边际低 | Research/财报 |
| Research | 研报/公告全文抽取 | 否 | 是 | 当前事件/财报缓存可先做辅助 | LLM/data pipeline |
| Long-term | 长期持有判断 | 否 | 是 | 当前版本只做 5/10/20 交易日 | 未来产品口径 |
| Allocation | 长期资产配置建议 | 否 | 是 | 不是单股三周期交易决策 | 组合系统 |
| Portfolio | 个性化真实持仓/账户 | 否 | 是 | 当前只有 `base_position_shares` 简单输入 | 账户/组合模块 |
| Tax | 税务影响 | 否 | 是 | 不属于 Stock Edge v2.2 三周期 | 外部账户数据 |
| Full market 5min | 全市场多年分钟线 | 否 | 是 | 数据量/收益比不适合当前阶段 | 数据预算 |
| 40d decision | 40 个交易日决策 | 否 | 是 | 已明确不做当前主周期 | 旧模型可 debug |
| PDF export | 报告 PDF 产品化 | 否 | 是 | HTML/MD 已足够当前验证 | UI/渲染 |

## 3. 边界原则

- 只要直接影响 5/10/20 的“买不买、怎么买、错了怎么办、何时止盈止损”，就属于当前必须。
- 只要是 Research/Fundamental 的深度增强，但不影响三周期主交易判断，就 deferred。
- 40d 和长期持有全部 deferred，不能混回三周期评分。
- LLM 只做解释、情景树、冲突说明和风险提示，不作为未校准 alpha 主轴。
- Intraday 数据补足属于当前必须，因为 5d execution score 依赖 VWAP/volume profile/T+0。
