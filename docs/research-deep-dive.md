# Research Deep Dive — 公司基本面深度调研

> **状态**：V2.2 财报分析主功能已实现（CLI/manual 内部生产）；HTTP / Telegram / quota / dashboard defer 到 V2.3
> **定位**：iFA 第一个「自下而上」的家族 — 给定一家上市公司，自动产出从速览到尽调级的多档基本面研究报告
> **数据边界**：严格基于 Tushare Pro 既有接口（已逐家逐板块实测验证）

---

## 1. 产品定位

### 1.1 我们到底在做什么

把「一家上市公司未来 1-2 年的基本面值不值得花时间深入」这一判断，从 **3-5 小时手工调研** 压缩到 **3 分钟自动产出 + 30 分钟分析师精读**。

不是 AI 选股、不是量化信号、不是新闻汇总。是**结构化的基本面研究替身**：把分析师本来要做的「读年报、对季报、查公告、刷互动易、看研报、列时间线、识别叙事矛盾」这些劳动密集型动作自动化，把分析师本人解放出来做最后一公里的判断。

### 1.2 三类用户画像

| 用户 | 触发场景 | 用法 | 关键诉求 |
|------|---------|------|---------|
| **基金经理** | 看到一家股票被提名进观察池 | 一句话："深度调研一下智微智能" | 5-10 条 watchpoint，每条带数据基础，可一眼判断要不要立项 |
| **买方分析师** | 接到立项任务做深度报告 | 先跑 deep 报告打底，再人工核实关键数字 | 完整时间线、所有公告摘要、叙事矛盾点高亮 |
| **个人投资者**（Telegram 入口） | 财报季前后查持仓股 | 一键查"最新季报怎么样" | 通俗易懂的中文、3-5 条结论、风险点 |

三类用户共用同一套引擎，差异只在**报告深度档位**和**呈现层措辞强度**。

### 1.3 与既有 iFA 家族的关系

```
Macro · Asset · Tech · Market · SmartMoney   ← 既有：自上而下的市场视角
─────────────────────────────────────────
Research（V2.2 新增）                         ← 自下而上的单公司视角
```

Research 不替代任何既有家族，但**反向输出**：当 SmartMoney 标记某板块异动、某只龙头出现，Research 提供该龙头的基本面尽调；当 Market 评论某只股票，Research 提供事实底稿。未来可作为 SmartMoney 候选股的基本面过滤器。

---

## 2. 报告产品矩阵

V2.2 的财报报告是单股报告，固定为“报表类型 × 深度档位”的四类。`quick` 与 `deep` 控制读取窗口，不是“是否深度调研”这个抽象标签；跨股票 comparison 不属于本模块当前交付范围。

| 类型 | CLI | 读取窗口 | 核心问题 |
|---|---|---|---|
| Quarterly Quick | `--analysis-type quarterly --tier quick` | 只读最新季报 | 本季度收入、利润、现金流是否同向，环比/同比有没有明显拐点 |
| Annual Quick | `--analysis-type annual --tier quick` | 只读最新年报 | 最新年报的盈利质量、现金兑现和资产负债结构是否健康 |
| Quarterly Deep | `--analysis-type quarterly --tier deep` | 最多三年/12 个季度；不足三年则读全部可用 | 每个季度的 YoY + QoQ 是否揭示趋势、季节性或边际恶化 |
| Annual Deep | `--analysis-type annual --tier deep` | 最多三年/3 份年报；不足三年则读全部可用 | 年度 YoY + 较上年变化是否支持长期质量判断 |

### 2.1 Quick Report（财报速览）— Tier 1

- **定位**：财报发布后 24 小时内的快速解读
- **输入**：公司 + 单期报告（最新季报或最新年报，由 `--analysis-type` 决定）
- **产出**：单页 HTML，~800 字
- **LLM 调用**：3-5 次
- **目标延迟**：≤ 60 秒

| § | 标题 | 数据源 | 模型介入 |
|---|------|-------|---------|
| 0 | 一句话结论 | 规则层 + LLM | gpt-5.4，temp=0.2 |
| 1 | 核心数字（YoY/QoQ 表） | 纯规则 | 否 |
| 2 | 报告期窗口公告/预告 | anns_d, forecast | gpt-5.4 摘要 |
| 3 | 三大异动（自动检出最大变化） | 规则 + LLM 措辞 | gpt-5.4，temp=0.3 |
| 4 | 风险标记 | 规则层 | 否 |

### 2.2 Standard Report（年度/多季对比）— Tier 2

- **定位**：季报/年报发布后的深入解读，含历史对比
- **输入**：公司 + 时间窗口（默认 2023-01-01 至今，新股自上市起）
- **产出**：多页 HTML，~3000 字
- **LLM 调用**：6-9 次
- **目标延迟**：≤ 3 分钟

### 2.3 Deep Research（深度调研）— Tier 3

- **定位**：立项级的全面尽调底稿
- **输入**：公司 + 最多三年财报历史（季度最多 12 期；年报最多 3 期）
- **产出**：完整 HTML/PDF 报告，5000-8000 字
- **LLM 调用**：12-18 次
- **目标延迟**：≤ 6 分钟

**完整章节结构（18 节）**：

