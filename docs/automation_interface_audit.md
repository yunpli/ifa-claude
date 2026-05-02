# iFA 报告系统 — 第三方自动化调用接口审计

> **版本**: 2.1.3+  
> **审计日期**: 2026-05-02  
> **审计范围**: 只读，不修改任何代码、配置、数据库  
> **用途**: 供第三方 skill / automation tool 按时间自动调用报告生成并分发 HTML/PDF

---

## 一、报告生成命令

### 1.1 CLI 调用形式

所有报告均通过同一 CLI 入口调用：

```bash
uv run python -m ifa.cli generate <family> --slot <slot> \
  --report-date YYYY-MM-DD \
  --mode production \
  --triggered-by <tag>
```

SmartMoney 使用独立子命令：

```bash
uv run python -m ifa.cli smartmoney evening \
  --report-date YYYY-MM-DD \
  --mode production \
  --triggered-by <tag>
```

**工作目录**: 必须在 `/Users/neoclaw/claude/ifa-claude`

---

### 1.2 各报告详细参数

| # | 报告名称 | family | slot | 默认 cutoff (BJT) | 建议触发时间 (BJT) |
|---|---|---|---|---|---|
| 1 | A股主早报 | `market` | `morning` | 09:10 | 09:15 |
| 2 | A股主中报 | `market` | `noon` | 12:15 | 12:30 |
| 3 | A股主晚报 | `market` | `evening` | 18:00 | 18:30 |
| 4 | Macro 宏观早报 | `macro` | `morning` | 08:45 | 08:50 |
| 5 | Macro 宏观晚报 | `macro` | `evening` | 17:30 | 17:40 |
| 6 | Asset 早报 | `asset` | `morning` | 08:50 | 08:55 |
| 7 | Asset 晚报 | `asset` | `evening` | 17:30 | 17:40 |
| 8 | Tech 早报 | `tech` | `morning` | 09:10 | 09:15 |
| 9 | Tech 晚报 | `tech` | `evening` | 18:00 | 18:10 |
| 10 | SmartMoney Report | `smartmoney` | `evening` | 18:00 | 18:30 |

---

### 1.3 完整命令、flag 说明与返回示例

#### 共用 flag（所有 `generate` 子命令）

| Flag | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `--slot` | `morning\|noon\|evening` | 必填 | 报告时段 |
| `--report-date` | `YYYY-MM-DD` | 今日 (BJT) | 报告对应交易日 |
| `--mode` | `test\|manual\|production` | `test` | 决定输出目录层级 |
| `--triggered-by` | string | `"manual"` | 写入 DB 的触发标签，便于追溯 |
| `--cutoff-time` | `HH:MM` | 见上表 | 数据截止时间 (BJT)，不需要通常不传 |
| `--user` | string | `"default"` | 用户标识（当前 tech 系列写入文件名，其余系列写 DB） |
| `--generate-pdf` | flag | 不生成 | 若传此 flag，HTML 生成后自动生成同目录 PDF |

#### 各报告具体命令

```bash
# 1. A股主早报
uv run python -m ifa.cli generate market \
  --slot morning --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 2. A股主中报
uv run python -m ifa.cli generate market \
  --slot noon --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 3. A股主晚报
uv run python -m ifa.cli generate market \
  --slot evening --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 4. Macro 早报
uv run python -m ifa.cli generate macro \
  --slot morning --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 5. Macro 晚报
uv run python -m ifa.cli generate macro \
  --slot evening --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 6. Asset 早报
uv run python -m ifa.cli generate asset \
  --slot morning --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 7. Asset 晚报
uv run python -m ifa.cli generate asset \
  --slot evening --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 8. Tech 早报
uv run python -m ifa.cli generate tech \
  --slot morning --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 9. Tech 晚报
uv run python -m ifa.cli generate tech \
  --slot evening --report-date 2026-04-30 \
  --mode production --triggered-by automation

# 10. SmartMoney 晚报
uv run python -m ifa.cli smartmoney evening \
  --report-date 2026-04-30 \
  --mode production --triggered-by automation
```

