# Stock Intel Deep Dive — 个股智能分析

> **状态**：V2.2 设计稿（未实现）
> **定位**：iFA 第一个**自下而上、单股聚焦**的研究助手 — 给一只股，得到 trader / FA / TA / PM 四视角综合判断
> **关键差异**：不是 Research（季度财务尽调）、不是 TA Family（市场层面策略）、不是聊天机器人；是**有记忆、有连续性、有更新逻辑、有概率分布预测**的单股研究伴侣

---

## 1. 战略定位

### 1.1 V2.2 三家族产品矩阵

```
                      自上而下                    自下而上
                    ─────────────              ─────────────
   纯基本面                                      Research（公司深度调研）
                                                · 17 节季度/年度尽调
                                                · 季度刷新

   纯策略层          TA Family（技术策略）
                    · 16 节晚盘报告
                    · 9 regime + 18 setup
                    · 每日刷新

   交易决策                                      Stock Intel ★（个股智能分析）
                    （与 TA 互补，市场视角←→个股视角）
                                                · Fast / Deep / Update 三档
                                                · 多周期 TA + FA + 资金 + 板块 + 催化
                                                · 1/3/6 月价格分布预测
                                                · 按需触发
```

**三家族正交**：
- "明天市场怎么看？" → TA Family
- "智微智能 Q1 怎么样？" → Research
- "这只股现在能不能看？" → **Stock Intel**

### 1.2 与 Research / TA / SmartMoney / Ningbo 的复用关系

Stock Intel 是**最终的整合层**，不重新计算任何数字：

```
Stock Intel  ←──消费──┐
                      ├── Research        （财务质量、五维评分、公司事件记忆）
                      ├── TA Family       （regime、setup 候选、催化事件、stk_factor_pro）
                      ├── SmartMoney      （板块强度、个股资金流、北向、龙虎榜）
                      ├── Ningbo          （dual_scorer ML 模型 + Kronos 嵌入设施）
                      └── Market          （当日市场上下文）
```

每只股的分析 = **多家族输出的横切**。

### 1.3 产品哲学（30 年 PM + 30 年 Trader 视角）

> **PM 视角**：好的单股分析不是把所有指标都列出来，而是**用对的指标回答对的问题**。问"长期能不能拿"用 5 年财务趋势 + 行业地位；问"这周能不能做"用日线形态 + 资金连续性；问"是不是要止损"用关键支撑 + 板块状态。**问什么决定看什么**。
>
> **Trader 视角**：单股分析的最大价值不是当下的判断，而是**让你 30 天后回头看时知道"上次错在哪"**。所以每次分析必须留下：当时数据快照 / 当时结论 / 当时验证条件 / 当时失效条件。这样下次更新分析才有意义。

四条核心原则：

1. **多周期分时回答不同问题** — 1Y 看长期、6M 看趋势、3M 看结构、1M 看强弱、1W 看节奏
2. **每个结论必须有证据来源** — 数字来自规则层，结论来自 LLM 解释，二者明确分离
3. **支撑 / 阻力是核心** — A 股交易者实际行动依据，必须用多源算法（pivot + swing + MA + 筹码 + 心理价位 + 缺口）综合
4. **更新模式是产品灵魂** — 没有"自上次以来变了什么"的连续性，单股分析就是一次性玩具

---

## 2. 三档分析产品矩阵

### 2.1 Fast Analysis（速报） — P0

- **目标延迟**：≤ 60 秒
- **使用场景**："快速看一下"、"值不值得加观察"
- **报告体量**：HTML 单页 ~600 字
- **LLM 调用**：3-5 次
- **章节**：9 节（详见 §5.1）

### 2.2 Deep Analysis（深度） — P0 ★

- **目标延迟**：5-10 分钟
- **使用场景**："认真研究一下"、"准备立项"
- **报告体量**：**HTML 多节** ~6000 字
- **LLM 调用**：15-18 次
- **章节**：16 节（详见 §5.2，含价格分布预测节）
- **包含**：1/3/6 月价格分布预测 + 历史相似形态检索（Kronos）

### 2.3 Update Analysis（更新） — P0

- **目标延迟**：≤ 45 秒
- **触发**：该股近 **14 个自然日**内已生成过 deep 报告
- **使用场景**："上次说要观察的，现在怎么样了？"
- **报告体量**：HTML ~1500 字
- **LLM 调用**：3-4 次
- **章节**：7 节（详见 §5.3）

### 2.4 默认路由

用户输入 `/stock <code>` 不带档位时：

```
检查近 14 天有 succeeded deep 报告？
   是 → 走 update 模式
   否 → 走 fast 模式
用户可显式 --deep 升级、--fresh 强制重做
```

### 2.5 后续档位（路线图）

- **Morning Refresh** — V2.2.1（隔夜外盘 + 早间新闻刷新近 7 天活跃报告）
- **Intraday Quick Check** — V2.2.3（接 5min 数据做日内验证）
- **Personalized Overlay** — V2.2.3（用户成本 / 持仓 / 风险偏好覆盖层）

---

## 3. 多周期技术分析框架

### 3.1 周期与问题映射

| 周期 | 回答的问题 | 数据来源 | 关键指标 |
|------|----------|---------|---------|
| 1Y | 长期处于什么阶段？ | 250 日 daily | 距 52 周高/低、年线斜率、累计涨幅 |
| 6M | 中期趋势何方？ | 120 日 daily | 半年线、波段、形态完整性 |
| 3M | 当下有无可交易结构？ | 60 日 daily | 突破/回踩/反转形态、量价配合 |
| 1M | 短期强弱如何？ | 20 日 daily | 月线斜率、相对板块强度 |
| 1W | 即时节奏怎样？ | 5 日 daily | 短均线、量比、当日异动 |
| **5min** | **日内微观结构** | **stk_mins 5min** | **VWAP、跳空填补、尾盘异动、成交密集区** |
| 60min | （从 5min 聚合）日内验证 | 派生 | 突破后回踩、量能配合 |
| 15min | （从 5min 聚合）入场点 | 派生 | 即时支撑、缺口 |

### 3.2 5min 数据策略 ★（关键升级）

**全市场 2 年 5min 数据持久化入库**（不再按需缓存）：