```
§01  公司画像              （业务模式 + 行业坐标 + 实控人结构）
§02  财务数据全景表         （多期 YoY/QoQ + 环比/同比双视角）
§03  盈利能力深度分析       （DuPont 三因素 + 行业相对位置）
§04  现金流与盈利质量       （CFO/NI · FCF · 应收/存货营运拐点）
§05  资产负债结构演化       （负债结构 · 流动性 · 商誉 · 质押）
§06  营运效率拐点           （周转天数 · 现金循环周期 CCC）
§07  资本配置图谱           （分红 · Capex · 融资 · 减持/增持）
§08  披露时间线             （2023 至今所有重大公告/预告/快报排序）
§09  管理层叙事 vs 数据 ★   （承诺 vs 兑现 · 口径变化 · 矛盾点）
§10  券商共识演化           （研报数量趋势 · 评级分布 · 目标价区间）
§11  投资者关切焦点         （互动易高频提问主题聚类）
§12  治理风险信号           （减持 · 质押 · 审计 · 高管异动）
§13  行业相对位置           （SW L2 同板块财务对标）
§14  五维质量评分           （盈利/增长/质量/资产/治理 5×10 分）
§15  关键观察点（5-10 条）  （每条含数据基础）
§16  下次披露预期           （disclosure_date 推算 + 关注事件）
§17  附录（数据完备性矩阵） （本报告依赖的每个数据源覆盖度）
§18  完整免责声明           （10 段中英对照，使用 disclaimer.py 全量版）
```

§17 数据完备性矩阵明确告诉读者哪些维度数据缺失、哪些结论受样本限制——这是分析师建立信任的关键。

§18 **强制使用 `ifa/core/report/disclaimer.py` 中的 `DISCLAIMER_PARAGRAPHS_ZH` + `DISCLAIMER_PARAGRAPHS_EN` 完整 10 段中英对照版本**，与 Market / Macro / Asset / Tech / SmartMoney / TA 报告保持一致。不得使用简短版（`FOOTER_SHORT_*`）。

---

## 3. 分析方法论

### 3.1 因子库设计原则

> 拒绝"啥都算"。每个因子必须能回答一个**具体的投资问题**。

每个因子标准结构：

```python
@dataclass
class Factor:
    name: str
    formula: str               # 公式或字段映射
    unit: str                  # %, x, 天, 元
    source_apis: list[str]     # 依赖哪些 Tushare 接口
    healthy_range: tuple       # 正常区间
    warning_threshold: float   # 黄灯
    critical_threshold: float  # 红灯
    industry_sensitive: bool   # 是否需要行业归一化
    interpretation: str        # 中文解读模板
```

### 3.2 五大因子族

#### 族 A · 盈利能力（Profitability）

| 因子 | 公式 | 健康区间 | 红灯 |
|------|------|---------|------|
| 毛利率 GPM | `1 - oper_cost/revenue` | 行业 P50 以上 | 同比下降 >5pp |
| 净利率 NPM | `n_income / revenue` | 行业 P50 以上 | <0 |
| 扣非净利率 | `profit_dedt / revenue` | 接近净利率 | 与净利率差距 >30% |
| ROE | `n_income / avg(equity)` | >10% | <5% |
| ROIC | `EBIT(1-T) / (debt+equity)` | >WACC | <5% |
| **DuPont 三因素** | NPM × 资产周转 × 杠杆 | — | 任一恶化即追问 |

> **CFO 视角**：扣非与净利率的差距是识别"靠政府补贴/资产处置撑业绩"的第一道筛子。Tushare 同时提供 `n_income` 与 `profit_dedt`，这一对比强制做。

#### 族 B · 增长（Growth）

| 因子 | 公式 | 健康区间 | 红灯 |
|------|------|---------|------|
| 营收 YoY | `revenue / revenue.shift(4) - 1` | >0 | 连续 2 季 <0 |
| 净利 YoY | 同上 | >营收 YoY | <营收 YoY × 0.5（杠杆性恶化） |
| 营收 3 年 CAGR | 滚动 | >行业 P50 | <0 |
| **预告达成率** | 实际净利 / forecast 中值 | 90-110% | <80% 或 >130% |

> **基金经理视角**：预告达成率偏离度是管理层"画饼能力"的硬证据。Tushare 的 `forecast` 表保留了所有历史预告，可以做"画饼准确度"评分。

#### 族 C · 现金质量（Cash Quality）

| 因子 | 公式 | 健康区间 | 红灯 |
|------|------|---------|------|
| CFO/NI | `经营性现金流 / 净利润` | 0.8-1.2 | <0.5 持续 4 季 |
| 自由现金流 FCF | `CFO - Capex` | >0 | 负值且扩大 |
| 应收账款增速 / 营收增速 | 比值 | <1.2 | >1.5 |
| 存货增速 / 成本增速 | 比值 | <1.2 | >1.5 |
| 现金循环周期 CCC | DSO + DIO - DPO | 行业归一化 | 同比恶化 >20% |

> **CFO 视角**：CFO/NI 持续 <0.5 是会计利润与现金利润背离的核心警报。结合应收/存货扩张速度，能识别"虚胖型增长"。

#### 族 D · 资产负债结构（Balance Sheet）

