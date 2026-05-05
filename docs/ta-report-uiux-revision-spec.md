# iFA TA Evening Report Final UIUX & Layout Revision Spec

> **基准报告**: `tmp/ifa_TA_evening_20260430_0115.html`（生产报告 2026-04-30，生成时间 2026-05-05 01:15）  
> **版本**: v2.0 Final · 2026-05-05  
> **范围**: UIUX / 信息层级 / 报告结构 / 文案合规 / 移动端展示 — 本次一次性完成所有修改项

---

## 1. Executive Summary

### 当前报告最大的问题

1. **无首屏摘要**：报告打开后没有任何"30秒结论"——体制、候选数量、风险状态全部埋在第三屏之后。高净值用户打开报告第一眼看不到任何行动信息。

2. **候选表零区分度**：§03 五星级候选展示了30行，每行 setup 名称相同（`C1_CHIP_CONCENTRATED`）、触发条件相同（6个完全一致的英文 tag）、评分相同（1.00）。读者扫描20行后无法区分任何候选标的的差异。§04 同样问题。

3. **上下文后置**：§07 Setup族分布（今日候选的全局结构）放在§03/04 候选列表之后。读者先看完60行候选才能看到今日的策略结构全貌。

4. **章节编号混乱**：§05/06/09/12 跳空缺失；§02-N/04-N/13-N 是编号噪声；§04 出现两次；§07 出现两次。

5. **全部英文机器码**：Setup 代码（`C1_CHIP_CONCENTRATED`）、触发条件（`regime_tailwind`）、验证状态（`confirmed/invalidated`）全部英文，非技术读者无法理解。

6. **候选标的无名称**：整份报告只显示股票代码（`000001.SZ`），没有股票名称。读者无法在不查询的情况下识别标的。

7. **合规表达缺失**：当前文案中"五星级候选"的"候选"用法、§14 中"将于 T+1 上涨 ≥ 2%"的表述、推荐价/止损/目标等词汇，均存在被误解为投资建议的风险。

8. **§04 四星级候选行号从 #1565 开始**：显示的是全局排名索引，对读者没有意义，且在视觉上暗示"共有 1600 个四星候选"，混淆信息。

### 本次修改后的目标状态

- 打开报告，30秒内从首屏仪表盘读取体制、观察池规模、风险状态、T+1验证胜率；
- 高优先级观察池（原五星级）显示股票名称，所有 Setup/触发条件显示中文标签；
- 报告结构按"市场环境→候选结构→高优先级→次优先级→风险→表现→假设"流向排列，没有上下文后置；
- 章节编号连续，无跳空，无编号噪声；
- 所有文案符合合规表达要求，无任何"推荐买入/止损/目标"类措辞；
- 移动端宽表可横向滚动，仪表盘4卡变2×2，Setup tag 自动换行；
- 过长的 LLM 解读段、详细触发条件、方法说明移入折叠区或 Appendix，正文保持紧凑。

### 保留的内容

| 类别 | 保留内容 |
|------|---------|
| 视觉体系 | 字体栈、色调（蓝 `#0969da`、绿 `#1a7f37`、红 `#cf222e`、灰 `#6e7681`）、边框 `#d0d7de`、背景 `#f6f8fa` |
| 渲染方式 | Self-contained HTML，无外部 JS/字体/CDN 依赖，邮件兼容 |
| 数据内容 | 所有数值、胜率、均收益、盈亏比、验证状态——数据本身不改 |
| 免责声明 | 文本内容完整保留，位置保留在报告末尾 |
| LLM 解读段 | 内容保留，改为折叠展示 |
| `.kpi` / `.tag` / `.stars` 基础样式 | 保留，只扩充 |

### 重排的内容

§07 前移至候选表之前；§13/§13-N 前移至候选表之后；§02-N/04-N/13-N 降级为父节折叠子块；§08/§10/§11 合并为同一"历史表现"节；所有节重编号（详见§7）。

### 必须替换的文案

见 §4 完整替换表。核心：五星级→高优先级观察池、止损→技术失效位、目标→T+15观察目标区、confirmed→验证成功、invalidated→技术失效、候选→技术观察标的。

### 必须调整的组件

见 §5 组件变更表。核心：候选表加名称列、setup 代码中文化、聚合行设计（多行相同 setup 时）、新增首屏仪表盘、移动端宽表横向滚动、LLM 段改 `<details>` 折叠。

---

## 2. Non-Negotiable Boundaries

本次修改严格遵守以下边界：

| 类别 | 不改动的内容 |
|------|------------|
| 算法 | 不改扫描算法、评分算法、ranker 权重 |
| 策略 | 不改 T+15 三周筛选目标、不改 Tier A/B/C 分级逻辑 |
| 参数 | 不改 `ta_v2.2.yaml` 中任何参数值 |
| Setup 定义 | 不改任何 setup 触发条件、阈值、分组 |
| Backtest | 不改回测窗口、不改绩效指标计算、不改 walk-forward 逻辑 |
| 数据库 | 不执行任何 Alembic migration，不改任何表结构，不改数据写入逻辑 |
| 核心文件 | 不碰 `setups/**`、`ranker.py`、`scanner.py`、`backtest/**`、`params/ta_v2.2.yaml`、`context_loader.py`（算法部分）|
| 渲染方式 | 不引入外部 JS 库、不引入 CSS 框架、不引入外部字体、不引入 CDN 依赖 |
| 视觉体系 | 不推翻现有色彩/字体/卡片风格，不重做设计系统 |
| 内容定性 | 报告不能写成买卖建议，不能出现任何"推荐买入/卖出/止损/目标价"等直接指令措辞 |
| 数据值 | 任何数值、胜率、均收益、样本量——数字本身不改，只改展示方式 |

