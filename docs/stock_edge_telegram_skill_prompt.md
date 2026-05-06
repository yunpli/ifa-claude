# Stock Edge / Research Telegram Skill Implementation Prompt

> **目标接收方：OpenClaw「龙虾」里的 main agent。**  
> **开发责任：main agent 负责设计、实现、测试、安装并 enable 这个 skill。**  
> **运行责任：skill 最终 enable 给 `CNStock` / `cnstock` agent，由 CNStock 在 Telegram 对话中执行。**  
> 使用场景：用户通过 Telegram 对 CNStock 说“重点分析某某股票”“看看朗科科技”“帮我看一下最新财报”等，CNStock 需要通过本机 iFA CLI 或本机 Python API 生成/复用报告，并把报告文件发回提问的 Telegram 用户。

---

## 1. Main Agent 的任务边界

你是 OpenClaw「龙虾」里的 main agent。本提示词不是要求你亲自在 Telegram 对话中长期执行个股分析，而是要求你完成以下开发与部署工作：

1. 为 `CNStock` / `cnstock` agent 设计并实现一个 Stock Edge / Research Telegram skill；
2. 把该 skill enable 给 CNStock agent；
3. 确保 Telegram 用户和 CNStock 对话时，CNStock 能按本文件定义的流程解析股票、引导用户、生成/复用报告并 deliver；
4. 确保所有本机路径、北京时间日期、报告复用、Telegram 文件发送、SmartMoney → TA deliver 编排都在 skill 层处理好；
5. 完成 smoke test 后，把实现位置、调用方式、已知限制和测试结果写入 handover。

CNStock agent 被 enable 后，在 Telegram 运行时的职责是：

1. 从 Telegram 用户消息中识别股票名称或代码；
2. 判断用户要的是财报 Research 分析、Stock Edge 个股作战室，还是两者都要；
3. 如果意图不清楚，用 1-2 轮简洁对话引导用户选择；
4. 调用本机 iFA 已实现的命令行或 Python API 生成/复用报告；
5. 使用龙虾系统已有 Telegram deliver / send document 能力，把报告发给发起请求的用户；
6. 只做基于本地系统输出的摘要，不编造财务数字、买卖价、概率或模型结论。

所有报告文件都在本机生成，本机路径以 `/Users/neoclaw/claude/ifaenv/` 为根。main agent 实现 skill 时不要把报告输出到 repo 目录，不要把报告 artifact commit。

---

## 2. 先判断用户要什么

### 2.1 股票解析

用户可能输入：

- `重点分析朗科科技`
- `看看 300042`
- `帮我看一下 300042.SZ`
- `智微智能最新季报怎么样`
- `鹏鼎控股能不能买`
- `我有底仓，帮我看下今天怎么做`

股票代码/名称可以直接传给 CLI，iFA 内部会解析：

- `300042.SZ`
- `300042`
- `朗科科技`
- `智微智能`

如果用户没有给出股票，先问：

> 你要分析哪一只股票？可以发股票名称或 6 位代码，例如“朗科科技”或“300042”。

如果名称可能歧义，调用本机 resolver 或 CLI 后把歧义结果列给用户选择；不要猜。

### 2.2 意图识别

优先按关键词判断：

| 用户表达 | 应理解为 |
|---|---|
| `能不能买`、`买点`、`卖点`、`止损`、`目标价`、`支撑`、`压力`、`走势`、`短线`、`波段`、`作战`、`作战室` | Stock Edge 个股作战室 |
| `财报`、`季报`、`年报`、`业绩`、`收入`、`利润`、`现金流`、`资产负债`、`ROE` | Research 财报分析 |
| `最新季报`、`当季`、`Q1/Q2/Q3/Q4` | Research quarterly quick，必要时 deep |
| `最新年报`、`年度`、`年报` | Research annual quick，必要时 deep |
| `深度`、`深入`、`详细`、`过去几年`、`三年`、`同比环比` | Research deep 或 Stock Edge deep，视上下文 |
| `重点分析某某` 但没说方向 | 进入引导，不要直接猜 |

