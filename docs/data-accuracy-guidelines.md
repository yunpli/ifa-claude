# 数据准确性手册（Data Accuracy Guidelines）

> **强制性规则**。所有 V2.2 三家族（Research / TA / Stock Intel）的实现都必须遵守。
> **审计依据**：每个 PR review 时，按本手册逐条核对。
> **历史教训**：V2.0 / V2.1 累计 7 起生产 bug，6 起与本手册某条相关。

---

## 第一条 · 金额单位

### 规则

1. **DB 永远存「元」**（NUMERIC，最多 2 位小数）。
2. **Tushare 不同接口的金额单位不一致**，详见 [`tushare-units-reference.md`](tushare-units-reference.md)。
3. **入库前**必须显式调用 `core/units.py` 的转换函数；**禁止裸数字写库**。
4. **渲染层**统一调用 `fmt_amt(yuan, mode='auto')`，自动选最大单位（亿/万/元）。
5. **中间层不带单位概念**：参数命名一律带后缀 `_yuan`，表示是 元 单位的数值。

### 推荐做法

```python
from ifa.core.units import normalize_tushare_value, fmt_amt

# Fetcher：边界转换
df = pro.income(...)
revenue_yuan = normalize_tushare_value('income', 'total_revenue', df.total_revenue.values[0])
profit_min_yuan = normalize_tushare_value('forecast', 'net_profit_min', df.net_profit_min.values[0])
# 入库
session.add(Income(total_revenue_yuan=revenue_yuan, ...))

# Renderer：边界格式化
html = render_template(revenue=fmt_amt(record.total_revenue_yuan))  # → "40.34 亿"
```

### 反模式（禁止）

```python
# ❌ 假设单位
revenue = df.total_revenue.values[0] * 10000  # 错误：income 已是元

# ❌ 渲染层重新算单位
yi = revenue_yuan / 100_000_000
html = f"{yi:.2f} 亿"  # 重复造轮子

# ❌ 字段名不带单位
class Income:
    total_revenue: Decimal  # 是元？万元？
```

---

## 第二条 · 百分比与小数

### 规则

1. DB 存 **0-100 percentage**（不是 0-1 小数）。
2. Tushare 多数接口已经是 0-100；少数接口（`cyq_chips.percent`、部分港股接口）是 0-1。
3. 入库前用 `to_pct_0_100(value, RatioUnit.DECIMAL_0_1)` 显式转换。
4. 渲染用 `fmt_pct(15.56)` → `"15.56%"`。

### 反模式

```python
# ❌ 在 0-100 上又除一次 100
roe = df.roe.values[0]   # 已是 6.21（百分比）
html = f"{roe * 100:.2f}%"  # 错：渲染成 621%
```

---

## 第三条 · 涨跌幅与同比环比

### 规则

1. `pct_change` / `pct_chg` 字段已是 0-100 percentage（5.0 表示 5%）。
2. YoY / QoQ 计算：
   ```python
   yoy = (current - prior) / abs(prior) * 100  # 显式 ×100，配 fmt_pct
   ```
3. **分母为 0 或负数** 时返回 `None`，不返回 `inf` 或 `0`。
4. **跨年报告期对比**用绝对值：上年净利 -100 万、今年 +200 万的 YoY 不是 -300%，应用业务定义（"扭亏"标记）。

---

## 第四条 · 价格与浮点

### 规则

1. 所有价格字段用 **NUMERIC(12, 4)**，不用 FLOAT / REAL。
2. **不直接用 == 比较价格**：
   ```python
   if abs(close - target) < Decimal('0.001'):
       ...
   ```
3. 复权：明确区分 bfq（不复权）/ hfq（后复权）/ qfq（前复权），字段名带后缀。报告**默认显示 qfq**。
4. 历史价格回溯用 hfq；当前价格展示用 qfq；涨跌幅计算用 bfq 或 qfq（视场景）。

---

## 第五条 · NaN 与空值

### 规则

1. Tushare 部分字段返回 NaN（pandas 默认 float NaN）；**禁止隐式转 0**。
2. 入库前显式：
   ```python
   import pandas as pd
   value = None if pd.isna(raw) else Decimal(str(raw))
   ```
3. 报告渲染 None 用「**—**」（U+2014），**不要**留空、不要写 0、不要写 "N/A"。
4. 计算函数遇 None：除非业务允许"视为 0"，否则跳过该样本（`continue` 或返回 `None`）。

---

## 第六条 · 时区与日期

### 规则

1. **DB 存 TIMESTAMPTZ UTC**，所有 INSERT 用 `now() AT TIME ZONE 'UTC'` 或 Python `datetime.now(tz=timezone.utc)`。
2. **业务日期用 BJT**（Asia/Shanghai）。报告标题、披露日期等显示均按 BJT。
3. 转换走既有 `core/report/timezones.py` 的 `to_bjt()` / `BJT` 常量。
4. **交易日**用 `trade_cal` 表，永远 PIT 验证：
   - 不能用日历天数算 T+5
   - 必须用 trade_cal 排除节假日 + 停牌日
5. **业绩预告 `ann_date`** 是 YYYYMMDD 字符串，转 DATE 时小心时区（公告时间可能跨午夜）。

---

## 第七条 · PIT（Point-in-Time）正确性

### 规则

1. **历史回测 / 历史 edge 计算 / Walk-forward** 严禁用未来信息。
2. SW 板块成员用 `sw_member_monthly` 的 `snapshot_month` 而不是当前成员表（V2.1 已修复，参考 `docs/sw-migration.md`）。
3. 财务数据按 `ann_date`（公告日）而不是 `end_date`（报告期）做 PIT 切片。
4. 公告事件按 `publish_time` 排序，禁止用 `created_at`（入库时间）。

