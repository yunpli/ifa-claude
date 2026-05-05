# TA Strategy Deep Dive — 技术策略晚盘报告家族

> **状态**：V2.2.1 (M10 P0+P1+P2) 已落地 + 经 180d walk-forward 验证
> **接手必读**: 📌 [`ta-handover-2026-05-04.md`](ta-handover-2026-05-04.md)
> **定位**：iFA 第一个**自上而下、跨家族汇聚**型策略产品 — 不是新策略，而是**策略元层**
> **关键差异**：不替代 Smart Money、不放大 Ningbo、不黑盒 AI 选股，是**纪律化的次日交易准备文档**
>
> **打分原则**：所有 30 个策略的内部加分 + ranker 多步加权均为 **连续 function**（非 boolean）；
> 跨族 Bayesian 共振 + 连续 regime boost + 边际自调 + 板块乘子 + 集中度约束 + regime-aware Tier sizing。
> 详见 [`scoring-principles.md`](scoring-principles.md) + [`ta-tier-tuning-iteration-1.md`](ta-tier-tuning-iteration-1.md) + [`ta-tier-tuning-iteration-2.md`](ta-tier-tuning-iteration-2.md)。
>
> **M10 全景 (2026-05-04)**：
> - **P0**: D 族双轨 universe + ATR 三段位推荐价 + Tier 折叠 + §13 红绿灯 dashboard
> - **P1**: 持仓状态机 (fill/stop/target/T+15) + walk-forward 回测引擎 + 黑天鹅过滤 + 基本面二筛 + 集中度约束
> - **P2**: 全 50+ gate 阈值 yaml 化 + Z3+R4 mean-reversion + 温和 Q3 自动降权 + regime-aware Tier sizing
> - **数据**: factor_pro / cyq_perf 回填 180d (Jun 2025 → Apr 2026)
> - **验证**: Tier A 180d realized -0.44%(vs market -1.11%, 跑赢 +0.67pp);Tier B +0.26pp。60d 同步跑赢 +0.64pp。
>
> **30 setup / 11 族 总览**：
> · **T 趋势**: T1 突破 / T2 回踩续涨 / T3 加速
> · **P 回踩**: P1 MA20 / P2 缺口 / P3 紧密整理
> · **R 反转**: R1 双底 / R2 头肩底 / R3 锤子线 / R4 MA60 支撑反弹 (M10 mean-reversion)
> · **F 形态**: F1 旗形 / F2 三角 / F3 矩形
> · **V 量价**: V1 量价齐升 / V2 缩量蓄势
> · **S 板块**: S1 共振 / S2 跟风 / S3 落后补涨
> · **C 筹码**: C1 集中 / C2 松动 (警示)
> · **O 主力资金** (M10): O1 机构连续抢筹 / O2 龙虎榜机构净买入 / O3 涨停封单结构
> · **D 顶部反转** (M10, 警示): D1 双顶 / D2 头肩顶 / D3 流星线 (跑 full liquid universe → warnings_daily,不进 Tier A/B)
> · **Z 统计** (M10): Z1 极端 z-score / Z2 超卖反弹 / Z3 横盘 fade-rally (mean-reversion)
> · **E 事件** (M10): E1 业绩预告/快报/披露窗口催化
>
> **历史重点池关注（§08）**：每日 Tier A（重点池）选股保留过去 15 个交易日的
> 跟踪记录，展示 T+1/T+3/T+5/T+10 实际收益（仅观察、不止盈止损）。冷启动
> 场景由 `scripts/ta_backfill.py --start <30d ago> --end <昨日>` 一次性回填。

---

## 1. 战略定位

### 1.1 现有家族的"短板"

| 家族 | 解答 | 局限 |
|------|------|------|
| Market | 今天市场发生了什么 | 描述性，非策略性 |
| SmartMoney | 资金流向哪里 | 单一维度（资金）；缺技术面与情绪面 |
| Ningbo | 神枪手/聚宝盆三套打法 | 单一流派；setup 数量少；不区分 regime |
| Macro / Asset / Tech | 宏观/资产/科技背景 | 不直接对应交易决策 |

**没有任何家族回答交易员每天必须回答的问题**：

> "明天我应该看什么？为什么看？什么条件出现就行动？什么条件出现就放弃？过去类似的观察兑现了吗？"

TA Family 填补这个空白。

### 1.2 产品哲学

> 30 年的交易员明白：**好的 setup 一周也就 2-3 个，多数日子最优解是空仓**。
> 真正赚钱的不是"今天能选出 20 只股"，而是"今天能识别出 0 只值得加仓、3 只值得观察、其余全部回避"。

四条核心原则：

1. **Setup 必须清晰** — 模糊的形态等于没有形态。宁可少不可滥。
2. **每个 setup 必须自带 invalidation** — "如果跌破 X 价 / 出现 Y 形态 / Z 不发生，立即放弃"。
3. **Regime 决定打法** — 同一个 setup 在不同 regime 下胜率天差地别，必须 regime gating。
4. **Strategy 会衰退** — 一年前赚钱的 setup 今年可能失效，必须持续监测每个 setup 的近期 edge。

### 1.3 与现有家族的关系

```
            ┌─────────────────────────────────────┐
            │   TA Family（V2.2 新增 · 元层）       │
            │   策略元编排 + Setup 库 + 表现追踪    │
            └────────────▲──────────▲────────────┘
                         │          │
         ┌──────消费──────┘          └──消费──────┐
         │                                        │
   ┌─────┴────┐  ┌──────────┐  ┌──────────┐  ┌──┴──────┐
   │  Market  │  │SmartMoney│  │ Ningbo   │  │Macro/Tech│
   │ 市场结构  │  │ 资金证据 │  │ 短线 setup│  │ 背景确认  │
   └──────────┘  └──────────┘  └──────────┘  └──────────┘
```