### 2.3 模糊请求的默认引导

如果用户只说“重点分析 XX”，不要直接跑一堆报告。先用下面的话引导：

> 我可以给你做两类分析：  
> 1. **Stock Edge 个股作战室**：看 5/10/20 个交易日的买点、止损、目标区间、风险和同板块位置。  
> 2. **Research 财报分析**：看最新季报或年报的盈利、增长、现金流、资产负债和治理质量。  
>   
> 如果你只是想先快速了解，我建议先看 **最新季报 quick**；如果你关心能不能做短线/波段，就看 **Stock Edge**。你要先看哪一种？

如果用户想省事，可以提供快捷选项：

> 回复 `1` 看 Stock Edge，`2` 看最新季报 quick，`3` 看最新年报 quick，`4` 看季报 deep，`5` 看年报 deep。

### 2.4 默认推荐

当用户说“最新财报怎么样”但没说季报/年报：

1. 优先推荐 `quarterly quick`，因为季报更贴近当前经营变化；
2. 如果当前没有有效季报或用户明确说“年度/完整”，再跑 `annual quick`；
3. 如果 quick 结论提示波动很大、现金流/增长/负债异常，建议用户升级到 deep；
4. 不要默认同时跑 quarterly deep + annual deep，除非用户要求“深入/完整/全部”。

当用户说“能不能买/买点/短线”：

1. 优先跑 Stock Edge quick；
2. 如果用户说有底仓，追问或解析底仓数量，传 `--base-position-shares`，这样报告可以输出 A 股 T+0 相关约束；
3. 如果没有底仓，不输出裸 T+0 建议。

---

## 3. 本机 iFA 入口和命令

工作目录：

```bash
cd /Users/neoclaw/claude/ifa-claude
```

### 3.1 Stock Edge 个股作战室

主要 CLI：

```bash
PYTHONUNBUFFERED=1 uv run python -m ifa.cli stock report "<股票名称或代码>" \
  --mode quick \
  --run-mode production
```

可选：

```bash
# 强制重算，不复用已有同参数同 cutoff 报告
--fresh

# 用户已有底仓时开启 T+0 约束说明
--base-position-shares 1000

# 复现实验或指定北京时间请求时刻
--requested-at 2026-04-30T15:30:00
```

常用别名：

```bash
uv run python -m ifa.cli stock quick "<股票名称或代码>"
uv run python -m ifa.cli stock today "<股票名称或代码>"
```

Stock Edge 已实现本地复用：

- `run_stock_edge_report()` 会先通过 `prepare_report_params()` 加载 YAML baseline、global preset、single overlay；
- 如果 `stock.analysis_record` 中已有同股票、同模式、同 data cutoff、同 param hash 的成功报告，会直接复用；
- 需要强制重算才加 `--fresh`。

输出目录：

```text
/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/stock_edge/
```

典型文件名：

```text
CN_stock_edge_300042_SZ_20260430_062610.html
CN_stock_edge_300042_SZ_20260430_062610.md
```

报告内容包括：

- 5 / 10 / 20 个交易日三周期决策；
- 买入区间、不追高线、止损/失效价、第一止盈、目标区间；
- 支撑/压力、K 线、MA5/MA20/MA60、MACD、动量；
- 同板块财务质量和交易位置；
- 策略矩阵、风险纪律、完整免责声明。

Stock Edge 默认会尝试确保目标股及部分同行的 Research deep 底稿可用：已有资产则复用，缺失才触发生成。

### 3.1.1 时区与报告查找 workaround

OpenClaw「龙虾」这台机器运行在美国西海岸，本机 `date.today()` / shell `date` 很可能不是北京时间日期。iFA 报告面向 A 股，目录里的 `YYYYMMDD` 通常是**北京时间报告日期 / A 股交易日**，不是机器 local date。

因此 skill 层必须遵守：