#### 成功时 stdout（解析关键行）

```
Report saved: /Users/neoclaw/claude/ifaenv/out/production/20260430/market/CN_market_morning_20260430_0915.html
```

若加 `--generate-pdf`，额外输出：

```
PDF saved: /Users/neoclaw/claude/ifaenv/out/production/20260430/market/CN_market_morning_20260430_0915.pdf
```

**第三方工具解析方法（bash）**:

```bash
output=$(uv run python -m ifa.cli generate market --slot morning \
  --report-date 2026-04-30 --mode production --triggered-by automation 2>&1)
rc=$?
html_path=$(echo "$output" | grep -E "^Report saved:" | head -1 \
  | sed -E 's/^Report saved:[[:space:]]*//')
pdf_path=$(echo "$output" | grep -E "^PDF saved:" | head -1 \
  | sed -E 's/^PDF saved:[[:space:]]*//')
```

#### 失败时行为

- exit code 非 0（Python 异常默认 exit 1）
- stderr 会有 traceback 或错误摘要
- stdout 的 `Report saved:` 行不存在

---

### 1.4 是否支持各选项

| 选项 | 支持 | 说明 |
|---|---|---|
| 指定 `--report-date` | ✅ | YYYY-MM-DD，默认今日 BJT |
| 指定 output_dir | ❌ | 由 `--mode` + `IFA_OUTPUT_ROOT` env var 决定，不可单独指定 |
| 指定 `--user` | ✅ 有限 | tech 系列写入文件名；其余只写 DB |
| 同时生成 HTML+PDF | ✅ | 加 `--generate-pdf` flag 即可 |
| idempotent 重跑 | ✅ | 重跑会生成新 timestamp 文件（不覆盖旧文件） |

---

## 二、报告输出位置

### 2.1 目录结构

```
$IFA_OUTPUT_ROOT/          # 默认: /Users/neoclaw/claude/ifaenv/out
                           # 可通过 IFA_OUTPUT_ROOT 环境变量覆盖
  production/
    <YYYYMMDD>/            # 报告日期（北京时间）
      market/              # A股主报告 (内部 family='main' → 对外显示 'market')
      macro/
      asset/
      tech/
      smartmoney/
  manual/
    <YYYYMMDD>/
      <family>/
  test/                    # 测试模式：flat，无日期/family 子目录
```

### 2.2 文件命名规则与绝对路径

| 报告 | 文件名模式 | 示例绝对路径 |
|---|---|---|
| A股主早报 | `CN_market_morning_YYYYMMDD_HHMM.html` | `/Users/neoclaw/claude/ifaenv/out/production/20260430/market/CN_market_morning_20260430_0915.html` |
| A股主中报 | `CN_market_noon_YYYYMMDD_HHMM.html` | `.../market/CN_market_noon_20260430_1230.html` |
| A股主晚报 | `CN_market_evening_YYYYMMDD_HHMM.html` | `.../market/CN_market_evening_20260430_1830.html` |
| Macro 早报 | `CN_macro_morning_YYYYMMDD_HHMM.html` | `.../macro/CN_macro_morning_20260430_0850.html` |
| Macro 晚报 | `CN_macro_evening_YYYYMMDD_HHMM.html` | `.../macro/CN_macro_evening_20260430_1740.html` |
| Asset 早报 | `CN_asset_morning_YYYYMMDD_HHMM.html` | `.../asset/CN_asset_morning_20260430_0855.html` |
| Asset 晚报 | `CN_asset_evening_YYYYMMDD_HHMM.html` | `.../asset/CN_asset_evening_20260430_1740.html` |
| Tech 早报 | `CN_tech_morning_{user}_YYYYMMDD_HHMM.html` | `.../tech/CN_tech_morning_default_20260430_0915.html` |
| Tech 晚报 | `CN_tech_evening_{user}_YYYYMMDD_HHMM.html` | `.../tech/CN_tech_evening_default_20260430_1810.html` |
| SmartMoney | `CN_smartmoney_evening_YYYYMMDD_HHMM.html` | `.../smartmoney/CN_smartmoney_evening_20260430_1830.html` |