| 维度 | 数据 |
|------|-----|
| 全市场覆盖 | 5000 股 × 250 交易日/年 × 2 年 |
| 单条数据 | 49 bars/股/日 |
| 总行数 | ~122 M 行 |
| 存储格式 | **DuckDB + Parquet（按月分区）** |
| 存储体积 | ~**3.0 GB** Parquet (zstd-3) |
| 一次性回填 | ~2-4 小时（并发 4-8） |
| 每日增量 | ETL 后 20-30 分钟全市场新交易日数据 |

为什么持久化全市场而非按需：
- **存储几乎免费**（3 GB 不到现有 SmartMoney raw_moneyflow 的 1 倍）
- 启用**全市场尺度的形态相似性检索**（Kronos 嵌入对全市场计算，跨股横向比较）
- 启用**5min 级别的回测**（V2.3 候选）
- 任何用户、任何股、任何时刻请求 deep 报告，5min 数据立即就绪（不再 14 秒额外抓取）

### 3.3 多周期一致性矩阵

每只股每次分析输出"6 周期对齐表"：

```
周期    趋势      MA结构    动量      量能     与板块强弱
1Y     上升      多头      强        匹配     强
6M     上升      多头      强        匹配     强
3M     上升      多头加速  强        放大     强
1M     上升      多头      转弱      平淡     平
1W     盘整      纠缠      中性      萎缩     弱
5min   尾盘冲高  —         异动     放大     —
─────────────────────────────────────────────
对齐度   多周期共振：4/5（短线分歧），中长期信号一致
日内信号 5min 尾盘异动放量（成交占当日 22%），需观察次日开盘验证
```

> **PM 视角**：这张表是 PM 一眼判断"长线持有 vs 短线规避"的核心依据。短线弱不代表长线坏，但短线弱 + 长线弱才真正坏。5min 行加进来后还能识别"日线看似平淡但日内已经有信号"。

---

## 4. 支撑阻力（S/R）— 多源识别

### 4.1 8 类来源

| # | 来源 | 算法 | 强度评分依据 |
|---|------|------|-------------|
| 1 | **Pivot Points** | 经典 (H+L+C)/3 派生 | 接近度 + 历史触碰次数 |
| 2 | **Swing High/Low** | N=5 局部极值 | 触碰次数 × 距今天数衰减 |
| 3 | **Moving Average** | MA5/10/20/60/120/250 | 当前价距 MA 距离 + MA 斜率 |
| 4 | **Bollinger Bands** | upper/mid/lower（多周期） | 波动率自适应 |
| 5 | **筹码集中区** ★ | `cyq_chips` cost_15/50/85pct | 筹码占比 + 距离 |
| 6 | **缺口（Gap）** | daily OHLC 跳空识别 | 缺口大小 + 是否回补 |
| 7 | **历史成交密集区** | **5min 数据计算真实 VWAP / 成交分布** ★ | 累积成交占比 |
| 8 | **心理价位** | 整数关口 (10/50/100/...) | 弱权重，需其他来源也指向 |

5min 数据让来源 7 真正可用：日线只能给"日均价"，5min 才能给"今日 09:45 那波放量在哪个价位完成的"。

### 4.2 输出结构

```python
@dataclass
class SupportResistanceLevel:
    price: float
    type: Literal["support", "resistance", "pivot"]
    sources: list[str]           # ["MA20", "swing_low", "chip_15pct"] — 多源加权
    strength: float              # 0-10
    distance_pct: float          # 距当前价百分比
    confidence: Literal["high", "medium", "low"]
    interpretation: str          # 多源解读
    if_broken_implication: str   # "若收盘有效跌破，下一支撑看 X 元"
```

### 4.3 多源融合规则

- 同一价位被 **≥3 类来源**同时识别 → strength × 1.5 加权
- 时间衰减：N 天前的 swing 高低点，权重 ×e^(-N/60)
- 价格聚类：相邻 ±0.5% 的多个 S/R 自动合并
- PIT 正确：Swing 识别用滚动窗口避免未来信息

报告 §06（deep）展示 Top 4-6 关键 S/R 位（最强 2-3 个 support + 2-3 个 resistance）。

---

## 5. 报告章节结构

### 5.1 Fast Report（HTML，9 节，~600 字）

```
§01  一句话结论 + 四色标签        🟢优质关注 / 🔵常规观察 / 🟡谨慎观察 / ⚫暂不参与
§02  当前状态卡                   价格 / 5 日表现 / 板块 / 板块强度 / 距 52 周高低
§03  快速 TA 标签                 趋势 + 形态 + 位置 + 风险（4 个 chip）
§04  快速 FA 标签                 盈利质量 + 估值水位 + 最新业绩（3 个 chip）
§05  资金信号                     近 5 日主力 + 北向 + 龙虎榜 / 大宗（一行）
§06  关键 S/R                     最重要的 1-2 个支撑 + 1-2 个阻力
§07  主要催化 / 风险               1-2 条
§08  跟踪建议                     加观察 / 升级 / 不跟（含原因）
§09  数据时效 + 完整免责           完整 10 段中英对照（disclaimer.py 全量版）
```

### 5.2 Deep Report（HTML，16 节，~6000 字）★

```
§01  执行摘要                    一句话 + 四色标签 + 三因素归因（FA/TA/资金）
§02  公司画像                    业务模式 + 行业坐标 + 实控人（reuse Research §01）
§03  财务质量与估值              五维评分 + 估值历史分位（reuse Research §02-§07 简版）
§04  行业坐标与板块强度          SW L2 同板块对标 + 板块当前状态（reuse SmartMoney）
§05  ★ 多周期 TA 全景            1Y / 6M / 3M / 1M / 1W + 5min 微观结构
§06  ★ 支撑与阻力地图            Top 4-6 个 S/R 位（多源验证，含 5min VWAP）
§07  量价结构与资金流            连续性 / 一致性 / 派发吸筹判断
§08  催化扫描                    近 30 日公告/研报/政策/业绩（reuse ta.catalyst_event_memory）
§09  风险扫描                    FA + TA + 流动性 + 监管 五类风险
§10  ★ 策略适配性矩阵            vs TA 18 setup 的契合度评分
§11  ★ 价格分布预测（1/3/6 月） 5 分位 + 三情景树 + Kronos 历史相似 Top 5
§12  ★ 三场景与触发条件          看多/中性/看空，各自验证 + 失效
§13  ★ 历史观察记录              先前是否在 TA 候选 / 本系统过往结论变迁
§14  跟踪计划                    Next watch 5 条
§15  数据完备性矩阵              本报告依赖的每个数据源覆盖度
§16  完整免责声明                10 段中英对照（disclaimer.py 全量版）
```