**不替代任何家族，不重做任何已有计算**。TA Family 是**汇聚层 + 增强层**：
- 复用 SmartMoney 的板块资金流（不再算）
- 复用 Ningbo 的神枪手等 setup 检测（作为 setup 库的一部分）
- 复用 Market 的指数 / 情绪 / 龙虎榜（不再算）
- **新增**：13+ 种 setup 类型、regime 分类器、edge 评分、衰退检测、风险预算

---

## 2. 报告产品矩阵（三时段）

### 2.1 晚盘策略报告（Evening · 主报告） — V2.2.0 必交付

- **生成时间**：交易日 17:30 BJT（在 Market evening 之后）
- **目标延迟**：≤ 4 分钟
- **核心目的**：复盘 + 次日准备
- **章节数**：16 节（详见 §5）
- **报告体量**：HTML ~5000 字，含 Top 5-10 高确信观察候选

### 2.2 早盘刷新（Morning · 9:00 BJT 前） — V2.2.1

- **目标延迟**：≤ 90 秒
- **逻辑**：不重做选股，只对昨日 Top 10 做 4 类标签更新：`confirmed` / `weakened` / `invalidated` / `unchanged`
- **输出**：刷新后的次日观察清单 + 早盘开盘节奏建议

### 2.3 盘中简报（Intraday · 11:00 / 14:00 BJT） — V2.2.2

- **目标延迟**：≤ 30 秒
- **输出**：每只观察候选的"已验证 / 在路上 / 失效"标签 + 板块异动告警

### 2.4 个性化自选股 TA（Personalized Watchlist） — V2.2.3

- 用户保存自选股清单
- 每个交易日晚自动针对自选股生成迷你 TA 卡（不分发，只入库）

**V2.2.0 范围只交付 2.1（晚盘主报告）**，2.2-2.4 列入路线图。

---

## 3. 市场体制（Regime）框架 — TA 的"基石"

### 3.1 为什么 Regime 优先于 Setup

```
错误工作流：找到 setup → 推送 → 失败后困惑
正确工作流：先识别 regime → 选 regime 下高胜率的 setup → 推送 → 失败也在预期内
```

每个 setup 都有自己的"舒适 regime"：
- **趋势突破**：在 `trend_continuation` 下胜率 65%，在 `range_bound` 下 32%
- **超跌反弹**：在 `cooldown / oversold` 下胜率 58%，在 `trend_continuation` 下 23%
- **板块龙头延续**：在 `early_risk_on` 下 70%，在 `distribution_risk` 下 28%

TA Family 的输出**必须 regime-gated**：在某个 regime 下，只推送该 regime 适配的 setup 类型。

### 3.2 9 种 Regime 分类（基于规则，可解释）

| # | Regime | 中文 | 判定规则（基于 5-20 日窗口） |
|---|--------|------|---------------------------|
| 1 | `trend_continuation` | 趋势延续 | 上证 20MA 上行 + 5MA>20MA + 涨家数/跌家数>1.5 + 成交量稳定 |
| 2 | `early_risk_on` | 风险偏好回升初期 | 20MA 拐点向上 + 涨停数环比+50% + 龙头出现 |
| 3 | `weak_rebound` | 弱反弹 | 5MA 反弹但未破 20MA + 量能不足 + 涨家数 50-55% |
| 4 | `range_bound` | 震荡区间 | 上证 20 日波动率 <8% + 5MA 与 20MA 缠绕 + 量能平淡 |
| 5 | `sector_rotation` | 板块轮动 | 大盘震荡 + SW L1 板块涨跌幅离散度高 + 资金高速换手 |
| 6 | `emotional_climax` | 情绪极度高潮 | 涨停 >120 + 连板高度 >7 + 北向超大流入 + 成交破前高 |
| 7 | `distribution_risk` | 顶部派发风险 | 高位放量滞涨 + 龙头分歧 + 量价背离 + 主力净流出 |
| 8 | `cooldown` | 退潮冷却 | 涨停数环比-50% + 跌家数>涨家数 + 5MA 跌破 20MA |
| 9 | `high_difficulty` | 高难度（无序） | 板块涨跌无规律 + 龙头无延续性 + 强势股闪崩频繁 |

### 3.3 Regime 输入数据源

| 维度 | 来源 | 说明 |
|------|------|------|
| 指数结构 | `smartmoney.raw_index_daily`（已有） | 上证 / 创业 / 科创 / 北证 |
| 涨跌停数 | `smartmoney.raw_limit_list_d`（已有） | 涨家数、跌家数、连板高度 |
| 板块离散度 | `smartmoney.sector_moneyflow_sw_daily`（已有） | SW L1/L2 板块涨幅标准差 |
| 北向资金 | `smartmoney.raw_moneyflow_hsgt`（已有） | 净流入与历史分位 |
| 涨停高度 | `smartmoney.raw_kpl_list`（已有） | 连板梯队 |
| 量能 | `smartmoney.raw_daily`（已有） | 成交额 vs 20 日均 |
| 情绪温度 | **新增 ths_hot, dc_hot** | 散户关注度 |

### 3.4 Regime 转移矩阵

借鉴 SmartMoney 的 `transition_matrix.py`，构建**全市场 regime 转移矩阵**：
- 输入：过去 60 个交易日的 regime 序列
- 输出：当前 regime 转入下一个 regime 的概率分布
- 用途：报告里写"当前 `early_risk_on`，60% 概率延续，25% 转 `trend_continuation`，15% 转 `cooldown`"

---

## 4. Setup 库 — 18 个标准 Setup（V2.2.0 全量首发）

### 4.1 Setup 设计原则

每个 setup 是独立 Python 模块，统一接口：