| 因子 | 公式 | 健康区间 | 红灯 |
|------|------|---------|------|
| 资产负债率 | `total_liab / total_assets` | 行业相关 | 同比上升 >10pp |
| 流动比率 | `流动资产 / 流动负债` | >1.5 | <1.0 |
| 速动比率 | `(流动资产-存货) / 流动负债` | >1.0 | <0.7 |
| 有息负债占比 | `interest-bearing debt / total_liab` | — | 同比急升 |
| 商誉/净资产 | `goodwill / equity` | <30% | >50% |
| **大股东质押率** | from `pledge_stat` | <30% | >70% |

#### 族 E · 治理与披露（Governance）

| 因子 | 数据源 | 红灯阈值 |
|------|-------|---------|
| 减持频次（12 月） | `stk_holdertrade` | >3 次或 >总股本 5% |
| 审计意见非标 | `fina_audit` | 出现保留/否定/无法表示 |
| 审计机构变更 | `fina_audit` 跨年比对 | 1 年内更换且无解释 |
| 高管离职率 | `stk_managers` | 12 月 >30% |
| 互动易"未回复"率 | 自计算 | >20% |
| 业绩公告延期 | `disclosure_date.actual_date - pre_date` | 延期 >7 天 |

> **基金经理视角**：以上 6 条任何一条触发，仓位上限自动减半。这是"用规则保护自己不受叙事影响"的护栏。

### 3.3 五维综合评分

把上述 50+ 因子聚合成 5 个 0-10 分的可视化雷达：

```
盈利能力 Profitability  ← 族A加权
成长性 Growth           ← 族B加权
现金质量 Quality        ← 族C加权
资产健康 Balance        ← 族D加权
治理水平 Governance     ← 族E加权
```

每维分数公式可解释（不是黑箱），权重在 `params/research_v2.2.yaml` 中可配置。**所有打分由规则层完成，LLM 不参与**。

### 3.4 行业相对化

> "公司毛利率 18%" 是没有意义的，"在 SW L2 计算机设备板块 78 家公司中排第 22"才有意义。

利用 `smartmoney.sw_member_monthly`（已有完整 PIT 数据），对每个因子做**同板块同期分位数排名**。报告里展示绝对值 + 板块分位（"22/78, P28"）。

### 3.5 叙事一致性检测（最有价值的 LLM 模块）

输入：

1. **管理层口径时间线**：把所有业绩预告的 `change_reason` 字段、年报董事长致辞、互动易回复按时间排列
2. **数据真相时间线**：财务因子的实际趋势

LLM 任务（gpt-5.4, temp=0.2, JSON 输出）：

```json
{
  "consistency_score": 0-10,
  "narratives": [
    {
      "period": "2024Q4",
      "claim": "管理层预告称利润下滑主因为股权激励费用与新厂房折旧",
      "data_check": "扣非净利同比 -76%，但费用率仅升 3pp，毛利率从 17% 降至 11%",
      "verdict": "解释只能覆盖约 30% 的利润降幅，剩余主要来自毛利率塌陷",
      "severity": "high"
    }
  ],
  "evolution_summary": "2024 提到激励费用，2025 转向智算业务订单，叙事重心切换..."
}
```

LLM 不是来"猜"的，是来**做对照标注**的——所有 claim 都来自原文，所有 data_check 都来自规则层算出的具体数字。

---

## 4. 数据层全景（Tushare 能力地图）

基于已有实测，划定 V2.2 的数据边界。

### 4.1 已验证可用的 23 个接口

| 用途 | 接口 | 字段重点 | TTL 缓存策略 |
|------|------|---------|-------------|
| 公司识别 | `stock_basic` | name, exchange, market | 7d |
| 公司画像 | `stock_company` | introduction, main_business, employees | 30d |
| 行业分类（PIT） | `smartmoney.sw_member_monthly` | l2_code, l2_name | 由 SmartMoney ETL 维护 |
| 利润表 | `income` | total_revenue, n_income, profit_dedt | 7d |
| 资产负债表 | `balancesheet` | total_assets, total_liab, goodwill, money_cap | 7d |
| 现金流量表 | `cashflow` | n_cashflow_act, c_pay_acq_const_fiolta | 7d |
| 财务指标 | `fina_indicator` | roe, eps, gross_margin, debt_to_assets | 7d |
| 业绩预告 | `forecast` | summary, change_reason, p_change_min/max | 1d（财报季） |
| 业绩快报 | `express` | revenue, n_income, perf_summary | 1d（财报季） |
| 审计意见 | `fina_audit` | audit_result, audit_agency, audit_fees | 30d |
| 公告列表 | `anns_d` | title, url（cninfo 网页 URL） | 1d |
| 研报列表 | `research_report` | title, url（dfcfw PDF 直链） | 1d |
| 互动 SH | `irm_qa_sh` | q, a | 1d |
| 互动 SZ | `irm_qa_sz` | q, a | 1d |
| 前十股东 | `top10_holders` | hold_amount, hold_ratio, hold_change | 30d |
| 前十流通股东 | `top10_floatholders` | 同上 | 30d |
| 股东增减持 | `stk_holdertrade` | in_de, change_vol, change_ratio, avg_price | 7d |
| 股权质押 | `pledge_stat` | pledge_count, pledge_ratio | 30d |
| 股本解禁 | `share_float` | float_date, float_share | 30d |
| 管理层 | `stk_managers` | name, lev, title, begin_date, end_date | 30d |
| 管理层薪酬 | `stk_rewards` | reward, hold_vol | 90d |
| 大宗交易 | `block_trade` | price, vol, buyer, seller | 1d |
| 披露日历 | `disclosure_date` | pre_date, actual_date | 1d |