> **注意**：`HHMM` 是生成时刻的北京时间，不是 cutoff 时间。同一天多次生成会产生多个文件（时间戳不同），不会互相覆盖。

### 2.3 PDF 路径

PDF 文件与 HTML 同目录，扩展名替换为 `.pdf`：

```
/Users/neoclaw/claude/ifaenv/out/production/20260430/market/
  CN_market_morning_20260430_0915.html
  CN_market_morning_20260430_0915.pdf   ← 若加 --generate-pdf
```

也可在 HTML 生成后单独调用：

```bash
uv run python scripts/html_to_pdf.py \
  /Users/neoclaw/claude/ifaenv/out/production/20260430/market/CN_market_morning_20260430_0915.html
```

成功输出：

```
Converting 1 HTML report(s) to PDF...
  ✓ CN_market_morning_20260430_0915.pdf  (1.2 MB)

Done: 1 converted
```

失败输出（exit 1）：

```
  ✗ CN_market_morning_20260430_0915.html — Chrome not found
Done: 0 converted, 1 failed
```

### 2.4 Manifest / Metadata 文件

**没有 JSON manifest 文件**。所有元数据（run_id、section 内容、模型输出、耗时、状态）存入 PostgreSQL：

- `smartmoney.report_runs` — 每次生成记录
- `smartmoney.report_sections` — 各节内容
- `smartmoney.model_outputs` — LLM 输出
- `smartmoney.report_judgments` — LLM 判断结果

### 2.5 第三方工具如何可靠找到最新报告

**方法1：解析 CLI stdout（推荐）**

```bash
html_path=$(uv run python -m ifa.cli generate market --slot morning ... 2>&1 \
  | grep "^Report saved:" | head -1 | sed 's/Report saved: //')
```

**方法2：按 glob 匹配最新文件**

```bash
OUT_ROOT="/Users/neoclaw/claude/ifaenv/out/production"
DATE="20260430"
FAMILY="market"
SLOT="morning"

# 找当天所有该 slot 的报告，取最新
html_path=$(ls -t "${OUT_ROOT}/${DATE}/${FAMILY}/CN_${FAMILY}_${SLOT}_${DATE}_"*.html 2>/dev/null | head -1)
```

---

## 三、交易日判断接口

### 3.1 脚本

```
/Users/neoclaw/claude/ifa-claude/scripts/is_trading_day.py
```

依赖本地 `smartmoney.trade_cal` 表（SSE 交易日历镜像，2015–2027年已预填充）。

### 3.2 命令与返回值

```bash
# 判断今天（北京时间，从 UTC 自动换算）
uv run python scripts/is_trading_day.py

# 判断指定日期
uv run python scripts/is_trading_day.py 2026-04-30
uv run python scripts/is_trading_day.py 2026-05-01
uv run python scripts/is_trading_day.py 20260430   # 也支持 YYYYMMDD 格式
```

**返回示例**：

```
# 交易日 → exit 0
true   2026-04-30 (Thursday) is a trading day

# 非交易日（节假日）→ exit 1
false  2026-05-01 (Friday) is NOT a trading day

# 非交易日（周末）→ exit 1
false  2026-05-02 (Saturday) is NOT a trading day

# 错误（DB连接失败 / 表为空 / 日期超出范围）→ exit 2
ERROR: No trade_cal record for 2030-01-01 ...
```

**Exit code 语义**：

| Code | 含义 |
|---|---|
| `0` | 是交易日 |
| `1` | 不是交易日（周末、节假日、调休日均返回1） |
| `2` | 系统错误（DB 连接失败、表未初始化、日期格式错误） |

### 3.3 Shell 中的使用方式

```bash
if uv run python scripts/is_trading_day.py 2026-04-30; then
  echo "是交易日，继续生成报告"
else
  rc=$?
  if [ $rc -eq 1 ]; then
    echo "非交易日，跳过"
  else
    echo "ERROR: 交易日检查失败（exit=$rc），请检查 DB 连接"
    exit 2
  fi
fi
```