### 反模式

```python
# ❌ 用 end_date 做 PIT
WHERE i.end_date <= :rd  # 错：2024Q4 报告 end_date 是 2024-12-31，但实际公告日是 2025-04-26

# ✅ 正确：用 ann_date
WHERE i.ann_date <= :rd
```

---

## 第八条 · 股票代码与板块

### 规则

1. **永远带后缀** `.SZ` / `.SH` / `.BJ`：`'001339.SZ'` 不是 `'001339'`。
2. 北交所代码前缀 8 / 4 / 920 三种共存，用后缀判定，不用前缀。
3. **SW 板块代码**用 6 位数字 + `.SI`，如 `801080.SI`。
4. 申万 L1（28 个）/ L2（约 100 个）必须区分；通常报告用 L2。

---

## 第九条 · 复权与价格序列

### 规则

1. K 线用前复权 `qfq`：避免老价格"过亿元"的视觉误导。
2. 历史回测信号：用后复权 `hfq` 计算收益率 + 涨跌穿越。
3. `stk_factor_pro` 已经预算好三种复权版本，**禁止自己复权**。
4. 显示给用户的图表：qfq；模型训练的特征：可以用任一种但前后一致。

---

## 第十条 · 缓存的 PIT

### 规则

1. 缓存键必须包含 `data_cutoff` 时间戳。
2. 缓存命中检查时：
   ```python
   cached.data_cutoff >= request.data_cutoff  # 缓存比请求新即命中
   ```
3. 报告 run 写入 DB 时记录 `data_cutoff_at`，便于事后审计。
4. ETL 触发的失效（公告 / 财报）必须传播到所有依赖该数据的报告 run（标记为 `superseded`）。

---

## 第十一条 · 浮点数据库存

### 规则

1. **金额**：NUMERIC(20, 2) — 最大支持万亿，2 位小数
2. **价格**：NUMERIC(12, 4) — 最大 99999999.9999
3. **股数**：NUMERIC(20, 0) 或 BIGINT
4. **比例**：NUMERIC(8, 4) — 0-100 或 -100-100
5. **倍数（PE/PB）**：NUMERIC(12, 4) — 可负

### 反模式

```sql
-- ❌ 用 FLOAT 存金额（精度问题）
total_revenue FLOAT

-- ❌ 用 INTEGER 存万元数（导致回退到老 bug）
n_income_wan INTEGER
```

---

## 第十二条 · 异常值与守卫

### 规则

1. **入库前守卫**：每个金额字段加范围校验：
   ```python
   if revenue_yuan and revenue_yuan > Decimal('1e15'):
       logger.error(f"Suspicious revenue: {revenue_yuan} 元 = {fmt_amt(revenue_yuan)}")
       raise SuspiciousValueError(...)
   ```
2. 单股市值 > 10 万亿、PE 绝对值 > 10000、单日涨跌幅 > 50%（除上市首日）等都需触发 alert。
3. **历史数据回填后**自动跑 `scripts/audit_*` 系列校验脚本。

---

## 第十三条 · 渲染层禁止做的事

1. ❌ 重新计算金额单位（`val * 10000` 这种代码不应出现在 sections 或 templates）
2. ❌ 重新算 YoY / QoQ（应来自 analyzer 层）
3. ❌ LLM prompt 中放原始数字让 LLM 自己换算单位（LLM 经常算错）
4. ❌ 在 Jinja 模板里写 Python 单位转换（用 fmt_* 过滤器）

---

## 第十四条 · LLM 数字守则

### 规则

1. **prompt 里的所有数字都来自规则层**，已经是渲染单位（"40.34 亿"），不是原始 yuan 数字。
2. **禁止让 LLM 重算数字**。LLM 输出的数字必须能在输入 prompt 中找到原文。
3. JSON 输出 schema 里数字字段都用字符串类型 + 单位后缀（`"revenue": "40.34 亿"`），避免 LLM 输出科学计数法。

---

## 第十五条 · 检查清单（PR 必做）

- [ ] 所有 Tushare 字段读取都通过 `normalize_tushare_value` 或显式查 `TUSHARE_UNITS`
- [ ] DB 字段命名带 `_yuan` / `_share` / `_pct` 后缀
- [ ] NUMERIC 精度符合第十一条
- [ ] None 处理符合第五条
- [ ] PIT 切片符合第七条
- [ ] 渲染层只调 `fmt_*` 函数
- [ ] LLM prompt 输入数字都已格式化
- [ ] 守卫条件已加（异常值检测）
- [ ] 单元测试覆盖 NaN / None / 0 / 负值四类边界

---

## 历史 Bug 索引（教训卷）

| 日期 | 现象 | 根因 | 防御 |
|------|------|------|-----|
| V2.0 | 营收显示成 40 万亿 | income.total_revenue 当成万元 | 第一条 + units.py |
| V2.0 | ROE 显示 621% | 把 0-100 当成 0-1 又乘 100 | 第二条 |
| V2.1 | 板块成员前视偏差 | 用 ths_member 当前快照 | 第七条 + sw_member_monthly |
| V2.1.1 | 时区错位（晚报标题日期错） | 时间戳直接用 server local | 第六条 |
| V2.1.2 | 北向资金单位混乱 | hsgt 字段当百万元 | 第一条（实测验证） |
| V2.1.2 | 业绩预告净利对不上 | forecast 万元 vs income 元混淆 | 第一条 |
| V2.1.3 | 大股东持股变成 0.36 元 | hold_amount 当金额 | 第一条 + 字段命名后缀 |

每条 bug 修复都对应本手册某条规则。新加规则时同步更新本表。