1. **不要用机器本地日期猜目录**；
2. **优先解析 CLI stdout 里的真实 HTML / MD 路径**，例如 `HTML → /Users/.../CN_stock_edge_...html`；
3. 如果用 Python API，优先使用返回对象里的 `html_path` / `md_path`；
4. 如果需要自己计算日期，必须用项目的北京时间工具：

```python
from ifa.core.report.timezones import bjt_now
date_key = bjt_now().strftime("%Y%m%d")
```

5. 如果必须显式传入 `--requested-at`，传北京时间 ISO，不要传美国本地时间：

```bash
--requested-at 2026-04-30T15:30:00+08:00
```

6. 如果报告刚生成但找不到文件，不要立刻判失败。按下面顺序兜底：
   - 解析 stdout 路径；
   - 查 API 返回对象；
   - 查数据库已登记路径；
   - 在 `/Users/neoclaw/claude/ifaenv/out/<run_mode>/` 下按北京时间今天、昨天、前天、明天做有限 glob；
   - 只选最新修改时间且文件非 0 字节的 `.html`。

通用兜底搜索示例：

```python
from pathlib import Path
from ifa.core.report.timezones import bjt_now

OUT = Path("/Users/neoclaw/claude/ifaenv/out")

def nearby_bjt_date_keys(days_back: int = 3, days_forward: int = 1) -> list[str]:
    now = bjt_now().date()
    return [
        (now + __import__("datetime").timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(-days_back, days_forward + 1)
    ]

def find_latest_report(run_mode: str, family: str, pattern: str) -> Path | None:
    candidates = []
    for date_key in nearby_bjt_date_keys():
        root = OUT / run_mode / date_key / family
        candidates.extend(p for p in root.glob(pattern) if p.is_file() and p.stat().st_size > 0)
    return max(candidates, key=lambda p: p.stat().st_mtime) if candidates else None
```

Stock Edge 查找参数：

```python
find_latest_report("production", "stock_edge", "CN_stock_edge_*.html")
```

### 3.2 Research 财报分析

主要 CLI：

```bash
PYTHONUNBUFFERED=1 uv run python -m ifa.cli research report "<股票名称或代码>" \
  --analysis-type quarterly \
  --tier quick
```

四类主要报告：

| 用户需求 | 命令 |
|---|---|
| 最新季报快速分析 | `--analysis-type quarterly --tier quick` |
| 最新年报快速分析 | `--analysis-type annual --tier quick` |
| 季报深度分析，最多三年/12 个季度，看 YoY + QoQ | `--analysis-type quarterly --tier deep --llm` |
| 年报深度分析，最多三年/3 份年报，看年度变化 | `--analysis-type annual --tier deep --llm` |

示例：

```bash
PYTHONUNBUFFERED=1 uv run python -m ifa.cli research report "朗科科技" \
  --analysis-type quarterly \
  --tier quick

PYTHONUNBUFFERED=1 uv run python -m ifa.cli research report "朗科科技" \
  --analysis-type annual \
  --tier deep \
  --llm
```

Research 已实现本地复用：

- 默认 `--reuse`；
- 同一 `ts_code + analysis_type + tier + latest_period` 已有成功报告时，直接复用 `research.report_runs.output_html_path` 和 `scope_json.md_path`；
- 需要强制重算时使用 `--fresh`。

输出目录：

```text
/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/research/
```

典型文件名：

```text
Stock-Analysis-300042.SZ-20260505-quarterly-quick.html
Stock-Analysis-300042.SZ-20260505-quarterly-quick.md
```

Research 查找参数：

```python
find_latest_report("production", "research", "Stock-Analysis-*.html")
```

注意：Research CLI / service 默认使用 `bjt_now().date()` 作为输出目录日期和文件 stamp。龙虾 agent 不得用美国本机日期推断 Research 路径。

---

## 4. Python API 可选入口

如果龙虾 agent 更适合用 Python API，而不是 shell，可以直接调用：

### 4.1 Stock Edge