### 3.4 Calendar 维护

交易日历每季度或每月刷新一次即可（TuShare 提前公布全年假期）：

```bash
uv run python scripts/is_trading_day.py --refresh
# 输出: ✓ Upserted 4383 rows into smartmoney.trade_cal
```

若 `trade_cal` 表为空（首次使用）：exit 2，错误信息提示运行 `--refresh`。

**无自动 fallback**：交易日历缺失时不会猜测，直接返回 exit 2，第三方工具应将此视为需要人工干预的错误，不应自动降级为"跳过"。

---

## 四、报告依赖与执行顺序

### 4.1 推荐执行顺序

**交易日早间**（建议串行，因为 A股主早报会读三辅摘要）：

```
1. Macro 早报   → 08:50
2. Asset 早报   → 08:55
3. Tech 早报    → 09:15
4. A股主早报   → 09:20（可读到上述三辅的摘要）
```

**交易日午间**（独立，无依赖）：

```
5. A股主中报   → 12:30
```

**交易日晚间**（建议串行）：

```
6. Macro 晚报         → 17:40
7. Asset 晚报         → 17:45
8. Tech 晚报          → 18:10
9. A股主晚报         → 18:30（可读到三辅摘要）
10. SmartMoney Report → 18:30（独立，可与主晚报并行）
```

### 4.2 A股主报告对三辅的依赖关系

**依赖类型：软依赖（graceful degradation）**

- 主报告生成时，会查询当天已成功的 macro/asset/tech 报告的摘要（从 DB `report_sections` 读）
- **如果三辅报告缺失**：主报告不会失败，对应 family 显示 `"数据缺失"`，LLM 照常生成其他内容
- **如果三辅报告存在**：主报告的"三辅联动分析"节会引用三辅摘要，内容更完整

**结论**：先跑三辅是最佳实践，但不是硬性前提条件。

### 4.3 SmartMoney 的依赖关系

**完全独立**，不读取主报告或三辅报告的内容。其数据来源全部来自 DB 的 `smartmoney` schema：

- `market_state_daily`, `factor_daily`, `sector_state_daily`（来自 ETL 计算）
- `sector_moneyflow_sw_daily`（来自原始数据聚合）
- `raw_kpl_list`, `raw_limit_list_d`（来自 TuShare ETL）
- `stock_signals_daily`, `predictions_daily`（来自 ML 训练）

SmartMoney 可与主晚报并行运行。

### 4.4 Prerequisite 汇总

| 报告 | 前置条件 | 若缺失 |
|---|---|---|
| 所有报告 | PostgreSQL DB 可连接 | 失败 exit 1 |
| 所有报告 | TuShare API 可访问 | 部分数据缺失，可能降级生成 |
| 所有报告 | LLM API（OpenAI relay）可访问 | 失败 exit 1 |
| A股主早/晚报 | 三辅报告已生成（当天） | **降级**，不失败 |
| SmartMoney | ETL 已运行（`ifa smartmoney etl --report-date`） | 数据缺失，可能降级 |
| SmartMoney | 模型已训练（RF/XGB .pkl 存在） | ML 节降级为 N/A |

---

## 五、后台任务盘点

### A. SmartMoney 模型回测与参数更新

#### 可用命令

```bash
# 训练 RF + XGB 模型
uv run python -m ifa.cli smartmoney train \
  --in-sample-start 2021-01-04 \
  --in-sample-end 2025-10-31 \
  --oos-start 2025-11-01 \
  --oos-end 2026-04-30 \
  --version v2026_05 \
  --short-horizon 1 \
  --long-horizon 20 \
  --source sw_l2 \
  --mode production

# 回测（评估策略表现）
uv run python -m ifa.cli smartmoney backtest \
  --start 2025-01-01 --end 2026-04-30 \
  --param-version v2026_05 \
  --windows 1,5 --topn 5 \
  --mode production

# 查看已有回测结果
uv run python -m ifa.cli smartmoney bt list

# 查看参数列表
uv run python -m ifa.cli smartmoney params list

# 冻结参数版本（将某版本标为生产用）
uv run python -m ifa.cli smartmoney params freeze v2026_05
```