### 5.3 Update Report（HTML，7 节，~1500 字）

```
§01  上次分析摘要                时间 + 标签 + 核心结论
§02  ★ 自上次以来的关键变化       结构化清单：价格/MA/资金/公告/板块/...
§03  价格行为兑现性               上次设的 validation/invalidation 是否触发？
§04  新增公告 / 新闻              上次以来新增的（消费 ta.catalyst_event_memory）
§05  ★ 本次结论 vs 上次           升级/降级/维持/失效 + 解释
§06  调整后的跟踪计划             new validation/invalidation
§07  完整免责声明                 同 deep（disclaimer.py 全量版）
```

### 5.4 Deep §05 多周期 TA 样例

```
五周期对齐表
─────────────────────────────────────────────
周期    趋势      MA 结构      动量    量能    vs 板块
1Y     上升 ↑    多头排列     强      匹配    强 ★
6M     上升 ↑    多头加速     强      放大    强 ★
3M     上升 ↑    5/20 多头    强      平淡    强
1M     盘整 →    20MA 缠绕    转弱    萎缩    平
1W     回调 ↓    跌破 5MA     弱      萎缩    弱
日内    尾盘冲高 —             异动     放大    —

【一致性】中长期信号强烈一致，短线分歧出现于 1M 起
【关键观察】1M 周期 20MA（68.50 元）若失守，将打破 6M 中期上行结构

【1Y 视角】距 52 周高 -8.3%，全年涨幅 +42%，处于年线之上
【6M 视角】半年内三次回踩 60MA 均成功反弹，目前处于第四次回踩
【3M 视角】上升通道完整，最近一次突破后未回补缺口（67.20）
【1M 视角】月初创新高 73.50 后回调，量能未配合上一波拉升
【1W 视角】跌破 5MA 但守住 10MA，量能萎缩属健康调整
【5min 视角】今日尾盘 14:30 后放量上冲，单一 30 分钟成交占全日 22%，
            VWAP 计算的成交密集区 67.50-68.20 形成日内强支撑
```

### 5.5 Deep §11 价格分布预测样例 ★

```
未来 1 / 3 / 6 月价格分布预测（基于历史与模型集成，不构成投资建议）
─────────────────────────────────────────────────────────────────────

      水平期    P10      P25      P50      P75      P90
       1 月    -8.2%    -2.5%    +3.1%    +9.8%    +18.2%
       3 月   -14.2%    -4.8%    +5.2%   +16.5%    +31.0%
       6 月   -21.5%    -8.2%    +8.5%   +24.5%    +46.0%

  上行概率    P(1M >+5%) = 43%   P(3M >+10%) = 41%   P(6M >+20%) = 35%
  下行概率    P(1M <-5%) = 31%   P(3M <-10%) = 30%   P(6M <-20%) = 24%

  当前 regime: trend_continuation
  历史相似 setup 数: 28（有效样本）
  模型集成置信度: 中等
    L2/L3/L4 三模型一致性: P50 偏差 <3pp（一致）
    样本量: 28 个 → 适合做分布判断
    Regime 稳定性: 当前 regime 已持续 12 天（中等成熟度）

  情景树（基于触发条件的条件概率）
  ─────────────────────────────────────────────────────
  · 看多情景（P=40%）
    触发：板块强度维持 P75 以上 + 突破 70.00 量比 >1.5
    1M 区间：72-82 元
    3M 区间：78-95 元

  · 中性情景（P=35%）
    触发：板块退到中性 + 高位震荡
    1M 区间：65-72 元
    3M 区间：63-75 元

  · 看空情景（P=25%）
    触发：板块退潮 + 跌破 60MA + 主力转出
    1M 区间：58-63 元
    3M 区间：52-60 元

  历史相似形态 Top 5（Kronos 嵌入相似度检索）
  ─────────────────────────────────────────────────────
  · 2024-08-15  003456.SZ  相似度 0.91   后 1M: +12.3%   后 3M:  +8.5%
  · 2023-11-22  002789.SZ  相似度 0.88   后 1M:  -3.8%   后 3M: -11.2%
  · 2025-02-08  300821.SZ  相似度 0.85   后 1M:  +8.5%   后 3M: +22.1%
  · 2024-03-14  600555.SH  相似度 0.83   后 1M:  +1.2%   后 3M:  +5.5%
  · 2023-05-09  001234.SZ  相似度 0.82   后 1M:  -7.5%   后 3M:  -2.1%
  ─────────────────────────────────────────────────────
  历史样本均值      +1.8%            +4.6%
  历史样本中位     +2.3%            +5.5%
  历史样本标准差   ±9.2%           ±15.3%
```

---

## 6. 算法分层架构（5 层）

### 6.1 总览

```
┌────────────────────────────────────────────────────────┐
│  L4 · LLM 层（gpt-5.4 / gpt-5.5 fallback）              │
│  解释 / 场景生成 / 历史对比 / 个性化润色                  │
│  严禁预测、严禁重算数字                                  │
└────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────┐
│  L3 · DL / 表征层（V2.2 启用，复用 ningbo/ml/kronos_lib）│
│  · Kronos-small 嵌入：128 日 OHLCV → 256-d 向量          │
│  · FAISS 相似性检索：全市场 1.25M 嵌入库 brute force <50ms │
│  · 异常波动检测（autoencoder）：V2.3 接口预留             │
└────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────┐
│  L2 · ML 层（V2.2 启用）                                │
│  · 复用 ningbo dual_scorer：aggressive + conservative    │
│  · 5 日预期收益分类：复用 ningbo features                │
│  · ★ LightGBM 量化回归：1M/3M/6M × 5 分位 = 15 模型      │
│  · 推理 <100ms / 股                                       │
└────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────┐
│  L1 · 统计层                                             │
│  · 因子 z-score、波动分位、估值分位                       │
│  · 价格相对强弱（vs 板块、vs 大盘）                       │
│  · 业绩超预期程度（vs forecast）                         │
│  · 事件研究（announcement → 后续 N 日异常收益分布）        │
│  · 历史 forward return conditional on regime + setup     │
└────────────────────────────────────────────────────────┘
┌────────────────────────────────────────────────────────┐
│  L0 · 规则层（基础）                                      │
│  · 趋势/MA/Boll/MACD/KDJ/RSI 经典规则                    │
│  · S/R 多源识别（8 类，含 5min VWAP）                    │
│  · 量价规则、量比、换手                                  │
│  · 财务红绿灯（reuse Research）                          │
│  · 公告分类规则                                          │
└────────────────────────────────────────────────────────┘
```