```python
class Setup(Protocol):
    name: str
    category: Literal["trend", "pullback", "reversal", "pattern",
                      "volume_price", "sector_driven", "chip"]
    suitable_regimes: list[Regime]      # 哪些 regime 下推荐
    forbidden_regimes: list[Regime]     # 哪些 regime 下禁用

    def detect(ctx: ScanContext) -> list[Candidate]: ...
    def validation_conditions(c: Candidate) -> list[Condition]: ...
    def invalidation_conditions(c: Candidate) -> list[Condition]: ...
    def evidence_pack(c: Candidate) -> EvidencePack: ...
```

每个 setup 必须能回答：识别规则 / 验证条件 / 失效条件 / 历史边际（近 60/180/365 天胜率与盈亏比）/ 适合的 regime。

### 4.2 18 个标准 Setup

#### A. 趋势型（Trend）

| # | Setup | 识别要点 | 适合 regime |
|---|-------|---------|------------|
| T1 | 趋势突破 N 日新高 | 突破 20/60 日新高 + 量比 >1.5 | trend_continuation, early_risk_on |
| T2 | 均线多头加速 | 5/10/20/60 全多头 + 5MA 斜率 >0.8% | trend_continuation |
| T3 | 周线趋势确认 | 周线 5/24 金叉 + 日线回踩 20MA | early_risk_on, trend_continuation |

#### B. 回踩型（Pullback）

| # | Setup | 识别要点 | 适合 regime |
|---|-------|---------|------------|
| P1 | 神枪手 strike_2（**复用 Ningbo**） | 5/24 金叉后二次回踩 24MA | trend_continuation |
| P2 | 强势股 0.382 黄金分割回踩 | 上涨 30%+ 后回踩 fib(0.382) 企稳 | trend_continuation |
| P3 | 旗形整理突破 | 强势上涨后窄幅 5-10 日整理 + 突破 | trend_continuation |

#### C. 反转型（Reversal）

| # | Setup | 识别要点 | 适合 regime |
|---|-------|---------|------------|
| R1 | 超跌反弹 | RSI(6) <20 + 缩量见底 + 次日放量 | cooldown |
| R2 | 底部 W 形 | 二次探底不破前低 + MACD 底背离 | cooldown, weak_rebound |
| R3 | MACD 底背离 | 价格新低但 MACD-DIF 未新低 | cooldown |

#### D. 形态型（Pattern）

| # | Setup | 识别要点 | 适合 regime |
|---|-------|---------|------------|
| F1 | 三角收敛突破 | 30 日内高低点收敛 + 突破上沿 | range_bound → early_risk_on |
| F2 | 矩形整理突破 | 横盘 20 日 + 突破上沿 | range_bound |
| F3 | 缺口突破 | 跳空高开 >2% + 不回补 | early_risk_on |

#### E. 量价型（Volume-Price）

| # | Setup | 识别要点 | 适合 regime |
|---|-------|---------|------------|
| V1 | 量价齐升 | 价升 >3% + 量比 >2 + 主力净流入 | trend_continuation, early_risk_on |
| V2 | 异动放量 | 量比 >3 + 创阶段新高 | early_risk_on |

#### F. 板块联动型（Sector-Driven）

| # | Setup | 识别要点 | 适合 regime |
|---|-------|---------|------------|
| S1 | 龙头延续 | 板块龙头再创新高 + 量能放大 | early_risk_on, trend_continuation, sector_rotation |
| S2 | 板块补涨 | 板块强（前 5 日涨 >5%）但个股滞涨 + 启动信号 | sector_rotation |
| S3 | 涨停板二板梯队 | 首板 + 次日量比 >2 + 不破首板低点 | early_risk_on |

#### G. 筹码型（Chip） — 新增 cyq_chips 后启用

| # | Setup | 识别要点 | 适合 regime |
|---|-------|---------|------------|
| C1 | 筹码集中度提升 | cost_85pct - cost_15pct 收窄 >20% | trend_continuation, early_risk_on |
| C2 | 低位筹码密集突破 | 价格突破 cost_50pct + 筹码集中区位于下方 | early_risk_on |

### 4.3 Setup 历史边际计算（1 年滚动窗口）

每个 setup 在 SmartMoney 已有 raw_daily 数据上做**滚动回测**，每日计算：

| 指标 | 公式 |
|------|------|
| 60 日胜率 | 近 60 个交易日触发样本，T+5 收益 >0 占比 |
| 60 日平均收益 | 同样本 T+5 平均收益 |
| 60 日盈亏比 | 平均盈利 / 平均亏损 |
| 250 日胜率 | 1 年长期基线（V2.2 选定的窗口） |
| **Decay Score** | (60 日胜率 - 250 日胜率) — 衡量 setup 是否衰退 |

**Decay Score < -10pp 即标 "decaying"**，从主推降级到观察。

每天的 §11 Strategy Performance Review 列出"近期表现最好的 3 个 setup"和"近期衰退中的 3 个 setup"。

---

## 5. 晚盘报告章节结构（16 节）

```
§01  当前市场体制识别              （9 种 regime + 转移概率）
§02  多维度市场状态盘              （指数/涨跌停/北向/情绪温度，复用 Market）
§03  板块强度与轮动雷达            （SW L1/L2 强度热力图，复用 SmartMoney）
§04  板块龙头与梯队结构            （强势板块的龙头/二线/三线 + 衰减度）
§05  资金证据集                    （北向 + 主力 + 龙虎榜 + 大宗交易，复用 SmartMoney）
§06  情绪与拥挤度扫描              （ths_hot/dc_hot 热榜 + 涨停连板高度 + 研报集中度）
§07  ★ 候选股池（按 Setup 类型）   （18 个 setup 各自检出，附 evidence + edge）
§08  ★ 次日 Top 5-10 高确信观察   （跨 setup 综合排名，仅 regime-fit 的）
§09  ★ 验证 / 失效条件矩阵        （每个 Top 候选的 confirm/invalidate 条件）
§10  历史候选追踪卡                （过去 1/3/5/10/30 日所有候选当前状态）
§11  ★ Setup 表现归因              （近 60 日各 setup 胜率/盈亏比/decay 排名）
§12  衰退中的策略                  （Decay Score < -10pp 的 setup 列表 + 警示）
§13  风险扫描                      （失败突破/弱量/拥挤/反转/系统性风险）
§14  次日假设清单                  （3-5 条可验证假设，T+1 自动评分）
§15  仓位与节奏建议（参考）         （根据 regime 动态给仓位上限，明确 "参考" 字样）
§16  完整免责声明                  （10 段中英对照，使用 disclaimer.py 中的全量版）
```

