# Stock Edge Deep Dive — 个股作战室

> **状态**：V2.2 第三主功能设计稿(institutional-grade revision 2026-05-04)
> **替代命名**：原 `Stock Intel` 更名为 **Stock Edge（个股作战室）**，避免与 Intel 品牌混淆，并强调本模块的核心目标是寻找可验证的交易 edge。
> **定位**：给定一只 A 股和一个数据截止时点，产出 20-40 个交易日持仓周期内的可执行交易计划：买入区间、卖出目标、止损/失效条件、T+0 底仓操作方案、概率分布和复盘条件。
> **核心目标**：寻找未来 20-40 个交易日具备 **+50% 右尾机会** 的交易结构，同时严格控制下行风险、流动性、可执行性和前视偏差。
>
> **2026-05-04 institutional revision**:基于 30 年华尔街 quant + A 股资深短线视角的深化,加入:
> - **§2.4** PIT defense + Probability calibration + Multiple testing + Stop hit 真实定义
> - **§3.5** A 股专属 5 大信号(限售解禁 / 融资融券 / 北向 per-stock / 大股东减持节奏 / 质押率)
> - **§5.2 F-J** 5 个新 strategy(北向连续抢筹 / 融资砍仓反转 / 大股东增持确认 / 业绩前蓄势 / 减持后修复)
> - **§6.5** Execution Layer(slippage 三档模型 / VWAP slicing / Order ticket / fill_log 闭环)
> - **§8.4** Portfolio Level Risk(correlation matrix / crowding score / stress test)
> - **§10.4** UI/UX 深化(mobile-first / QR code / interactive analog / color-blind / compliance print)
> - **§11.4** 工程闸门(idempotency cache / compute lock / model registry / audit log / drift monitor)

---

## 1. 产品重新定义

Stock Edge 不是传统“个股分析报告”，也不是聊天式问答。它是一个面向真实交易流程的单股决策引擎：

```
Research     → 基本面质量与财报拐点
TA Family    → 30 setup / 11 family / regime / 真实持仓回测 edge
SmartMoney   → 板块强度、资金流、龙虎榜、机构参与
Ningbo       → ML scoring、Kronos 设施、历史短线经验
Stock Edge   → 汇总成一张可执行交易计划
```

它回答的问题不是“这家公司怎么样”，而是：

> 这只股票现在有没有值得承担风险的交易机会？  
> 如果有，应该在哪个价格区间买、买多少、错了在哪里认错、对了在哪里兑现、未来 20-40 个交易日达到 +50% 的概率有多大？

### 1.0.1 核心产品能力：预测执行卡

Stock Edge 的亮点不是“分析很多维度”，而是把所有维度压缩成一张可执行的预测卡。报告第一优先级必须回答：

| 问题 | 输出 |
|---|---|
| 今天能不能买 | 今日 Buy / Watch / Avoid 结论 |
| 今天什么价格买 | 今日可执行买入区间，而不是单点神奇价格 |
| 接下来 5 个交易日怎么买 | 回踩买入、突破回踩买入、低吸买入等条件化方案 |
| 买错了怎么办 | 止损/失效价和取消未来买入计划的条件 |
| 买对了怎么卖 | 20%、30%、50% 目标价和 20-40 个交易日持仓窗口 |
| 为什么有这个预测 | 策略矩阵、TA、SmartMoney、Research、Ningbo ML、Kronos 作为证据层 |

产品原则：

- 分析能力服务预测能力，不能反过来。
- 没有改变买点、卖点、失效条件或概率的内容，只能作为附录证据。
- 所有预测必须记录 as_of、模型/参数版本和是否校准；数据新鲜度仅作为内部审计 metadata，不在用户报告中展示。

### 1.1 数据截止规则

为了避免盘中漂移和不可复盘，Stock Edge 必须固定数据快照：

| 生成时间 | 使用数据 |
|---|---|
| 交易日 15:00 前 | 最近一个已完成交易日，T-1 |
| 交易日 15:00 后 | 当日 T 收盘后数据 |
| 非交易日 | 最近一个已完成交易日 |

每份报告必须记录：

- `as_of_trade_date`
- `data_cutoff_at`
- 每个数据源的 `freshness`
- 是否存在 delayed / degraded 数据源

如果 15:00 后某些当日数据尚未落库，不能混用半截数据；必须显式降级为上一可用日期或标注 delayed。

### 1.2 输出不是“推荐语”，而是 Trade Plan

核心输出字段：

| 字段 | 含义 |
|---|---|
| Action | Buy / Watch / Avoid / Exit / Update |
| Confidence | High / Medium / Low |
| Setup Type | 突破、回踩、反转、主升延续、事件驱动、高风险博弈 |
| Entry Zone | 建仓价格区间，不给单点价 |
| Add Zone | 加仓触发区间 |
| Stop / Invalidation | 止损价和逻辑失效条件 |
| Targets | T1 / T2 / Right-tail target |
| Holding Window | 20-40 个交易日为主 |
| P(+50%) | 20/40 日内达到 +50% 的概率 |
| Expected Return | 概率加权期望收益 |
| Expected Drawdown | 预期最大回撤 |
| Position Size | 观察仓 / 试错仓 / 标准仓 / 禁止 |
| T+0 Plan | 有底仓情况下的日内高抛低吸计划 |
| What Changes Our Mind | 复盘和失效条件 |

---

## 2. 机构级设计原则

### 2.1 目标函数

用户目标是“20-40 个交易日，目标至少 +50%”。系统不能硬凑目标价，而要输出概率和赔率：

```
primary_objective =
  maximize P(max_return_40d >= +50%)
  subject to:
    expected_drawdown <= risk_budget
    liquidity >= minimum_capacity
    invalidation_distance acceptable
    setup edge positive across 60d / 180d / 360d
```

核心指标：

| 指标 | 定义 |
|---|---|
| `p50_20d`, `p50_40d` | 20/40 日收益中位数 |
| `p75_20d`, `p75_40d` | 右尾收益 |
| `p90_20d`, `p90_40d` | 极端右尾收益 |
| `prob_hit_50_40d` | 40 日内最高价触及 +50% 的概率 |
| `prob_stop_first` | 先触发止损而非目标的概率 |
| `reward_risk` | 上行空间 / 失效距离 |
| `time_to_target` | 历史相似样本达到目标所需交易日 |

### 2.2 三层否决

不是所有高弹性股票都能交易。必须有 veto：

1. **硬风控否决**
   - ST / 退市风险
   - 停牌 / 流动性严重不足
   - 连续一字板导致不可成交
   - 重大监管、立案、财务造假风险

2. **交易结构否决**
   - Entry 离支撑太远，止损过宽
   - 上方强阻力太近，赔率不足
   - 量能衰竭且高位派发
   - 板块退潮且个股无独立催化

3. **模型置信否决**
   - TA edge 在 60d / 180d / 360d 其中任一窗口显著失效
   - Kronos analog 样本分歧极大
   - ML 右尾概率高但规则层证据不足
   - LLM 只能解释，不能推翻数字层 veto

### 2.3 A 股特别约束