### 4.2 五大板块数据覆盖差异（实测）

| 维度 | SSE 主板 | SSE 科创板 | SZSE 主板 | SZSE 创业板 | BSE 北交所 |
|------|:---:|:---:|:---:|:---:|:---:|
| 公告完整性 | ✅ | ✅ | ✅ | ✅ | ⚠️ 只有近期 |
| 互动问答历史 | 2023.6+ | 2023.6+ | 2010+ | 2010+ | ❌ 不支持 |
| 研报覆盖 | 取决于市值 | 较好 | 取决于热度 | 较好 | 一般 |
| 财务历史深度 | 上市起 | 上市起 | 上市起 | 上市起 | 含挂牌期 |
| 管理层数据 | ✅ | ✅ | ✅ | ✅ | ⚠️ 仅近 1-2 年 |

**北交所降级提示**：报告内显式标注"该公司为北交所，公告与管理层数据覆盖有限，结论仅基于近期可用数据"。

### 4.3 数据黑洞（V2.2 不覆盖，明确告知用户）

| 缺失维度 | 替代方案 |
|---------|---------|
| 营收分产品/分地区拆分 | 仅在年报 PDF 中，需 OCR/LLM 提取（V2.3 候选） |
| 客户/供应商集中度 | 同上 |
| 关联交易明细 | 同上 |
| 估值数据（PE/PB） | `daily_basic` 有部分，V2.2.1 加入 |
| 股价预测/目标价 | `research_report` 不返回结构化目标价（仅在 PDF 中） |

### 4.4 PDF 处理矩阵

| 来源 | URL 模式 | 可达性 | 文字提取 |
|------|---------|-------|---------|
| cninfo 公告 | `static.cninfo.com.cn/finalpage/YYYY-MM-DD/<id>.PDF` | ✅ 实测 200 | pdfplumber，扫描版降级 |
| dfcfw 研报 | `pdf.dfcfw.com/pdf/H3_AP*_1.pdf` | ✅ 实测 200 | 同上 |

**V2.2 范围**：仅做年报/半年报/季报 PDF 的可选深读（用户加 `--with-pdf` 触发），不强制。报告主体仍由结构化数据驱动。

### 4.5 复用 Macro / Asset 已验证的数据模式

| 模式 | 来源 | Research 复用方式 |
|------|------|---------------------|
| `TimeSeries` dataclass | `macro/data.py` | 所有财务指标多期序列（ROE/毛利率/CFO/NI/营收等）的统一容器，`§02-§07` 财务面板直接消费 |
| `Snapshot + History + data_status` | `asset/data.CommoditySnapshot` | 公司当日基本面快照 + 近 N 期历史 + 数据状态打包，避免下游 section 重复查 DB |
| LLM 抽取事件记忆表（带 stable hash event_id） | `jobs/macro_policy_memory/repo.py` | 新建 `research.company_event_memory`（详见 §5.4），常驻表，多次报告复用，不每次重抽 |

### 4.6 与 Stock Intel 家族的协作

Research 是 Stock Intel 的**财务面数据源**。两者关系：

| 协作点 | 数据流向 | 说明 |
|-------|---------|------|
| 五维评分 | Research → Stock Intel | Stock Intel `§03 财务质量` 直接读 Research 最新 deep 报告的五维评分（盈利/增长/质量/资产/治理） |
| 公司画像 | Research → Stock Intel | Stock Intel `§02 公司画像` 直接读 Research `§01` 内容 |
| 分期财报拆解 | Research → Stock Intel / TA | 从 `research.period_factor_decomposition` 读取最多 12 季度 / 3 年的盈利、增长、现金质量、资产结构、治理序列，用作基本面 lineup |
| 研报摘要 | Research → Stock Intel | 从 `research.pdf_extract_cache` 读取最近研报 PDF 解析结果与 key points，避免重复下载解析 |
| 公司事件记忆 | Research ←→ Stock Intel | 两家族共用 `research.company_event_memory` 表 |
| 缓存级联失效 | 双向 | Research deep 重做时通知 Stock Intel 失效相关缓存；新公告进入 `company_event_memory` 时双向失效 |
| 触发 Research | Stock Intel → Research | Stock Intel deep 启动时若发现该股 30 天内无 Research deep，可可选触发一次 Research deep（`--with-research` 参数） |

**实现要点**：
- Stock Intel / TA 读 Research 先调用 `ifa.families.research.memory.load_fundamental_lineup(engine, ts_code)`，不要解析 HTML
- 报告 section 可以作为展示快照复用，但财报数字的权威源是 `period_factor_decomposition`
- 不复制数据，每次按需读；DuckDB 只做 scratch / ad hoc OLAP，不作为 Research 财务面 canonical memory
- Research 永远是单股财务的"权威源"，Stock Intel 不重做财务计算

---

## 5. 系统架构

### 5.1 总体分层