### 5.1 §07 候选股池呈现样式

```
[T1 趋势突破]  001339 智微智能  ⭐⭐⭐⭐
─────────────────────────────────────
触发条件   突破 60 日新高 67.20，量比 2.3x，主力净流入 1.2 亿
证据       [Evidence Pack 可点开]
        ├─ 收盘 68.50（+5.2%）
        ├─ 60 日新高确认
        ├─ 量比 2.3，今日成交额 28 亿（20 日均 12 亿）
        ├─ MACD-DIF 上穿 0 轴
        ├─ 板块：计算机设备（板块今日 +2.8%，强势）
        ├─ 主力净流入 1.2 亿（连续 3 日净流入）
        └─ 筹码集中度 cost_85-15 收窄 18%

历史边际    60 日胜率 67%（18/27），平均 T+5 收益 +4.2%，盈亏比 2.1
           250 日胜率 62%，Decay Score +5pp（趋稳向上）

适配 regime  trend_continuation ✓（当前 regime 匹配）

风险标记    无大股东减持、无质押超 50%、非北交所、近 30 日无停牌
```

### 5.2 §08 次日 Top 综合评分

```
final_score =
    setup_60d_winrate × 40%
+ regime_fit_bonus × 20%
+ capital_confirmation × 15%      （主力流入 + 北向）
+ sector_strength × 15%           （板块今日表现 + 5 日趋势）
+ liquidity_score × 5%             （成交额 / 流通市值）
+ catalyst_bonus × 5%              （来自 ta.catalyst_event_memory）
```

### 5.3 §09 验证 / 失效条件矩阵

| 候选 | Setup | 验证条件（满足即视为兑现） | 失效条件（满足即放弃观察） |
|------|------|--------------------------|-------------------------|
| 智微智能 | T1 趋势突破 | T+1 收盘 >68.50 且量比 >1.3 | T+1 收盘 <65.00 或回补缺口 |

**这是 V2.2 的"假设系统"核心** — 全部沉淀到 `ta.report_judgments`，T+1 自动评分。

### 5.4 §11 Setup 表现归因

```
近 60 日 Setup 表现排行
─────────────────────────
Setup            触发数  胜率   平均收益  盈亏比  Decay
T1 趋势突破        45    67%   +4.2%    2.1    +5pp ↑
S1 龙头延续        38    71%   +5.1%    2.4    +8pp ↑↑
P1 神枪手strike_2  29    62%   +3.8%    1.9    -2pp →
V1 量价齐升        52    58%   +3.1%    1.7    -3pp →
R1 超跌反弹        18    44%   +1.8%    1.2   -15pp ↓↓ ⚠ 衰退中
F2 矩形整理突破    12    50%   +2.1%    1.5    -8pp ↓
```

### 5.5 §15 仓位与节奏建议（参考）

明确标注 "**参考**" 字样，措辞为"内部参考性指引"。基于当日 regime 给出仓位上限映射：

| Regime | 推荐总仓位上限（参考） | 推荐单只上限（参考） | 备注 |
|--------|--------------|------------|------|
| trend_continuation | 100% | 15% | 最舒适的 regime |
| early_risk_on | 80% | 12% | 仍在确认中 |
| weak_rebound | 50% | 8% | 谨慎 |
| range_bound | 60% | 10% | 高频但小仓 |
| sector_rotation | 70% | 12% | 板块押注 |
| emotional_climax | 30% | 5% | 拥挤度顶点 |
| distribution_risk | 20% | 3% | 防御为主 |
| cooldown | 40% | 8% | 反弹品种 |
| high_difficulty | 10% | 0%（仅观察） | 不参与 |

报告示例文本：
> "当前 regime: distribution_risk（顶部派发风险）。**仓位与节奏（参考）**：总仓位上限 20%，单只新增不超过 3%。今日观察清单仅 3 只，且全部为反弹型（R1/R2），不推荐趋势型加仓。"

### 5.6 §16 完整免责声明

**强制使用 `ifa/core/report/disclaimer.py` 中的 `DISCLAIMER_PARAGRAPHS_ZH` + `DISCLAIMER_PARAGRAPHS_EN` 完整 10 段中英对照版本**，与 Market / Macro / Asset / Tech / SmartMoney 报告保持一致。不得使用简短版（FOOTER_SHORT_*）。

---

## 6. 数据架构与复用策略

### 6.1 复用既有 schema（不再造）

| 表 | 来源家族 | 用途 |
|---|---------|------|
| `smartmoney.raw_daily` | SmartMoney | 个股 OHLCV |
| `smartmoney.raw_moneyflow` | SmartMoney | 个股资金流 |
| `smartmoney.raw_index_daily` | SmartMoney | 指数行情 |
| `smartmoney.raw_kpl_list` | SmartMoney | 涨停池 |
| `smartmoney.raw_top_list` | SmartMoney | 龙虎榜 |
| `smartmoney.raw_limit_list_d` | SmartMoney | 涨跌停 |
| `smartmoney.raw_block_trade` | SmartMoney | 大宗交易 |
| `smartmoney.raw_moneyflow_hsgt` | SmartMoney | 北向 |
| `smartmoney.sector_moneyflow_sw_daily` | SmartMoney | SW L2 板块流 |
| `smartmoney.sw_member_monthly` | SmartMoney | SW 成员（PIT） |
| `smartmoney.market_state_daily` | SmartMoney | 市场状态 |
| `smartmoney.sector_state_daily` | SmartMoney | 板块状态 |
| `ningbo.candidates_daily` | Ningbo | 神枪手等候选 |
| `ningbo.recommendations_daily` | Ningbo | Ningbo 已推荐 |