### 6.2 M1 8GB 资源占用（实测推算）

| 组件 | 内存 | 速度 | 启用 |
|------|------|------|-----|
| L0/L1 全跑 | <150MB | <3s | ✓ |
| L2 模型加载 + 推理 | ~250MB | <100ms/股 | ✓ |
| L3 Kronos-small（fp16） | ~50MB | M1 MPS 600 个嵌入/秒 | ✓ |
| L3 FAISS 全市场索引 | ~500MB | <50ms / 检索 | ✓ |
| L3 LightGBM 15 模型 | ~750MB | <200ms 全部 | ✓ |
| L4 LLM | 0（外部 API） | 5-30s/调用 | ✓ |
| **峰值估算** | **<1.7 GB** | — | M1 8GB 安全 ✓ |

### 6.3 价格分布预测的三模型集成

预测节由 3 个独立模型集成，**取加权中位数**：

| 模型 | 性质 | 训练 | 输出 |
|------|------|------|------|
| **L4 历史 analog**（最透明） | 非参数，Kronos 嵌入 + KNN | 无需训练，用现有嵌入库 | 30 个相似 case 的实际 forward return 分布 |
| **L3 LightGBM 量化回归** | 参数模型，主力 | 1.25M 样本，2018-2025 walk-forward | 15 个分位预测 (3 horizon × 5 quantile) |
| **L2 统计基线** | 鲁棒 sanity check | 仅历史均值/分位 conditional on regime | 1 个分位分布 |

三者一致 → 置信度 **high**；分歧 >5pp → **low**；中间 → **medium**。

### 6.4 Kronos-small 集成（详解）

**复用既有资产**：
- `ifa/families/ningbo/ml/kronos_lib/`（kronos.py + module.py，~50KB 代码）
- `ifa/families/ningbo/ml/kronos_features.py`（pipeline 完整）

**Stock Intel 用法**：

#### 用法 A · 形态相似性检索

```python
from ifa.families.stock.embeddings import kronos_finder

current_emb = kronos_finder.embed(ts_code='001339.SZ', end_date=today, lookback=128)
similar = kronos_finder.find_top_k(current_emb, k=30, exclude_recent_days=5)
# → [(ts_code, end_date, similarity, return_1m, return_3m), ...]
```

#### 用法 B · 嵌入作为 LightGBM 特征

```python
features = np.concatenate([
    ta_factors_80d,     # 80 维
    fin_factors_5d,     # 5 维五维评分
    sector_onehot,      # 11 维
    kronos_emb,         # 256 维
])
forecast = lightgbm_quantile.predict(features)  # 15 个分位
```

#### 用法 C · 异常波动检测（V2.3 候选）

接口预留，autoencoder reconstruction error。

### 6.5 离线嵌入库构建

```
夜间任务（每日 17:30 ETL 后）
─────────────────────────────────
1. 增量计算：当日新交易日的全市场 Kronos 嵌入
   · 5000 股 × 1 日 ≈ 8 秒（M1 MPS batch=32）
2. 写入 ~/claude/ifaenv/duckdb/kronos_embeddings.parquet
   · 按年分文件（emb_2024.parquet, emb_2025.parquet, ...）
3. FAISS 索引刷新（增量）
```

历史首次回填（V2.2 上线时一次性）：
- 5000 股 × 500 日（2 年）= 2.5M 嵌入
- M1 MPS 全跑 ~70 分钟（夜间一次性）
- 总存储 ~2.5 GB Parquet

---

## 7. 数据架构（双 DB）

### 7.1 PostgreSQL — 元数据与事务

```sql
CREATE SCHEMA stock;

-- ============ 分析记录（核心）============
CREATE TABLE stock.analysis_record (
    record_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_code            VARCHAR(12) NOT NULL,
    analysis_type      TEXT CHECK (analysis_type IN
        ('fast', 'deep', 'update', 'morning_refresh', 'intraday')),
    base_record_id     UUID REFERENCES stock.analysis_record(record_id),
    triggered_at       TIMESTAMPTZ DEFAULT now(),
    triggered_by_user  UUID,
    data_cutoff        TIMESTAMPTZ NOT NULL,
    status             TEXT CHECK (status IN
        ('running', 'succeeded', 'partial', 'failed', 'cached')),
    -- 结论快照
    conclusion_label   VARCHAR(16) CHECK (conclusion_label IN
        ('high_watch', 'normal_watch', 'cautious', 'avoid')),
    conclusion_text    TEXT,
    -- 关键决策快照（用于 update 对比）
    key_levels_json    JSONB,
    setup_match_json   JSONB,
    validation_json    JSONB,
    invalidation_json  JSONB,
    next_watch_json    JSONB,
    -- ★ 价格分布预测快照（用于事后对照）
    forecast_json      JSONB,
    -- 质量与资源
    duration_seconds   NUMERIC,
    llm_calls          INT,
    llm_tokens         INT,
    output_html_path   TEXT,
    output_pdf_path    TEXT,
    error_summary      TEXT
);
CREATE INDEX ON stock.analysis_record (ts_code, triggered_at DESC);
CREATE INDEX ON stock.analysis_record (analysis_type, status);

CREATE TABLE stock.report_sections (
    section_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id       UUID REFERENCES stock.analysis_record(record_id),
    section_key     TEXT,
    section_order   INT,
    content_json    JSONB,
    status          TEXT,
    skip_reason     TEXT,
    model_used      TEXT,
    prompt_version  TEXT,
    latency_seconds NUMERIC,
    UNIQUE (record_id, section_key)
);

CREATE TABLE stock.support_resistance (
    sr_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_code      VARCHAR(12) NOT NULL,
    trade_date   DATE NOT NULL,
    price        NUMERIC NOT NULL,
    sr_type      TEXT CHECK (sr_type IN ('support', 'resistance', 'pivot')),
    sources      TEXT[],
    strength     NUMERIC,
    distance_pct NUMERIC,
    confidence   TEXT,
    computed_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON stock.support_resistance (ts_code, trade_date);

CREATE TABLE stock.tracking_log (
    track_id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    record_id       UUID REFERENCES stock.analysis_record(record_id),
    ts_code         VARCHAR(12) NOT NULL,
    eval_date       DATE NOT NULL,
    days_after_base INT,
    price_change_pct NUMERIC,
    validation_status TEXT CHECK (validation_status IN
        ('confirmed', 'partial', 'invalidated', 'pending', 'expired')),
    validation_evidence JSONB,
    UNIQUE (record_id, eval_date)
);

CREATE TABLE stock.user_watchlist (
    watchlist_id    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL,
    ts_code         VARCHAR(12) NOT NULL,
    added_at        TIMESTAMPTZ DEFAULT now(),
    priority        TEXT CHECK (priority IN ('key', 'normal', 'condition_only')),
    note            TEXT,
    last_record_id  UUID REFERENCES stock.analysis_record(record_id),
    UNIQUE (user_id, ts_code)
);

-- 个性化层（V2.2.3 启用，先建表）
CREATE TABLE stock.user_context (
    user_id         UUID NOT NULL,
    ts_code         VARCHAR(12) NOT NULL,
    holding_status  TEXT,
    cost_basis      NUMERIC,
    position_size_pct NUMERIC,
    horizon         TEXT,
    style           TEXT,
    risk_tolerance  TEXT,
    updated_at      TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (user_id, ts_code)
);

-- 并发去重锁（核心）
CREATE TABLE stock.analysis_lock (
    lock_key        VARCHAR(64) PRIMARY KEY,
    holder_record_id UUID,
    acquired_at     TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    waiter_count    INT DEFAULT 0
);
```