```
┌──────────────────────────────────────────────────────┐
│  入口层 Entry Layer                                   │
│  ─ CLI: ifa research <args>                          │
│  ─ HTTP API: POST /research/run （Telegram bot 复用） │
└────────────────┬─────────────────────────────────────┘
                 │
┌────────────────▼─────────────────────────────────────┐
│  编排层 Orchestrator (research/report.py)            │
│  ─ 解析请求 → 选档位 → 检缓存 → 拉数据 → 跑分析       │
│  ─ 调 LLM → 渲染 → 写库 → 返回路径                    │
└─────┬──────────┬──────────┬──────────┬───────────────┘
      │          │          │          │
┌─────▼──┐ ┌────▼────┐ ┌───▼────┐ ┌──▼──────┐
│ 数据层  │ │ 分析层   │ │ LLM 层  │ │ 渲染层   │
│ Fetcher│ │ Analyzer│ │ Sections│ │ Renderer│
│ + Cache│ │ + Factors│ │ + Prompt│ │ HTML/PDF│
└────────┘ └─────────┘ └─────────┘ └─────────┘
      │          │          │          │
┌─────▼──────────▼──────────▼──────────▼───────┐
│  持久层 Persistence                            │
│  PostgreSQL `research` schema · 文件系统(out/)│
└──────────────────────────────────────────────┘
```

### 5.2 单次报告生命周期

```
T+0ms     CLI/API 触发
T+50ms    解析参数 → 公司识别 → 查 research.report_runs 是否有最近成功记录
T+100ms   缓存命中？返回旧报告。否则继续
T+200ms   并发拉所有结构化数据（asyncio.gather）
T+5000ms  全量数据到位（财报数据慢的话最多 8s）
T+5100ms  跑确定性分析层（pandas，纯 CPU，<2s）
T+7000ms  并发调 LLM（多个 section 并行，每个独立超时）
T+25000ms 全部 LLM 返回，组装 sections
T+25500ms 渲染 HTML（Jinja2）
T+27000ms （可选）Chrome 转 PDF
T+27500ms 写库 + 返回路径
```

**关键设计**：
- LLM 调用全部并行，不串行（每个 section 独立 prompt）
- 每个 LLM 调用独立超时 + 失败降级（写"本节因模型超时降级"）
- 任何一节失败不阻塞整体（partial status）

### 5.3 模块组织

```
ifa/families/research/
├── __init__.py
├── resolver.py                公司名 → ts_code
├── fetcher/
│   ├── client.py              基于 TuShareClient 的扩展 + 重试
│   ├── cache.py               api_cache + computed_cache 读写
│   └── pdf.py                 PDF 下载 + pdfplumber 提取
├── analyzer/
│   ├── factors.py             50+ 因子定义（@dataclass）
│   ├── profitability.py       族A
│   ├── growth.py              族B
│   ├── cash_quality.py        族C
│   ├── balance.py             族D
│   ├── governance.py          族E
│   ├── trends.py              多期趋势分类器
│   ├── peer.py                行业对标（依赖 sw_member_monthly）
│   ├── timeline.py            披露时间线生成器
│   └── scoring.py             5 维综合评分
├── sections/
│   ├── overview.py            §01
│   ├── financial_panel.py     §02-§07
│   ├── timeline.py            §08
│   ├── narrative.py           §09 ★ 叙事一致性
│   ├── analyst.py             §10
│   ├── investor_concerns.py   §11
│   ├── governance.py          §12
│   ├── peer.py                §13
│   ├── scoring.py             §14
│   ├── watchpoints.py         §15
│   ├── next_disclosure.py     §16
│   └── data_completeness.py   §17
├── prompts/
│   ├── overview_v1.py
│   ├── narrative_v1.py        ★ 这个版本会迭代很多次
│   └── ...                     每个 section 一个文件，独立版本号
├── report.py                  编排器
└── render.py                  Jinja2 模板装填
```

### 5.4 数据库 Schema