### 6.2 新增 Tushare 接口（已实测全部可用）

| 接口 | 字段亮点 | 用途 | 必要性 |
|------|---------|------|-------|
| **`stk_factor_pro`** | **261 字段，全套技术指标 bfq/hfq/qfq** | 替代手算 MACD/KDJ/BOLL 等 | ★★★★★ |
| **`cyq_chips`** | 每日各价位筹码占比 | 筹码集中度 / 形态学 | ★★★★ |
| **`cyq_perf`** | his_low/high, cost_5/15/50/85pct | 历史成本分布、套牢盘分析 | ★★★★ |
| `ths_hot` | 散户热度排名 + hot 数值 | 情绪温度、拥挤度 | ★★★ |
| `dc_hot` | 东财热榜 | 同上 | ★★★ |
| `kpl_concept` | 涨停板概念热度 | 当日热点概念 | ★★ |
| `kpl_concept_cons` | 概念成分 | 概念 → 个股映射 | ★★ |
| `stk_limit` | up_limit / down_limit | 是否能买判断 | ★★ |
| `suspend_d` | 停复牌 | 黑名单过滤 | ★★ |
| `broker_recommend` | 券商月度金股 | 拥挤度 / 共识 | ★ |
| `report_rc` | 券商盈利预测明细 | 一致预期变化 | ★ |

**核心新增**：`stk_factor_pro` 一接口取代既有系统手算几十个指标的所有逻辑，巨大简化。

### 6.3 `stk_factor_pro` 存储方案

**采用方案 B：精选 80 字段入 PostgreSQL**（实测 ~1.7 GB / 年含索引）。

入库字段精选清单：
- OHLCV / 成交：`close_qfq, open_qfq, high_qfq, low_qfq, vol, amount, turnover_rate, turnover_rate_f, volume_ratio`
- 估值：`pe_ttm, pb, ps_ttm, total_mv, circ_mv`
- 均线：`ma_qfq_5/10/20/30/60/90/250`，`ema_qfq_5/10/20/30/60/90/250`
- MACD：`macd_qfq, macd_dea_qfq, macd_dif_qfq`
- KDJ：`kdj_qfq, kdj_d_qfq, kdj_k_qfq`
- BOLL：`boll_upper_qfq, boll_mid_qfq, boll_lower_qfq`
- RSI：`rsi_qfq_6/12/24`
- BIAS：`bias1_qfq, bias2_qfq, bias3_qfq`
- 其他常用：`cci_qfq, wr_qfq, mfi_qfq, obv_qfq, atr_qfq, psy_qfq, mtm_qfq, roc_qfq, trix_qfq`
- DMI：`dmi_adx_qfq, dmi_pdi_qfq, dmi_mdi_qfq`
- 趋势计数：`updays, downdays, topdays, lowdays`
- 通道：`bbi_qfq, ktn_upper/mid/down_qfq, expma_12_qfq, expma_50_qfq, taq_up/mid/down_qfq`

余下 ~180 字段需要时从 Tushare 现拉（带 7d 缓存），从来不会全字段同时计算。

### 6.4 新增 ETL 任务

| ETL | 频率 | 写入表 |
|-----|------|-------|
| `fetch_stk_factor_pro` | 每交易日 16:30 | `ta.factor_pro_daily`（80 字段） |
| `fetch_cyq_chips` | 每交易日 16:30 | `ta.cyq_chips_daily` |
| `fetch_cyq_perf` | 每交易日 16:30 | `ta.cyq_perf_daily` |
| `fetch_ths_dc_hot` | 每交易日 16:30 + 早 9:00 | `ta.hot_rank_daily` |
| `fetch_suspend_limit` | 每交易日 16:30 | `ta.suspend_daily` / `ta.stk_limit_daily` |
| `extract_catalyst_events` | 每交易日 17:00 | `ta.catalyst_event_memory`（LLM 抽取） |

### 6.5 数据库 Schema（新增 `ta` schema）