---

## 3. Final Report Structure

本节给出最终实现的完整报告结构。

| Final Section | 来源于当前哪些 section | 最终展示内容 | 默认展示/折叠 | 必须改动 |
|---|---|---|---|---|
| **Header · 报告定位** | §00（已有但未在当前 HTML 中显示）+ 报告标题 + meta | 报告标题、生成时间、一行系统定位声明："本报告为 iFA TA 算法三周摆动技术观察报告，不构成投资建议" | 默认展示 | 加系统定位声明；meta 格式统一 |
| **§01 · 今日快览 Dashboard** | 无（新增） | 4张卡片：市场体制 + 置信度 / 今日高优先级观察标的数 + 次优先级数 / 最多命中 Setup 族 / 昨日验证胜率 | 默认展示，全屏首位 | 新增全新 block，CSS 四列 grid，移动端2×2 |
| **§02 · 市场环境** | §01 市场概览 + §02 市场状态盘 + §02-N 体制解读 | 体制 badge + 置信度、指数收盘/涨跌幅、成交额（当日/60日均比）、涨跌家数、涨停/跌停、北向资金（当日）、市态；体制解读折叠 | 默认展示；体制解读默认折叠 | §01/02 合并；§02-N 降级为折叠子块；KPI chip 格式统一（不换行）|
| **§03 · 候选结构总览** | §07 候选股池（按 Setup 族） | Setup 族命中数 / ≥ 系统优先级 4 数 / 占比 / 主导 Setup；体制相关性标注 | 默认展示 | 前移至候选表之前；加"占比"列；加体制强势族标注；Setup 代码全部中文化 |
| **§04 · SmartMoney 板块资金闸门** | 无（新增，读取已有 SmartMoney 数据） | 当日净流入前 5 板块；资金闸门颜色指示：强流入（绿）/ 中性（灰）/ 流出（红）；与当日观察标的的板块重叠率 | 默认展示，简表 | 新增 block；builder.py 加 SmartMoney 板块数据查询（读取 `sector_moneyflow_sw_daily`）；模板加渲染 |
| **§05 · 高优先级观察池 Tier A** | §03 五星级候选 | 每个标的：代码 + **名称** + Setup 中文标签 + 系统优先级 + 触发条件中文 tags + 入池观察价区 / 技术失效位 / T+15观察目标区；多行相同 setup 时聚合展示；每个标的提供单股深度分析承接提示 | 默认展示；聚合行默认折叠个股明细 | Setup 中文化；加名称列；聚合行设计；文案替换；加深度分析承接提示 |
| **§06 · 次优先级观察池 Tier B** | §04 四星级候选 + §04-N 候选解读 | 简化表格：代码 + **名称** + Setup 中文标签 + 系统优先级 + 触发条件中文 tags；行号从1开始，节标题注明总数；§04-N 解读折叠 | 默认展示（简化表格）；LLM 解读折叠 | Setup 中文化；加名称列；行号改为局部序号；§04-N 降级折叠 |
| **§07 · 风险扫描** | §13 风险扫描 + §13-N 策略评论 | 筹码松动标的数（C2）/ Setup 衰减超阈值列表 / D族顶部警示数 / 黑名单触发数；策略评论折叠 | 默认展示；策略评论折叠 | §13 前移；§13-N 降级折叠；加 D 族警示数和黑名单触发数 |
| **§08 · 历史观察池表现** | §08 验证回顾(T+1) + §10 Setup滚动边际 + §11 近5日表现归因 | 昨日验证汇总（胜率 + 四态分布）+ 前20条验证明细；Setup 综合统计宽表（60日/近5日并列，含颜色编码）| 验证汇总默认展示；Setup 详表默认折叠 | 三节合并；加验证胜率汇总行；Setup 表加颜色编码；§10/11 合并为宽表；验证状态中文化 |
| **§09 · 今日主导 Setup / 策略族** | §07 主导 Setup 详细分析 + §04-N 部分内容 | 今日命中最多的 3 个 Setup 的特征说明、当前体制下的历史胜率、代表性触发条件说明 | 默认展示 | 从§07/04-N 中提炼主导 Setup 信息；Setup 中文化 |
| **§10 · 可证伪假设** | §14 次日假设 | 精简表格：代码 + 名称 + Setup + 假设方向 + 入池观察价区 + 技术失效位；节头声明"以下为系统生成可证伪预测，T+1 = {next_trade_date}" | 默认展示 | §14 重构为表格；文案替换；去除重复句式 |
| **§11 · Appendix / 方法说明** | 无（新增折叠区） | Setup 代码中英文对照表；触发条件定义；分数计算说明；胜率/均收益/盈亏比定义；体制类型说明；数据口径说明 | 默认折叠（`<details>`）| 新增 Appendix block；将方法说明、术语表移至此处 |
| **§12 · 完整免责声明** | 免责声明 | 原有免责声明文本完整保留；加一句"候选标的经算法筛选，不代表应买入或持有" | 默认展示 | 小幅补充一句；位置保持末尾 |

---

## 4. Exact Wording Replacement Table

以下为全量文案替换，所有项均为本次一次性完成。