| 约束 | 设计影响 |
|---|---|
| T+1 | 当日买入不能当日卖出；所有回测必须使用可交易库存假设 |
| T+0 只能基于底仓 | T+0 模块只对已有底仓给高抛低吸计划，不允许裸 T+0 |
| 涨跌停 | Entry / stop / target 必须考虑涨跌停不可成交；主板 ±10% / 创业板/科创板 ±20% / 北交所 ±30%，规则不同必须分别处理 |
| 集合竞价 | 开盘跳空可能导致 entry 无法成交，需要 gap handling |
| 一字板 | 右尾概率高但成交概率低，必须区分 "理论收益" 和 "可成交收益" |
| 龙虎榜上榜规则 | 净买/净卖/连续涨跌幅触发上榜，理解阈值；早封 vs 尾盘封 vs 炸板回封语义不同 |
| 散户情绪 | 短线 20-40 日目标高度依赖资金结构，不可只看财务 |

### 2.4 PIT、Calibration 与 Statistical Hygiene

机构级系统必须**主动防御四大无声陷阱**。这一节是 institutional 与 retail-grade 的分界线。

#### A. Survivorship Bias 防御 — A 股每年退市 30-50 只

A 股 5 年里数百只退市/ST/重整,如果回测 universe 只用"今天还在交易"的股票,
**所有概率都会被系统性高估 5-15pp**。

**必加表 `stock.universe_history`**:

| 字段 | 含义 |
|---|---|
| snapshot_date | PIT 日期 |
| ts_code | 当时存在的所有 A 股 |
| listing_status | active / suspended / delisted |
| board | 主板 / 创业板 / 科创板 / 北交所 |
| max_pct_chg | 当日涨跌停幅度上限(±10/20/30%) |

回测 + 分析全部走 `WHERE snapshot_date = :as_of_date AND ts_code IN universe_history` 这条 PIT 路径,
绝不允许用 "今天的全市场列表" 反推历史。

#### B. 概率校准 — Reliability Diagram + Brier Score

模型说 "prob_hit_50_40d = 18%" 但**真实的 18% 兑现率不一定是 18%**。

**必加 `stock.calibration_metrics`** 周度任务:

```
按预测 prob 分桶 [0, 5, 10, 15, ..., 100]
每桶统计实际兑现率:expected vs actual
- 偏差 ≤ 3pp → calibrated ✓
- 偏差 3-7pp → re-calibrate via Platt scaling
- 偏差 > 7pp → 模型停用,触发再训练
```

输出指标:

- **Brier score**: `mean((pred - actual)^2)` 越低越好,< 0.20 视为可用
- **Log loss**: 每周记录 trend
- **AUC** (right-tail classifier): > 0.62 视为有 signal
- **Quantile coverage** (forecaster): P75 真实覆盖率必须 ∈ [70%, 80%]

#### C. Multiple Testing Corrective + Holdout

30 setup × 11 regime × 5 horizon = 1,650 个 (setup, regime, horizon) 组合,
即使**完全随机**也会有 ~80 个看起来 p < 0.05。

**两层防御**:

1. **Bonferroni 修正**:任何参数 freeze 前,显著性必须 ÷ 1650
2. **永久 holdout window**:
   - 把 2025 Q1 (Jan-Mar) 设为**永远不参与调参**的 holdout
   - 每次 freeze 新参数,必须先在 holdout 跑 alpha ≥ +0.3pp 才能上线
   - 任何 holdout window 数据**绝不进入训练集 / 验证集**(签字画押)

#### D. Stop Hit 真实定义

A 股频繁跳空 + 涨跌停 + T+1,stop hit 的定义影响 **5-10pp 回测准确度**:

| 类型 | 定义 | 适用 |
|---|---|---|
| **intraday_stop_mark** | 5min low ≤ stop 触发标记(但不实际卖,因 T+1) | 内部诊断 |
| **eod_stop** | 当日收盘 ≤ stop → T+1 早盘卖 | 散户实操 |
| **next_open_stop**(默认) | T+1 集合竞价市价卖,fill = T+1 open | 推荐 backtest 标准 |
| **gap_down_stop** | T+1 open << stop:实际亏损 = (open - entry),超出预期 | 必须 model |

回测 + 报告 + 真实交易必须**统一用 next_open_stop**,这是 retail / 机构都能复现的 fill 假设。
gap-down 风险在 expected_drawdown 单独显示。

---

## 3. 数据源与持久化策略

### 3.1 Tushare 可用数据

| 数据层 | Tushare / 本地表 | 用途 |
|---|---|---|
| 日线 OHLCV | `raw_daily` / `daily` | 趋势、形态、波动、S/R |
| 复权因子 | `adj_factor` / `stk_factor_pro` | 前复权价格、技术指标 |
| 日基本面 | `daily_basic` | 市值、换手、PE/PB/PS、量比 |
| 分钟线 | `stk_mins` / 5min parquet | VWAP、成交密集区、T+0 模拟、Kronos intraday |
| 资金流 | `raw_moneyflow` | 主力/大单/超大单连续性 |
| 龙虎榜 | `raw_top_list`, `raw_top_inst` | 机构/游资参与、净买净卖 |
| 涨跌停 | `raw_limit_list_d`, `raw_kpl_list` | 涨停结构、封单、炸板 |
| 板块 | `sw_member_monthly`, `raw_sw_daily`, SmartMoney state | 行业坐标和板块强度 |
| 财报 | Research memory | 年报 deep + 季报 deep 基本面 lineup |
| 事件 | `anns_d`, forecast, express, disclosure_date | 业绩、重组、立案、减持、催化 |
| 筹码 | `cyq_perf` / cyq 相关 | 筹码集中、松动、成本区 |

### 3.2 5min 数据是否必须 backfill？

结论：**5min 数据必须被能力化，但 V2.2 不强制全市场 2 年预回填。**

生产路径采用 local-first + strategy-needed backfill：

- 报告目标股：当执行/T+0/分钟成交密集区策略启用且本地缺数据时，按需拉取 5min 30 日、30min 60 日、60min 90 日。
- 周末全局 preset：优先选择高流动性 500 只股票做训练样本；如果运行时间、存储、收益验证都通过，再扩大到全市场。
- 2 年全市场 5min 是后续增强项，不是 V2.2 生成首份报告的阻塞条件。

原因：

1. **支撑阻力必须用成交密集区**
   日线只能看到 H/L/C，无法知道真实成交发生在哪里。5min VWAP / volume profile 可以识别真正的成本密集区。

2. **T+0 底仓策略需要日内数据**
   没有 5min，无法验证“冲高卖、回落买”的可执行性，也无法估计 T+0 对持仓收益和回撤的改善。

3. **分钟线可以补充 Kronos / 形态证据**
   当前宁波 Kronos 主线使用日线 embedding，Stock Edge V2.2 先复用该缓存；分钟线 embedding 只在后续证明增益后扩展。

4. **执行价调参必须避免幻觉**
   如果 entry/stop/target 都基于日线，fill 假设太粗。5min 可以判断 entry zone 是否真的成交、stop 是否盘中触发、target 是否可卖。

### 3.3 1min 数据是否需要？

V2.2 主线不建议全市场 1min 回填，性价比不如 5min。

建议：