```python
from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.families.stock import StockEdgeRequest
from ifa.families.stock.report import run_stock_edge_report

settings = get_settings()
engine = get_engine(settings)
request = StockEdgeRequest(
    ts_code="300042.SZ",
    mode="quick",
    run_mode="production",
    has_base_position=False,
    base_position_shares=None,
    fresh=False,
)
result = run_stock_edge_report(request, engine=engine, settings=settings)
html_path = result.html_path
```

### 4.2 Research

```python
from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.families.research.report import ensure_research_report

settings = get_settings()
engine = get_engine(settings)
result = ensure_research_report(
    engine,
    ts_code="300042.SZ",
    analysis_type="quarterly",
    tier="quick",
    settings=settings,
    reuse=True,
    llm=False,
    triggered_by="telegram",
)
html_path = result.html_path
md_path = result.md_path
```

---

## 5. 其他 iFA 报告家族的路径兼容

虽然这个 skill 的主任务是 Stock Edge / Research，但龙虾里同一个 Telegram deliver 体系也会处理一主三辅、SmartMoney、Ningbo、TA 等报告。它们同样受美国本机日期 vs 北京日期影响。

### 5.1 通用日期原则

所有 A 股报告查找都按这个顺序：

1. **生成命令 stdout 或 API 返回路径优先**；
2. `report_runs` / 对应 family 的持久化路径次之；
3. BJT 日期窗口 glob 兜底；
4. 不使用 `datetime.date.today()`、shell `date +%Y%m%d` 或系统 local timezone 作为唯一依据。

### 5.2 常见 family folder 与文件模式

production / manual 的通用布局：

```text
/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/<family>/
```

常见 family：

| 报告 | family folder | 常见 HTML pattern | 备注 |
|---|---|---|---|
| 一主三辅里的 A 股主报告 | `market` | `CN_market_*.html` | 内部 `report_family='main'`，输出 folder 是 `market` |
| Macro | `macro` | `CN_macro_*.html` | 盘前/盘后 |
| Asset | `asset` | `CN_asset_*.html` | 盘前/盘后 |
| Tech | `tech` | `CN_tech_*.html` | 盘前/盘后 |
| SmartMoney | `smartmoney` | `CN_smartmoney_*.html` | 板块资金流晚报 |
| Ningbo | `ningbo` | `CN_ningbo_*.html` | 宁波派短线策略 |
| TA | `ta` | `ifa_TA_*.html` | TA 晚盘技术策略报告 |
| Research | `research` | `Stock-Analysis-*.html` | 财报分析 |
| Stock Edge | `stock_edge` | `CN_stock_edge_*.html` | 个股作战室 |

如果具体 pattern 和实际输出不一致，skill 不要硬失败；应在对应 family folder 下取最新非空 `.html`，并在日志中记录 fallback。

### 5.3 为什么会找不到报告

典型场景：

- 美国西海岸 2026-05-04 下午，对应北京时间已经是 2026-05-05；
- 报告生成器按北京时间写入 `.../20260505/...`；
- Telegram skill 如果按本机 local date 去找 `.../20260504/...`，就会误判“报告不存在”；
- 同理，盘后报告的 `report_date` 可能是 A 股交易日，而不是生成机器当天日期。

解决方法只有一个：**不要猜目录；解析真实路径 + BJT date window fallback。**

### 5.4 SmartMoney → TA deliver 编排

TA report 的 Telegram 分发节奏应跟一主三辅的 deliver 编排类似，但 TA 对 SmartMoney 有更明确的顺序要求：

1. SmartMoney report 先生成并 deliver；
2. TA report 在 SmartMoney report **成功 deliver 后**再触发；
3. 默认延迟：
   - SmartMoney deliver 成功后 10 分钟开始生成/查找 TA report；或
   - 如果 TA 已经由外部 cron 生成，则 SmartMoney deliver 成功后 15 分钟发送最新 TA report；
4. 不要让 TA deliver 抢在 SmartMoney 前面；
5. 不要只靠固定 wall-clock 时间，因为 SmartMoney 有时会慢；依赖条件是“SmartMoney 已成功 deliver”，不是“到了 18:40”。