| Current Text | Final Text | Applies To | Reason |
|---|---|---|---|
| 五星级候选 | 高优先级技术观察标的 | §03/§05 节标题 | "五星级"易被理解为投资评级 |
| 四星级候选 | 次优先级技术观察标的 | §04/§06 节标题 | 同上 |
| ★★★★★ / ★★★★☆ | 系统优先级 A / 系统优先级 B | 候选表星级列 | 避免"五星"被误解为推荐评级 |
| 推荐价 / 建议挂单 | 入池观察价区 | candidates_daily 推荐价展示列 | "推荐"违反合规要求 |
| 止损 | 技术失效位 | candidates_daily 止损列 | "止损"暗示具体交易操作指引 |
| 目标价 / 目标 | T+15 观察目标区 | candidates_daily 目标列 | "目标价"暗示价格预测承诺 |
| 推荐日 | 入池日期 | 追踪表入池日期列 | "推荐"违反合规要求 |
| 高 conviction | 高优先级观察 | 任意出现处 | 英文混用且含评级暗示 |
| 重点池 | 高优先级观察池 | 任意出现处 | 统一命名 |
| 候选池 | 次优先级观察池 | 任意出现处 | 统一命名 |
| 候选 | 技术观察标的 | 报告正文所有处（除"观察池"组合词外）| 避免"候选"暗示选出即可行动 |
| 达标 ≥+5% | 期间最大涨幅 ≥+5% | §08 验证表 | "达标"暗示有预设收益目标 |
| 正收益 | 当前累计为正 | §08 验证表 | 措辞中性化 |
| 负收益 | 当前累计为负 | §08 验证表 | 措辞中性化 |
| confirmed | 验证成功 | §08 验证状态 | 英文不可读 |
| invalidated | 技术失效 | §08 验证状态 | 英文不可读 |
| partial | 部分验证 | §08 验证状态 | 英文不可读 |
| timeout | 观察中 | §08 验证状态 | 英文不可读；"超时"有误导 |
| 触发条件 | 技术特征 | 候选表列标题 | "触发"为系统内部术语，读者陌生 |
| 分（评分列） | 系统评分 | 候选表列标题 | 加明确语义 |
| T+1 上涨 ≥ 2% | 模型预期 T+1 上涨 ≥ 2%（可证伪假设） | §14/§10 | "将于 T+1 上涨"表述过于确定 |
| 次日假设（可证伪） | 可证伪技术假设 | 节标题 | 去掉"次日"防止读者理解为次日必须交易 |
| 体制 | 市场体制 | §01/§02 KPI chip | 加完整词 |
| trend_continuation | 趋势延续 | 体制显示 | 中文化 |
| distribution_risk | 分配风险 | 体制显示 | 中文化 |
| range_bound | 震荡区间 | 体制显示 | 中文化 |
| transition | 体制切换 | 体制显示 | 中文化 |
| uptrend_stack | 上涨堆叠 | 触发条件 tag | 中文化 |
| chip_concentrated<=15% | 筹码集中≤15% | 触发条件 tag | 中文化 |
| above_ma20 | MA20上方 | 触发条件 tag | 中文化 |
| regime_tailwind | 体制顺风 | 触发条件 tag | 中文化 |
| very_concentrated | 极度筹码集中 | 触发条件 tag | 中文化 |
| balanced_winners | 盈面均衡 | 触发条件 tag | 中文化 |
| T1_BREAKOUT | T1 突破 | Setup 代码所有出现处 | 中文化 |
| T2_PULLBACK_RESUME | T2 回踩续涨 | Setup 代码所有出现处 | 中文化 |
| T3_ACCELERATION | T3 加速 | Setup 代码所有出现处 | 中文化 |
| P1_MA20_PULLBACK | P1 均线回踩 | Setup 代码所有出现处 | 中文化 |
| P2_GAP_FILL | P2 缺口回补 | Setup 代码所有出现处 | 中文化 |
| P3_TIGHT_CONSOLIDATION | P3 紧密整理 | Setup 代码所有出现处 | 中文化 |
| R1_DOUBLE_BOTTOM | R1 双底 | Setup 代码所有出现处 | 中文化 |
| R2_HS_BOTTOM | R2 头肩底 | Setup 代码所有出现处 | 中文化 |
| R3_HAMMER | R3 锤子线 | Setup 代码所有出现处 | 中文化 |
| F2_TRIANGLE | F2 三角形 | Setup 代码所有出现处 | 中文化 |
| F3_RECTANGLE | F3 矩形 | Setup 代码所有出现处 | 中文化 |
| V1_VOL_PRICE_UP | V1 量价齐升 | Setup 代码所有出现处 | 中文化 |
| V2_QUIET_COIL | V2 缩量蓄势 | Setup 代码所有出现处 | 中文化 |
| S1_SECTOR_RESONANCE | S1 板块共振 | Setup 代码所有出现处 | 中文化 |
| S2_LEADER_FOLLOWTHROUGH | S2 龙头跟风 | Setup 代码所有出现处 | 中文化 |
| S3_LAGGARD_CATCHUP | S3 补涨 | Setup 代码所有出现处 | 中文化 |
| C1_CHIP_CONCENTRATED | C1 筹码集中 | Setup 代码所有出现处 | 中文化 |
| C2_CHIP_LOOSE | C2 筹码松动 ⚠️ | Setup 代码所有出现处 | 中文化；加警示符 |
| 近5日表现归因 | 近5日Setup表现 | 节标题 | "归因"为量化术语，读者陌生 |
| 体制解读 | （合并入市场环境节，不单独命名）| §02-N | 降级为折叠子块，不单独命名 |
| 候选解读 | （合并入次优先级观察池节，不单独命名）| §04-N | 降级为折叠子块，不单独命名 |
| 策略评论 | （合并入风险扫描节，不单独命名）| §13-N | 降级为折叠子块，不单独命名 |
| 衰减（§10 列名） | 趋势变化（60日 vs 250日，pp） | §08/§10 统计表列标题 | 原名含义不明 |
| 胜率 60d | 胜率（近60日）| §08/§10 统计表列标题 | 加括号说明时间窗 |
| 均收益 60d | 平均T+1收益（近60日）| §08/§10 统计表列标题 | 明确收益计算口径 |