#### 建议调度

| 任务 | 建议频率 | 建议时间 | 预计耗时 |
|---|---|---|---|
| `train` | 每月一次 | 周六深夜 | 30–90 分钟 |
| `backtest` | 每月一次 | `train` 完成后 | 10–30 分钟 |
| `params freeze` | 需人工 review | 看回测报告后手动执行 | < 1 分钟 |

**不支持 dry-run / no-apply 模式**：`train` 会直接写入 `.pkl` 文件。

**模型文件路径**：

```
~/claude/ifaenv/models/smartmoney/
  random_forest_v2026_05.pkl
  xgboost_v2026_05.pkl
  manifest.json                ← 原子写入，包含各版本元信息
```

**生产报告如何使用新参数**：SmartMoney 晚报在生成时从 `manifest.json` 加载当前 active 版本的 `.pkl`。`params freeze` 将该版本标记为 active。**建议：`train` → `backtest` → 人工确认回测指标 → `params freeze`**，不要全自动更新。

### B. Backfill / 历史补跑

#### ETL Backfill（原始数据）

```bash
# SmartMoney 原始数据 ETL 回填（按日期范围）
uv run python -m ifa.cli smartmoney backfill \
  --start 20260401 --end 20260430 \
  --mode production

# 补跑 SW L2 板块聚合
uv run python scripts/backfill_sw_l2_daily.py \
  --start 2026-04-01 --end 2026-04-30
```

#### 报告 Backfill（重新生成历史报告）

当前没有专门的报告 backfill CLI。使用标准生成命令 + `--report-date` 即可：

```bash
for d in 2026-04-24 2026-04-27 2026-04-28 2026-04-29 2026-04-30; do
  uv run python -m ifa.cli generate market --slot evening \
    --report-date "$d" --mode production --triggered-by backfill
done
```

#### 覆盖行为

- **原始数据**：ETL 使用 `ON CONFLICT DO UPDATE`，幂等，安全重跑
- **报告 HTML/PDF**：每次生成新文件（带当前时刻 HHMM），不覆盖旧文件
- **DB 记录**：每次生成均写入新的 `report_runs` 记录

#### 历史数据缺失时的 fallback

| 数据缺失类型 | 行为 |
|---|---|
| TuShare 当日 daily 数据缺失 | 该节显示 "—" 或 "数据缺失"，其余节继续生成 |
| SmartMoney factor_daily 缺失 | 相关节降级为空，LLM 得到空上下文 |
| 三辅报告未生成 | 主报告降级（见第四节） |

### C. 归档任务

**当前无独立归档任务**。现有系统中：

- HTML/PDF 文件落在 `production/<YYYYMMDD>/<family>/` 目录，本身即为按日期归档的结构
- 所有元数据在 PostgreSQL（`report_runs` 等），已持久化
- **没有** Obsidian 同步、知识库归档、source snapshot 归档
- **没有** archive manifest 文件

**第三方工具建议**：可在报告生成后自行拷贝 HTML/PDF 至邮件/存储系统，无需等待额外归档步骤。

### D. 新闻 / 宏观数据抽取任务

```bash
# 宏观文本抽取（新增贷款/贷款余额等数值从 news 表中提取）
uv run python -m ifa.cli job text-capture \
  --lookback-days 90 --batch-size 5 --mode production

# 政策事件记忆整理（从 news/npr 中提炼活跃政策事件）
uv run python -m ifa.cli job policy-memory \
  --lookback-days 90 --batch-size 5 --mode production
```

**数据来源**：读取 DB 中 `news` / `major_news` / `npr` 表（由 TuShare ETL 定期填充）

**调度建议**：每天晚报生成前跑一次，或每周末跑一次

**报告是否依赖**：macro/market 报告读取 policy memory 结果增强分析，但非硬依赖；若无结果，报告照常生成，只是政策分析节内容较少

---

## 六、第三方自动化调用推荐方案

### 6.1 完整调用流程（伪代码）