推荐状态机：

```text
smartmoney_report_generated
  → deliver_smartmoney_html
  → mark_delivery_success(family="smartmoney", report_date=<BJT/A-share date>)
  → wait 10-15 minutes
  → ensure_or_find_ta_report(report_date)
  → deliver_ta_html_via_cnstock
  → mark_delivery_success(family="ta", report_date=<same date>)
```

TA 生成入口：

```bash
cd /Users/neoclaw/claude/ifa-claude

# 完整 TA daily pipeline：ETL → scan → track → report
PYTHONUNBUFFERED=1 uv run python -m ifa.cli ta run \
  --date YYYY-MM-DD \
  --slot evening

# 如果 TA ETL/scan 已经跑过，只需要生成报告
PYTHONUNBUFFERED=1 uv run python -m ifa.cli ta evening-report \
  --date YYYY-MM-DD \
  --slot evening
```

TA 输出目录：

```text
/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/ta/
```

TA 文件名：

```text
ifa_TA_evening_YYYYMMDD_HHMM.html
ifa_TA_evening_YYYYMMDD_HHMM.md
```

TA 查找参数：

```python
find_latest_report("production", "ta", "ifa_TA_evening_*.html")
```

TA 和 SmartMoney 的日期必须一致：

- SmartMoney 的 `report_date` 是 A 股交易日 / 北京日期；
- TA 的 `--date` 应使用同一个 `report_date`；
- 不要用美国本机日期；
- 如果 SmartMoney stdout/API 返回了实际路径，先从该路径中解析 `YYYYMMDD` 或从 SmartMoney delivery metadata 中取 `report_date`，再传给 TA。

如果 SmartMoney deliver 失败：

- 不自动发 TA；
- 记录 pending 状态；
- 可以提示 operator 或 main agent：“SmartMoney 未成功分发，TA 已暂缓，避免顺序错乱。”

如果 TA report 已存在：

- 不必重跑；
- 用 BJT 日期窗口 + `ifa_TA_evening_*.html` 找同一 report date 的最新非空文件；
- 直接由 `cnstock` 的 Telegram deliver 发出。

如果 TA report 不存在：

- 等 SmartMoney deliver 成功后再运行 `ta run` 或 `ta evening-report`；
- 生成成功后解析 stdout 中的 `✓ HTML` 路径；
- 再 deliver。

---

## 6. Telegram deliver 规则

龙虾系统已经有自己的 Telegram 发送/附件 deliver 能力。实现 skill 时必须使用龙虾已有的 Telegram deliver/send document 工具或封装，不要用默认聊天文本代替报告发送。

基本规则：

1. 谁在 Telegram 里发起请求，就把报告发回谁的 `chat_id`；
2. 不要广播到群或其他用户，除非用户明确要求；
3. 优先发送 HTML 报告文件作为 document；
4. 可附带 Markdown 摘要或同名 `.md` 文件；
5. 如果 Telegram 客户端无法直接预览 HTML，也仍然作为文件发送；
6. 发送前检查文件存在且大小非 0；
7. 发送消息中给 3-5 条摘要，不要复制整篇报告；
8. 摘要必须来自报告结构化输出或报告正文，不要编造；
9. 不要发送 token、DB URL、param hash、调参 artifact、内部路径细节给普通用户；
10. 如果报告生成失败，告诉用户失败原因和下一步，不要假装成功。

建议回复格式：

```text
已生成：朗科科技 · Stock Edge 个股作战室

核心看点：
1. 5日：……
2. 10日：……
3. 20日：……
4. 风险：……

我已把完整 HTML 报告作为附件发给你。报告仅供研究参考，不构成投资建议。
```

Research 回复格式：

```text
已生成：朗科科技 · 最新季报 quick

核心看点：
1. 盈利质量：……
2. 增长：……
3. 现金流：……
4. 风险点：……

我已把完整 HTML 报告作为附件发给你。需要的话可以继续生成季报 deep 或年报 deep。
```