---

## 5. Final Component-Level Changes

| Component | Current Problem | Final Required Change | HTML/CSS Notes | Acceptance Criteria |
|---|---|---|---|---|
| **Header** | 只有 `<h1>` 标题 + `.meta` 生成时间行，无系统定位声明 | 在 `<h1>` 下方加一行系统定位声明：`本报告为 iFA TA 算法三周摆动技术观察报告，不构成投资建议` | `<p class="ifa-positioning-line">` 样式：小字号、灰色、border-bottom | 打开报告，第一行下方即可看到系统定位声明 |
| **30秒 Dashboard** | 不存在 | 新增 `.ifa-dashboard` block，四张卡片：市场体制/今日观察池规模/最多命中Setup族/昨日验证胜率 | `display:grid; grid-template-columns:repeat(4,1fr)` 四列；各卡片含 label / value（大字加粗 + 蓝色）/ sub（灰色小字）| 任何设备打开报告，首屏即可看到 Dashboard；数值与下方正文一致 |
| **Market Regime Card** | 体制显示为 `trend_continuation(0.75)` 英文，置信度括号内跟随，同行显示 | 单独 badge 展示：体制中文名主文、置信度次文；若置信 ≥ 0.7 蓝底白字，0.5-0.7 灰底，< 0.5 橙底 | `.regime-badge { border-radius:4px; padding:2px 8px; }` | 体制名称显示为中文；置信度分级有颜色区别 |
| **KPI Cards（§02）** | `(60d 0%)` 在窄屏换行到下一行；北向资金无时间说明 | 所有 KPI chip 加 `white-space: nowrap`；北向资金显示为 `北向 +35.1亿（当日）` | `.kpi { white-space: nowrap; }` 已有 `.kpi`，加属性即可 | 移动端375px宽度下 chip 内部不换行 |
| **SmartMoney Sector Gate（§04）** | 不存在 | 新增 block，显示当日净流入前5 SW L2 板块；每行含板块名、净流入金额（亿）、颜色指示（绿/灰/红）；与 Tier A 标的的板块重叠数 | 简单5行表格；颜色 class：`.flow-in`（绿）/ `.flow-flat`（灰）/ `.flow-out`（红）| 显示5条板块数据；与 Tier A 板块重叠数字准确 |
| **Tier A Stock Display（§05）** | 无名称；全英文代码；所有行 setup 相同时无聚合；评分无解释；星级无语义说明 | 加名称列；Setup 显示中文标签；多行 setup 相同时显示聚合头行 + 折叠个股明细；列标题从"分/★"改为"系统评分/系统优先级"；每个标的底部加"→ 查看单股深度分析"承接提示 | 聚合头行 `class="setup-group-header"`；折叠用 `<details><summary>` | 标的行显示股票名称；Setup 为中文；相同 setup 有聚合；有深度分析承接提示 |
| **Tier B Stock Display（§06）** | 无名称；全英文代码；行号从 #1565 开始（全局索引）；§04-N 独立编号 | 加名称列；Setup 中文化；行号改 `loop.index`，节标题注明总数；§04-N 降级为`<details>` 折叠子块 | 同§05 名称列和中文化；`<details class="llm-note">` | 行号从1开始；有股票名称；LLM 解读为折叠状态 |
| **Setup Tags** | 全英文，如 `regime_tailwind` / `chip_concentrated<=15%` | 所有 tag 显示中文（见§4替换表）；通过 `labels.py` 的 `TRIGGER_ZH` dict 在模板渲染时替换 | `{{ TRIGGER_ZH.get(tag, tag) }}` in Jinja loop | 报告中无裸英文 trigger tag；中文 tag 在移动端自动换行 |
| **Risk Tags** | C2 松动用普通文字（"筹码松动候选 (C2)：37"）；无颜色区分；无 D 族计数 | C2 松动数显示为橙色 badge；若 Setup 有衰减超阈值显示红色 badge；D族警示数单独一行 | `.risk-badge { background:#fff3cd; color:#856404; }` 橙色 | 风险扫描节一眼可见橙/红 badge |
| **Historical Tracking Panel（§08）** | §08/§10/§11 三节分散，无颜色编码，验证状态全英文，无胜率汇总行 | 三节合并为一个"历史观察池表现"节；验证状态中文化；加胜率汇总行；Setup 统计表加胜率颜色（绿/橙/红）；§10/§11 合并为宽表（60日+近5日并列）| `.winrate-high { color:#1a7f37 }` / `.winrate-mid { color:#9a6700 }` / `.winrate-low { color:#cf222e }` | 胜率 ≥30% 绿色；25-30% 橙色；<25% 红色；验证状态显示中文 |
| **LLM Interpretation Blocks** | §02-N / §04-N / §13-N 为独立 `<h2>` 节，造成编号噪声，强制展开 | 全部降级为父节的 `<details>` 折叠子块，默认折叠，summary 文字为"算法解读 ▼" | `<details class="llm-note"><summary>算法解读 ▼</summary>` | LLM 块不占用独立编号；默认折叠；可点击展开 |
| **Appendix** | 不存在，术语/方法说明散落正文或不存在 | 新增`<details class="appendix-block">` 折叠区，包含：Setup 代码对照表、触发条件定义、评分说明、胜率/盈亏比定义、体制类型说明、数据口径说明 | `<details><summary>📎 方法说明 & 术语表</summary>` | Appendix 默认折叠；包含完整术语对照；不影响正文长度 |
| **Mobile Layout** | 宽表（§10/§11）无横向滚动；KPI chips 多行断裂；Dashboard 四列在小屏溢出 | 见§8最终移动端规则 | 见§8 | 375px 下无内容溢出；宽表可横向滑动 |
| **Print/PDF Layout** | 未针对打印优化 | `@media print` 规则：隐藏 `<details>` 折叠按钮（全部展开）；Dashboard 印出为2×2；颜色保留（`-webkit-print-color-adjust: exact`）| `@media print { details { display: block; } summary { display: none; } }` | 打印/PDF 导出时所有内容展开可读 |
| **Disclaimer** | 当前内容完整，末尾位置正确 | 在现有文本末尾加一句：`候选标的经算法筛选，不代表应买入或持有` | 无结构改动，仅追加一句 | 免责声明包含完整的算法生成声明和操作风险声明 |

