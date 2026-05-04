# Tushare Pro 接口单位实测参考表

> **版本**：2026-05-03 实测
> **用途**：所有 Tushare ETL 实现者的"圣经"。任何写入 DB 前的字段都必须按本表显式声明源单位并转换。
> **维护原则**：DB 永远存「元」/「股」/「百分比 0-100」。Tushare 原始单位仅在 fetcher 转换边界出现。

---

## 1. 校验方法

每个接口取智微智能 (001339.SZ) 实测数据，与公开年报 / 同花顺 / 东财数字对账判定单位。

---

## 2. 完整字段单位表

### 2.1 财务报表类

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `income` | total_revenue | **元** | 直接存 | ⚠️ 不要乘 10000 |
| `income` | n_income | **元** | 直接存 | |
| `income` | profit_dedt | **元** | 直接存 | |
| `income` | oper_cost | **元** | 直接存 | |
| `balancesheet` | total_assets | **元** | 直接存 | |
| `balancesheet` | total_liab | **元** | 直接存 | |
| `balancesheet` | money_cap | **元** | 直接存 | |
| `balancesheet` | goodwill | **元** | 直接存 | |
| `cashflow` | n_cashflow_act | **元** | 直接存 | |
| `cashflow` | c_pay_acq_const_fiolta | **元** | 直接存 | |
| `fina_indicator` | roe / eps / margins / 等 | **百分比 0-100** | 直接存 | ⚠️ 不要除 100 |

### 2.2 业绩预告 / 快报

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `forecast` | net_profit_min | **万元** | × 10000 | ⚠️ 与 income 不一致！ |
| `forecast` | net_profit_max | **万元** | × 10000 | ⚠️ |
| `forecast` | last_parent_net | **万元** | × 10000 | |
| `forecast` | p_change_min/max | **百分比 0-100** | 直接存 | |
| `express` | revenue / n_income / 等 | **元** | 直接存 | 注意与 forecast 不同 |

### 2.3 行情

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `daily` | open / high / low / close | **元/股** | 直接存 | |
| `daily` | vol | **手**（100 股 = 1 手） | 直接存（保持手） | ⚠️ moneyflow 的 vol 也是手吗？见下 |
| `daily` | amount | **千元** | × 1000 | ⚠️ 不是元，不是万元 |
| `daily` | pct_chg | **百分比 0-100** | 直接存 | |
| `daily_basic` | turnover_rate | **百分比 0-100** | 直接存 | |
| `daily_basic` | total_mv / circ_mv | **万元** | × 10000 | ⚠️ |
| `pro_bar` (W/M) | OHLCV | **元/手/千元** 同 daily | 同上 | |
| `stk_mins` (5/15/30/60min) | OHLC | **元/股** | 直接存 | |
| `stk_mins` | vol | **股**（不是手！） | 直接存 | ⚠️ 与 daily 不一致 |
| `stk_mins` | amount | **元** | 直接存 | ⚠️ 与 daily 不一致 |

### 2.4 资金流

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `moneyflow` | buy_*_amount / sell_*_amount | **万元** | × 10000 | ⚠️ |
| `moneyflow` | net_mf_amount | **万元** | × 10000 | |
| `moneyflow` | buy_*_vol / sell_*_vol | **手** | 直接存 | |
| `moneyflow_hsgt` | north_money / south_money | **万元** | × 10000 | ⚠️ 不是百万元 |
| `moneyflow_hsgt` | hgt / sgt / ggt_ss / ggt_sz | **万元** | × 10000 | |

### 2.5 股本与股东

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `stock_basic` | — | — | — | |
| `stock_company` | reg_capital | **万元** | × 10000 | |
| `stock_company` | employees | **人** | 直接存 | |
| `top10_holders` | hold_amount | **股** | 直接存 | ⚠️ 不是元 |
| `top10_holders` | hold_ratio | **百分比 0-100** | 直接存 | |
| `top10_floatholders` | 同上 | 同上 | 同上 | |
| `stk_holdertrade` | change_vol | **股** | 直接存 | |
| `stk_holdertrade` | change_ratio | **百分比 0-100** | 直接存 | |
| `stk_holdertrade` | avg_price | **元/股** | 直接存 | |
| `share_float` | float_share | **股** | 直接存 | |
| `share_float` | float_ratio | **百分比 0-100** | 直接存 | |
| `pledge_stat` | pledge_ratio | **百分比 0-100** | 直接存 | |

### 2.6 分红

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `dividend` | cash_div / cash_div_tax | **元/股**（每股分红） | 直接存 | ⚠️ 不是总分红额 |
| `dividend` | stk_div | **股/股**（送股比例） | 直接存 | |
| `dividend` | base_share | **万股** | × 10000 | ⚠️ 不是股 |
| `dividend` | div_proc | 文本（预案/股东大会通过/实施） | — | |

### 2.7 大宗交易 / 龙虎榜

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `block_trade` | price | **元/股** | 直接存 | |
| `block_trade` | vol | **万股** | × 10000 | ⚠️ |
| `block_trade` | amount | **万元** | × 10000 | ⚠️ |
| `top_list` | amount | **元** | 直接存 | |
| `limit_list_d` | amount / limit_amount | **元** | 直接存 | |