| 层级 | 方案 |
|---|---|
| V2.2 必做 | 目标股按需分钟线 sweep：5min 30日、30min 60日、60min 90日 |
| V2.2 周末任务 | 高流动性 500 只 universe 的全局 preset 训练，可按策略需要补分钟线 |
| V2.2 可选 | 对报告目标股临时拉最近 20-60 日 1min，用于精细 T+0 |
| V2.3 再考虑 | 全市场 1min 或 2年5min，前提是执行模块证明显著增益 |

1min 容易带来数据量、噪声和过拟合问题。机构级做法是先用 5min 建立稳健 edge，再在单股执行层用 1min 优化挂单。

### 3.4 推荐存储

| 数据 | 存储 | 原因 |
|---|---|---|
| 日线/资金/财务/事件 | Postgres | 权威结构化数据、审计、PIT |
| 5min / embedding / analog cache | DuckDB + Parquet | 大规模时序、扫描快、成本低 |
| report asset | Postgres + HTML/MD out | 可复用、可定位、可追踪 |
| 模型 artifact | `~/claude/ifaenv/models/stock/` | 与 repo 解耦 |

### 3.5 A 股专属信号 — 5 大必加数据源

通用技术面 + 资金流远不够。下面 5 类 A 股专属信号是 institutional 量化必备,
任何一项缺失都会让推荐失去 alpha 来源:

| 信号 | Tushare | 用途 | 优先级 |
|---|---|---|---|
| **限售股解禁** | `share_float` | 解禁前 30d 平均 -4 ~ -7%;解禁日历 catalyst + veto | P0 |
| **融资融券余额** | `margin_detail` | 个股融资余额 / 流通市值 是散户杠杆位置;5d 变化率 IC > 0.05 | P0 |
| **北向资金 per-stock** | `hk_hold` | 北向连续 5d 净买入是顶级先行指标(机构必跟) | P0 |
| **大股东 / 高管增减持** | `stk_holdertrade` | 减持公告 → 实际减持 30-60d 间隔;期内回升被压制,期满后大概率反弹 | P1 |
| **股权质押** | `pledge_stat` | 质押率 + 平仓线 = 死亡螺旋风险;P0 hard veto | P0 |

#### 3.5.1 解禁数据使用方式

```
# 30 天内解禁,且解禁市值 / 流通市值 > 5% → veto
# 14 天内解禁,且解禁市值 / 流通市值 > 10% → hard avoid
# 解禁后 60 天内,必须 model `in_post_lockup_window`
```

#### 3.5.2 融资融券作为 setup 特征

融资余额 5d 变化率作为 ML right-tail classifier 输入特征。  
此外构建独立 setup `O4_NORTHBOUND_ACCUMULATION` (见 §5.2 F)。

#### 3.5.3 质押率 hard veto

**必加 hard veto 规则**:

```
if pledge_ratio_total > 60% AND price < pledge_warning_line × 1.1:
    return Action.AVOID  # 死亡螺旋风险
elif pledge_ratio_top1 > 50% AND price < pledge_close_line × 1.2:
    return Action.AVOID  # 大股东被强平在即
```

---

## 4. 算法总架构

```
L0 Rules        规则层：趋势、形态、S/R、T+0、风控 veto
L1 Stats        统计层：条件收益、事件研究、波动分位、流动性、capacity
L2 ML           机器学习：右尾分类、收益分位、stop-first 模型、仓位模型
L3 DL           深度学习：Kronos embedding、形态 analog、异常检测
L4 LLM          解释层：归因、场景、自然语言计划；不算数字、不越权
Portfolio       决策层：合成 action、entry、stop、target、size
```

LLM 是最后一层，不是预测引擎。所有价格、概率、仓位、触发条件必须来自 L0-L3。

---

## 5. 策略族设计

### 5.1 复用 TA 已实现 setup

TA 已完成 30 setups / 11 families，是 Stock Edge 的核心技术输入。

| 家族 | 用法 |
|---|---|
| T 趋势 | 主升、突破、加速 |
| P 回踩 | 趋势内低吸 |
| R 反转 | 底部结构和 MA60 支撑反弹 |
| F 形态 | 旗形、三角、矩形整理 |
| V 量价 | 放量突破、缩量蓄势 |
| S 板块 | 板块共振、补涨、龙头跟随 |
| C 筹码 | 筹码集中/松动 |
| O 主力资金 | 机构连续抢筹、龙虎榜净买 |
| D 顶部反转 | 风险警示，不进入 long conviction |
| Z 统计 | 极端 z-score、超卖、横盘 fade |
| E 事件 | 业绩预告/快报/披露窗口催化 |

Stock Edge 要在单股维度进一步回答：

- 该股票历史上最有效的是哪些 setup？
- 当前 setup 是否与市场 regime 匹配？
- setup 在 20/40 日目标上是否有效，而不仅是 T+5/T+15？
- 当前 entry/stop/target 是否优于 TA 报告中的泛化推荐？

### 5.2 规则型策略

规则型策略负责可解释和风控。

#### A. Breakout With Base

适合目标：20-40 日 +50% 的主力策略。

条件：

- 60 日或 120 日平台突破
- 成交额放大，非单日孤立放量
- 板块强度上行
- 上方 20% 内无强阻力
- Research 财务无硬红旗

Entry：

- 首选突破后 1-3 日回踩 VWAP / MA5 / 缺口上沿
- 追高 entry 需要 reward/risk ≥ 3

Stop：

- 跌回平台上沿且收盘确认
- 或跌破关键 5min 成交密集区

#### B. Pullback In Uptrend

条件：

- 120 日趋势向上
- 回踩 MA20 / MA60 / 前高支撑
- 缩量回踩，未破结构
- TA P 族或 R4 触发

适合：

- 胜率较高，右尾不如突破，但 risk/reward 稳定。

#### C. Event + Technical Coil

条件：

- 财报/订单/政策/研报催化
- 价格未充分反映
- 矩形/三角收敛
- 成交额逐步抬升

适合：

- A 股常见的事件驱动启动前形态。

#### D. Oversold Reversal

条件：

- Z2/Z3/R1/R4 触发
- 下跌接近强支撑
- 卖压衰竭
- 板块不再继续恶化

限制：

- 不能用 +50% 作为默认目标；多数是 10-25% 修复。
- 只有叠加强催化或低位主线切换，才进入 +50% 候选。

#### E. Leader Continuation

条件：

- 已经是板块龙头或容量中军
- SmartMoney 板块强度高
- 龙虎榜/机构/大单资金连续
- 涨停后不炸板或炸板后快速修复

风险：

- 高波动，必须有 T+0 底仓操作和严格止损。

#### F. Northbound Accumulation (M10 P3 新增)

条件:

- 北向 (`hk_hold`) 连续 5 天净增持 (`hold_ratio` 5d 上升)
- 净增持市值占流通市值 > 0.3%
- 个股 5d 涨幅 < 板块 5d 涨幅 (北向先于价格上行)
- TA regime 不在 distribution_risk

适合: 机构标准的"先于趋势"信号,持仓周期可拉到 40-60 日。

#### G. Margin Squeeze Reversal

条件:

- 融资余额 5d 下降 > 10% (散户砍仓)
- 同期股价已跌 > 15%
- 板块指数企稳
- TA Z2 / R4 / R1 触发 (技术筑底)