```bash
#!/usr/bin/env bash
set -uo pipefail

DATE=$(python3 -c "
import datetime, zoneinfo
print(datetime.datetime.now(tz=zoneinfo.ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d'))
")
DATE_COMPACT="${DATE//-/}"
OUT_ROOT="/Users/neoclaw/claude/ifaenv/out/production"
WORK_DIR="/Users/neoclaw/claude/ifa-claude"

# ─── Step 1: 判断交易日 ────────────────────────────────────────────
cd "$WORK_DIR"
result=$(uv run python scripts/is_trading_day.py "$DATE" 2>&1)
rc=$?
if [ $rc -eq 1 ]; then
  echo "[$DATE] 非交易日，退出"
  exit 0
elif [ $rc -eq 2 ]; then
  echo "[$DATE] 交易日检查失败: $result"
  exit 2   # alert 人工
fi

# ─── Step 2: 生成报告 ──────────────────────────────────────────────
run_report() {
  local family=$1; local slot=$2; local retries=2
  for attempt in $(seq 1 $retries); do
    out=$(uv run python -m ifa.cli generate "$family" \
      --slot "$slot" --report-date "$DATE" \
      --mode production --triggered-by automation \
      --generate-pdf 2>&1)
    rc=$?
    if [ $rc -eq 0 ]; then
      html=$(echo "$out" | grep "^Report saved:" | head -1 | sed 's/Report saved: //')
      pdf=$(echo "$out" | grep "^PDF saved:" | head -1 | sed 's/PDF saved: //')
      echo "OK [$family $slot] html=$html pdf=$pdf"
      return 0
    fi
    echo "FAIL attempt $attempt [$family $slot] rc=$rc"
    sleep 30
  done
  echo "ALERT: [$family $slot] 全部 retry 失败"
  return 1
}

run_report macro morning
run_report asset morning
run_report tech morning
run_report market morning

run_report market noon        # 午间独立触发

run_report macro evening
run_report asset evening
run_report tech evening
run_report market evening
run_report smartmoney evening  # 独立，可与 market evening 并行
```

### 6.2 SmartMoney Evening 调用

```bash
out=$(uv run python -m ifa.cli smartmoney evening \
  --report-date "$DATE" \
  --mode production --triggered-by automation \
  --generate-pdf 2>&1)
html=$(echo "$out" | grep "^Report saved:" | head -1 | sed 's/Report saved: //')
pdf=$(echo "$out" | grep "^PDF saved:" | head -1 | sed 's/PDF saved: //')
```

### 6.3 如何找到 HTML/PDF 附件

**优先用 stdout 解析**（最可靠）：

```bash
html_path=$(echo "$output" | grep "^Report saved:" | head -1 | sed 's/Report saved: //')
pdf_path="${html_path%.html}.pdf"   # PDF 与 HTML 同目录，同名
```

**备选用 glob**（重跑场景）：

```bash
# 最新一份（-t 按修改时间排序）
html_path=$(ls -t "${OUT_ROOT}/${DATE_COMPACT}/market/CN_market_morning_${DATE_COMPACT}_"*.html 2>/dev/null | head -1)
```

### 6.4 故障处理策略

| 故障类型 | 建议 |
|---|---|
| exit 1（报告生成失败） | 等 30s，重试最多 2 次；仍失败则 alert |
| exit 2（交易日检查失败） | 直接 alert，不重试，不生成报告 |
| HTML 存在但 PDF 失败 | 单独重调 `html_to_pdf.py`；仍失败可仅发 HTML |
| TuShare 超时（网络抖动） | 包含在报告 retry 策略中 |
| LLM API 超时 | 同上 |

### 6.5 防重复生成

系统本身不提供 lock 机制，重复调用会生成多个文件（HHMM 不同）。第三方工具自行管理：

```bash
# 方案：检查文件是否已存在（glob），存在则跳过
if compgen -G "${OUT_ROOT}/${DATE_COMPACT}/market/CN_market_morning_${DATE_COMPACT}_"*.html > /dev/null; then
  echo "已存在，跳过"
else
  run_report market morning
fi
```

### 6.6 重跑的幂等性