### 7.2 DuckDB — 时间序列与嵌入（新增引擎）

**位置**：`~/claude/ifaenv/duckdb/stock.duckdb`（或 Parquet 目录 + 元数据 DuckDB）

```sql
-- 全市场 5min 数据（按月分区 Parquet）
-- 路径: ~/claude/ifaenv/duckdb/parquet/intraday_5min/year=YYYY/month=MM/*.parquet
CREATE VIEW stock.intraday_5min AS
SELECT * FROM read_parquet('parquet/intraday_5min/**/*.parquet');

-- Kronos 嵌入库（按年分文件）
-- 路径: ~/claude/ifaenv/duckdb/parquet/kronos/year=YYYY/*.parquet
CREATE VIEW stock.kronos_embeddings AS
SELECT * FROM read_parquet('parquet/kronos/**/*.parquet');

-- 多周期快照缓存（每日刷新）
CREATE TABLE stock.timeframe_snapshot (
    ts_code      VARCHAR NOT NULL,
    trade_date   DATE NOT NULL,
    timeframe    VARCHAR CHECK (timeframe IN ('1Y','6M','3M','1M','1W','5min')),
    snapshot     JSON,
    computed_at  TIMESTAMP,
    PRIMARY KEY (ts_code, trade_date, timeframe)
);

-- 历史相似 case 预计算结果（增量）
CREATE TABLE stock.analog_cache (
    ts_code         VARCHAR NOT NULL,
    end_date        DATE NOT NULL,
    top_k_json      JSON,           -- 30 个相似 case 的元信息与 forward return
    computed_at     TIMESTAMP,
    PRIMARY KEY (ts_code, end_date)
);
```

### 7.3 跨 DB 查询

```python
import duckdb
conn = duckdb.connect('~/claude/ifaenv/duckdb/stock.duckdb')
conn.execute("INSTALL postgres; LOAD postgres")
conn.execute("ATTACH 'host=127.0.0.1 port=55432 dbname=ifavr user=ifa' AS pg (TYPE postgres)")

# 跨库 join：DuckDB 5min 数据 + PG 的 SR 元数据
result = conn.execute("""
    SELECT i.trade_time, i.close, sr.price as support_level
    FROM stock.intraday_5min i
    JOIN pg.stock.support_resistance sr ON i.ts_code = sr.ts_code
    WHERE i.ts_code = '001339.SZ' AND i.trade_time >= today - INTERVAL '7 days'
""").fetch_df()
```

### 7.4 三级缓存策略

| 层 | 缓存对象 | TTL / 失效条件 |
|----|---------|---------------|
| L1 上游数据 | research / ta 既有 api_cache | 已有规则，沿用 |
| L2 中间结果 | DuckDB `timeframe_snapshot` / `analog_cache` | 当日有效，下个交易日重算 |
| L3 报告产出 | PG `stock.analysis_record` | fast 4h、deep 24h；新公告 / 财报触发失效 |

**精细化失效**：
- 公司发布新公告 → 失效该 stock 的 L2/L3
- 财报披露 → 联动失效 Research 缓存
- 新交易日 → 全部 L2 失效
- 用户 `--no-cache` → 跳过所有

---

## 8. 全市场 5min 数据 ETL ★

### 8.1 一次性历史回填（M1 安装时）

| 项 | 数值 |
|----|------|
| 时间范围 | 2 年（约 500 交易日） |
| 股票数 | 5000 |
| 总行数 | ~122M |
| Parquet 体积 | ~3 GB |
| API 调用数 | 5000（每股一次多日范围请求） |
| 顺序耗时 | ~2 小时（每股 1.3s 实测）|
| 并发 4 耗时 | ~30 分钟 |
| 限速考虑 | Tushare 多数接口 200/min，并发 4 安全 |

**回填脚本**：`scripts/stock_intraday_backfill.py --years 2 --workers 4`

写入路径：`~/claude/ifaenv/duckdb/parquet/intraday_5min/year=YYYY/month=MM/<ts_code_prefix>.parquet`（按月分区，便于增量与压缩）。

### 8.2 每日增量 ETL

| 时间 | 任务 |
|------|-----|
| T+0 16:00 | 上游 SmartMoney ETL 完成 |
| T+0 16:30 | TA Family ETL（factor_pro / cyq / hot 等） |
| **T+0 17:00** | **stock 5min 全市场增量（5000 股 × 当日，并发 4 ~30 分钟）** |
| T+0 17:30 | Kronos 嵌入增量（全市场 当日，~8 秒）+ FAISS 索引刷新 |
| T+0 18:00 | 当日 timeframe_snapshot 与 SR 重算 |

### 8.3 ETL 任务模块