适合: A 股特有的"散户被洗 → 机构低吸 → 反弹"模式。
持仓周期 20-40 日。

#### H. Insider Buy Confirmation

条件:

- 大股东或高管 (`stk_holdertrade`) 增持公告
- 增持金额 > 个股 5d 平均成交额
- 公告后 5 日内股价回踩公告日 ±3%
- TA P 族或 R 族确认

适合: A 股最 robust 的信号之一,
机构 backtest IC 0.07+ at 40-day horizon。

#### I. Pre-Earnings Coiling

条件:

- 距下次披露日 (`disclosure_date`) 7-21 天
- 60d 横盘 + 量能逐步抬升
- Research 季报 / 年报 deep 显示基本面拐点
- 板块强度不退潮

适合: 业绩预期驱动的事件型 setup, 持仓周期 ~披露日 +5。

#### J. Post-Reduction Recovery

条件:

- 大股东减持窗口已结束 (`reduction_end_date < as_of - 5d`)
- 减持期内股价跌幅 > 8%
- 减持完成后量能回升 > 平台均值 1.3×
- 无新增减持公告

适合: A 股经典的"减持压力释放后修复"。

### 5.3 统计策略

统计层是防止“看图说话”的核心。

| 策略 | 方法 | 输出 |
|---|---|---|
| Conditional Forward Return | 按 setup + regime + sector_phase 分组统计未来 20/40 日收益 | 分位数、胜率、回撤 |
| Event Study | 公告/业绩/研报后 N 日异常收益 | 催化有效性 |
| S/R Touch Study | 触及支撑/阻力后的反弹/跌破概率 | entry/stop 质量 |
| Volume Shock Study | 放量日后 5/20/40 日收益分布 | 是否真启动 |
| Relative Strength Persistence | 个股强于板块/指数后延续概率 | 趋势可信度 |
| T+0 Improvement Study | 底仓高抛低吸是否提升收益/降低回撤 | T+0 模块开关 |

验证层必须覆盖多窗口：

- 60d：近期适应性
- 180d：中期稳健性
- 360d：跨 regime 稳健性

TA 的经验已经证明：只看 60d 很容易过拟合，必须同时看 60/180/360。报告触发时的单股调参不是 rolling walk-forward，而是基于最近可用历史做一次 bounded overlay search；walk-forward/OOS 用于周末 preset 晋级和算法治理。

### 5.4 ML 策略

ML 不直接给“买卖建议”，而是给概率和分位。

#### Model 1: Right-Tail Classifier

目标：

```
label_hit_50_40d = max(high[t+1:t+40]) / entry_price - 1 >= 50%
```

输出：

- `prob_hit_50_20d`
- `prob_hit_50_40d`
- `prob_hit_30_40d`
- `prob_stop_first`

特征：

- TA setup one-hot + scores
- regime / sector phase
- Research 5 维财务分数和最近季度变化
- 资金流连续性
- S/R reward-risk
- 5min volume profile
- Kronos analog stats

#### Model 2: Quantile Return Forecaster

预测未来 20 / 40 / 60 日收益分布：

- P10, P25, P50, P75, P90
- 回撤分布
- time-to-target 分布

推荐 LightGBM quantile / XGBoost quantile，先不要上复杂深度模型做主预测。

#### Model 3: Entry Fill / Execution Model

判断推荐 entry zone 是否可成交：

- 次日 low 是否触达 entry
- 触达后是否先 stop
- 开盘 gap 是否导致追高
- 涨停是否无法买入

这是 A 股交易计划里非常关键的一层。

#### Model 4: Position Sizing Model

输入：

- confidence
- reward/risk
- drawdown
- liquidity
- model agreement

输出：

- no_trade
- watch
- trial_size
- normal_size
- high_conviction_size

仓位模型要保守，宁愿少给高 conviction。

### 5.5 DL / Kronos 策略

Kronos-small 的最佳用途不是黑盒预测，而是表征学习。

#### A. Daily Shape Analog

输入：

- 128 日复权 OHLCV
- 可加 volume / turnover / amount

输出：

- Top K 历史相似形态
- 相似样本未来 20/40 日收益分布
- 是否出现右尾样本
- 是否出现快速失败样本

#### B. Intraday Shape Analog

输入：

- 最近 20-60 日 5min bars
- VWAP deviation
- 尾盘量能
- 开盘承接

输出：

- 类似日内结构后的隔日/5日表现
- T+0 高抛低吸胜率
- 是否存在尾盘抢筹/诱多风险

#### C. Regime-Conditional Embedding

Kronos analog 必须按 regime / sector phase 过滤或加权：

- 同形态在 trend_continuation 可能上涨
- 在 distribution_risk 可能是假突破
- 在 range_bound 可能只是震荡噪声

#### D. Anomaly Detection

识别当前形态是否远离历史分布：

- 价格异常
- 成交量异常
- 日内波动异常
- 高位派发异常

异常不是自动看多，很多时候是风险。

### 5.6 LLM 策略

LLM 只做四件事：

1. 把 L0-L3 的数字证据转成 PM/Trader 能读的解释。
2. 写三场景树：bull / base / bear。
3. 整理“什么会改变我们的判断”。
4. 做报告 UI 文案和风险提示。

工程约束：

- 必须使用项目内置 `ifa.core.llm.LLMClient`，不要用开发者当前对话里的模型临场生成报告内容。
- LLM 输出要按项目既有方式持久化，至少记录 model、prompt version、raw response、latency 和 as_of。
- LLM 只能解释结构化证据，不能替代策略、ML、Kronos、S/R 或风控层生成数字。

禁止：

- 编造价格
- 重算概率
- 给没有数据依据的目标价
- 用“必涨”“稳赚”等措辞

---

## 6. 买卖价格生成

### 6.1 Entry Zone

Entry 不是一个点，而是区间：

```
entry_low  = max(strong_support, vwap_cluster_low, pullback_level)
entry_high = min(breakout_retest, current_price * (1 + max_chase_pct))
```

来源：

- MA20 / MA60
- 平台上沿
- 缺口上沿/下沿
- 5min 成交密集区
- VWAP
- ATR 回撤
- 筹码成本区

### 6.2 Stop / Invalidation

止损价必须同时对应价格和逻辑：

| 类型 | 失效条件 |
|---|---|
| Breakout | 跌回平台并连续收盘确认 |
| Pullback | 跌破 MA60 / swing low |
| Event | 催化证伪或公告不及预期 |
| Leader | 板块退潮 + 个股跌破 5/10MA |
| Mean Reversion | 跌破支撑后无反抽 |

Stop 距 entry 过远时，自动降低仓位或不交易。

### 6.3 Target

目标价分三层：

| 目标 | 用途 |
|---|---|
| T1 | 保守兑现，通常来自最近阻力或 +10-20% |
| T2 | 主目标，来自 measured move / P75 / 强阻力 |
| Right-tail | +50% 目标，来自 P90 / analog right-tail / 主升浪测算 |

目标 +50% 不能单独存在。必须显示：

- 达到概率
- 预计用时
- 期间预期回撤
- 失效条件

### 6.4 ATR 三段位

继承 TA 经验：