```sql
CREATE SCHEMA ta;

-- ============ 原始扩展数据 ============
CREATE TABLE ta.factor_pro_daily (
    trade_date  DATE NOT NULL,
    ts_code     VARCHAR(12) NOT NULL,
    -- 精选 80 字段（详见 §6.3）
    close_qfq   NUMERIC,
    -- ... 其余字段
    PRIMARY KEY (trade_date, ts_code)
);
CREATE INDEX ON ta.factor_pro_daily (ts_code, trade_date DESC);

CREATE TABLE ta.cyq_chips_daily (
    trade_date  DATE NOT NULL,
    ts_code     VARCHAR(12) NOT NULL,
    chips_json  JSONB,              -- [{"price": 67.2, "percent": 0.012}, ...]
    PRIMARY KEY (trade_date, ts_code)
);

CREATE TABLE ta.cyq_perf_daily (
    trade_date     DATE NOT NULL,
    ts_code        VARCHAR(12) NOT NULL,
    his_low        NUMERIC,
    his_high       NUMERIC,
    cost_5pct      NUMERIC,
    cost_15pct     NUMERIC,
    cost_50pct     NUMERIC,
    cost_85pct     NUMERIC,
    cost_95pct     NUMERIC,
    weight_avg     NUMERIC,
    winner_rate    NUMERIC,
    PRIMARY KEY (trade_date, ts_code)
);

CREATE TABLE ta.hot_rank_daily (
    trade_date  DATE NOT NULL,
    src         VARCHAR(8),         -- 'ths' | 'dc' | 'kpl'
    data_type   VARCHAR(16),
    ts_code     VARCHAR(12),
    rank        INT,
    hot         NUMERIC,
    pct_change  NUMERIC,
    PRIMARY KEY (trade_date, src, data_type, ts_code)
);

CREATE TABLE ta.suspend_daily (...);
CREATE TABLE ta.stk_limit_daily (...);

-- ============ 跨家族催化事件记忆（借鉴 macro_policy_event_memory）============
CREATE TABLE ta.catalyst_event_memory (
    event_id        VARCHAR(32) PRIMARY KEY,    -- hash(source_url + publish_time + title)
    capture_date    DATE NOT NULL,
    event_type      VARCHAR(32),                 -- 'industry_policy' | 'company_announcement'
                                                 -- | 'earnings_disclosure' | 'sector_catalyst'
    target_ts_codes TEXT[],                       -- 涉及的股票
    target_sectors  TEXT[],                       -- 涉及的板块
    title           TEXT,
    summary         TEXT,                         -- LLM 提取的结构化摘要
    polarity        TEXT CHECK (polarity IN ('positive', 'neutral', 'negative')),
    importance      TEXT CHECK (importance IN ('high', 'medium', 'low')),
    source_url      TEXT,
    publish_time    TIMESTAMPTZ,
    extraction_model TEXT,
    extraction_prompt_version TEXT,
    valid_until     DATE,                         -- 事件失效日（用于 §08 catalyst_bonus 衰减）
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON ta.catalyst_event_memory (capture_date DESC);
CREATE INDEX ON ta.catalyst_event_memory USING GIN (target_ts_codes);
CREATE INDEX ON ta.catalyst_event_memory USING GIN (target_sectors);

-- ============ 计算层 ============
CREATE TABLE ta.regime_daily (
    trade_date  DATE PRIMARY KEY,
    regime      VARCHAR(32) NOT NULL,
    confidence  NUMERIC,                  -- 0-1
    evidence_json JSONB,
    transitions_json JSONB                -- 各 regime 的转入概率
);

CREATE TABLE ta.setup_metrics_daily (
    trade_date     DATE NOT NULL,
    setup_name     VARCHAR(32) NOT NULL,
    triggers_count INT,
    winrate_60d    NUMERIC,
    avg_return_60d NUMERIC,
    pl_ratio_60d   NUMERIC,
    winrate_250d   NUMERIC,                -- 1 年滚动
    decay_score    NUMERIC,
    suitable_regimes TEXT[],
    PRIMARY KEY (trade_date, setup_name)
);

-- ============ 候选与追踪 ============
CREATE TABLE ta.candidates_daily (
    candidate_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    trade_date     DATE NOT NULL,
    ts_code        VARCHAR(12) NOT NULL,
    setup_name     VARCHAR(32) NOT NULL,
    rank           INT,
    final_score    NUMERIC,
    star_rating    INT,                  -- 1-5 星
    regime_at_gen  VARCHAR(32),
    evidence_json  JSONB,
    validation_json JSONB,
    invalidation_json JSONB,
    in_top_watchlist BOOLEAN,
    UNIQUE (trade_date, ts_code, setup_name)
);
CREATE INDEX ON ta.candidates_daily (trade_date, in_top_watchlist DESC);

CREATE TABLE ta.candidate_tracking (
    track_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    candidate_id   UUID REFERENCES ta.candidates_daily(candidate_id),
    horizon_days   INT NOT NULL,         -- 1, 3, 5, 10, 30
    eval_date      DATE NOT NULL,
    return_pct     NUMERIC,
    max_return_pct NUMERIC,
    max_drawdown_pct NUMERIC,
    validation_status TEXT CHECK (validation_status IN
        ('confirmed', 'partial', 'invalidated', 'timeout', 'pending')),
    confirmation_evidence JSONB,
    UNIQUE (candidate_id, horizon_days)
);

CREATE TABLE ta.report_judgments (
    judgment_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    report_run_id  UUID,
    judgment_type  TEXT,                 -- 'next_day_hypothesis' | 'setup_signal' | 'regime_call'
    statement      TEXT,
    target         TEXT,
    horizon_days   INT,
    validation_rule_json JSONB,
    review_status  TEXT,                 -- 'pending'/'validated'/'partial'/'failed'
    reviewed_at    TIMESTAMPTZ,
    review_evidence JSONB
);

-- ============ 用户自选股（V2.2.3 用） ============
CREATE TABLE ta.user_watchlist (
    user_id        UUID,
    ts_code        VARCHAR(12),
    added_at       TIMESTAMPTZ DEFAULT now(),
    note           TEXT,
    PRIMARY KEY (user_id, ts_code)
);
```

---

## 7. 系统架构与流水线

### 7.1 流水线总览

```
                        ┌──────────────────────────────────┐
T+0 (16:30)             │  ETL 扩展层（V2.2 新增）          │
                        │  factor_pro / cyq / hot / limit  │
                        └────────────────┬─────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
T+0 (16:35)     │  Layer 1 · Regime 识别                          │
                │  → ta.regime_daily                              │
                └────────────────────────┬────────────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
T+0 (16:40)     │  Layer 2 · Setup 检测（18 个 setup 并行）       │
                │  → ta.candidates_daily（每 setup N 个候选）     │
                └────────────────────────┬────────────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
T+0 (16:50)     │  Layer 3 · Setup 历史 Edge 计算（增量 1 年滚动）│
                │  → ta.setup_metrics_daily                       │
                └────────────────────────┬────────────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
T+0 (17:00)     │  Layer 4 · 综合排名 + Regime Gating              │
                │  → ta.candidates_daily.in_top_watchlist          │
                └────────────────────────┬────────────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
T+0 (17:05)     │  Layer 5 · 历史候选追踪（T-1, T-3, T-5, T-10）   │
                │  → ta.candidate_tracking                         │
                └────────────────────────┬────────────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
T+0 (17:10)     │  Layer 6 · 假设评估（昨日的 judgments）          │
                │  → ta.report_judgments.review_status             │
                └────────────────────────┬────────────────────────┘
                                         │
                ┌────────────────────────▼────────────────────────┐
T+0 (17:15)     │  Layer 7 · LLM 解释 + 报告渲染                   │
                │  → 16 节 HTML / PDF                              │
                └─────────────────────────────────────────────────┘
T+0 (17:30) 报告就绪
```