```
ifa/families/stock/etl/
├── intraday_5min.py        全市场 5min 增量
├── kronos_embed.py         Kronos 嵌入增量
├── faiss_index.py          FAISS 索引重建
├── snapshots.py            多周期快照刷新
└── runner.py               每日 ETL 编排
```

### 8.4 数据完整性自检

每日 ETL 后跑 `scripts/check_intraday_coverage.py`：
- 当日预期 5000 股 × 49 bars = 245K 行，实际 vs 预期偏差 >2% 报警
- 缺失股 ts_code 列表写入 `stock.etl_health_log`
- 次日 ETL 优先重补缺失股

---

## 9. 并发去重（产品核心）

### 9.1 锁机制

```python
def acquire_or_wait(ts_code, analysis_type, data_cutoff_date, max_wait_sec=300):
    lock_key = f"{analysis_type}:{ts_code}:{data_cutoff_date}"
    
    while True:
        # 尝试抢锁
        try:
            with engine.begin() as conn:
                conn.execute(text("""
                    INSERT INTO stock.analysis_lock 
                    (lock_key, holder_record_id, expires_at)
                    VALUES (:k, :rid, now() + interval '5 minutes')
                """), {"k": lock_key, "rid": new_record_id})
            return AcquiredLock(holder=True, record_id=new_record_id)
        except IntegrityError:
            pass
        
        # 检查持有者是否僵死
        holder = conn.execute(text("""
            SELECT l.holder_record_id, l.acquired_at, l.expires_at,
                   r.status, r.started_at
            FROM stock.analysis_lock l
            LEFT JOIN stock.analysis_record r ON l.holder_record_id = r.record_id
            WHERE l.lock_key = :k
        """), {"k": lock_key}).fetchone()
        
        if holder is None:
            continue  # 锁被释放，重试
        
        # 僵死检测：锁过期 OR (running 但 started > 5 分钟)
        is_stale = (
            holder.expires_at < now()
            or (holder.status == 'running' and 
                holder.started_at < now() - timedelta(minutes=5))
        )
        if is_stale:
            # 清理僵死 record + 锁
            cleanup_stale_run(holder.holder_record_id, lock_key)
            continue  # 重试抢锁
        
        # 等待已存在的分析完成
        if (now() - waited_start_at).seconds >= max_wait_sec:
            raise TimeoutError("等待 5 分钟仍未完成，请稍后重试")
        time.sleep(2)
```

### 9.2 僵死清理逻辑

```python
def cleanup_stale_run(record_id, lock_key):
    with engine.begin() as conn:
        # 1. 标记 record 为 failed
        conn.execute(text("""
            UPDATE stock.analysis_record 
            SET status='failed', error_summary='stale_run_cleanup',
                completed_at=now()
            WHERE record_id=:rid AND status='running'
        """), {"rid": record_id})
        
        # 2. 删除锁
        conn.execute(text("""
            DELETE FROM stock.analysis_lock WHERE lock_key=:k
        """), {"k": lock_key})
        
        logger.warning(f"清理僵死 run: {record_id} / {lock_key}")
```

### 9.3 共享层 vs 个性化层切分

```
共享底层报告（一次跑出，多人复用，共享缓存）
       │
       ▼
个性化层（V2.2.3 后启用，每用户独立渲染）
   · 用户成本基础下的 S/R 可读化
   · 风险偏好对应的措辞调整
   · 与该用户其他持仓的关联提示
```

V2.2.0 仅交付共享层；个性化层路线图 P3。

---

## 10. 更新模式（产品灵魂）

### 10.1 触发与路由

```python
def route_analysis_request(ts_code, requested_type='auto', user_id=...):
    base = find_latest_succeeded_deep(ts_code, since_days=14)
    
    if requested_type == 'auto':
        if base is None:
            requested_type = 'fast'   # 默认 fast
        else:
            requested_type = 'update'
    
    if requested_type == 'update' and base is None:
        return Error("未找到 14 天内的 deep 报告，请先 --deep")
    
    if requested_type == 'fresh' or 'force_refresh' in flags:
        return route_to_fresh(...)
    
    days_since = (today - base.triggered_at.date()).days if base else 999
    
    if days_since == 0:
        return CachedReport(base)
    elif days_since <= 14 and requested_type == 'update':
        return route_to_update(base_record_id=base.record_id)
    elif days_since <= 14 and requested_type == 'deep':
        return route_to_fresh('deep')  # 用户主动要新 deep
    else:
        return route_to_fresh(requested_type)
```

### 10.2 Update §02 关键变化结构

```python
@dataclass
class ChangesSinceBase:
    days_since: int
    
    # 价格行为
    price_change_pct: float
    high_change_pct: float
    drawdown_from_base_high: float
    
    # 技术结构
    ma_alignment_changed: bool
    new_ma_crossings: list[str]
    breaches_of_levels: list[Breach]
    
    # 资金面
    capital_flow_5d: float
    flow_consistency_changed: bool
    new_dragon_tiger_appearances: int
    
    # 板块
    sector_strength_change: int
    rotation_status_change: str
    
    # 公告 / 事件
    new_announcements: list[CatalystEvent]
    new_research_reports: int
    new_irm_qa: list[QA]
    
    # 上次预设条件
    validation_triggered: list[str]
    invalidation_triggered: list[str]
    
    # 综合
    label_change: tuple[str, str]
```

### 10.3 LLM 在 Update 模式的 prompt

```
【已知事实】
上次分析时间：5 天前
上次结论标签：normal_watch（常规观察）
上次设的 validation 条件：T+5 内收盘突破 70.00 + 量比 >1.3
上次设的 invalidation 条件：跌破 65.00 收盘

【自上次以来变化】（结构化清单，prompt 不让 LLM 重算）
- 价格：68.50 → 67.20，跌 1.9%
- MA：5MA 下穿 20MA（4 天前发生）
- 资金：5 日累计主力净流出 0.8 亿
- 公告：新增 1 条减持 0.5% 公告
- validation 条件：未触发
- invalidation 条件：未触发
- 板块：板块强度第 12 名 → 第 28 名

【任务】
基于已知事实写 200-300 字总结，并明确：
1) 应该升级 / 降级 / 维持 / 失效？
2) 一句话核心理由
3) 调整后的下次 validation/invalidation 条件
```

LLM 输出 JSON，模板渲染。

---

## 11. 用户使用与体验

### 11.1 CLI