- entry: 支撑位附近，或突破回踩
- stop: `entry - k_stop * ATR`
- target: `entry + k_target * ATR`

但 Stock Edge 要叠加 S/R：

- 如果 ATR target 正好撞上强阻力，目标下调。
- 如果 strong support 高于 ATR stop，stop 上调。
- 如果 entry 到 stop 太宽，仓位下降。

### 6.5 Execution Layer — 推荐价 ≠ 实际成交价

机构系统必须把推荐 entry zone 转成**可执行的订单方案**,
否则零售用户照单下单会持续亏在 slippage 上。

#### 6.5.1 Slippage / Market Impact 模型

slippage 不是单一数字,按订单大小分三档:

| 订单大小 (% 流通市值) | slippage model |
|---|---|
| < 0.01% (小单) | `ATR × 5%` |
| 0.01-0.1% (中单) | `ATR × 20% + 0.5 × bid-ask spread` |
| 0.1-1% (大单) | square-root impact: `0.1 × ATR × sqrt(size_pct)` |
| > 1% (机构) | TWAP/VWAP 必须分批,model `0.3 × ATR × (size_pct)^0.5` |

每份 trade plan **必须输出** capacity:

```
Recommended Position Size: ¥100K (retail capacity)
Capacity (institutional): ¥3M (slippage budget < 30bp)
Beyond ¥10M: must use TWAP slicing across 5 trade days
```

#### 6.5.2 5min Volume Profile 驱动的 Slicing

大单不能一次吃。按 day-of-week 历史 volume profile 切分:

```
Time slot     | Avg vol % | Recommended slice
9:30-10:00    | 18%       | 25% (开盘流动性最好)
10:00-11:30   | 28%       | 35%
13:00-14:00   | 18%       | 20%
14:00-14:50   | 24%       | 15%
14:50-15:00   | 12%       | 5%  (尾盘只清剩余)
```

输出 `optimal_execution_schedule` 字段。

#### 6.5.3 Order Ticket 输出格式

机构 trader 要的是**可直接复制粘贴到券商客户端**的订单:

```
─────────────────────────────────────────────
ORDER TICKET — 600519.SH 贵州茅台
─────────────────────────────────────────────
方向    : 买入 1000 股 (¥186,000)
触发    : 当日 low ≤ ¥180.00
执行    : 限价 ¥186.00 ÷ 3 笔分批,每笔 333 股
止损    : 收盘 ≤ ¥172.00 → 次日开盘市价卖出
目标 T1 : 限价卖 25% @ ¥210.00
目标 T2 : 限价卖 50% @ ¥240.00
目标 RT : 限价卖 25% @ ¥279.00 (+50% right-tail)
有效期  : T+1 集合竞价 → T+5 收盘
slippage 预算: 30bp
─────────────────────────────────────────────
```

#### 6.5.4 Slippage Tracking — 推荐价 vs 实际成交

闭环验证必须采集真实 fill 价。新增表 `stock.fill_log`:

| 字段 | 含义 |
|---|---|
| plan_id | 关联 trade_plan |
| ts_code | 股票 |
| recommended_price | 推荐价 |
| actual_fill_price | 用户回报真实成交价 |
| fill_time | 成交时间 |
| size | 成交股数 |
| slippage_bps | (actual - recommended) / recommended × 10000 |

按市值分桶统计 slippage,用于 capacity model 持续校准。

---

## 7. T+0 底仓模块

### 7.1 A 股 T+0 的真实定义

A 股不能裸 T+0。只有已有底仓时，可以：

- 盘中高位卖出一部分底仓
- 盘中低位买回
- 或低位先买，使用原有底仓在高位卖出

系统必须明确：

```
eligible_for_t0 = existing_position > 0
```

无底仓用户只输出“次日 entry plan”，不输出 T+0 操作。

### 7.2 T+0 策略类型

| 策略 | 条件 | 动作 |
|---|---|---|
| High Sell / Low Buy | 开盘冲高到阻力，量能不跟 | 卖出 20-30% 底仓，回落 VWAP 买回 |
| Low Buy / High Sell | 开盘急跌到支撑，承接强 | 买入临时仓，反弹到 VWAP/阻力卖出底仓 |
| VWAP Mean Reversion | 日内围绕 VWAP 大幅偏离 | 偏离阈值交易 |
| Gap Fill | 跳空后回补概率高 | 按缺口边界做日内计划 |
| Tail Rush Risk | 尾盘拉升但日线高位 | 不追，次日观察承接 |

### 7.3 T+0 输出格式

```
T+0 Plan: Eligible only if holding base position
Base Position: 100%
Sell Zone: 31.80-32.40 (near resistance + VWAP extension)
Buyback Zone: 30.20-30.60 (VWAP cluster + MA5)
Max T+0 Size: 20% of base
Do Not T+0 If: open gap > +6%, limit-up one-line, volume < 50% normal
Expected Improvement: +0.4% daily alpha / -0.8pp drawdown reduction based on 5min backtest
```

### 7.4 T+0 回测标签

必须用 5min 数据验证：

- daily return without T+0
- return with T+0
- drawdown improvement
- failed buyback rate
- sell-too-early opportunity cost
- liquidity-adjusted fill rate

如果 T+0 策略长期不能提升收益/降低回撤，则只作为提示，不进入 action。

---

## 8. 最终决策合成

### 8.1 Score Components

| 组件 | 权重初始值 | 说明 |
|---|---:|---|
| TA Setup Edge | 25% | 来自 TA 30 setups 与单股历史表现 |
| S/R Reward-Risk | 20% | Entry/stop/target 质量 |
| Right-tail Probability | 20% | ML + analog 的 +50% 概率 |
| Fundamental Quality | 15% | Research 年报/季报 deep |
| Money / Sector | 10% | SmartMoney、龙虎榜、资金流 |
| Catalyst | 5% | 事件驱动 |
| Execution Quality | 5% | 流动性、成交、涨跌停 |

权重必须 YAML 化，可按 regime 动态调整。

### 8.2 Action Mapping

| 条件 | Action |
|---|---|
| p50 正、P(+50%) 高、RR ≥ 3、无 veto | Buy |
| 结构好但 entry 不好 | Watch |
| 基本面/资金/TA 冲突 | Watch / Avoid |
| 跌破失效条件 | Exit |
| 旧 deep 14 天内存在 | Update |

### 8.3 Position Size

| 等级 | 仓位建议 |
|---|---|
| 禁止 | 0 |
| 观察 | 0 |
| 试错 | 0.25x |
| 标准 | 0.5x |
| 高置信 | 1.0x |

这里的 x 是策略预算，不是账户满仓。任何”高置信”都必须满足流动性和 stop-first 风险。

### 8.4 Portfolio Level Risk — 单股 Plan 不够,组合层必须

机构组合管理的核心:**单股 alpha 真实,但组合可能因集中而崩盘**。

#### 8.4.1 Trade Plan Correlation Matrix

每天系统对多只股票喊 BUY,如果它们高度相关,**风险不是简单加和而是相关矩阵加权**。

每份 portfolio_view 必须输出:

```
今日推荐组合(BUY action 共 5 只):
- 600519 / 000858 / 002304 (3 只白酒,板块 80% 相关)
- 600276 (医药)
- 300750 (新能源车)

Sector Exposure: 白酒 60% / 医药 20% / 新能源 20%
Portfolio Beta: 1.21 (vs CSI 300)
Portfolio Expected Drawdown: -12.3% (用历史相关矩阵 simulate)
集中度警告: 白酒 60% > 30% threshold ⚠

建议: 削减白酒至 30%, 加 1-2 只科技/消费 plan 平衡
```

任意单 sector 暴露 > 30% → portfolio-level YELLOW flag,
> 50% → RED flag (强制分散)。

#### 8.4.2 Crowding Metric

**机构拥挤度 = 多少其他系统/玩家也在喊买这只票**:

```
crowding_score = w1 × 龙虎榜机构席位数_norm
               + w2 × 北向 5d 净增持_pct_norm
               + w3 × ETF 持仓变化_pct_norm
               + w4 × 公募季报新进股东数_norm
       ∈ [0, 1]
```

机构 backtest 显示: **拥挤度 > 0.7 的股票,出场流动性差,真实 drawdown 翻倍**。

Position size 必须按 `(1 - 0.5 × crowding_score)` 折算。
推荐 confidence:HIGH 的票如果 crowding > 0.8,自动降为 MEDIUM。

#### 8.4.3 Stress Test Mode

不能只看 60d/180d/360d 平均表现。必须显式跑历史压力情景:

| 压力情景 | 时间窗 | 测试什么 |
|---|---|---|
| 2018 贸易战 | 2018-06 → 2018-12 | 系统性风险 + 流动性枯竭 |
| 2024-01 流动性危机 | 2024-01 (单月) | 极端 drawdown 情景 |
| 2015 股灾 | 2015-06 → 2015-09 | 千股跌停场景 |
| 2021-02 抱团瓦解 | 2021-02 → 2021-04 | 风格切换风险 |
| 2024-09 政策反转 | 2024-09-24 后 5 周 | 单边大涨执行能力 |

**新增 CLI**:

```bash
ifa stock-edge stress-test --scenario 2018-trade-war --portfolio current
```

输出:

- max drawdown 在该情景下
- recovery time(几天回到 0)
- 单股最大亏损
- correlation 暴露在该情景下是否爆表

任何参数 freeze 前必须通过所有 5 个 stress test 的 max DD < risk_budget × 1.5。

---

## 9. 回测与调参体系

### 9.1 必须复用 TA 的经验

TA 开发已经证明：

- 只看短窗口会过拟合。
- range_bound 是结构性 alpha 黑洞。
- ATR entry/stop/target 是重要杠杆。
- regime-aware sizing 比硬选票更稳。
- setup historical performance 是滞后信号，不能过度加权。
- 60d / 180d / 360d 多窗口验证必做。

Stock Edge 的任何参数调整都必须跑：

```
60d  recent adaptation
180d medium robustness
360d cross-regime robustness
```

### 9.2 标签体系

| 标签 | 定义 |
|---|---|
| `hit_20pct_20d` | 20 日内最高价达到 +20% |
| `hit_50pct_40d` | 40 日内最高价达到 +50% |
| `max_return_40d` | 40 日内最大收益 |
| `max_drawdown_40d` | 40 日内最大回撤 |
| `stop_first` | 先触发 stop 再触发 target |
| `time_to_50pct` | 达到 +50% 所需交易日 |
| `entry_filled` | entry zone 是否成交 |
| `t0_alpha` | T+0 相对不操作的收益改善 |

### 9.3 回测必须模拟执行

不能只用 close-to-close：

- entry zone 是否触达
- stop 是否盘中触发
- target 是否盘中可卖
- 涨跌停是否可成交
- 一字板是否排队失败
- T+1 限制
- T+0 必须有底仓

### 9.4 调参原则

参数全部 YAML 化，并采用三层来源：

1. **全局 preset**：周末在高流动性 500 只股票或全市场样本上重训，产出默认参数。
2. **单股 pre-report overlay**：用户触发报告时，如果 10 天内没有新 artifact，就基于该股票历史做一次连续参数搜索，生成该股 overlay。
3. **离线验证参数**：walk-forward/OOS/holdout 用来决定 preset 是否可晋级，不作为报告时的实时滚动调参。

参数全部连续化，避免硬阈值造成不稳定跳变：

- holding_days: 20-40 主窗口，60 仅作风险观察
- right_tail_target: 0.50
- min_prob_hit_50
- min_reward_risk
- max_stop_pct
- min_turnover_amount
- setup weights
- regime weights
- t0 thresholds
- Kronos top_k
- S/R clustering pct
- ATR k_stop / k_target

调参目标不是最大收益，而是预测执行质量：

```
objective = 0.30 * hit_target_40d_quality
          + 0.20 * expected_return_40d
          + 0.15 * entry_fill_quality
          + 0.15 * reward_risk
          + 0.10 * calibration_quality
          - 0.15 * expected_drawdown
          - 0.10 * stop_first_rate
          - 0.05 * turnover_liquidity_penalty
```

优秀统计学习/机器学习成果可以直接借鉴的部分：

- **isotonic / monotonic binning**：把规则分数校准为真实概率，防止“分数看起来很高但命中率不高”。
- **Platt / logistic calibration by regime bucket**：按牛熊、震荡、SW L2 phase、大小盘风格分桶校准概率。
- **quantile regression / conformal prediction**：输出 20/40 日 p10/p50/p90 收益区间，而不是只给一个点预测。
- **survival / hazard model**：估计未来 N 日先触及目标价、先触及止损、或一直不成交的时间分布。
- **Bayesian shrinkage / hierarchical model**：单股样本少时向行业、风格、全市场 preset 收缩，避免过拟合。
- **gradient boosting / random forest meta-model**：把规则、TA、SmartMoney、Research、Kronos、分钟执行特征融合成右尾概率和 stop-first 概率。
- **nearest-neighbor analog / Kronos embedding**：找相似历史形态，读取之后 20-40 日路径分布，为右尾概率加证据。
- **uplift / treatment-style model for T+0**：判断底仓高抛低吸是否真正改善收益回撤，而不是只增加交易次数。

---

## 10. Report / UI 设计

### 10.1 页面原则

这是交易工具，不是营销页。

- 首屏必须显示 Action、Entry、Stop、Targets、P(+50%)。
- 不做大 hero，不做装饰性图。
- 数据密度高但清晰。
- 所有数字都带来源和 as_of 日期。
- 涨为红，跌为绿，风险红灯仍按风险语义。

### 10.2 Deep Report Sections

```
§01 Trade Plan 一页总览
§02 买卖时机预测卡（今日/未来5日买点，20/30/50%卖点，失效条件）
§02A 预测执行场景树（今日执行/今日等待/未来5日/失效路径）
§03 Data Cutoff + 数据完备性
§04 Research 基本面摘要（年报 deep + 季报 deep）
§05 TA Setup Evidence（命中 setup + 历史 edge）
§06 多周期趋势矩阵
§07 支撑阻力地图 + Entry/Stop/Target
§08 资金与板块强度
§09 催化与风险事件
§10 Kronos 历史相似形态
§11 20/40/60 日收益分布预测
§12 T+0 底仓操作计划
§13 What Changes Our Mind
§14 Tracking Plan
§15 完整免责声明
```