---

## 7. 对话状态机建议

实现 skill 时可以按这个状态机做：

```text
START
  ├─ 没有股票 → ask_stock
  ├─ 有股票 + 明确 Stock Edge → run_stock_edge
  ├─ 有股票 + 明确 Research 类型 → run_research
  ├─ 有股票 + 模糊“重点分析” → ask_analysis_type
  └─ 多股票 → ask_one_or_batch

ask_analysis_type
  ├─ 用户选 1 → run_stock_edge
  ├─ 用户选 2 → run_research quarterly quick
  ├─ 用户选 3 → run_research annual quick
  ├─ 用户选 4 → run_research quarterly deep --llm
  └─ 用户选 5 → run_research annual deep --llm

run_report
  ├─ 成功/复用 → deliver_html_and_summary
  ├─ 股票解析失败 → ask_clarify_stock
  ├─ 数据不足但可 backfill → tell_user_wait_and_retry_or_run_backfill
  └─ 失败 → report_error
```

多股票请求：

- 默认一次只跑一只，避免 Telegram 用户等太久；
- 如果用户明确要求批量，先列出预计耗时，并按顺序生成和发送；
- Stock Edge deep/Research deep 可能较慢，要先提示“可能需要几分钟”。

---

## 8. 生成前后的用户引导话术

### 8.1 模糊请求

用户：

> 重点分析朗科科技

回答：

> 我可以从两个角度看朗科科技：  
> 1. Stock Edge 个股作战室：5/10/20 个交易日的买点、止损、目标区间和风险。  
> 2. Research 财报分析：最新季报或年报的盈利、增长、现金流和资产负债。  
>   
> 如果你只是先快速了解基本面，我建议先看“最新季报 quick”；如果你关心能不能做短线/波段，就看“Stock Edge”。你要先看哪一种？

### 8.2 用户问交易

用户：

> 朗科科技现在能不能买？

动作：

运行 Stock Edge quick。

回复：

> 我先给你跑 Stock Edge 个股作战室，会看 5/10/20 个交易日的买入区、止损、第一止盈、目标区间和风险。稍等，我生成后把 HTML 报告发给你。

### 8.3 用户问财报

用户：

> 朗科科技最新财报怎么样？

动作：

默认运行 `quarterly quick`，除非用户明确要年报或 deep。

回复：

> 我先看最新季报 quick，更适合快速判断近期经营变化。如果你看完还想深入，我可以继续生成季报 deep 或年报 deep。

### 8.4 用户有底仓

用户：

> 我有 3000 股朗科科技，今天怎么做？

动作：

运行：

```bash
PYTHONUNBUFFERED=1 uv run python -m ifa.cli stock report "朗科科技" \
  --mode quick \
  --run-mode production \
  --base-position-shares 3000
```

说明：

- A 股 T+0 只能针对已有底仓；
- 没有底仓不能输出裸 T+0 计划。

---

## 9. 安全边界

必须遵守：

1. 不要直接用大模型编财务数字、价格目标、概率或指标；
2. 报告数字必须来自 iFA 生成结果；
3. Research 的 LLM narrative 只能通过项目内工具和缓存体系生成；
4. 不要打印或泄露 Tushare token、Telegram token、DB URL、系统环境变量；
5. 不要把未校准概率说成确定性上涨概率；
6. 不要把报告说成投资建议；
7. 不要绕过本地复用逻辑反复重算，除非用户明确要求刷新；
8. 不要把 QA 截图、调参 artifact、manifest、日志当成用户报告发送；
9. 不要污染 repo 输出目录，报告全部在 `/Users/neoclaw/claude/ifaenv/` 下；
10. 出错时诚实说明，不要补写“看起来像”的结论。
11. 不要把美国本机日期当成 A 股报告日期；路径和 cutoff 一律以北京时间 / 报告返回路径为准。

---

## 10. 最小实现清单

main agent 请在龙虾环境中开发这个 skill，并在完成后 enable 给 `CNStock` / `cnstock` agent 执行。建议 skill 命名：