```bash
# 默认路由（智能选）
ifa stock <code>                       # 14 天内有 deep → update；否则 fast
ifa stock 智微智能                      # 名称同样支持
ifa stock 001339

# 显式档位
ifa stock fast --code 001339
ifa stock deep --code 001339 --user u_abc
ifa stock update --code 001339          # 必须 14 天内有 deep

# 强制
ifa stock deep --code 001339 --fresh    # 跳过 update 直接重做
ifa stock <code> --no-cache             # 失效所有缓存

# 跟踪
ifa stock watchlist add --user u_abc --code 001339 --priority key
ifa stock watchlist list --user u_abc
ifa stock track --record-id <uuid>     # 看某次分析的事后兑现

# 健康检查
ifa stock data-check --code 001339      # 仅出数据完备性矩阵
```

### 11.2 HTTP API

```http
POST /api/stock/analyze
{
  "code": "001339",
  "depth": "auto",                  # auto | fast | deep | update | fresh
  "user_external_id": "telegram:123456",
  "options": {"include_pdf": true}
}

→ {
  "record_id": "uuid",
  "status": "running" | "cached" | "shared_with_existing",
  "estimated_seconds": 600,
  "is_shared_runner": false,
  "progress_url": "/api/stock/records/<id>/progress"
}
```

### 11.3 Telegram 体验

```
用户: /stock 智微智能

Bot: 智微智能 (001339.SZ) — 检测到 5 天前已有 deep 分析
     启动【更新】模式...
     ▓▓▓▓▓░░░░ 35%  对比关键变化
     [预计 30 秒]

Bot: ✅ 智微智能 · 更新分析 · 2026-05-03

     标签变化：常规观察 → 谨慎观察 ↓
     
     近 5 天关键变化：
     • 价格 -1.9%，跌破 5MA
     • 5MA 下穿 20MA（4 天前）
     • 主力净流出 0.8 亿
     • 板块强度第 12 → 第 28
     • 新增 1 条减持公告（0.5%）
     
     上次 validation 条件（T+5 突破 70.00+量比 1.3）：未触发
     上次 invalidation 条件（跌破 65.00）：未触发
     
     调整后的关注：
     · 若 67.00 失守 → 进一步下看 60MA (63.50)
     · 若放量收回 5MA → 重新转中性
     
     [完整报告 HTML] [PDF] [查看上次 Deep] [加入观察]
```

---

## 12. LLM 工程规范

### 12.1 模型与温度

| 用途 | 模型 | temp | max_tokens |
|------|------|------|-----------|
| Fast 一句话标签 | gpt-5.4 | 0.2 | 200 |
| Fast 标签解读 | gpt-5.4 | 0.3 | 400 |
| Deep §05 多周期叙述 | gpt-5.4 | 0.3 | 1200 |
| Deep §06 S/R 解读 | gpt-5.4 | 0.2 | 800 |
| Deep §11 价格分布解读 | gpt-5.4 | 0.2 | 1200 |
| Deep §12 三场景 | gpt-5.4 | 0.3 | 1500 |
| Deep §10 策略适配 | gpt-5.4 | 0.2 | 1000 |
| Update §05 vs 上次 | gpt-5.4 | 0.2 | 600 |

### 12.2 守则

- 数字一律来自规则层 / 模型层，prompt 中以"已知事实"出现
- LLM **只解释、不预测、不重算**
- §11 价格分布预测的所有数字来自 L2/L3/L4 集成模型，LLM 仅做翻译
- 输出 JSON，模板渲染
- 每次输出存 `report_model_outputs` 入库便于回溯
- 严禁"建议买入"、"必涨"、"目标价"等措辞
- 总称使用"观察候选"、"setup"、"validation/invalidation"

### 12.3 总成本估算

| 报告 | LLM 调用 | 输入 token | 输出 token |
|------|---------|-----------|-----------|
| Fast | 3-5 次 | ~5K | ~1.5K |
| Deep | 15-18 次 | ~35K | ~10K |
| Update | 3-4 次 | ~6K | ~2K |

---

## 13. 模块组织

```
ifa/families/stock/
├── __init__.py
├── resolver.py                  公司名/代码 → ts_code（复用 research.resolver）
├── orchestrator/
│   ├── router.py                fresh / update / cached 路由
│   ├── lock.py                  分布式锁 + 僵死清理
│   └── runner.py                主编排器
├── etl/
│   ├── intraday_5min.py         全市场 5min 增量
│   ├── intraday_5min_backfill.py 一次性 2 年回填
│   ├── kronos_embed.py          Kronos 嵌入增量
│   ├── faiss_index.py           FAISS 索引重建
│   ├── snapshots.py             多周期快照刷新
│   └── runner.py                每日 ETL
├── data/
│   ├── reuse.py                 从 research/ta/smartmoney 取数的薄包装
│   ├── duckdb_client.py         DuckDB 连接 + 跨库查询
│   ├── parquet_store.py         Parquet 写入 / 分区
│   └── timeframe.py             多周期数据派生
├── algos/
│   ├── L0_rules/
│   │   ├── trend.py
│   │   ├── ma_structure.py
│   │   ├── volume_price.py
│   │   ├── support_resistance.py    ★ 多源 S/R（含 5min VWAP）
│   │   └── catalysts.py
│   ├── L1_stats/
│   │   ├── relative_strength.py
│   │   ├── volatility.py
│   │   ├── valuation_percentile.py
│   │   └── event_study.py
│   ├── L2_ml/
│   │   ├── ningbo_wrapper.py        薄包装 ningbo dual_scorer
│   │   ├── return_classifier.py     5 日预期收益分类
│   │   └── quantile_forecaster.py   ★ LightGBM 量化回归 1M/3M/6M
│   ├── L3_dl/
│   │   ├── kronos_embedder.py       ★ 复用 ningbo/ml/kronos_lib
│   │   ├── faiss_finder.py          ★ FAISS 相似性检索
│   │   └── anomaly.py               Stub（V2.3）
│   └── L4_llm/                      用 prompts/ 模块
├── sections/
│   ├── fast/                        9 节
│   ├── deep/                        16 节
│   └── update/                      7 节
├── prompts/
│   ├── fast_v1.py
│   ├── deep_*_v1.py
│   ├── forecast_v1.py               ★ 价格分布解读
│   ├── update_v1.py
│   └── catalyst_filter_v1.py
├── tracking/
│   ├── recorder.py                  写 stock.analysis_record
│   ├── reviewer.py                  T+1/T+3/T+5 自动评估
│   └── watchlist.py                 用户观察清单
├── render.py                        Jinja2 模板装填（HTML 主输出）
└── report.py                        三档主入口
```