### 10.3 首屏卡片

```
Action: BUY / WATCH / AVOID
As of: 2026-05-05 close
Entry: 28.40-29.20
Stop: 26.80 close below
Targets: 33.50 / 39.80 / right-tail 43.80 (+50%)
P(+50%, 40d): 18%
Reward/Risk: 3.4
Position: trial 0.25x
🚦 拥挤度: 中等 (机构 12 / 北向 +¥80M / ETF +0.3%)
T+0: eligible only with base position
```

### 10.4 UI/UX 深化 — 5 处机构级改进

#### 10.4.1 Mobile-First First-Screen

A 股交易员 60%+ 时间在手机看推荐。首屏必须**移动端一屏内**完成 BUY / WATCH / AVOID 决策:

- Action / Entry / Stop / Targets / P(+50%) 必须放在**屏幕首屏**(< 600px 高)
- 拥挤度和关键风险随后；数据新鲜度留在内部审计记录，不进入报告 UI
- 详细 evidence 折叠在下方
- 横向滚动: Tier A 多只之间 swipe 切换

#### 10.4.2 Order Ticket QR Code

Order ticket(§6.5.3 输出)生成 QR 码,
扫码打开手机券商 app **自动填单**(对接同花顺/华泰/中信等主流 broker schema)。

机构 demo 必杀技,显著降低执行 slippage。

#### 10.4.3 Heatmap + Interactive Analog

§5 多周期趋势矩阵 / §09 Kronos analog 不能是静态图,必须 interactive:

- 点击某个 analog case → 弹出该样本完整 OHLC + 关键事件
- Heatmap 网格悬停 → 显示该 (regime, horizon, setup) 的统计 detail
- 让用户可以**质疑系统的判断**,这是机构 PM 信任系统的前提

#### 10.4.4 Color-Blind Friendly + 双编码

红绿不够 — A 股客户 5-8% 色盲:

- 红绿 + **形状/纹理双编码**: ↑△ 涨 vs ↓▽ 跌
- 风险红灯 = 红 + 实心圆
- 黄灯 = 黄 + 三角
- 绿灯 = 绿 + 方块

#### 10.4.5 Compliance Print Mode

中国监管对量化推荐合规要求越来越严。必须可一键 export:

- PDF 完整页面 + 所有数据来源 + as_of timestamp
- **Input data hash** (SHA256 of input snapshot)
- Model versions used
- Parameter YAML hash
- 操作员 ID + 生成时间(BJT, with UTC offset)

PDF 必须可被合规存档 7 年(中国证券业协会要求)。

---

## 11. DB / Artifact 设计

### 11.1 Postgres

| 表 | 用途 |
|---|---|
| `stock.analysis_record` | 一次分析 run（已存在） |
| `stock.report_sections` | 报告章节（已存在） |
| `stock.support_resistance` | 多源 S/R（已存在） |
| `stock.tracking_log` | 后续兑现与复盘（已存在） |
| `stock.user_watchlist` | 观察清单（已存在） |
| `stock.user_context` | 用户持仓/偏好上下文（已存在） |
| `stock.analysis_lock` | 并发锁（已存在） |
| `stock.prediction_snapshot` | 20/40/60 日分位预测（需要时新增） |
| `stock.analog_cases` | Kronos 相似案例（需要时新增） |
| `stock.t0_plan` | 底仓 T+0 计划（需要时新增） |

### 11.2 DuckDB / Parquet

| 数据 | 路径 |
|---|---|
| 5min bars | `~/claude/ifaenv/duckdb/parquet/intraday_5min/` |
| Kronos embeddings | `~/claude/ifaenv/duckdb/parquet/kronos/` |
| Analog cache | DuckDB native |
| Backtest snapshots | Parquet partitioned by run |

### 11.3 模型 artifacts

```
~/claude/ifaenv/models/stock/
  right_tail_classifier_v1/
  quantile_forecaster_v1/
  stop_first_model_v1/
  t0_model_v1/
  kronos_index_v1/
```

### 11.4 Compute Lock + Cache + Audit + Version

机构系统的四道工程闸门,任何一项缺失都不算 production-ready:

#### 11.4.1 Idempotency Cache

同一个 `(ts_code, as_of_trade_date)` 多次 request 必须**返回缓存**,
不允许重新跑 ML 模型(成本高 + 结果应该 deterministic)。

新增表 `stock.result_cache`:

| 字段 | 含义 |
|---|---|
| cache_key | hash(ts_code + as_of_date + model_version + param_hash) |
| trade_plan_json | 完整 plan 序列化 |
| computed_at | 生成时间 |
| ttl | 缓存过期 (默认 trade_date + 1 day) |

第二次同 key request → 直接返回 cache,latency < 100ms。
ML 模型 / Kronos 重跑只在 model_version 或 param_hash 变化时触发。

#### 11.4.2 Compute Lock — 防并发重复计算

使用已存在的 `stock.analysis_lock` (文档 §11.1 已提,这里展开):

| 字段 | 含义 |
|---|---|
| lock_key | (ts_code, as_of_date) |
| holder_id | 当前持锁的 process / request id |
| acquired_at | 获取时间 |
| expires_at | 锁超时(默认 5 分钟) |

伪代码:

```python
with try_acquire_lock(ts_code, as_of_date, timeout=5min) as lock:
    if cached := result_cache.get(cache_key):
        return cached
    plan = compute_trade_plan(...)  # ~30-60s
    result_cache.put(cache_key, plan)
    return plan
# 第二个并发 request 等锁释放,直接读 cache
```

#### 11.4.3 Model Version Registry

V2.2 阶段还没有已上线的生产模型,因此**不做 A/B switching**。V2.2 只做模型版本登记、
结果留痕、单一 baseline 的 forward evaluation 和 calibration,避免在模型尚未成熟前引入流量切分复杂度。

任何模型 artifact 必须带版本,不允许 silent swap:

```yaml
models:
  right_tail_classifier:
    baseline_version: v0.1
    status: forward_eval       # no A/B in V2.2
    promoted_at: null

  quantile_forecaster:
    baseline_version: v0.1
    status: forward_eval
```

所有预测 result 必须记录 `model_version_used` 字段,
后续可按 version 反查 alpha / drift。

V2.3+ 才考虑 A/B switching,前提是至少存在一个经过 calibration 和 forward eval 验证的 production baseline,
以及一个候选模型能在固定 holdout / forward window 上稳定提升。

#### 11.4.4 Audit Log — 任何参数改动留痕

新增表 `stock.param_change_log`:

| 字段 | 含义 |
|---|---|
| changed_at | 时间 |
| user_id | 操作员 (cron / human / agent) |
| param_path | yaml dotted path (如 `recommended_price.k_stop`) |
| old_value | 改前 |
| new_value | 改后 |
| reason | 必填,文字说明(如 "180d backtest +0.4pp alpha") |
| backtest_run_id | 关联 backtest 结果(如有) |

任何 yaml 改动通过 CLI / cron 必须经过这个 log。
合规审计 / debug 一查就知道哪天哪个参数动过。

#### 11.4.5 Calibration & Drift Monitoring

