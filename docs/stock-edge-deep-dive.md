# Stock Edge Deep Dive — 个股作战室

> **状态**：V2.2 第三主功能设计稿  
> **替代命名**：原 `Stock Intel` 更名为 **Stock Edge（个股作战室）**，避免与 Intel 品牌混淆，并强调本模块的核心目标是寻找可验证的交易 edge。  
> **定位**：给定一只 A 股和一个数据截止时点，产出 20-40 个交易日持仓周期内的可执行交易计划：买入区间、卖出目标、止损/失效条件、T+0 底仓操作方案、概率分布和复盘条件。  
> **核心目标**：寻找未来 20-40 个交易日具备 **+50% 右尾机会** 的交易结构，同时严格控制下行风险、流动性、可执行性和前视偏差。

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
| 涨跌停 | Entry / stop / target 必须考虑涨跌停不可成交 |
| 集合竞价 | 开盘跳空可能导致 entry 无法成交，需要 gap handling |
| 一字板 | 右尾概率高但成交概率低，必须区分 “理论收益” 和 “可成交收益” |
| 散户情绪与龙虎榜 | 短线 20-40 日目标高度依赖资金结构，不可只看财务 |

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

结论：**必须做 5min backfill，至少 2 年，全市场。**

原因：

1. **支撑阻力必须用成交密集区**
   日线只能看到 H/L/C，无法知道真实成交发生在哪里。5min VWAP / volume profile 可以识别真正的成本密集区。

2. **T+0 底仓策略必须有日内数据**
   没有 5min，无法验证“冲高卖、回落买”的可执行性，也无法估计 T+0 对持仓收益和回撤的改善。

3. **Kronos pattern 不应只看日线**
   20-40 日右尾交易经常由日内放量、尾盘抢筹、开盘承接决定。5min embedding 可以补足日线看不到的微观结构。

4. **调参必须避免幻觉**
   如果 entry/stop/target 都基于日线，fill 假设太粗。5min 可以判断 entry zone 是否真的成交、stop 是否盘中触发、target 是否可卖。

### 3.3 1min 数据是否需要？

V2.2 主线不建议全市场 1min 回填，性价比不如 5min。

建议：

| 层级 | 方案 |
|---|---|
| V2.2 必做 | 全市场 2 年 5min |
| V2.2 可选 | 对报告目标股临时拉最近 20-60 日 1min，用于精细 T+0 |
| V2.3 再考虑 | 全市场 1min，前提是 T+0 模块证明显著增益 |

1min 容易带来数据量、噪声和过拟合问题。机构级做法是先用 5min 建立稳健 edge，再在单股执行层用 1min 优化挂单。

### 3.4 推荐存储

| 数据 | 存储 | 原因 |
|---|---|---|
| 日线/资金/财务/事件 | Postgres | 权威结构化数据、审计、PIT |
| 5min / embedding / analog cache | DuckDB + Parquet | 大规模时序、扫描快、成本低 |
| report asset | Postgres + HTML/MD out | 可复用、可定位、可追踪 |
| 模型 artifact | `~/claude/ifaenv/models/stock_edge/` | 与 repo 解耦 |

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

必须使用 walk-forward：

- 60d：近期适应性
- 180d：中期稳健性
- 360d：跨 regime 稳健性

TA 的经验已经证明：只看 60d 很容易过拟合，必须同时看 60/180/360。

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

这里的 x 是策略预算，不是账户满仓。任何“高置信”都必须满足流动性和 stop-first 风险。

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

参数全部 YAML 化：

- holding_days: 20 / 40 / 60
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

调参目标不是最大收益，而是：

```
score = 0.35 * prob_hit_50_40d
      + 0.25 * expected_return_40d
      + 0.20 * reward_risk
      - 0.15 * expected_drawdown
      - 0.05 * stop_first_rate
```

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
§02 Data Cutoff + 数据完备性
§03 Research 基本面摘要（年报 deep + 季报 deep）
§04 TA Setup Evidence（命中 setup + 历史 edge）
§05 多周期趋势矩阵
§06 支撑阻力地图 + Entry/Stop/Target
§07 资金与板块强度
§08 催化与风险事件
§09 Kronos 历史相似形态
§10 20/40/60 日收益分布预测
§11 T+0 底仓操作计划
§12 三场景树
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
T+0: eligible only with base position
```

---

## 11. DB / Artifact 设计

### 11.1 Postgres

| 表 | 用途 |
|---|---|
| `stock_edge.analysis_record` | 一次分析 run |
| `stock_edge.trade_plan` | action / entry / stop / target / size |
| `stock_edge.support_resistance` | 多源 S/R |
| `stock_edge.prediction_snapshot` | 20/40/60 日分位预测 |
| `stock_edge.analog_cases` | Kronos 相似案例 |
| `stock_edge.t0_plan` | 底仓 T+0 计划 |
| `stock_edge.tracking_log` | 后续兑现与复盘 |
| `stock_edge.analysis_lock` | 并发锁 |

### 11.2 DuckDB / Parquet

| 数据 | 路径 |
|---|---|
| 5min bars | `~/claude/ifaenv/duckdb/parquet/stock_edge/intraday_5min/` |
| Kronos embeddings | `~/claude/ifaenv/duckdb/parquet/stock_edge/kronos/` |
| Analog cache | DuckDB native |
| Backtest snapshots | Parquet partitioned by run |

### 11.3 模型 artifacts

```
~/claude/ifaenv/models/stock_edge/
  right_tail_classifier_v1/
  quantile_forecaster_v1/
  stop_first_model_v1/
  t0_model_v1/
  kronos_index_v1/
```

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

- 全市场 2 年 5min
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

---

## 14. Completion Criteria

Stock Edge V2.2 主功能完成的标准：

1. 给定任意 A 股，能基于正确 `as_of_trade_date` 生成 trade plan。
2. 年报 deep + 季报 deep 基本面被正确复用。
3. TA 30 setup / warnings / metrics 被正确消费。
4. Entry / stop / target 来自可审计 S/R + ATR + execution 模型。
5. 5min 数据至少 2 年可查，支持 S/R、Kronos、T+0 回测。
6. P(+50%, 40d) 和收益分位来自 ML / analog / 统计集成。
7. T+0 只在有底仓时输出，并有 5min 历史验证。
8. 所有参数 YAML 化。
9. 60d / 180d / 360d 回测均不失效。
10. 报告 UI 首屏能让 PM/Trader 在 30 秒内判断：买、等、避、卖。

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