---

## 14. 验收标准（V2.2.0 GA）

1. ✅ 三档报告（fast / deep / update）端到端可用，HTML 主输出
2. ✅ Fast P50 ≤ 60s，Deep P50 5-10 分钟，Update P50 ≤ 45s
3. ✅ 多周期 TA 全景（含 5min 微观结构）正确生成
4. ✅ 多源 S/R 至少 5 类来源生效（pivot + swing + MA + 筹码 + 5min VWAP 必备）
5. ✅ Update 模式：14 天内有 deep 自动触发，结构化变化清单完整
6. ✅ 默认路由：用户不指定档位走 fast；14 天内有 deep 走 update
7. ✅ 并发去重锁：同股同档 5 分钟内并发请求只跑一次
8. ✅ 僵死清理：5 分钟未完成的 running 状态正确清理
9. ✅ 全市场 2 年 5min 数据回填完成（~3 GB Parquet）
10. ✅ 每日 5min 增量 ETL 在 30 分钟内完成
11. ✅ Kronos-small 嵌入库全市场 2 年完成（~2.5 GB Parquet）
12. ✅ FAISS 检索 brute force <50ms / 查询
13. ✅ LightGBM 量化回归 15 模型训练完成 + walk-forward 验证通过
14. ✅ Deep §11 价格分布预测完整：5 分位 + 3 情景 + 历史相似 Top 5
15. ✅ ML 层成功复用 Ningbo dual_scorer，单股推理 <100ms
16. ✅ 历史观察记录闭环：T+1/T+3/T+5 自动评估
17. ✅ HTML 报告中 disclaimer 使用 `disclaimer.py` 完整 10 段中英对照版
18. ✅ 用语合规：不含违禁词
19. ✅ Telegram bot 三档分析端到端可用
20. ✅ 黄金集 30 个 case（fast 10 + deep 10 + update 10）通过率 >80%
21. ✅ M1 8GB 上 5 个 deep 并行内存峰值 <2GB

---

## 15. 工期估算（14 个里程碑，41 天）

| 里程碑 | 内容 | 工期 |
|-------|------|------|
| **SI-M1** | DB schema (PG + DuckDB) + 数据复用层 + 锁机制 + 僵死清理 | 3 天 |
| **SI-M2** | L0 规则层（含 S/R 多源识别） | 4 天 |
| **SI-M3** | L1 统计层（相对强弱、估值分位、事件研究） | 2 天 |
| **SI-M4** | L2 ML 层（Ningbo dual_scorer 包装 + 收益分类） | 2 天 |
| **SI-M5a** | L3 DL 接口骨架（Kronos / FAISS / 异常） | 1 天 |
| **SI-M5b** | DuckDB + 全市场 2 年 5min 回填 + 每日增量 ETL | 2 天 |
| **SI-M5c** | Kronos-small 启用 + 全市场嵌入库构建 + FAISS 检索 | 3 天 |
| **SI-M5d** | LightGBM 量化回归训练（15 模型）+ walk-forward 验证 | 4 天 |
| **SI-M6** | 多周期 TA 派生（含 5min）+ S/R 缓存 | 3 天 |
| **SI-M7** | Fast 9 节 + Deep 15 节（不含 §11） + Update 7 节 sections | 5 天 |
| **SI-M7b** | Deep §11 价格分布预测节实现（集成 L2/L3/L4） | 2 天 |
| **SI-M8** | LLM prompts + 三档 HTML 模板 + disclaimer | 3 天 |
| **SI-M9** | 智能路由 + 并发锁 + 缓存失效 | 2 天 |
| **SI-M10** | CLI + HTTP API + Telegram 适配 | 2 天 |
| **SI-M11** | 黄金集（30 case）+ 回归脚本 | 3 天 |
| **总计** | | **41 天** |

**关键路径**：M1 → M2 → M5b → M5c → M5d → M7 → M7b → M8 → M11

---

## 16. 演进路线

| 版本 | 内容 |
|-----|------|
| **V2.2.0** | Fast / Deep / Update 三档（含价格分布预测） |
| **V2.2.1** | Morning Refresh + 用户观察清单批量分析 |
| **V2.2.2** | SME MVP1 资金结构 family release |
| **V2.2.3** | Intraday Quick Check（接 5min 数据做日内验证）+ Personalized Overlay（个性化层） |
| **V2.3** | DL 接口替换为更强模型（部署到服务器后）+ 异常波动检测启用 |
| **V2.4** | 跨股关联（同板块联动 / 供应链 / 概念聚类） |

---

## 17. 与 Research / TA 的边界

```
Research                Stock Intel              TA Family
─────────             ─────────────              ─────────
单股 + 长周期            单股 + 综合                市场 + 策略
财务尽调                 交易决策助手               晚盘策略报告
17 节 deep              Fast/Deep/Update           16 节 evening
季度刷新                 按需触发（默认 fast）       每日刷新
LLM 重叙事               LLM 多角色                LLM 解释
"值不值得研究"           "现在该不该看 + 怎么看"     "明天大盘看什么"
```

**复用关系**：Stock Intel 在 Research deep 已存在时直接读其结论，不重做财务分析；在 TA candidates 表里查本股是否曾被某 setup 选中；在 SmartMoney 里取板块强度；在 Ningbo 里取 Kronos 嵌入设施与 ML 模型。它是**最 thin 的整合层**。

---

## 附录 · 词汇表

| 术语 | 定义 |
|------|------|
| Fast / Deep / Update | 三档分析模式 |
| Regime | 市场体制（来自 TA Family） |
| Setup | 技术形态（来自 TA Family 18 setup） |
| S/R | 支撑（Support）/ 阻力（Resistance） |
| VWAP | Volume-Weighted Average Price，成交量加权均价 |
| Kronos | 预训练 OHLCV 编码器（HuggingFace NeoQuasar/Kronos-Tokenizer-2k） |
| FAISS | Facebook AI 相似度检索库 |
| Quantile Regression | 量化回归，预测分位数而非点估值 |
| Analog | 历史类似形态 |
| PIT | Point-in-Time，时间正确，不引入未来信息 |