```text
cnstock_stock_edge_research_skill
```

Skill 至少包含：

1. `parse_stock_query(message)`：解析股票名/代码、底仓、是否 fresh；
2. `classify_intent(message)`：Stock Edge / Research / ambiguous；
3. `ask_analysis_type(chat_id, stock)`：模糊请求时引导选择；
4. `run_stock_edge(stock, run_mode="production", base_position_shares=None, fresh=False)`；
5. `run_research(stock, analysis_type, tier, llm, fresh=False)`；
6. `extract_report_paths(stdout_or_result)`：解析 HTML/MD 路径；
7. `bjt_date_keys()`：返回北京时间 today/yesterday/tomorrow 等有限搜索窗口；
8. `find_report_fallback(run_mode, family, pattern)`：stdout/API 路径失败时按 BJT 日期窗口找最新 HTML；
9. `summarize_report(path)`：只做 3-5 条短摘要，可从 Markdown 或结构化 section 抽取；
10. `deliver_report(chat_id, html_path, md_path=None, summary=None)`：调用龙虾已有 Telegram deliver；
11. `handle_failure(chat_id, error)`：明确失败原因和下一步。

---

## 11. 交付验收

用以下 Telegram 对话做 smoke test：

1. `重点分析朗科科技`  
   预期：CNStock 询问 Stock Edge / 最新季报 quick / 最新年报 quick / deep 选择。

2. `朗科科技现在能不能买`  
   预期：生成或复用 Stock Edge 报告，并把 HTML 发给同一 chat。

3. `朗科科技最新财报怎么样`  
   预期：默认生成或复用 quarterly quick Research 报告。

4. `朗科科技年报深度分析`  
   预期：生成或复用 annual deep Research 报告，必要时启用项目 LLM。

5. `我有3000股朗科科技，今天怎么做`  
   预期：Stock Edge 带 `--base-position-shares 3000`，报告中允许 T+0 底仓约束说明。

6. `刷新朗科科技作战室`  
   预期：Stock Edge 加 `--fresh` 强制重算。

7. `看一下一个不存在的股票`  
   预期：解析失败，要求用户提供正确股票名或代码。

8. 在美国西海岸日期与北京时间日期不同的窗口触发 `朗科科技现在能不能买`  
   预期：CNStock 仍能通过 stdout 路径或 BJT 日期窗口找到 HTML，不能误报报告不存在。

9. SmartMoney 晚报成功 deliver 后触发 TA  
   预期：TA 不早于 SmartMoney；SmartMoney deliver success 后延迟 10-15 分钟，由 CNStock 查找或生成同一 BJT/report_date 的 `ifa_TA_evening_*.html` 并发送。

---

## 12. 给 main agent 的一句话总指令

请你作为 OpenClaw「龙虾」里的 main agent，设计、实现、测试并 enable 一个 Telegram skill 给 CNStock agent。CNStock 在运行时负责处理用户的单股分析请求：先解析股票和意图；意图不清则用简洁对话引导用户选择 Stock Edge 个股作战室、最新季报 quick、最新年报 quick、季报 deep 或年报 deep；然后调用本机 `/Users/neoclaw/claude/ifa-claude` 下的 iFA CLI / Python API 生成或复用报告；报告路径优先使用 CLI stdout / API 返回值，不要用美国本机日期猜目录，兜底时用北京时间日期窗口搜索 `/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/{stock_edge|research|market|smartmoney|ningbo|ta}/`；最后使用龙虾已有 Telegram deliver/send document 能力，把完整 HTML 报告和 3-5 条摘要发回发起请求的 chat。同时由 main agent 在 skill 中实现 TA 晚盘报告 deliver 编排：SmartMoney report 成功 deliver 后，延迟 10-15 分钟，由 CNStock 查找或生成同一 BJT/report_date 的 `ifa_TA_evening_*.html` 并发送；TA 不得抢在 SmartMoney 前面。不要编造数字，不要泄露 token，不要把报告当投资建议。