---

## 6. Candidate Table Final Design

### Tier A（高优先级观察池，原五星级候选）

| Field | Current Display | Final Display | Notes |
|---|---|---|---|
| 行序号 | `1`, `2`, `3`... | 保持 `1`, `2`, `3` | 局部序号，OK |
| 股票代码 | `000001.SZ` | `000001.SZ` | 保持 |
| 股票名称 | **不存在** | **`平安银行`**（从 `raw_daily_basic.name` 取）| builder.py 需在候选查询中 JOIN name 字段 |
| Setup 代码 | `C1_CHIP_CONCENTRATED`（英文） | `C1 筹码集中`（中文）| `{{ SETUP_ZH[row.setup] }}` via `labels.py` |
| 系统评分 | `分` 列，值 `1.00` | `系统评分` 列，值 `1.00`；列标题加 `<abbr title="0-1分，由 ranker 综合多因子计算">ⓘ</abbr>` | `<abbr>` tooltip 说明评分含义 |
| 星级 | `★★★★★`（`.stars` 样式） | 改为 `系统优先级 A`，badge 样式，蓝色 | `<span class="tier-badge tier-a">系统优先级 A</span>`；`.tier-a { background:#dbeafe; color:#1d4ed8 }` |
| 触发条件 | `<span class="tag">regime_tailwind</span>...`（英文6个）| `<span class="tag">体制顺风</span>...`（中文）| `{{ TRIGGER_ZH.get(tag, tag) }}` in Jinja loop |
| 聚合行（多行相同 setup）| **不存在**，30行重复相同内容 | 当同一 setup 有 N 只时，显示一行聚合头：`C1 筹码集中 × 30 只 [系统评分 1.00]` + 公共触发条件；展开后显示个股明细 | `<details><summary>C1 筹码集中 × 30只 ▼</summary>` 内嵌个股行 |
| 价格信息（入池观察价区）| **不存在**（candidates_daily 有数据但未展示）| `入池观察价区`列，显示 `stop_price` 和 `target_price`；标注 `技术失效位` / `T+15 观察目标区` | 从 builder 查询中补充这两个字段（已有列）|
| 深度分析承接提示 | **不存在** | 每个标的右侧或底部显示 `→ 查看单股深度分析`（链接指向当日 research report，若存在）| `<a class="deep-link" href="research_{code}.html">→ 单股深度</a>`；若文件不存在则隐藏 |

### Tier B（次优先级观察池，原四星级候选）

| Field | Current Display | Final Display | Notes |
|---|---|---|---|
| 行序号 | 全局排名序号（从 `#1565` 开始）| 局部序号 `{{ loop.index }}`；节标题加 `（共 {{ tier_b_count }} 只）`| 去掉全局序号 |
| 股票代码 | `000006.SZ` | `000006.SZ` | 保持 |
| 股票名称 | **不存在** | **`深振业A`**（同 Tier A，JOIN name）| 同 Tier A |
| Setup 代码 | 英文 | 中文（同 Tier A）| 同 Tier A |
| 系统评分 | `0.80` | `0.80`；列标题加 tooltip | 同 Tier A |
| 星级 | `★★★★☆` | 改为 `系统优先级 B`，灰色 badge | `<span class="tier-badge tier-b">系统优先级 B</span>` |
| 触发条件 | 英文 tag | 中文 tag | 同 Tier A |
| 聚合行 | **不存在** | 同 Tier A 聚合逻辑 | 同 Tier A |
| 价格信息 | **不存在** | 同 Tier A（`入池观察价区` / `技术失效位` / `T+15 观察目标区`）| 同 Tier A |
| 深度分析承接提示 | **不存在** | Tier B 不加深度分析链接（保持简化表格）| 与 Tier A 区分 |
| LLM 解读 | 独立 `<h2>§04-N>` 节 | `<details class="llm-note">` 折叠，在 Tier B 表格下方，默认折叠 | 见§5 LLM Blocks |

---

## 7. Final Section Renumbering