### 7.2 模块组织

```
ifa/families/ta/
├── __init__.py
├── etl/
│   ├── factor_pro.py            stk_factor_pro 入库
│   ├── cyq.py                   筹码数据入库
│   ├── hot_rank.py              ths/dc/kpl 热榜
│   ├── suspend_limit.py         停复牌、涨跌停
│   └── runner.py                每日 ETL 编排
├── regime/
│   ├── classifier.py            9 种 regime 识别（规则）
│   ├── transitions.py           regime 转移矩阵
│   └── snapshot.py              证据快照生成
├── setups/
│   ├── base.py                  Setup Protocol + Candidate dataclass
│   ├── trend.py                 T1-T3
│   ├── pullback.py              P1-P3（含复用 ningbo.sniper）
│   ├── reversal.py              R1-R3
│   ├── pattern.py               F1-F3
│   ├── volume_price.py          V1-V2
│   ├── sector.py                S1-S3
│   ├── chip.py                  C1-C2
│   └── registry.py              setup 注册中心
├── ranking/
│   ├── edge_score.py            Layer 3 · 历史 edge
│   ├── final_score.py           Layer 4 · 综合排名
│   └── regime_gate.py           regime gating 逻辑
├── tracking/
│   ├── reviewer.py              Layer 5 · 历史候选评分
│   ├── judgment.py              Layer 6 · 假设评估
│   └── decay.py                 衰退检测
├── catalyst/
│   ├── extractor.py             LLM 抽取催化事件（借鉴 macro_policy_memory）
│   └── repo.py                  ta.catalyst_event_memory 读写
├── sections/                    16 节
│   ├── §01_regime.py
│   ├── §02_market_state.py
│   ├── ...
│   └── §16_disclaimer.py
├── prompts/
│   ├── regime_explainer_v1.py
│   ├── candidate_narrator_v1.py
│   ├── strategy_review_v1.py
│   └── catalyst_extractor_v1.py
├── render.py
└── report.py                    主编排器
```

### 7.3 与 SmartMoney/Ningbo 的协议接口

- **TA → SmartMoney**：只读消费，通过 SQL 直接查 `smartmoney.*` 表。永不写入。
- **TA → Ningbo**：将 `ningbo.candidates_daily` 作为 P1 setup 的检出来源，封装在 `setups/pullback.py`。永不修改 ningbo schema。
- **TA → Market**：消费 Market 已写入 `report_runs / report_sections` 的若干结论（如当日 regime 评级），但不依赖 Market 报告完成（独立运行）。

### 7.4 与 Stock Intel 家族的协作

TA 是 Stock Intel 的**市场视角与策略 setup 来源**。两者关系：

| 协作点 | 数据流向 | 说明 |
|-------|---------|------|
| 当日 regime | TA → Stock Intel | Stock Intel `§04 行业坐标与板块强度` 引用当日 `ta.regime_daily` 作为大盘背景 |
| 18 setup 适配性 | TA → Stock Intel | Stock Intel `§10 策略适配性矩阵` 调 TA 的 setup detect 函数判定本股对各 setup 的契合度 |
| 候选历史 | TA → Stock Intel | Stock Intel `§13 历史观察记录` 查 `ta.candidates_daily` 看本股何时被哪些 setup 选中 |
| Setup edge | TA → Stock Intel | Stock Intel 推荐策略类型时引用 `ta.setup_metrics_daily` 当前 60 日胜率 + decay |
| 催化事件 | TA → Stock Intel | 共用 `ta.catalyst_event_memory`；Stock Intel `§08 催化扫描` 直接查 |
| Top 候选触发 | TA → Stock Intel | TA 晚盘报告 §08 的 Top 5-10 候选，可在 Telegram bot 中点击直接触发 Stock Intel deep 分析 |

**接口契约**：
- TA 永远是单向输出，不消费 Stock Intel 数据
- Stock Intel 通过 SQL 直接查 TA 表，不通过 HTTP API
- TA 的 setup detect 函数对外暴露为可独立调用的接口，Stock Intel 直接 import 使用

### 7.4 复用现有 dataclass / 模式

参考 Macro / Asset 已经验证过的模式：

| 模式 | 来源 | TA 复用 |
|------|------|--------|
| `TimeSeries` dataclass | `macro/data.py` | regime 演变曲线、setup edge 曲线、板块强度趋势 |
| `Snapshot + History + data_status` | `asset/data.CommoditySnapshot` | TA `Candidate` 数据形态：当日值 + 近 N 日 + data_status 一次性打包 |
| LLM 抽取事件记忆表（带 stable hash event_id） | `jobs/macro_policy_memory/repo.py` | `ta.catalyst_event_memory` 直接借鉴 |

---

## 8. LLM 工程规范

### 8.1 LLM 在 TA 中的严格边界

> 30 年的交易员有一句话：**"模型告诉你买什么，永远不要让它告诉你为什么"**。
>
> LLM 在这个家族里**不参与任何价格预测、不参与任何排名打分、不参与任何 setup 判定**。

LLM 只做 4 件事：