```sql
CREATE SCHEMA research;

-- ========== 公司维度 ==========
CREATE TABLE research.company_identity (
    ts_code         VARCHAR(12) PRIMARY KEY,
    name            VARCHAR(64) NOT NULL,
    exchange        VARCHAR(8),
    market          VARCHAR(16),
    list_date       DATE,
    list_status     CHAR(1),
    sw_l1_code      VARCHAR(12),
    sw_l1_name      VARCHAR(32),
    sw_l2_code      VARCHAR(12),
    sw_l2_name      VARCHAR(32),
    last_refreshed  TIMESTAMPTZ DEFAULT now(),
    UNIQUE (name)
);

-- ========== 缓存 ==========
CREATE TABLE research.api_cache (
    ts_code         VARCHAR(12) NOT NULL,
    api_name        VARCHAR(64) NOT NULL,
    params_hash     VARCHAR(64) NOT NULL,
    response_json   JSONB NOT NULL,
    fetched_at      TIMESTAMPTZ DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (ts_code, api_name, params_hash)
);
CREATE INDEX ON research.api_cache (expires_at);

CREATE TABLE research.computed_cache (
    ts_code         VARCHAR(12) NOT NULL,
    compute_key     VARCHAR(128) NOT NULL,
    inputs_hash     VARCHAR(64) NOT NULL,
    result_json     JSONB NOT NULL,
    computed_at     TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ts_code, compute_key)
);

-- ========== 报告运行 ==========
CREATE TABLE research.report_runs (
    run_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ts_code         VARCHAR(12) NOT NULL,
    company_name    VARCHAR(64),
    report_type     TEXT CHECK (report_type IN ('quick', 'standard', 'deep')),
    scope_json      JSONB,
    status          TEXT CHECK (status IN ('running', 'succeeded', 'partial', 'failed', 'cached')),
    triggered_by    TEXT,
    user_id         UUID,
    template_version VARCHAR(32),
    prompt_version  VARCHAR(32),
    run_mode        TEXT CHECK (run_mode IN ('test', 'manual', 'production')),
    started_at      TIMESTAMPTZ DEFAULT now(),
    completed_at    TIMESTAMPTZ,
    duration_seconds NUMERIC,
    output_html_path TEXT,
    output_pdf_path  TEXT,
    output_json_path TEXT,
    llm_calls       INT DEFAULT 0,
    llm_tokens      INT DEFAULT 0,
    fallback_used   BOOLEAN DEFAULT false,
    error_summary   TEXT
);
CREATE INDEX ON research.report_runs (ts_code, started_at DESC);

CREATE TABLE research.report_sections (
    section_id      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID REFERENCES research.report_runs(run_id),
    section_key     TEXT NOT NULL,
    section_order   INT,
    content_json    JSONB,
    status          TEXT CHECK (status IN ('ok', 'degraded', 'skipped')),
    skip_reason     TEXT,
    model_used      TEXT,
    prompt_name     TEXT,
    prompt_version  TEXT,
    latency_seconds NUMERIC,
    UNIQUE (run_id, section_key)
);

CREATE TABLE research.period_factor_decomposition (
    ts_code        TEXT NOT NULL,
    factor_family  TEXT NOT NULL,
    factor_name    TEXT NOT NULL,
    period         TEXT NOT NULL,
    period_type    TEXT CHECK (period_type IN ('annual', 'quarterly')),
    value          NUMERIC,
    unit           TEXT,
    source         TEXT,
    source_hash    TEXT,
    payload_json   JSONB,
    computed_at    TIMESTAMPTZ DEFAULT now(),
    PRIMARY KEY (ts_code, factor_family, factor_name, period)
);
CREATE INDEX ON research.period_factor_decomposition (ts_code, period_type, period DESC);

CREATE TABLE research.pdf_extract_cache (
    url_hash      TEXT PRIMARY KEY,
    ts_code       TEXT,
    source_url    TEXT NOT NULL,
    title         TEXT,
    source_date   DATE,
    page_count    INT,
    extractable   BOOLEAN,
    text_hash     TEXT,
    extract_json  JSONB,
    extracted_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON research.pdf_extract_cache (ts_code, source_date DESC);

CREATE TABLE research.report_judgments (
    judgment_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id          UUID REFERENCES research.report_runs(run_id),
    judgment_type   TEXT,                   -- 'inconsistency' | 'risk_signal' | 'watchpoint'
    severity        TEXT CHECK (severity IN ('high', 'medium', 'low', 'info')),
    text            TEXT,
    data_basis      JSONB,
    created_at      TIMESTAMPTZ DEFAULT now()
);

-- ========== 公司事件记忆（借鉴 macro_policy_event_memory）==========
CREATE TABLE research.company_event_memory (
    event_id        VARCHAR(32) PRIMARY KEY,    -- hash(source_url + publish_time + title)
    ts_code         VARCHAR(12) NOT NULL,
    capture_date    DATE NOT NULL,
    event_type      TEXT,                       -- 'major_contract' | 'related_party' | 'asset_restructuring'
                                                -- | 'shareholder_change' | 'management_change' | 'audit_alert'
                                                -- | 'forecast_revision' | 'regulatory_inquiry' | 'other'
    title           TEXT,
    summary         TEXT,                       -- LLM 提取的结构化摘要
    polarity        TEXT CHECK (polarity IN ('positive', 'neutral', 'negative')),
    importance      TEXT CHECK (importance IN ('high', 'medium', 'low')),
    source_type     TEXT,                       -- 'announcement' | 'irm_qa' | 'research_report' | 'news'
    source_url      TEXT,
    publish_time    TIMESTAMPTZ,
    extraction_model TEXT,
    extraction_prompt_version TEXT,
    valid_until     DATE,                       -- 事件影响窗口
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON research.company_event_memory (ts_code, capture_date DESC);
CREATE INDEX ON research.company_event_memory (event_type, importance);

-- ========== 用户与配额 ==========
CREATE TABLE research.users (
    user_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    external_id     TEXT NOT NULL,
    external_type   TEXT DEFAULT 'telegram',
    display_name    TEXT,
    tier            TEXT DEFAULT 'free',
    daily_quota     INT DEFAULT 5,
    created_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE (external_id, external_type)
);

CREATE TABLE research.usage_log (
    log_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID REFERENCES research.users(user_id),
    run_id          UUID REFERENCES research.report_runs(run_id),
    report_type     TEXT,
    ts_code         TEXT,
    cache_hit       BOOLEAN,
    created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON research.usage_log (user_id, created_at DESC);
```

### 5.5 缓存与失效策略

**三级缓存**：