新增表 `stock.calibration_metrics` (周度任务):

| 字段 | 含义 |
|---|---|
| run_date | 运行日期 |
| model_name | 哪个模型 |
| model_version | 模型版本 |
| brier_score | 越低越好 |
| log_loss | 越低越好 |
| auc | right-tail classifier |
| reliability_buckets | JSONB 各 prob 桶的 expected vs actual |
| drift_kl_divergence | 当前预测分布 vs 历史训练分布的 KL |

周度任务 cron 跑,如果连续 2 周 metric 显著恶化 → **自动停用模型 + alert**。

---

## 12. 与 Research / TA 的接口

### 12.1 Research

调用：

```python
load_fundamental_lineup(engine, ts_code)
```

要求：

- 若年报 deep 或季报 deep 不存在，先触发 Research 生成。
- 使用已登记 report asset，不重复生成。
- 只读结构化 memory，不解析 HTML。

### 12.2 TA

读取：

- `ta.candidates_daily`
- `ta.warnings_daily`
- `ta.position_events_daily`
- `ta.setup_metrics_daily`
- `ta.regime_daily`
- `ta.blacklist_daily`

Stock Edge 不能重新发明 setup；它要消费 TA 的 setup 和 edge，再做单股层面的二次决策。

### 12.3 SmartMoney

读取：

- 板块资金流
- 板块 phase
- 个股资金流
- 龙虎榜/机构参与

---

## 13. Milestones

### SE-M1: Schema + data cutoff router

- `as_of_trade_date` 规则
- report asset / lock
- Research / TA / SmartMoney interfaces

### SE-M2: 5min backfill + DuckDB

- 目标股按需分钟线 sweep
- 周末高流动性 universe 训练样本 backfill
- 全市场 2 年 5min 作为验证增益后的扩展项
- 单股查询 <50ms
- coverage check

### SE-M3: S/R + Entry/Stop/Target

- 多源 S/R
- ATR 三段位
- execution simulator

### SE-M4: TA Edge Integration

- 30 setup 消费
- 单股历史 setup 表现
- 60/180/360d robustness

### SE-M5: Kronos Analog

- daily embedding
- 5min embedding
- Top K analog
- analog forward return distribution

### SE-M6: ML Prediction

- right-tail classifier
- quantile forecaster
- stop-first model
- T+0 model

### SE-M7: T+0 Module

- 底仓资格
- 5min 回测
- high-sell/low-buy plan

### SE-M8: Report + UI

- trade plan first screen
- charts / S/R map / probability distribution
- desktop/mobile verification

### SE-M9: Tracking

- 每日兑现检查
- validation / invalidation
- Update mode

### SE-M10: A 股专属信号 ETL (P0)

- `share_float` 限售解禁日历 ETL
- `margin_detail` 融资融券余额日度 ETL
- `hk_hold` 北向 per-stock ETL
- `stk_holdertrade` 大股东减持 ETL
- `pledge_stat` 股权质押 ETL
- 5 个新 setup (F-J) 注册到 SETUPS dict

### SE-M11: PIT + Calibration

- `stock.universe_history` PIT 表(含退市股)
- `stock.calibration_metrics` 周度任务
- Reliability diagram + Brier + Log loss 周报
- 2025 Q1 holdout window 锁定不调参

### SE-M12: Portfolio + Stress Test

- `portfolio_view` correlation matrix + sector exposure
- `crowding_score` 计算 + 在 confidence 中折算
- `ifa stock-edge stress-test` CLI + 5 历史压力情景
- Position size 按 crowding 折算

### SE-M13: Execution Layer

- Slippage / market impact 三档模型
- 5min volume profile slicing
- Order ticket 输出格式 + QR code
- `stock.fill_log` 闭环采集

### SE-M14: Engineering 闸门

- `result_cache` (idempotency)
- `analysis_lock` (并发)
- `param_change_log` (audit)
- model_version registry + forward eval
- Compliance Print mode (PDF + hashes)

---

## 14. Completion Criteria

Stock Edge V2.2 主功能完成的标准（institutional grade）：

### 数据 + 数据质量

1. 给定任意 A 股,能基于正确 `as_of_trade_date` 生成 trade plan
2. 年报 deep + 季报 deep 基本面被正确复用
3. TA 30 setup / warnings / metrics 被正确消费
4. 5min 数据至少 2 年可查,支持 S/R、Kronos、T+0 回测
5. **`universe_history` PIT 表存在且回测严格 PIT**(防 survivorship bias)
6. **A 股专属 5 类信号 ETL 全部上线**(限售解禁 / 融资融券 / 北向 / 减持 / 质押)

### 算法 + 量化严谨性

7. Entry / stop / target 来自可审计 S/R + ATR + execution 模型
8. P(+20%, 15d) / P(+30%, 25d) / P(+50%, 40d)、收益分位、MFE/MAE 收益风险面、路径形态簇、买入路线概率、开盘跳空风险、同行财务 alpha、涨停事件路径和连续仓位来自 ML / analog / 统计集成
9. **概率校准达标** Brier < 0.20, AUC > 0.62, reliability 偏差 ≤ 5pp
10. **Stop hit 默认 next_open_stop**, gap-down 风险单独显示
11. T+0 只在有底仓时输出,并有 5min 历史验证

### 调参 + 验证

12. 所有参数 YAML 化
13. 60d / 180d / 360d 回测均不失效
14. **2025 Q1 holdout window 永不调参**, freeze 前必须在 holdout 跑赢 +0.3pp
15. **5 个历史压力情景 stress test 全部通过** (max DD < risk_budget × 1.5)

### 执行 + 组合

16. **每份 trade plan 输出 capacity** (retail / institutional 两档 + slippage 预算)
17. **portfolio_view 输出** sector exposure / portfolio beta / correlation drawdown
18. **crowding_score** 影响 position size, > 0.7 自动降级 confidence
19. Order ticket 可直接复制到主流券商客户端

### UI + 合规

20. 报告 UI 首屏能让 PM/Trader 在 30 秒内判断:买、等、避、卖
21. **Mobile-first 首屏**(< 600px 高完成决策)
22. **Color-blind friendly** (双编码)
23. **Compliance Print mode** PDF + input hash + model version + param hash 全留痕

### 工程闸门

24. `result_cache` + `analysis_lock` 防并发重算
25. `param_change_log` 任何参数改动留痕
26. Model version registry + forward eval 框架就绪;A/B switching defer 到 V2.3+
27. `calibration_metrics` 周度任务运行,自动检测漂移并报警

---

## 15. 核心结论

Stock Edge 的目标不是“预测一只股票会不会涨”，而是构建一套机构级单股交易决策系统：

- Research 判断这家公司有没有基本面硬伤或财报拐点。
- TA 判断当前形态和 regime 有没有历史 edge。
- SmartMoney 判断资金和板块是否站在同一边。
- Kronos 判断当前形态在历史上像什么。
- ML 给出右尾概率、收益分布、先止损概率。
- 规则层给出买入区间、止损、目标、仓位和 T+0 底仓计划。
- LLM 只把这些证据解释成人能快速执行和复盘的交易计划。

最终输出不是一句“看好”，而是一张能被执行、被质疑、被跟踪、被复盘的交易单。