| 任务 | Section | 模型 | temp |
|------|---------|------|------|
| 把 evidence_pack 翻译成专业可读叙述 | §07 候选股池每只候选 | gpt-5.4 | 0.3 |
| 解释当前 regime 与 transitions | §01 | gpt-5.4 | 0.2 |
| 撰写 strategy review 章节叙述 | §11 §12 | gpt-5.4 | 0.2 |
| 生成次日假设的可读形式 | §14 | gpt-5.4 | 0.3 |

外加一个独立的离线任务：
| 任务 | 频率 | 模型 | temp |
|------|------|------|------|
| 抽取催化事件结构化数据 | 每交易日 17:00 | gpt-5.4 | 0.2 |

**规则层一切都已经准备好**：候选名单、评分、验证条件、失效条件、历史 edge。LLM 只做润色与解读。

### 8.2 Prompt 守则（继承自 ningbo/narrative.py 经验）

- 严禁让 LLM 推荐买卖
- 严禁让 LLM 改动验证 / 失效条件
- 严禁让 LLM 重算数字
- 数字一律以"已知事实"出现在 prompt 中
- 输出 JSON，套用 Jinja 模板

### 8.3 总 LLM 成本预估

每日晚盘报告：
- §01 regime 解释：1 次
- §07 候选叙述：批量 8-15 次（每批 2-3 只候选）
- §11 §12 表现归因：1 次
- §14 次日假设：1 次
- §15 仓位建议（参考）：1 次
- 催化抽取（离线）：每日 1 次批处理
- 总计 ~15-20 次 LLM 调用

---

## 9. 用户使用与体验

### 9.1 CLI

```bash
# 主报告（晚盘）
ifa ta evening --report-date 2026-04-30 --user default --generate-pdf

# 早盘刷新（V2.2.1）
ifa ta morning --report-date 2026-05-01

# 盘中（V2.2.2）
ifa ta intraday --slot 11am --report-date 2026-05-01

# 自选股 TA 卡（V2.2.3）
ifa ta watchlist --user user_001

# 单 setup 检测（调试用）
ifa ta scan --setup T1 --date 2026-04-30

# 历史回测某 setup
ifa ta backtest --setup T1 --start 2024-01-01 --end 2026-04-30
```

### 9.2 报告呈现风格

**视觉锚点**：
- 顶部：当前 regime 大字 + 转移概率横条 + 仓位建议（参考）
- 中部：5 维评分雷达（市场结构 / 板块强度 / 资金面 / 情绪面 / 风险）
- §08 Top 候选用卡片式，每张卡 = 1 只
- §09 验证矩阵用表格 + 颜色编码
- §11 表现归因用排行榜 + 趋势箭头

**语言风格**（30 年分析师的口吻）：
- 不用"建议买入" → 用"观察候选"
- 不用"涨幅惊人" → 用"日内涨 5.2%，量比 2.3x，与板块同步"
- 不用"机会难得" → 用"该 setup 近 60 日胜率 67%，当前 regime 适配"
- 仓位上限措辞：**"仓位与节奏（参考）"**，避免暗示个人化建议

---

## 10. 验收标准（V2.2.0 GA）

1. ✅ 9 种 regime 分类器在 SmartMoney 已有 2021-2026 历史数据上人工抽检（每个 regime 抽 3 个交易日）准确率 >80%
2. ✅ 18 个 setup 全部有可解释规则、validation/invalidation 条件、历史 edge 数据
3. ✅ 晚盘报告 16 节端到端可生成，P50 延迟 <4 分钟
4. ✅ §08 Top 候选 100% 有 evidence pack + 验证 / 失效条件
5. ✅ §10 历史追踪闭环跑通：T-1 / T-3 / T-5 / T-10 / T-30 自动评分
6. ✅ §14 次日假设 T+1 自动评估 review_status 字段
7. ✅ Setup decay 检测在 60 日窗口内对至少 1 个已知衰退的 setup 正确预警
8. ✅ §15 仓位建议（参考）正确显示并显式标注"参考"字样
9. ✅ §16 完整免责声明使用 `disclaimer.py` 完整 10 段中英对照版
10. ✅ 用语合规审查通过（不含"建议买入 / 必涨"等违禁词）
11. ✅ Telegram bot 可触发并返回 PDF
12. ✅ 文档：deep-dive、todo、family-reference、CHANGELOG 更新完整

---

## 11. 演进路线

| 版本 | 内容 |
|-----|------|
| **V2.2.0** | 晚盘主报告（16 节 + 18 setup + 9 regime） |
| **V2.2.1** | 早盘刷新报告 + 自选股 TA 卡 |
| **V2.2.2** | 盘中简报 + 板块异动告警推送 |
| **V2.2.3** | 个性化推荐（基于用户历史交互调权） |
| **V2.3** | ML 排名层（在规则 setup 之上加学习层） |
| **V2.4** | 跨日策略（多日 swing trading 框架） |

---

## 12. 关键产品差异化（一句话总结）

> **TA Family 不是给你 20 只股票推荐，而是告诉你今天市场是什么 regime、当前 regime 下哪个 setup 仍在赚钱、Top 5 候选每只的 evidence 与 invalidation、过去 10 天我们说过的话兑现了几条、明天该用多大仓位看多少只股（参考）。**

---

## 附录 · 词汇表

| 术语 | 定义 |
|------|------|
| Regime | 市场体制 / 阶段，9 种之一 |
| Setup | 技术形态 / 策略入场条件 |
| Edge | 策略统计学优势（胜率、盈亏比） |
| Decay Score | 衰退分数 = 60 日胜率 - 250 日胜率 |
| Validation Condition | 候选兑现的具体可验证条件 |
| Invalidation Condition | 候选失效的具体可验证条件 |
| Catalyst | 催化事件（政策、财报、行业事件） |
| Regime Gating | 仅在特定 regime 下启用相应 setup |
| PIT | Point-in-Time，时间正确，不引入未来信息 |