| 层级 | 表 | 失效时机 |
|------|-----|---------|
| L1 API 响应 | `api_cache` | TTL 到期 OR 检测到新公告/新财报 |
| L2 计算结果 | `computed_cache` | 任一上游 `api_cache.fetched_at` 比 `computed_at` 新 |
| L3 报告产出 | `report_runs` | 任一 L1 项更新（仅 deep 报告复用谨慎） |

**财报季强制刷新机制**：每天 18:00 BJT 守护任务扫描 `disclosure_date.actual_date` 当日命中的所有公司，相关财务类 `api_cache` 立即失效。

**报告资产复用规则**：
- 同一 `ts_code + analysis_type + tier + latest_period` 已有 `succeeded` 报告时，入口层直接返回 `research.report_runs.output_html_path` / `scope_json.md_path`
- `manual` / `production` 只是输出目录与运行来源不同，不作为强制重算边界；命中历史资产时直接列出原路径
- 用户显式 `--fresh` 或检测到新财报披露时才重算
- Stock Intel 需要财报底稿时先调用 `find_reusable_report(...)`；未命中则同步触发对应 quick/deep 生成，再读 `load_fundamental_lineup(...)`

**用户控制**：
- `--no-cache`：跳过 L1/L2/L3
- `--refresh-data`：失效 L1，保留 L2/L3 让其重算
- `--rerun-report`：保留 L1/L2，仅重跑 LLM 与渲染

---

## 6. LLM 工程规范

### 6.1 模型分配总表

| Section | 用途 | 模型 | temp | max_tokens | 平均输入 | 平均输出 |
|---------|------|------|------|-----------|---------|---------|
| §01 公司画像 | 业务模式叙述 | gpt-5.4 | 0.4 | 600 | 800 | 400 |
| §02-07 财务叙述 | 趋势点评 | gpt-5.4 | 0.2 | 800 | 1500 | 600 |
| §08 时间线压缩 | 把 200 条公告压成 20 条要点 | gpt-5.4 | 0.3 | 1500 | 6000 | 1200 |
| §09 叙事一致性 ★ | 矛盾检测 | gpt-5.4 | 0.2 | 1800 | 4000 | 1500 |
| §10 研报共识 | 评级分布解读 | gpt-5.4 | 0.3 | 500 | 800 | 300 |
| §11 投资者关切 | 互动易主题聚类 | gpt-5.4 | 0.3 | 1200 | 3000 | 800 |
| §12 治理风险 | 信号汇总叙述 | gpt-5.4 | 0.2 | 600 | 1000 | 400 |
| §15 关注点 | 综合提炼 5-10 条 | gpt-5.4 | 0.3 | 1500 | 3000 | 1200 |
| 兜底 | 任意失败重试 | gpt-5.5（fallback） | 同上 | 同上 | — | — |

**总成本预估（Deep 报告）**：输入 ~25K token，输出 ~7K token。

### 6.2 Prompt 工程规范

所有 prompt 文件必须：

1. **版本号显式**：文件名 `narrative_v1_2.py`，版本号入库
2. **System prompt 用三段式**：角色定义 / 任务定义 / 硬约束（禁用词、字数、JSON schema）
3. **Few-shot 至少 2 个**：分别示范"信号清晰"和"信号矛盾"两种典型情况
4. **JSON Schema 强制**：用 `response_format={"type": "json_object"}`
5. **失败重试 prompt**：第二次比第一次更严格、更短，专攻 JSON 合法性

### 6.3 LLM 调用守则

- 严禁把原始数据表喂给 LLM，**只喂规则层算好的结构化结论**
- 严禁让 LLM 重算数字（数字一定来自规则层并在 prompt 里以"已知事实"出现）
- 严禁让 LLM 自由发挥（temp 永远 ≤ 0.4，多数 0.2）
- 输出必须 JSON，便于程序校验、模板渲染、入库
- 每次 LLM 输出必须存 `report_model_outputs` 以便事后回溯

### 6.4 评估与回归

建立**黄金集**：
- 选 30 家有代表性的公司（覆盖 5 个板块、不同市值、不同景气阶段）
- 每家由人工分析师标注：5 维评分应该是多少、3 个最重要的 watchpoint、是否有叙事矛盾
- 每次 prompt 改动都跑一遍黄金集，对比新旧版差异
- 关键指标：watchpoint 召回率、矛盾识别精确率

---

## 7. 用户使用

### 7.1 CLI 接口

```bash
# 季报速览（只读最新季报）
uv run ifa research report 智微智能 --analysis-type quarterly --tier quick

# 年报速览（只读最新年报）
uv run ifa research report 智微智能 --analysis-type annual --tier quick

# 季报 deep（最多三年季度，YoY + QoQ）
uv run ifa research report 智微智能 --analysis-type quarterly --tier deep --llm

# 年报 deep（最多三年年报，YoY + 较上年）
uv run ifa research report 智微智能 --analysis-type annual --tier deep --llm --pdf

# 用代码或简称都行
uv run ifa research report 中电电机 --analysis-type annual --tier deep
uv run ifa research report 603988 --analysis-type annual --tier deep

# 指定输出
uv run ifa research report 测绘股份 --analysis-type quarterly --tier quick --output ~/Desktop/

# 控制缓存
uv run ifa research report 智微智能 --analysis-type annual --tier deep --no-persist

# 健康检查（只跑数据完备性矩阵，不出报告）
uv run ifa research scan-status --hours 24 --failures
```