- HTML/PDF：重跑产生新文件（时间戳不同），不删旧文件 → **发送时取最新文件**即可
- DB：每次生成写入新 `report_runs` 记录 → 不影响历史记录

---

## 七、总表

### 报告总表

| Report | Slot | Default Cutoff | 建议触发时间 | CLI Command | 平均耗时 | HTML 输出路径 | PDF 路径 | 文件名前缀 | 支持 `--report-date` | 支持 `--user` | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| A股主早报 | morning | 09:10 BJT | 09:15 | `generate market --slot morning` | 3–8 min | `.../production/YYYYMMDD/market/` | 同目录 `.pdf` | `CN_market_morning_` | ✅ | ✅ (写DB) | 软依赖三辅早报 |
| A股主中报 | noon | 12:15 BJT | 12:30 | `generate market --slot noon` | 3–8 min | `.../production/YYYYMMDD/market/` | 同目录 `.pdf` | `CN_market_noon_` | ✅ | ✅ | 无依赖 |
| A股主晚报 | evening | 18:00 BJT | 18:30 | `generate market --slot evening` | 3–8 min | `.../production/YYYYMMDD/market/` | 同目录 `.pdf` | `CN_market_evening_` | ✅ | ✅ | 软依赖三辅晚报 |
| Macro 早报 | morning | 08:45 BJT | 08:50 | `generate macro --slot morning` | 3–8 min | `.../production/YYYYMMDD/macro/` | 同目录 `.pdf` | `CN_macro_morning_` | ✅ | ❌ | 无依赖 |
| Macro 晚报 | evening | 17:30 BJT | 17:40 | `generate macro --slot evening` | 3–8 min | `.../production/YYYYMMDD/macro/` | 同目录 `.pdf` | `CN_macro_evening_` | ✅ | ❌ | 无依赖 |
| Asset 早报 | morning | 08:50 BJT | 08:55 | `generate asset --slot morning` | 3–8 min | `.../production/YYYYMMDD/asset/` | 同目录 `.pdf` | `CN_asset_morning_` | ✅ | ❌ | 无依赖 |
| Asset 晚报 | evening | 17:30 BJT | 17:45 | `generate asset --slot evening` | 3–8 min | `.../production/YYYYMMDD/asset/` | 同目录 `.pdf` | `CN_asset_evening_` | ✅ | ❌ | 无依赖 |
| Tech 早报 | morning | 09:10 BJT | 09:15 | `generate tech --slot morning` | 3–8 min | `.../production/YYYYMMDD/tech/` | 同目录 `.pdf` | `CN_tech_morning_{user}_` | ✅ | ✅ (写文件名) | 无依赖 |
| Tech 晚报 | evening | 18:00 BJT | 18:10 | `generate tech --slot evening` | 3–8 min | `.../production/YYYYMMDD/tech/` | 同目录 `.pdf` | `CN_tech_evening_{user}_` | ✅ | ✅ (写文件名) | 无依赖 |
| SmartMoney | evening | 18:00 BJT | 18:30 | `smartmoney evening` | 5–15 min | `.../production/YYYYMMDD/smartmoney/` | 同目录 `.pdf` | `CN_smartmoney_evening_` | ✅ | ❌ | 完全独立 |

> 平均耗时受 LLM API 响应速度影响，主要是 LLM 调用，历史批跑约 3–8 min/报告。

---

### 后台任务总表