### 2.8 管理层 / 高管薪酬

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `stk_managers` | — 文本字段 | — | — | |
| `stk_rewards` | reward | **元** | 直接存 | ⚠️ 不是万元 |
| `stk_rewards` | hold_vol | **股** | 直接存 | |

### 2.9 技术因子库

| 接口 | 字段 | 实测单位 | 入库前转换 | 易错风险 |
|------|------|---------|-----------|---------|
| `stk_factor_pro` | open/high/low/close (含 hfq/qfq) | **元/股** | 直接存 | |
| `stk_factor_pro` | vol | **手** | 直接存 | |
| `stk_factor_pro` | amount | **千元** | × 1000 | ⚠️ |
| `stk_factor_pro` | total_mv / circ_mv | **万元** | × 10000 | ⚠️ |
| `stk_factor_pro` | turnover_rate / pct_chg | **百分比 0-100** | 直接存 | |
| `stk_factor_pro` | pe / pb / ps | **倍** | 直接存 | |
| `stk_factor_pro` | dv_ratio / dv_ttm | **百分比 0-100** | 直接存 | |
| `stk_factor_pro` | total_share / float_share / free_share | **万股** | × 10000 | ⚠️ |
| `stk_factor_pro` | 各 MA/EMA/MACD/KDJ/BOLL/RSI/ATR 等技术指标 | 价格相关用 **元/股**；震荡指标无单位 | 直接存 | |

### 2.10 筹码

| 接口 | 字段 | 实测单位 |
|------|------|---------|
| `cyq_chips` | price | **元/股** |
| `cyq_chips` | percent | **百分比 0-1**（小数） |
| `cyq_perf` | his_low / his_high / cost_*pct | **元/股** |
| `cyq_perf` | winner_rate | **百分比 0-1** |

⚠️ **注意 cyq_chips.percent 与其他接口的"百分比"约定不同 — 这里是 0-1 小数。入库前应 × 100 统一为 0-100，或专门标注。**

### 2.11 热度榜

| 接口 | 字段 | 实测单位 |
|------|------|---------|
| `ths_hot` / `dc_hot` | hot | **整数热度值**（无固定量纲） |
| `ths_hot` / `dc_hot` | rank | **整数排名** |
| `ths_hot` / `dc_hot` | pct_change | **百分比 0-100** |
| `ths_hot` / `dc_hot` | current_price | **元/股** |

### 2.12 公告 / 研报 / 互动

| 接口 | 关键字段 | 类型 |
|------|---------|------|
| `anns_d` | title (str), url (str), ann_date (YYYYMMDD) | — |
| `research_report` | title (str), url (PDF), trade_date | — |
| `irm_qa_sh` / `irm_qa_sz` | q (str), a (str), pub_time (timestamp) | — |
| `disclosure_date` | pre_date (预计披露日), actual_date (实际披露日) | YYYYMMDD |
| `fina_audit` | audit_result, audit_fees | audit_fees 单位 **元** |

---

## 3. 单位约定（入库后）

所有 ETL 写入 DB 的字段，统一约定：

| 维度 | DB 单位 |
|------|--------|
| 金额 | **元**（NUMERIC，最多 2 位小数） |
| 股数 | **股**（NUMERIC，0 位小数 / BIGINT） |
| 比例 / 涨跌幅 | **百分比 0-100**（NUMERIC(8,4)）|
| 倍数（PE/PB） | **倍**（NUMERIC） |
| 价格 | **元/股**（NUMERIC(12,4)） |
| 时间 | **TIMESTAMPTZ UTC** |
| 业务日期 | **DATE**（BJT 日期） |

---

## 4. 渲染层约定

报告输出层统一调用：

```python
from ifa.core.units import Money, fmt_amt, fmt_pct, fmt_share

# 金额智能选单位
fmt_amt(40_3414_19_12, mode='auto')  # → "40.34 亿"
fmt_amt(2800_0000, mode='wan')        # → "2,800 万"

# 百分比
fmt_pct(15.5558)  # → "15.56%"

# 股数
fmt_share(99_800_000)  # → "9,980 万股"
```

---

## 5. 易错点速查（高 stake）

| 易错点 | 正确做法 |
|-------|---------|
| `forecast.net_profit_min` 与 `income.n_income` 单位不一致 | forecast 是万元，income 是元，统一转 元 入库 |
| `daily.amount` 是千元，不是元 | × 1000 转元 |
| `stk_factor_pro.total_mv` 是万元，不是元 | × 10000 转元 |
| `total_share` 是万股，不是股 | × 10000 转股 |
| `cyq_chips.percent` 是 0-1 小数，不是 0-100 | × 100 统一为 0-100 |
| `daily.vol` 是手，`stk_mins.vol` 是股 | 不要假设一致；分接口处理 |
| `stk_mins.amount` 是元，`daily.amount` 是千元 | 同上，分接口处理 |
| `dividend.cash_div` 是每股分红（元/股），不是总分红 | 总分红 = cash_div × base_share |
| `block_trade.vol` 是万股 | × 10000 转股 |

---

## 6. 维护规则

1. 新接口接入前，必须用智微智能跑一次实测，查阅本表对账后再写代码
2. 本表与 `core/units.py` 的 `TushareUnit` 常量保持同步
3. 每季度抽样 10% 字段对账（防 Tushare 单位变更）
4. 任何时候发现单位不一致，先在本表标记，再统一修复