### 7.2 HTTP API（V2.3 defer，供 Telegram bot 复用）

V2.2 Research 以 CLI/manual 内部生产为完成边界。HTTP API、Telegram 入口、用户 quota 与 dashboard 统一进入 V2.3 入口层。

```http
POST /api/research/run
{
  "company": "智微智能",
  "report_type": "deep",
  "user_external_id": "telegram:123456789",
  "options": {"with_pdf_extraction": false, "force_refresh": false}
}

→ {
  "run_id": "uuid...",
  "status": "running",
  "estimated_seconds": 240,
  "progress_url": "/api/research/runs/<run_id>/progress"
}
```

### 7.3 报告呈现

**HTML 主报告**：
- 顶部：5 维雷达图 + 一句话总结
- 中部：分章节 collapse/expand
- 关键数字色彩编码（红=同比恶化、绿=同比改善、黄=持平）
- 每个判断都可点开"为什么这么说"看支撑数据
- 底部：完整数据完备性矩阵 + 免责声明

**PDF 版本**：沿用 V2.1 已有的 Chrome headless 转换。

---

## 8. 关键工程细节

### 8.1 公司识别（resolver.py）

模糊匹配优先级：代码精确 → 名称精确 → 名称去常见后缀模糊匹配（threshold 0.85） → 多结果报歧义。

### 8.2 PDF 处理

cninfo 网页 URL → 静态 PDF 直链转换：
```
http://www.cninfo.com.cn/new/disclosure/detail?...announcementId=<id>...announcementTime=2026-04-24
→ https://static.cninfo.com.cn/finalpage/2026-04-24/<id>.PDF
```

`pdfplumber` 提取，去页眉页脚，合并断行。扫描版（提取字数 <200）直接降级 `extractable=False`，报告内显式标注。

### 8.3 异步并发

Tushare Pro rate limit 多数 200 次/分钟。23 个接口分组并发，组间串行 + tenacity 重试。LLM 调用全部并发（每个 section 独立超时）。

---

## 9. 可观测性

| 类别 | 指标 | 阈值 |
|------|------|------|
| 性能 | Deep 报告 P50 / P99 延迟 | <4 分钟 / <8 分钟 |
| 性能 | LLM 单次调用 P99 | <30 秒 |
| 成功率 | report_runs.status='succeeded' 占比 | >95% |
| 成功率 | section.status='ok' 占比 | >90% |
| 数据 | api_cache 命中率 | >70% |
| 成本 | 单份 Deep 平均 token 数 | <35K total |
| 质量 | LLM 输出 JSON 解析失败率 | <2% |
| 质量 | 兜底模型使用率 | <5% |

每次 run 写一条 JSONL 日志，含 ts_code、duration、token 用量、各 section 状态。

---

## 10. 风险与限制

| 风险 | 缓解 |
|------|------|
| Tushare 接口偶发 502/超时 | tenacity 三次重试，仍失败则该项标缺 |
| Tushare 数据修订（财务重述） | 每次重抓时记录字段变化，触发 alert |
| 北交所早期数据缺失 | 报告显式提示，建议手工补 |
| LLM 编造数字 | 数字全部由规则层提供，prompt 里以"已知事实"出现 |
| 叙事一致性误判 | 每条矛盾必须带原文引用与数据引用，可人工核对 |
| 主模型故障 | 自动 fallback 到 gpt-5.5；两边都失败则该 section 降级 |
| 合规 | §18 强制使用 `disclaimer.py` 完整 10 段中英对照版；禁用"投资建议"用语 |

---

## 11. 演进路线

### V2.2.0（首版交付）
财报分析核心链路完成：四类单股报告、结构化财报因子落库、研报 PDF 摘要缓存、报告资产复用、Stock Intel / TA 基本面 lineup 接口、HTML/MD 输出与桌面/移动渲染验证。人工黄金集评估不再阻断 V2.2；保留为后续评分/LLM 调优时的评估工具。

### V2.2.1（数据完备性增强）
- 年报/招股书 PDF 文字提取深读 → 补"营收分产品/分地区"维度
- 互动易历史增量回填（深市从 2010 年开始拉历史）
- 接入 `daily_basic` 添加估值维度（PE/PB/PS）

### V2.2.2（横向对比）
- 同行业对标报告（一次出 N 家公司的对比卡）
- 行业景气度叠加（接入 SmartMoney 的板块状态）

### V2.3（入口层与多模态）
- HTTP API / Telegram bot / 用户 quota / dashboard
- 扫描版 PDF OCR
- 业绩说明会纪要单独提取
- 港股 Deep Research（Tushare 港股财务数据已可用）

---

## 附录 · 词汇表

| 术语 | 定义 |
|------|------|
| PIT | Point-in-Time，时间正确，不引入未来信息 |
| YoY / QoQ | 同比 / 环比 |
| CFO | Cash Flow from Operations，经营性现金流 |
| FCF | Free Cash Flow = CFO - Capex |
| DSO / DIO / DPO | 应收账款周转天数 / 存货周转天数 / 应付账款周转天数 |
| CCC | Cash Conversion Cycle = DSO + DIO - DPO |
| DuPont | 杜邦分析，ROE 三因素分解 |
| SW L2 | 申万二级行业分类 |
| Watchpoint | 投资者应关注的风险点或机会点 |