| Current Section | Final Section | Action |
|---|---|---|
| *(标题 + meta 行)* | **Header** | 保留并扩充（加系统定位声明）|
| *(无)* | **§01 · 今日快览 Dashboard** | 新增 |
| §01 市场概览 | **§02 · 市场环境**（合并）| 与§02合并 |
| §02 市场状态盘 | **§02 · 市场环境**（合并）| 与§01合并 |
| §02-N 体制解读 | **§02 折叠子块**（不单独编号）| 降级为`<details>`，移除独立 `<h2>` |
| §03 五星级候选 | **§05 · 高优先级观察池 Tier A** | 重编号；内容改造（见§6）|
| §04 四星级候选 | **§06 · 次优先级观察池 Tier B** | 重编号；内容改造（见§6）|
| §04-N 候选解读 | **§06 折叠子块**（不单独编号）| 降级为`<details>` |
| *(无)* | **§03 · 候选结构总览**（原§07前移）| 前移并重编号 |
| *(无)* | **§04 · SmartMoney 板块资金闸门** | 新增 |
| §07 候选股池（按 Setup 族）| **已前移为§03**，原位置移除 | 前移 |
| §08 验证回顾 (T+1) | **§08 · 历史观察池表现**（合并）| 与§10/§11合并 |
| *(§09 跳空)* | *(消除跳空)* | 无 §09 跳空（由 §09 今日主导 Setup 填补）|
| §10 Setup 滚动边际 | **§08 · 历史观察池表现**（合并）| 合并入§08 |
| §11 近5日表现归因 | **§08 · 历史观察池表现**（合并）| 合并入§08 |
| *(§12 跳空)* | *(消除跳空)* | 无 §12 跳空（由 §11 Appendix 填补）|
| §13 风险扫描 | **§07 · 风险扫描**（前移）| 前移至§06之后 |
| §13-N 策略评论 | **§07 折叠子块**（不单独编号）| 降级为`<details>` |
| §14 次日假设（可证伪）| **§10 · 可证伪技术假设** | 重编号；文案替换；改为表格 |
| *(无)* | **§09 · 今日主导 Setup / 策略族** | 新增，从§03候选结构和§04-N提炼 |
| *(无)* | **§11 · Appendix / 方法说明** | 新增折叠区 |
| 免责声明 | **§12 · 完整免责声明** | 重编号；补充一句 |

**最终节序（连续无跳空）**：

```
Header · 报告定位
§01 今日快览 Dashboard
§02 市场环境
§03 候选结构总览
§04 SmartMoney 板块资金闸门
§05 高优先级观察池 Tier A
§06 次优先级观察池 Tier B
§07 风险扫描
§08 历史观察池表现
§09 今日主导 Setup / 策略族
§10 可证伪技术假设
§11 Appendix / 方法说明
§12 完整免责声明
```

---

## 8. Final Mobile Layout Rules

以下为最终移动端（`max-width: 720px`）规则，全部写入 `styles.css`。

| 元素 | 最终规则 |
|------|---------|
| **Header** | `<h1>` 字号从 `1.6em` 降至 `1.3em`；系统定位声明折行但不截断 |
| **Dashboard（§01）** | `grid-template-columns: repeat(2, 1fr)`：4卡变2×2布局；每卡 `.ifa-dash-card__value` 字号不低于 `1.2em` |
| **Tier A 候选表** | 整个 `<table>` 包裹在 `<div style="overflow-x:auto">` 内；最小列宽：代码列 `80px`，名称列 `80px`，Setup列 `90px`，触发条件列 `160px`（允许横向滚动）|
| **Tier B 候选表** | 同 Tier A，`overflow-x: auto` |
| **§08 历史表现宽表** | 同上，`overflow-x: auto`；宽表在移动端不折行，只允许横向滚动 |
| **KPI Chips** | `flex-wrap: wrap`；每个 `.kpi` 加 `white-space: nowrap`；chips 之间换行但 chip 内部不换行 |
| **Setup Tags** | `flex-wrap: wrap`；每个 `.tag` 独立换行；最小字号 `0.78em` |
| **LLM 折叠块** | `<details>` 原生支持触摸展开，无需 JS |
| **Appendix 折叠块** | `<details>` 原生支持触摸展开，无需 JS |
| **体制 Badge** | 固定高度 `28px`，文字不折行 |
| **价格信息列** | `white-space: nowrap`，允许表格横向滚动显示完整价格 |
| **字体最小字号** | 正文最小 `0.78em`（约12px）；表头最小 `0.82em`；报告全局 `font-size` 基准 `16px` |
| **Dashboard 卡片值** | 移动端 `.ifa-dash-card__value` 最小 `1.1em`，不得小于 `18px` |

所有 `@media (max-width: 720px)` 规则集中写入 `styles.css` 末尾，单独 media block。

---

## 9. Final Appendix Rules

以下内容从正文移入 `§11 Appendix / 方法说明`（`<details>` 折叠区，默认折叠）：

| 内容 | 当前位置 | Appendix 处理方式 |
|------|---------|-----------------|
| Setup 代码英文→中文对照表 | 不存在 | 新增完整对照表（22个 setup，含族说明）|
| 触发条件（trigger tag）定义说明 | 不存在 | 新增：每个 tag 的含义一句话说明 |
| 系统评分（0-1分）计算说明 | 不存在 | 新增：说明 ranker 综合因子评分逻辑（不涉及参数值）|
| 胜率定义 | 不存在 | 新增：`胜率 = T+1实际涨幅≥+2%的标的数 / 全部有效出场标的数` |
| 盈亏比（赔率）定义 | 不存在 | 新增：`盈亏比 = 平均正收益 / |平均负收益|` |
| `趋势变化（60日 vs 250日，pp）`定义 | 列名隐含但未说明 | 新增一行说明 |
| 体制类型说明 | 不存在 | 新增：`趋势延续/分配风险/震荡区间/体制切换` 各一句定义 |
| 数据口径说明 | 不存在 | 新增：`T+1收益 = 次日收盘价相对今日收盘价涨跌幅`；`TuShare 数据源；申万二级行业分类 2021年版` |
| LLM 长段解读（§02-N / §04-N / §13-N 内容）| 独立 `<h2>` 节 | 降级为各父节内嵌 `<details>`，内容完整保留，默认折叠 |
| debug / 内部运行详情（如有）| 不适用 | 如存在 debug 行，移入 Appendix 末尾独立折叠块 |

Appendix 不删除任何内容。所有移入 Appendix 的内容仍在同一 HTML 文件中，可展开查看。

---

## 10. Final Implementation Checklist

**文件范围**：
- `ifa/families/ta/report/templates/ta_evening.html` — 主要改动
- `ifa/families/ta/report/templates/styles.css` — CSS 新增/扩充
- `ifa/families/ta/report/labels.py` — 文案映射 dict
- `ifa/families/ta/report/builder.py` — 仅补充候选查询中的 name 字段 + SmartMoney 板块查询