| Task | 用途 | 命令 | 建议调度 | 输出路径 | 适合第三方自动化 | 备注 |
|---|---|---|---|---|---|---|
| `is_trading_day` | 判断交易日 | `uv run python scripts/is_trading_day.py [DATE]` | 每次报告前调用 | stdout (true/false) | ✅ | exit 0/1/2 |
| `is_trading_day --refresh` | 刷新交易日历 | `uv run python scripts/is_trading_day.py --refresh` | 每月或每季 | `smartmoney.trade_cal` (DB) | ✅ | 约 5 秒 |
| `smartmoney etl` | 拉取当日原始数据 | `ifa smartmoney etl --report-date YYYY-MM-DD` | 每日收盘后 | `smartmoney.raw_*` (DB) | ✅ | SmartMoney 报告前置 |
| `job text-capture` | 宏观数值抽取 | `ifa job text-capture --lookback-days 90` | 每日或每周 | `smartmoney.macro_*` (DB) | ✅ | 非硬依赖 |
| `job policy-memory` | 政策事件整理 | `ifa job policy-memory --lookback-days 90` | 每日或每周 | DB | ✅ | 非硬依赖 |
| `smartmoney compute` | 重算因子/状态 | `ifa smartmoney compute --report-date ...` | 按需 / backfill | `factor_daily`, `sector_state_daily` (DB) | ⚠️ 谨慎 | 影响历史数据 |
| `smartmoney train` | 训练 RF/XGB 模型 | `ifa smartmoney train ...` | 每月 | `~/claude/ifaenv/models/smartmoney/` | ⚠️ 需人工确认 | 训练后需 `params freeze` |
| `smartmoney backtest` | 评估策略表现 | `ifa smartmoney backtest ...` | `train` 后 | `backtest_runs/metrics` (DB) | ⚠️ 需人工查看 | `bt list` 查看结果 |
| `smartmoney backfill` | 历史 ETL 补跑 | `ifa smartmoney backfill --start ... --end ...` | 按需 | DB | ⚠️ 长时运行 | 非日常 |

---

### Automation Checklist

```
每个交易时段触发前：

1. ✅ CHECK TRADE DAY
   uv run python scripts/is_trading_day.py $DATE
   → exit 0: 继续
   → exit 1: 跳过今日
   → exit 2: ALERT，人工处理

2. ✅ CHECK PREREQUISITES（可选，生产中默认已就绪）
   - DB 可连接
   - TuShare API key 有效（healthcheck 命令可用）
   - LLM relay 可访问

3. ✅ RUN REPORT COMMANDS（按顺序）
   早间: macro morning → asset morning → tech morning → market morning
   午间: market noon（独立触发）
   晚间: macro evening → asset evening → tech evening → market evening
         smartmoney evening（可与 market evening 并行）

4. ✅ WAIT FOR COMPLETION
   每个命令同步等待（CLI 是阻塞的）
   检查 exit code；非 0 即失败

5. ✅ READ OUTPUT PATH
   从 stdout 解析 "Report saved: <path>"
   PDF 路径 = html_path 替换 .html → .pdf

6. ⬜ ARCHIVE REPORT（当前系统无归档机制，第三方自行实现）
   可选：cp HTML/PDF 至存档目录

7. ✅ ATTACH HTML/PDF
   优先发 PDF（排版完整）
   PDF 失败时退化发 HTML

8. ✅ RECORD DELIVERY STATUS
   记录到第三方系统（非 iFA DB）

9. ✅ RETRY OR ALERT ON FAILURE
   最多 retry 2 次，间隔 30s
   仍失败：alert 人工，记录失败日志

10. ⬜ LOCK FILE（可选防重入）
    第三方自行用 /tmp/ifa_lock_$DATE_$SLOT 实现
    生成开始前 touch，完成后 rm
    存在则跳过，避免并发重复生成
```

---

## 附录：环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| `IFA_OUTPUT_ROOT` | `/Users/neoclaw/claude/ifaenv/out` | 报告输出根目录 |
| `IFA_MODEL_DIR` | `~/claude/ifaenv/models/smartmoney` | ML 模型文件目录 |
| `IFA_REPORT_RUN_BADGE` | 无（从 DB URL 推断） | 覆盖 run-mode badge 显示 |

## 附录：常用目录

| 路径 | 用途 |
|---|---|
| `/Users/neoclaw/claude/ifa-claude/` | 项目根目录（所有命令从此运行） |
| `/Users/neoclaw/claude/ifaenv/out/production/` | 生产报告根目录 |
| `/Users/neoclaw/claude/ifaenv/models/smartmoney/` | ML 模型文件 |
| `/Users/neoclaw/claude/ifa-claude/scripts/` | 独立工具脚本 |