**不碰**：setups / params yaml / backtest / scan / ranker / alembic migration / context_loader 算法段

| # | File / Template | Change | Acceptance Criteria |
|---|---|---|---|
| 1 | `labels.py` | 新增 `SETUP_ZH: dict[str, str]`，覆盖所有22个 setup 代码的中文标签 | `SETUP_ZH['C1_CHIP_CONCENTRATED'] == 'C1 筹码集中'`；覆盖全部 setup |
| 2 | `labels.py` | 新增 `TRIGGER_ZH: dict[str, str]`，覆盖所有 trigger tag 的中文标签 | `TRIGGER_ZH['regime_tailwind'] == '体制顺风'`；所有常见 tag 有中文 |
| 3 | `labels.py` | 新增 `STATUS_ZH: dict[str, str]`：`confirmed→验证成功`，`invalidated→技术失效`，`partial→部分验证`，`timeout→观察中` | `STATUS_ZH['confirmed'] == '验证成功'` |
| 4 | `labels.py` | 新增 `REGIME_ZH: dict[str, str]`：体制英文→中文 | `REGIME_ZH['trend_continuation'] == '趋势延续'` |
| 5 | `builder.py` | 候选查询（`_build_tier_a_rows` / `_build_tier_b_rows`）JOIN `raw_daily_basic.name` 字段，加入返回结果 | `rows[0].name` 存在且非空 |
| 6 | `builder.py` | 新增 `_build_smartmoney_gate(engine, on_date)` 函数，查询 `sector_moneyflow_sw_daily` 当日净流入前5 SW L2 板块 | 返回5条板块记录，含 `l2_name` / `net_amount` / `flow_signal` |
| 7 | `ta_evening.html` | Header 区域加系统定位声明 `<p class="ifa-positioning-line">` | 渲染后第一行下方可见系统定位声明 |
| 8 | `ta_evening.html` | 新增 `§01 今日快览 Dashboard` block，四张卡片，变量从 context 读取 | 渲染后显示体制/观察池数量/最强Setup/T+1胜率四卡片 |
| 9 | `ta_evening.html` | §01 市场概览 + §02 市场状态盘合并为 `§02 市场环境`，修正 KPI chip 格式（北向加"当日"说明）| 渲染后无"市场概览"和"市场状态盘"两个独立节 |
| 10 | `ta_evening.html` | §02-N 体制解读改为 `<details class="llm-note">` 折叠块，置于§02节内，不再是独立 `<h2>` | 渲染后无独立的"§02-N 体制解读"节；折叠块存在于§02下方 |
| 11 | `ta_evening.html` | §07 候选股池 block 前移至候选表之前，重编号为 `§03 候选结构总览` | 渲染后§03 在§05/06 之前 |
| 12 | `ta_evening.html` | §03 候选结构总览（原§07）Setup 代码改用 `{{ SETUP_ZH[setup_code] \| default(setup_code) }}` | 渲染后族表格中无英文 setup 代码 |
| 13 | `ta_evening.html` | §03 候选结构总览加"占比"列（`≥优先级4数 / 命中数 × 100%`）和体制强势族标注 | 渲染后有"占比"列；体制相关族有标注 |
| 14 | `ta_evening.html` | 新增 `§04 SmartMoney 板块资金闸门` block，使用 `smartmoney_gate` context 变量渲染 | 渲染后显示5条板块记录，含颜色指示 |
| 15 | `ta_evening.html` | §03 五星级候选重命名为 `§05 高优先级观察池 Tier A` | 渲染后节标题正确 |
| 16 | `ta_evening.html` | §05 候选表加 `名称` 列（`{{ row.name }}`），列位置在代码列右侧 | 渲染后候选表有股票中文名称 |
| 17 | `ta_evening.html` | §05 候选表 Setup 列改为 `{{ SETUP_ZH[row.setup] \| default(row.setup) }}` | 渲染后候选行 Setup 显示中文 |
| 18 | `ta_evening.html` | §05 候选表触发条件 tags 改为 `{{ TRIGGER_ZH.get(tag, tag) }}` in loop | 渲染后触发条件全部显示中文 |
| 19 | `ta_evening.html` | §05 候选表列标题：`分` → `系统评分（ⓘ）`，`★` → `系统优先级` | 渲染后列标题正确 |
| 20 | `ta_evening.html` | §05 候选表实现聚合行逻辑：按 setup 分组，相同 setup 多只时用 `<details><summary>` 包裹个股明细 | 相同 setup 30只时显示1行聚合头 + 折叠展开 |
| 21 | `ta_evening.html` | §05 每个标的加"→ 查看单股深度分析"承接提示（链接存在时显示）| 渲染后标的行有深度分析承接提示 |
| 22 | `ta_evening.html` | §05 价格列：加 `入池观察价区` / `技术失效位` / `T+15 观察目标区` 三列 | 渲染后有完整价格区间三列 |
| 23 | `ta_evening.html` | §04 四星级候选重命名为 `§06 次优先级观察池 Tier B` | 渲染后节标题正确 |
| 24 | `ta_evening.html` | §06 行号改为 `{{ loop.index }}`，节标题加 `（共 {{ tier_b_count }} 只）` | 渲染后行号从1开始；标题有总数 |
| 25 | `ta_evening.html` | §06 同§05做名称列、Setup中文、触发条件中文、价格列改动 | 同§05 acceptance criteria |
| 26 | `ta_evening.html` | §04-N 候选解读降级为§06内嵌 `<details class="llm-note">` | 渲染后无独立"§04-N"节 |
| 27 | `ta_evening.html` | §13 风险扫描前移至§06之后，重编号为 `§07 风险扫描`；加 D 族警示数和黑名单触发数显示 | 渲染后§07 紧跟§06 |
| 28 | `ta_evening.html` | §13-N 策略评论降级为§07内嵌 `<details class="llm-note">` | 渲染后无独立"§13-N"节 |
| 29 | `ta_evening.html` | §07 风险扫描中 C2 数量显示为橙色 badge | 渲染后 C2 数量有橙色视觉区分 |
| 30 | `ta_evening.html` | §08 验证回顾、§10 Setup滚动边际、§11 近5日合并为 `§08 历史观察池表现`；加胜率汇总行 | 渲染后三节合并为一节 |
| 31 | `ta_evening.html` | §08 验证状态改为中文（`{{ STATUS_ZH[row.status] }}`）| 渲染后验证状态全中文 |
| 32 | `ta_evening.html` | §08 验证表 T+1收益列加颜色（正值绿/负值红 inline class）| 渲染后正负收益有颜色区别 |
| 33 | `ta_evening.html` | §10/§11 合并为宽表（加近5日列），Setup 代码中文化，胜率列加颜色 class | 渲染后宽表有60日+近5日并列；有颜色编码 |
| 34 | `ta_evening.html` | 新增 `§09 今日主导 Setup / 策略族` block，展示当日命中数前3 Setup 的特征说明 | 渲染后§09 存在；有3个主导 Setup 说明 |
| 35 | `ta_evening.html` | §14 次日假设重构为 `§10 可证伪技术假设`，改为紧凑表格（代码+名称+Setup+方向+价格），节头加可证伪声明和 T+1 日期 | 渲染后无重复句式；有完整表格 |
| 36 | `ta_evening.html` | 新增 `§11 Appendix / 方法说明` 折叠块（`<details>`），包含 setup/trigger/scoring/胜率/盈亏比/体制/数据口径说明 | 渲染后 Appendix 存在；默认折叠；内容完整 |
| 37 | `ta_evening.html` | 免责声明重编号为 `§12`，末尾加"候选标的经算法筛选，不代表应买入或持有"| 渲染后免责声明有补充句 |
| 38 | `styles.css` | 新增 `.ifa-dashboard` / `.ifa-dashboard__grid` / `.ifa-dash-card` / `.ifa-dash-card__label/__value/__sub` | Dashboard 样式正确；4列 grid |
| 39 | `styles.css` | 新增 `.regime-badge`（带置信度分级颜色：蓝/灰/橙）| 体制 badge 有颜色分级 |
| 40 | `styles.css` | 新增 `.tier-badge.tier-a`（蓝色）/ `.tier-badge.tier-b`（灰色）| 优先级 badge 有颜色区分 |
| 41 | `styles.css` | 新增 `.winrate-high` / `.winrate-mid` / `.winrate-low` 颜色 class | 胜率颜色编码正确 |
| 42 | `styles.css` | 新增 `.llm-note` `<details>` 折叠块样式（含 `summary` hover 状态）| LLM 折叠块视觉完整 |
| 43 | `styles.css` | 新增 `.risk-badge`（橙色）/ `.risk-badge-high`（红色）| 风险 badge 视觉正确 |
| 44 | `styles.css` | 新增 `.flow-in`（绿）/ `.flow-flat`（灰）/ `.flow-out`（红）for SmartMoney Gate | 板块资金颜色正确 |
| 45 | `styles.css` | 所有 `table` 包裹在 `overflow-x: auto` div 中 | 宽表在移动端横向可滚动 |
| 46 | `styles.css` | `.kpi { white-space: nowrap; }` | 所有 KPI chip 内部不换行 |
| 47 | `styles.css` | `@media (max-width: 720px)` 规则集：Dashboard 2×2 / 字号缩小 / 宽表横向滚动 / tag 换行 / Header 字号缩小 | 375px 宽度下无内容溢出 |
| 48 | `styles.css` | `@media print` 规则：`details { display: block; }` / `summary { display: none; }` / `-webkit-print-color-adjust: exact` | 打印时所有折叠内容展开可读；颜色保留 |
| 49 | `ta_evening.html` | 验证：报告全文无裸英文 setup 代码（`_CHIP_CONCENTRATED` 等）出现在用户可见的展示文字中 | `grep -i "CHIP_CONCENTRATED\|PULLBACK_RESUME\|BREAKOUT" output.html` 仅在 HTML 注释/data 属性中出现，不在可见文字中 |
| 50 | `ta_evening.html` | 验证：报告全文无"止损"、"推荐价"、"目标价"词汇 | `grep "止损\|推荐价\|目标价" output.html` 返回空 |

---

## 11. Final Developer Notes

这不是产品重构，不是算法重构，也不是策略重构。TA screening 系统的扫描逻辑、评分体系、backtesting、参数配置均已封版（iter19），完全不动。

本次任务是把一份**功能正确但只有开发者能读懂**的内部技术报告，在完全不改变其自包含 HTML / 无外部依赖 / 邮件兼容的渲染方式下，改造成**高净值用户打开后30秒内能理解今日结论、合规表达无歧义、移动端可用**的正式产品报告。

改动的边界只有四个文件：模板 HTML、CSS、文案 label dict、builder 中补充 name 字段和 SmartMoney 板块查询。所有改动完成后，`ta run --date $DATE` 的输出应直接产出符合本规格的报告，无需任何手动后处理。

---

*基准 HTML：`tmp/ifa_TA_evening_20260430_0115.html`（2026-04-30 报告，生成于 2026-05-05 01:15）*  
*本规格所有改动为一次性完成项，无分批、无可选项。*
