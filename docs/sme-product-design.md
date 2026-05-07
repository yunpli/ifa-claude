# Smart Money Enhanced (SME) 产品与技术设计文档

> **状态**: Proposal / Production Architecture Design
> **日期**: 2026-05-06
> **简称**: SME
> **产品名**: Smart Money Enhanced / 资金雷达
> **目标**: 用可审计、PIT 正确、可验证的资金流研究体系，预测未来 1/3/5/10/20 个交易日 SW L2 板块热度迁移、延续、退潮与拥挤风险。
> **核心原则**: SME 对现有 `SmartMoney` 代码 0 依赖；对现有 `smartmoney.*` 本地数据只读依赖；所有新增数据、派生计算、模型输出、报告运行记录均写入 SME 自有 schema / 表。
> **生产化补充**: SME 必须具备可被第三方平台稳定集成的工程契约：高效率 ETL、北京 22:40 增量任务、23:10 同交易日简报、统一 `ifa.cli` 入口、严格单位治理、数据质量阻断、模型版本可追溯、存储预算默认不超过 10GB。

---

## 1. 一句话定位

SME 不是旧 SmartMoney 的改版，而是一个全新的资金流研究与预测 family：

- 面向交易：回答“未来几天钱会去哪里、现在能不能追、哪些热度正在退潮”。
- 面向投顾：把复杂资金结构翻译成简单结论：关注、等待、谨慎追高、回避。
- 面向量化研究：用严格的点时正确性、回测、OOS、OOC、概率校准和漂移监控验证资金流是否真的有预测力。
- 面向工程生产：新代码、新表、新模型 registry、新报告资产，和旧 SmartMoney 解耦，避免历史包袱和混合写入风险。

SME 的主战场不是“解释今天涨了什么”，而是：

> **预测未来 5-10 个交易日 SW L2 板块热度的迁移概率，并识别当下资金状态处于启动、扩散、加速、高潮、派发、退潮还是潜伏。**

---

## 2. 设计原则

### 2.1 代码隔离

SME 新建独立 package：

```text
ifa/families/sme/
  __init__.py
  data/
  etl/
  features/
  labels/
  models/
  validation/
  report/
  params/
  db/
```

硬规则：

- 不 import `ifa.families.smartmoney.*`。
- 可以 import 通用基础设施，例如 `ifa.core.db`、`ifa.core.tushare`、`ifa.core.render`、`ifa.core.llm`。
- 可以通过 SQL 只读查询 `smartmoney.*` 现有原始表。
- 不 update / insert / delete 任何 `smartmoney.*` 表。
- 不复用 SmartMoney 的旧因子、状态机、ML 模型、报告构建器。
- 与 Stock Edge / TA / Research 的交互必须通过明确接口，不直接读对方内部对象。

### 2.2 数据隔离

推荐新建 PostgreSQL schema：

```sql
CREATE SCHEMA IF NOT EXISTS sme;
```

表名使用 SQL 友好的 snake_case：

```text
sme.sme_source_audit_daily
sme.sme_stock_orderflow_daily
sme.sme_sector_orderflow_daily
sme.sme_sector_diffusion_daily
sme.sme_sector_state_daily
sme.sme_feature_panel_daily
sme.sme_labels_daily
sme.sme_predictions_daily
sme.sme_model_runs
sme.sme_report_runs
```

用户提到 `ifa-sme-***`，产品命名可以沿用 `IFA-SME`，但数据库物理表不建议使用 hyphen，因为 SQL 引号成本和迁移风险都更高。工程上用 `sme.sme_*`，报告和 UI 展示为 `IFA-SME`。

### 2.3 只读依赖边界

SME 在当前 IFA 主环境中可以只读依赖这些现有表：

```text
smartmoney.raw_moneyflow
smartmoney.raw_daily
smartmoney.raw_daily_basic
smartmoney.raw_sw_daily
smartmoney.raw_index_daily
smartmoney.raw_moneyflow_hsgt
smartmoney.raw_margin
smartmoney.raw_top_list
smartmoney.raw_top_inst
smartmoney.raw_block_trade
smartmoney.raw_limit_list_d
smartmoney.raw_kpl_list
smartmoney.raw_sw_member
smartmoney.sw_member_monthly
```

但这是部署优化，不是生产假设。SME 必须支持两种部署模式：

```text
Mode A: co-located
  环境中已有 smartmoney.* 原始表，SME 只读复用，不重复拉取。

Mode B: standalone
  环境中没有 smartmoney.*，SME 自己从 TuShare 预填充 sme.sme_raw_*，
  后续每日 incremental ETL 也由 SME 自己维护。
```

如果 SME 需要额外数据：

- 通过 SME 自己的 ETL 拉 TuShare。
- 写入 `sme.sme_raw_*` 或 `sme.sme_ext_*`。
- 不补写旧 SmartMoney raw 表。

### 2.4 点时正确性

SME 所有计算默认按 `as_of_trade_date` 执行。

行业归属：

- 优先使用 `index_member_all` 的 `in_date/out_date` 做精确 PIT。
- 可为了性能物化 `sme.sme_sw_member_daily` 或 `sme.sme_sw_member_monthly`。
- 训练标签、特征、报告都不能使用未来成员关系。

财务、事件、龙虎榜：

- 只在披露日期之后可见。
- 数据更新时间晚于收盘的源，在当日报告中必须标注是否可用。
- 训练时必须模拟当时可见性，不允许用修正后的未来状态。

### 2.5 单位治理

SME 必须有统一单位规范：

| 类别 | TuShare 常见单位 | SME 存储单位 | 报告显示 |
|---|---:|---:|---:|
| `moneyflow.*_amount` | 万元 | 元 | 亿元/万元 |
| `daily.amount` | 千元 | 元 | 亿元 |
| `daily_basic.total_mv/circ_mv` | 万元 | 元 | 亿元 |
| `stk_factor_pro.amount` | 千元 | 元 | 亿元 |
| `margin.rzye/rqye` | 元 | 元 | 亿元 |
| `moneyflow_hsgt.north_money` | 万元 | 元 | 亿元 |

任何表中只要列名带 `_yuan`，必须已经归一到元。任何列名带 `_wan`，必须保留万元。禁止同名字段在不同表中混用不同单位。

---

## 3. 产品目标

### 3.1 一级目标

1. 预测未来 `1/3/5/10/20` 个交易日 SW L2 板块热度。
2. 判断板块当前资金状态：潜伏、启动、扩散、加速、高潮、派发、退潮、反弹。
3. 识别资金行为：真流入、假流入、散户接盘、主力撤退、资金沉默吸筹、拥挤交易。
4. 生成简单直观的投顾结果：该看什么、该等什么、该避开什么。

### 3.2 不是目标

SME v1 不做：

- 个股完整买卖计划。
- 单股 Stock Edge 深度报告替代。
- 分钟级高频交易。
- 纯题材 NLP 选股。
- LLM 自行创造资金解释。
- 旧 SmartMoney 的兼容性保守迁移。

SME 可以给 Stock Edge 提供板块顺逆风和资金热点迁移 prior，但不直接替代个股作战室。

---

## 4. 核心用户与场景

### 4.1 PM / 交易员

问题：

- 明天和未来一周资金最可能去哪几个 SW L2？
- 今天强的板块还能追吗？
- 哪些高热板块其实在派发？
- 哪些板块资金开始潜伏，但价格还没反应？
- 哪些板块从龙头扩散到中军，说明行情进入更健康阶段？

SME 输出：

- Top 升温板块。
- Top 延续板块。
- Top 潜伏板块。
- Top 退潮/拥挤板块。
- 每个板块 3 条证据和 1 个动作建议。

### 4.2 投顾 / 客户经理

问题：

- 怎么用普通人能懂的话解释“资金在流入半导体，但不建议追高”？
- 哪些方向可以重点关注？
- 风险在哪里？

SME 输出：

- 资金雷达日报。
- 红黄绿状态。
- “钱正在进 / 钱已经太挤 / 钱在撤 / 钱还没动”。
- 不展示复杂公式，不展示模型内部过程。

### 4.3 量化研究员

问题：

- 资金流是否对未来收益有稳定预测力？
- 是总净流有效，还是主力/散户分歧更有效？
- 哪些 regime 下有效，哪些 regime 下失效？
- 模型概率是否校准？

SME 输出：

- OOS / OOC / walk-forward 报告。
- 因子 IC、RankIC、top-N 收益、回撤、换手、容量。
- 分 regime、分年份、分行业、分流动性验证。

---

## 5. 数据源设计

### 5.1 TuShare 官方核心数据

| 源 | API | 用途 | SME 角色 |
|---|---|---|---|
| 个股资金流 | `moneyflow` | 小/中/大/特大单买卖、净流入 | 核心燃料 |
| 日线行情 | `daily` | OHLCV、收益、成交额 | 收益标签和价格确认 |
| 每日指标 | `daily_basic` | 换手、量比、市值、估值 | 流动性、容量、风格控制 |
| 申万行业成分 | `index_member_all` | L1/L2/L3 成分、纳入/剔除日期 | PIT 行业归属 |
| 申万行业日线 | `sw_daily` | 行业价格、成交 | 板块价格确认 |
| 股票技术因子 | `stk_factor_pro` | 全历史技术面、成交、估值、复权字段 | 可选增强特征 |
| 沪深港通资金 | `moneyflow_hsgt` | 北向/南向资金 | 外资风险偏好 |
| 融资融券 | `margin` / `margin_detail` | 杠杆资金 | 风险偏好和追涨约束 |
| 龙虎榜 | `top_list` / `top_inst` | 异动席位、机构席位 | 事件型资金确认 |
| 大宗交易 | `block_trade` | 折溢价、承接压力 | 潜在减持/承接信号 |
| 涨跌停 | `limit_list_d` / `stk_limit` | 涨停、炸板、封单 | 短线热度和风险 |

官方口径重点：

- `moneyflow` 用于沪深 A 股资金流向，提供小单、中单、大单、特大单买卖金额和净流入，数据开始于 2010 年，金额单位为万元。
- `index_member_all` 提供申万 L1/L2/L3 行业代码、名称、成分股票、`in_date`、`out_date`，适合 PIT 行业归属。
- `stk_factor_pro` 提供股票每日技术因子，含价格、成交额、换手、量比、估值、市值、复权字段。

### 5.2 本地只读源

SME v1 在当前主环境中可以直接复用本地已有覆盖，减少初期 ETL 压力：

| 本地表 | 只读用途 |
|---|---|
| `smartmoney.raw_moneyflow` | 个股资金流 |
| `smartmoney.raw_daily` | 日线收益与成交 |
| `smartmoney.raw_daily_basic` | 市值、换手、估值 |
| `smartmoney.raw_sw_daily` | SW 行业日线 |
| `smartmoney.raw_moneyflow_hsgt` | 北向资金 |
| `smartmoney.raw_margin` | 两融 |
| `smartmoney.raw_top_list` | 龙虎榜 |
| `smartmoney.raw_top_inst` | 机构席位 |
| `smartmoney.raw_block_trade` | 大宗交易 |
| `smartmoney.raw_limit_list_d` | 涨跌停/炸板 |
| `smartmoney.raw_sw_member` | SW 成员历史 |
| `smartmoney.sw_member_monthly` | SW 成员月度快照 |

只读约束应在代码和数据库层都表达：

- 代码层：SME data gateway 只暴露 `SELECT`。
- DB 层：未来可建立只读 role，SME runtime 使用该 role 访问 `smartmoney.*`。
- 审计层：`sme_source_audit_daily` 记录每次读取覆盖率和 row count。

如果部署环境没有 `smartmoney.*`，这些表必须由 SME 自己的 raw mirror 替代。SME data gateway 不应该把 `smartmoney.*` 写死为唯一来源，而应通过 source resolver 映射：

```text
logical_source: daily        -> smartmoney.raw_daily        OR sme.sme_raw_daily
logical_source: daily_basic  -> smartmoney.raw_daily_basic  OR sme.sme_raw_daily_basic
logical_source: moneyflow    -> smartmoney.raw_moneyflow    OR sme.sme_raw_moneyflow
logical_source: sw_member    -> smartmoney.raw_sw_member    OR sme.sme_raw_sw_member
logical_source: sw_daily     -> smartmoney.raw_sw_daily     OR sme.sme_raw_sw_daily
```

### 5.3 SME 自有外部数据

如果现有 SmartMoney 没有或覆盖不够，SME 可新拉：

| SME 表 | API | 目的 |
|---|---|---|
| `sme.sme_raw_stk_factor_pro` | `stk_factor_pro` | 技术因子增强 |
| `sme.sme_raw_margin_detail` | `margin_detail` | 个股级杠杆资金 |
| `sme.sme_raw_hk_hold` | `hk_hold` | 北向持股变化 |
| `sme.sme_raw_moneyflow_ths` | `moneyflow_ths` | 资金流交叉验证 |
| `sme.sme_raw_moneyflow_ind_ths` | `moneyflow_ind_ths` | 行业资金源对照 |
| `sme.sme_raw_moneyflow_ind_dc` | `moneyflow_ind_dc` | 东财行业资金源对照 |

v1 不强依赖这些增强源；v2 用于 source ensemble 和异常校验。

---

## 6. 数据治理

### 6.1 数据层分层

SME 使用四层数据：

```text
Raw Readonly Layer
  smartmoney.* 只读
  sme.sme_raw_* SME 自采

Normalized Layer
  sme.sme_stock_orderflow_daily
  sme.sme_sw_member_daily
  sme.sme_market_context_daily

Feature Layer
  sme.sme_sector_orderflow_daily
  sme.sme_sector_diffusion_daily
  sme.sme_sector_state_daily
  sme.sme_feature_panel_daily

Prediction / Product Layer
  sme.sme_labels_daily
  sme.sme_predictions_daily
  sme.sme_model_runs
  sme.sme_report_runs
```

### 6.2 Source audit

`sme.sme_source_audit_daily` 每个交易日记录：

```text
trade_date
source_table
source_schema
row_count
distinct_stock_count
distinct_sector_count
min_trade_date
max_trade_date
null_rate_json
unit_profile_json
coverage_status
error_json
computed_at
```

用途：

- 判断某天数据是否可用于训练。
- 判断报告是否降级。
- 发现 TuShare 字段缺失、单位异常、row count 暴跌。

### 6.3 PIT 行业归属

SME 不应只依赖月度快照。推荐物化 daily membership：

`sme.sme_sw_member_daily`

```text
trade_date
ts_code
name
l1_code
l1_name
l2_code
l2_name
l3_code
l3_name
in_date
out_date
source_version
```

生成逻辑：

```sql
SELECT d.trade_date, m.*
FROM trading_calendar d
JOIN smartmoney.raw_sw_member m
  ON m.in_date <= d.trade_date
 AND (m.out_date IS NULL OR m.out_date > d.trade_date)
```

如果 daily member 生成成本过高，v1 可用 monthly snapshot，但必须在模型运行元数据里标注 `membership_granularity = monthly`。

### 6.4 缺失值规则

缺失值不能静默填 0，必须区分：

- `0`：真实没有资金流。
- `NULL`：源数据缺失。
- `NaN`：计算不可定义。
- `degraded`：可报告但信心下降。
- `invalid`：不可训练，不可报告。

每个派生表保留：

```text
quality_flag
coverage_ratio
degraded_reasons_json
```

---

## 7. 数据模型细节

### 7.1 个股资金画像

表：`sme.sme_stock_orderflow_daily`

主键：

```text
(trade_date, ts_code)
```

核心列：

```text
trade_date
ts_code
name
open_yuan
high_yuan
low_yuan
close_yuan
pct_chg
amount_yuan
turnover_rate
volume_ratio
total_mv_yuan
circ_mv_yuan

buy_sm_amount_yuan
sell_sm_amount_yuan
buy_md_amount_yuan
sell_md_amount_yuan
buy_lg_amount_yuan
sell_lg_amount_yuan
buy_elg_amount_yuan
sell_elg_amount_yuan

sm_net_yuan
md_net_yuan
lg_net_yuan
elg_net_yuan
main_net_yuan
retail_net_yuan
net_mf_amount_yuan

sm_net_ratio
md_net_ratio
lg_net_ratio
elg_net_ratio
main_net_ratio
retail_net_ratio

main_buy_pressure
main_sell_pressure
retail_absorb_score
flow_price_divergence_score
flow_persistence_3d
flow_persistence_5d
flow_persistence_10d
flow_decay_score
quality_flag
computed_at
```

关键定义：

```text
sm_net = buy_sm - sell_sm
md_net = buy_md - sell_md
lg_net = buy_lg - sell_lg
elg_net = buy_elg - sell_elg
main_net = lg_net + elg_net
retail_net = sm_net + md_net
net_mf_amount = sm_net + md_net + lg_net + elg_net

main_net_ratio = main_net / amount_yuan
retail_net_ratio = retail_net / amount_yuan
elg_net_ratio = elg_net / amount_yuan
```

资金行为标签：

| 标签 | 条件草案 | 含义 |
|---|---|---|
| `true_accumulation` | `main_net > 0 AND retail_net < 0 AND pct_chg >= 0` | 主力进，散户卖，价格确认 |
| `silent_accumulation` | `main_net > 0 AND pct_chg <= 0 AND turnover not extreme` | 主力潜伏吸筹 |
| `retail_chase` | `retail_net > 0 AND main_net <= 0 AND pct_chg > 0` | 散户追高 |
| `distribution` | `main_net < 0 AND pct_chg > 0` | 主力撤，价格仍强 |
| `panic_absorb` | `main_net > 0 AND pct_chg < -2%` | 跌中承接 |
| `fake_inflow` | `net_mf > 0 AND main_net <= 0` | 总净流入好看但主力不强 |

### 7.2 板块资金画像

表：`sme.sme_sector_orderflow_daily`

主键：

```text
(trade_date, l2_code)
```

核心列：

```text
trade_date
l1_code
l1_name
l2_code
l2_name
member_count
matched_stock_count
coverage_ratio

sector_amount_yuan
sector_float_mv_yuan
sector_return_equal_weight
sector_return_amount_weight
sector_return_sw_index

sm_net_yuan
md_net_yuan
lg_net_yuan
elg_net_yuan
main_net_yuan
retail_net_yuan
net_mf_amount_yuan

main_net_ratio
retail_net_ratio
elg_net_ratio
main_net_z_60d
main_net_pct_rank_252d
main_net_cross_rank

flow_breadth
main_positive_breadth
elg_positive_breadth
retail_positive_breadth
price_positive_breadth

top1_main_net_share
top3_main_net_share
top5_main_net_share
flow_concentration_score
leader_symbol
leader_name
leader_main_net_yuan
leader_return_5d

quality_flag
computed_at
```

板块聚合方法：

- 金额字段：成员求和。
- 比率字段：先金额求和后除以成交额，不对个股比率直接平均。
- 收益字段：保留等权、成交额加权、SW 指数收益三套。
- 覆盖率：`matched_stock_count / member_count`。
- 集中度：top N 主力净流入占板块主力净流入。

### 7.3 扩散画像

表：`sme.sme_sector_diffusion_daily`

主键：

```text
(trade_date, l2_code)
```

核心列：

```text
trade_date
l2_code
l2_name

leader_return_1d
leader_return_3d
leader_return_5d
median_member_return_5d
tail_member_return_5d
leader_to_median_spread
median_to_tail_spread

flow_breadth_1d
flow_breadth_3d
flow_breadth_5d
flow_breadth_10d
diffusion_slope_5_20
diffusion_acceleration

leader_main_net_ratio
median_main_net_ratio
tail_main_net_ratio
main_flow_dispersion

role_distribution_json
top_members_json
diffusion_phase
diffusion_score
computed_at
```

扩散阶段：

| 阶段 | 描述 |
|---|---|
| `leader_only` | 只有龙头强，板块未扩散 |
| `leader_confirmed` | 龙头资金和价格共振 |
| `midcap_following` | 中军开始跟随 |
| `broad_diffusion` | 多数成员资金转正 |
| `tail_chase` | 后排补涨，可能接近高潮 |
| `diffusion_breakdown` | 龙头弱化，中位数也转弱 |

### 7.4 状态机

表：`sme.sme_sector_state_daily`

状态定义：

| 状态 | 资金 | 价格 | 扩散 | 交易含义 |
|---|---|---|---|---|
| `dormant` 潜伏 | 主力轻微流入 | 价格未动 | 低 | 可观察 |
| `ignition` 启动 | 主力显著流入 | 价格确认 | 龙头先动 | 初始机会 |
| `diffusion` 扩散 | 主力持续 | 中军跟随 | 扩散上升 | 健康主升 |
| `acceleration` 加速 | 流入放大 | 涨幅放大 | 扩散高 | 可持有，追高需谨慎 |
| `climax` 高潮 | 成交极热 | 涨幅极高 | 尾部补涨 | 不宜新追 |
| `distribution` 派发 | 主力流出 | 价格仍强 | 扩散钝化 | 高风险 |
| `cooldown` 退潮 | 资金转负 | 价格回落 | 扩散下降 | 回避 |
| `rebound` 反弹 | 跌后承接 | 弱修复 | 局部 | 只做短线观察 |

状态机不应只靠规则，应有两层：

1. 规则层：可解释、稳定、能落地报告。
2. 统计层：Bayesian transition / HMM 验证状态转移概率。

### 7.5 标签表

表：`sme.sme_labels_daily`

主键：

```text
(trade_date, l2_code, horizon)
```

标签：

```text
horizon                -- 1 / 3 / 5 / 10 / 20
future_return
future_excess_return_vs_market
future_excess_return_vs_l1
future_rank_pct
future_top_quantile_label
future_heat_delta
future_heat_up_label
future_drawdown
future_max_runup
hit_target_before_stop
time_to_peak
turnover_adjusted_return
tradable_capacity_score
label_quality_flag
```

主标签建议：

- `5d_heat_up_label`：未来 5 日热度排名显著上升。
- `10d_top_quantile_label`：未来 10 日收益或热度进入前 20%。
- `5d_distribution_risk_label`：当前高热后未来 5 日跑输且回撤放大。

SME 不是纯收益预测，核心标签应是“热度迁移 + 可交易收益 + 风险惩罚”的组合。

### 7.6 预测表

表：`sme.sme_predictions_daily`

主键：

```text
(as_of_trade_date, l2_code, horizon, model_version)
```

核心列：

```text
as_of_trade_date
l2_code
l2_name
horizon
model_version

heat_up_probability
continuation_probability
cooldown_probability
distribution_risk_probability
expected_excess_return
expected_drawdown
confidence_level
prediction_rank

current_state
next_state_most_likely
next_state_prob_json
evidence_json
risk_flags_json
top_member_json
action_label
created_at
```

`action_label` 固定枚举：

```text
focus_now
watch_pullback
hold_if_owned
avoid_chasing
avoid
```

---

## 8. 特征工程

### 8.1 个股特征

资金结构：

- `main_net_ratio_1d/3d/5d/10d`
- `elg_net_ratio_1d/3d/5d`
- `retail_net_ratio_1d/3d/5d`
- `main_minus_retail_ratio`
- `buy_elg_vs_sell_elg_imbalance`
- `main_flow_acceleration`
- `main_flow_decay`
- `main_flow_persistence`

价量确认：

- `return_1d/3d/5d/10d`
- `amount_ratio_5_20`
- `turnover_z_60d`
- `price_position_60d`
- `volatility_10d/20d`
- `gap_risk`

行为识别：

- `true_accumulation_score`
- `silent_accumulation_score`
- `distribution_score`
- `retail_chase_score`
- `panic_absorb_score`

### 8.2 板块特征

板块资金：

- `sector_main_net_ratio`
- `sector_elg_net_ratio`
- `sector_retail_net_ratio`
- `sector_main_net_z_60d`
- `sector_main_net_pct_252d`
- `sector_flow_turnover_adjusted`

扩散：

- `main_positive_breadth`
- `price_positive_breadth`
- `flow_breadth_slope`
- `flow_breadth_acceleration`
- `top5_flow_concentration`
- `leader_to_median_return_spread`
- `leader_to_median_flow_spread`

相对强弱：

- `sector_return_vs_market`
- `sector_return_vs_l1`
- `flow_rank_vs_l1`
- `heat_rank_change_1d/3d/5d`

拥挤：

- `crowding_score`
- `turnover_z`
- `flow_price_divergence`
- `main_out_price_up`
- `tail_chase_score`

### 8.3 市场上下文

全市场：

- 全 A 成交额。
- 上证/深成指/创业板/科创板收益。
- 市场上涨家数比例。
- 涨停/跌停数量。
- 北向净流入及 60 日分位。
- 融资余额变化。

Regime：

- `risk_on`
- `risk_off`
- `trend_up`
- `range_bound`
- `panic`
- `rebound`
- `liquidity_expansion`
- `liquidity_contraction`

所有模型输出必须按 regime 分层评估。

---

## 9. 模型设计

### 9.1 三层模型架构

SME 不应一开始就堆复杂模型。生产路径是：

```text
Layer 1: 规则 + 统计基线
Layer 2: Gradient Boosting / Ranking Model
Layer 3: Sequence / Graph / Regime Model
```

### 9.2 Layer 1：统计基线

必须先建立可解释 baseline：

1. Cross-sectional Rank IC
   - 每日截面因子对未来 `1/3/5/10/20d` 收益和热度变化的 Spearman IC。

2. Fama-MacBeth Regression
   - 每日横截面回归，观察因子系数均值和 t-stat。

3. Panel Regression
   - 固定效应：日期、L1 行业、市场 regime。

4. Quantile Regression
   - 预测右尾热点，不只预测均值。

5. Bayesian Transition Matrix
   - 当前状态到下一状态的概率。
   - 分全局、L1、L2 自身历史三层 shrinkage。

6. Hazard Model
   - 估计 `ignition -> diffusion`、`climax -> cooldown` 的危险率。

### 9.3 Layer 2：主力 ML

主模型：

- XGBoost / LightGBM ranker：预测未来热度排序。
- XGBoost classifier：预测 `heat_up_label`、`top_quantile_label`、`distribution_risk_label`。
- RandomForest：稳健非线性 baseline。
- ElasticNet / Logistic：解释性 challenger。

推荐目标：

| 模型 | 目标 | 周期 |
|---|---|---|
| `sme_xgb_heat_ranker` | SW L2 热度排名 | 5/10d |
| `sme_xgb_heat_up_classifier` | 升温概率 | 3/5/10d |
| `sme_xgb_distribution_classifier` | 派发/退潮风险 | 3/5d |
| `sme_rf_baseline` | 稳健 baseline | 1/3/5d |
| `sme_elasticnet_explainable` | 因子方向解释 | 5/10d |

概率输出必须校准：

- Isotonic calibration。
- Platt scaling。
- Calibration curve。
- Brier score。

### 9.4 Layer 3：序列和图模型

只有 Layer 1/2 证明有效后再做：

1. TCN / Temporal CNN
   - 输入 20/60 日资金流序列。
   - 输出未来热度状态。

2. HMM / Switching Model
   - 识别资金 regime。

3. Sector Graph Model
   - 节点：SW L2。
   - 边：历史资金迁移、收益领先滞后、产业链关系。
   - 目标：预测热点从一个板块迁移到另一个板块。

图模型的商业价值很高，但上线前必须证明比 XGBoost ranker 有稳定增益。

---

## 10. 验证体系

### 10.1 Backfill 范围

建议分三档：

| 档位 | 时间 | 目的 |
|---|---|---|
| MVP | 2021-01 至今 | 复用当前本地数据，快速闭环 |
| Production | 2016-01 至今 | 覆盖多轮风格切换 |
| Full Research | 2010-01 至今 | 对齐 `moneyflow` 起始时间，做长期稳健性 |

如果 TuShare 配额不足，优先保证：

1. `raw_moneyflow`
2. `raw_daily`
3. `raw_daily_basic`
4. `index_member_all`
5. `raw_sw_daily`
6. `moneyflow_hsgt`
7. `margin`
8. `top_list/top_inst`

### 10.2 样本切分

时间 OOS：

```text
Train:      2021-01-01 ~ 2023-12-31
Validation: 2024-01-01 ~ 2024-12-31
Test:       2025-01-01 ~ 2026-04-30
Live paper: 2026-05-01 onward
```

滚动 walk-forward：

```text
train_window: 504 trading days
validation_window: 63 trading days
test_window: 21 trading days
embargo: max(horizon) trading days
step: 21 trading days
```

短样本 MVP：

```text
train_window: 252 trading days
validation_window: 42 trading days
test_window: 21 trading days
```

### 10.3 OOC 验证

OOC 比普通 OOS 更重要。SME 必须检查：

- 牛市、熊市、震荡市。
- 高成交额、低成交额。
- 风格偏大盘、风格偏小盘。
- 北向大幅流入/流出。
- 融资余额扩张/收缩。
- 政策冲击期。
- 财报密集期。
- 高涨停数量短线情绪期。
- 低波动磨底期。

输出必须回答：

- 哪些 regime 有效？
- 哪些 regime 失效？
- 失效时是否能识别并降低置信度？

### 10.4 统计检验

必须做：

- IC mean / IC IR / IC positive rate。
- RankIC。
- Top-N portfolio return。
- Long-short spread。
- Hit rate。
- Max drawdown。
- Turnover。
- Capacity。
- Slippage sensitivity。
- Bootstrap confidence interval。
- Multiple testing correction。
- Placebo test。
- Feature ablation。
- Label leakage audit。

### 10.5 上线门槛

模型进入 production 必须满足：

```text
OOS top5 5d excess return > 0
OOS RankIC > 0 且 positive_rate > 52%
OOC 至少 70% regime bucket 不为负
Calibration Brier score 优于未校准 baseline
Top-N turnover 在可交易范围
最近 60 日 paper trading 未明显漂移
Feature ablation 证明资金特征贡献为正
Placebo test 不显著
```

如果模型只在一个 regime 有效，可作为 regime-specific model，不允许作为全局模型。

---

## 11. 产品输出设计

### 11.1 报告名称

面向用户：

```text
IFA-SME 资金雷达
```

副标题：

```text
未来 5-10 个交易日 SW L2 热度迁移预测
```

### 11.2 第一屏

第一屏只回答四件事：

1. 钱最可能去哪。
2. 哪些热度还能延续。
3. 哪些地方正在派发。
4. 哪些方向值得等回踩。

示例：

```text
今日资金结论

最可能升温：通信设备 / 半导体 / 汽车零部件
仍可延续：消费电子 / 电网设备
谨慎追高：影视院线 / 游戏
资金潜伏：工业金属 / 光学光电子
```

每个板块最多 3 个标签：

```text
主力进场
扩散上升
价格确认
拥挤偏高
主力撤退
散户接盘
```

### 11.3 板块卡片

每个板块一张卡：

```text
通信设备
状态：启动 -> 扩散
未来 5 日：升温概率 72%
动作：关注回踩，避免追开盘急拉

证据：
1. 主力净流入处于 252 日 86% 分位
2. 板块内 63% 成员主力净流为正
3. 龙头强，中军开始跟随，扩散健康

风险：
拥挤度中等；若明日主力净流转负且涨幅不跟随，降级为等待
```

### 11.4 榜单

固定五张榜：

1. **升温榜**
   - 未来 5/10 日热度上升概率最高。

2. **延续榜**
   - 已热但未拥挤，资金和价格仍匹配。

3. **潜伏榜**
   - 主力进场，价格尚未明显反应。

4. **退潮榜**
   - 主力流出、扩散下降、价格转弱。

5. **别追榜**
   - 热度极高但拥挤、散户接盘或派发概率高。

### 11.5 语言体系

SME 不使用“买入/卖出”作为默认措辞，而用投顾友好动作：

| 动作 | 含义 |
|---|---|
| `重点关注` | 资金和价格共振，未来几天值得跟踪 |
| `等回踩` | 方向好但短线位置不舒服 |
| `持有观察` | 已有仓位可观察，新增需谨慎 |
| `谨慎追高` | 热度强但拥挤上升 |
| `回避` | 退潮或派发风险高 |

### 11.6 解释边界

报告必须展示结论，不展示复杂过程。

可以展示：

- 升温概率。
- 当前状态。
- 三条证据。
- 风险触发条件。
- 代表性股票。

不展示：

- 模型公式。
- 冗长特征表。
- 训练细节。
- LLM 自由发挥的宏大叙事。

---

## 12. 报告结构

建议 HTML 报告章节：

```text
§01 今日资金雷达总览
§02 未来 5-10 日升温榜
§03 热度延续榜
§04 潜伏吸筹榜
§05 退潮与别追榜
§06 SW L2 热度迁移图
§07 板块状态矩阵
§08 代表性股票与扩散结构
§09 市场资金环境
§10 模型可信度与降级提示
§11 术语解释
```

其中 §10 只给用户必要透明度：

```text
今日模型置信度：中高
可用数据：资金流、日线、SW 成员、北向、两融
降级项：龙虎榜当日尚未更新
```

---

## 13. CLI 与使用方式

### 13.1 Backfill

```bash
uv run python -m ifa.cli sme backfill \
  --start 2021-01-01 \
  --end 2026-05-06 \
  --sources core
```

`core` 包含：

```text
moneyflow
daily
daily_basic
sw_member
sw_daily
```

增强源：

```bash
uv run python -m ifa.cli sme backfill \
  --start 2021-01-01 \
  --end 2026-05-06 \
  --sources hsgt,margin,top_list,top_inst,block_trade,limit
```

### 13.2 Compute

```bash
uv run python -m ifa.cli sme compute \
  --start 2021-01-01 \
  --end 2026-05-06
```

分步：

```bash
uv run python -m ifa.cli sme compute-stock-flow --date 2026-05-06
uv run python -m ifa.cli sme compute-sector-flow --date 2026-05-06
uv run python -m ifa.cli sme compute-diffusion --date 2026-05-06
uv run python -m ifa.cli sme compute-state --date 2026-05-06
uv run python -m ifa.cli sme compute-labels --start 2021-01-01 --end 2026-04-15
```

### 13.3 Train

```bash
uv run python -m ifa.cli sme train \
  --train-start 2021-01-01 \
  --train-end 2023-12-31 \
  --valid-start 2024-01-01 \
  --valid-end 2024-12-31 \
  --test-start 2025-01-01 \
  --test-end 2026-04-30 \
  --horizons 3,5,10 \
  --model xgb_heat_ranker
```

### 13.4 Validate

```bash
uv run python -m ifa.cli sme validate \
  --model-version sme_xgb_heat_ranker_v2026_05 \
  --oos \
  --ooc \
  --calibration \
  --ablation
```

### 13.5 Predict

```bash
uv run python -m ifa.cli sme predict \
  --as-of 2026-05-06 \
  --horizons 3,5,10
```

### 13.6 Report

```bash
uv run python -m ifa.cli sme report \
  --date 2026-05-06 \
  --mode production
```

输出目录：

```text
/Users/neoclaw/claude/ifaenv/out/<run_mode>/<YYYYMMDD>/sme/
```

---

## 14. 工程架构

### 14.1 Package 结构

```text
ifa/families/sme/
  __init__.py

  db/
    schema.py
    read_gateway.py          # 只读 smartmoney.*
    write_gateway.py         # 只写 sme.*
    audit.py

  etl/
    tushare_fetchers.py
    source_audit.py
    member_materializer.py
    runner.py

  data/
    calendar.py
    units.py
    contracts.py
    snapshots.py

  features/
    stock_orderflow.py
    sector_orderflow.py
    diffusion.py
    state_machine.py
    market_context.py
    feature_panel.py

  labels/
    forward_returns.py
    heat_labels.py
    target_stop.py

  models/
    baseline_stats.py
    bayesian_transition.py
    hazard.py
    xgb_ranker.py
    xgb_classifier.py
    calibration.py
    registry.py

  validation/
    splits.py
    metrics.py
    oos.py
    ooc.py
    ablation.py
    placebo.py
    drift.py

  report/
    builder.py
    html.py
    cards.py
    terminology.py

  params/
    sme_v0.1.yaml
```

### 14.2 依赖方向

```text
CLI
  -> sme.etl / sme.features / sme.models / sme.report
    -> sme.db
      -> ifa.core.db
      -> SQL read smartmoney.*
      -> SQL write sme.*
```

禁止方向：

```text
sme.* -> smartmoney Python modules
sme.* -> stock internals
sme.* -> report builder of SmartMoney
```

### 14.3 参数治理

主参数文件：

```text
ifa/families/sme/params/sme_v0.1.yaml
```

参数分区：

```yaml
data:
  membership_granularity: daily
  min_sector_coverage_ratio: 0.85

features:
  windows: [1, 3, 5, 10, 20, 60, 252]
  winsorize_pct: 0.01
  min_amount_yuan: 100000000

states:
  main_net_pct_high: 0.80
  breadth_high: 0.60
  crowding_high: 0.75

labels:
  horizons: [1, 3, 5, 10, 20]
  top_quantile: 0.20
  embargo_days: 20

models:
  primary: xgb_heat_ranker
  calibration: isotonic

report:
  top_n: 8
  show_member_count: 5
```

所有生产参数必须 YAML 化。实验参数写入 `sme_model_runs.params_json`，验证通过后才晋升 YAML。

---

## 15. LLM 使用边界

SME 可以使用 LLM，但只能用于：

- 把结构化证据压缩成中文短句。
- 为报告生成“通俗解释”。
- 生成术语解释。
- 对异常情况生成诊断摘要。

LLM 不能：

- 改写价格、概率、排名、资金金额。
- 自行推断没有结构化字段支持的结论。
- 替代模型判断。
- 编造政策或新闻原因。

LLM 输入必须是结构化 JSON：

```json
{
  "sector": "通信设备",
  "state": "ignition",
  "heat_up_probability": 0.72,
  "evidence": [
    "主力净流入 252 日分位 86%",
    "主力正流股票占比 63%",
    "龙头强，中军开始跟随"
  ],
  "risk_flags": ["拥挤度中等"]
}
```

LLM 输出必须经过 schema 校验。

---

## 16. 与其他 family 的关系

### 16.1 SmartMoney

关系：

- SmartMoney 是旧日报系统。
- SME 是新资金流研究和预测系统。
- SME 可以读 SmartMoney raw 表。
- SME 不依赖 SmartMoney 代码。
- 未来如果 SME 成熟，可以替代 SmartMoney 的报告位置。

### 16.2 Stock Edge

SME 给 Stock Edge 提供：

```text
target_stock_l2_state
target_stock_l2_heat_up_probability
target_stock_l2_distribution_risk
sector_top_members
sector_flow_tailwind_score
```

Stock Edge 不直接读 SME 内部特征表，而通过稳定接口：

```python
load_sme_sector_prior(ts_code, as_of_trade_date)
```

### 16.3 TA

SME 给 TA 提供：

- 板块资金顺风。
- 板块退潮 veto。
- 热点扩散状态。

TA 可以把 SME 作为 setup 排名加权，不应让 SME 替代 TA 信号。

---

## 17. MVP 交付计划

### Phase 0：Schema 与边界

交付：

- `ifa/families/sme` 空 family scaffold。
- `sme` schema migration。
- 只读 gateway。
- source audit。
- 单位规范。

验收：

- `sme` 不 import `smartmoney` Python modules。
- 能读取 smartmoney raw 表。
- 不写 smartmoney schema。

### Phase 1：核心资金画像

交付：

- `sme_stock_orderflow_daily`
- `sme_sector_orderflow_daily`
- `sme_sector_diffusion_daily`
- `sme_labels_daily`

验收：

- 对 2021-01 至今完成 backfill。
- 每天 SW L2 覆盖率可审计。
- 资金金额单位统一为元。

### Phase 2：统计验证

交付：

- RankIC。
- Fama-MacBeth。
- Panel regression。
- Transition matrix。
- OOS/OOC validation report。

验收：

- 找到至少 3 组稳定资金因子。
- 明确哪些 regime 有效、哪些 regime 不适用。

### Phase 3：ML 预测

交付：

- XGBoost heat ranker。
- XGBoost distribution risk classifier。
- RF baseline。
- 概率校准。
- `sme_predictions_daily`。

验收：

- OOS/OOC 通过上线门槛。
- paper trading 运行至少 20 个交易日。

### Phase 4：资金雷达报告

交付：

- HTML 报告。
- Top 升温、延续、潜伏、退潮、别追榜。
- 板块卡片。
- 简洁投顾语言。

验收：

- 用户第一屏 30 秒内能知道看什么和避开什么。
- 每个结论都有结构化证据。
- 报告不展示无意义模型细节。

---

## 18. 风险与防线

### 18.1 最大风险：资金流伪 alpha

资金流数据容易过拟合。防线：

- OOC 分 regime。
- Placebo test。
- Ablation。
- 多窗口验证。
- Paper trading。

### 18.2 最大工程风险：单位错

防线：

- `_yuan` / `_wan` 命名强约束。
- 单位 registry。
- source audit 检查异常数量级。
- 报告统一 formatter。

### 18.3 最大数据风险：行业归属前视

防线：

- `in_date/out_date` PIT。
- daily membership。
- 回测使用当日成员，不用最新成员。

### 18.4 最大产品风险：解释太复杂

防线：

- 首页只显示四类结果。
- 每个板块最多三条证据。
- 术语解释放最后。
- 不把模型训练过程给普通用户。

---

## 19. 成功标准

SME 成功不是报告更漂亮，而是以下指标成立：

1. 能稳定识别未来 5-10 日热度上升 SW L2。
2. 能提前识别部分高热板块退潮/派发。
3. 资金结构特征在 OOS/OOC 中有正贡献。
4. 模型概率经过校准，可信度不是装饰。
5. 用户能在 30 秒内得到 actionable conclusion。
6. Stock Edge 可以消费 SME 作为板块 prior。
7. 每个结论都能追溯到数据、特征、模型版本和验证记录。

---

## 20. 生产系统 Review 与升级结论

### 20.1 客观 Review

从生产系统和第三方集成角度看，原设计已经覆盖产品、模型、表设计和报告形态，但还不够“硬”。如果 SME 进入生产并被其他平台集成，真正的失败点通常不是模型少一个特征，而是：

1. Backfill 期间没有先把 ETL 和数据契约做扎实，导致调参时样本不断变化。
2. 开发数据和生产数据混用，调出来的参数无法复现。
3. 每天增量任务没有严格的 as-of、watermark、幂等和失败降级。
4. CLI 入口分散，第三方集成只能靠脚本拼接。
5. 单位、精度、缺失值、PIT 行业归属中任何一项错，最终会产生 wrong result。
6. 生产报告或 API 在数据不完整时仍给出强结论，形成投资和信任风险。

因此 SME 的生产化优先级必须调整：

```text
P0: 数据契约、ETL、单位治理、质量阻断、统一 CLI
P1: 核心资金画像、标签、OOS/OOC 验证
P2: ML 调参、概率校准、模型 registry
P3: 报告美化、LLM 解释、第三方高级导出
```

SME 的第一阶段不应该先追求模型“很聪明”，而应该先确保“数据永远不会悄悄错”。

### 20.2 新增生产硬约束

SME 生产版本必须满足：

- 默认本地新增存储不超过 10GB。
- 报告生成和第三方导出不直接调用 TuShare。
- 所有 TuShare 调用只发生在 ETL 层。
- 所有 ETL 可重跑、可断点续跑、可审计。
- 所有 dense table 禁止使用无边界 JSONB 存储核心数值。
- 所有金额列用单位后缀表达：`_yuan`、`_wan`、`_pct`、`_ratio`。
- 所有预测都带 `model_version`、`feature_version`、`source_snapshot_id`、`as_of_trade_date`。
- 数据质量不达标时，系统必须降级或阻断，而不是输出看似正常的 wrong result。

---

## 21. Backfill、ETL 与存储设计

### 21.1 数据库目标选择

SME 的生产权威库使用 PostgreSQL：

```text
schema: sme
target: PostgreSQL / same cluster as current IFA DB
purpose: production authoritative store
```

选择理由：

- 当前项目已经以 PostgreSQL 为主生产库。
- 需要事务、主键、upsert、schema migration、审计表和第三方稳定查询。
- SME 的核心粒度是日频 A 股全市场和 SW L2，规模可控。
- 只要列设计紧凑、分区正确、避免重复存储宽 raw，10GB 内可控。

DuckDB / Parquet 只用于：

- 研究 scratch。
- 临时训练矩阵缓存。
- 大规模实验中间产物。

DuckDB / Parquet 不能作为 SME 生产权威记忆。

### 21.2 存储预算

默认存储目标：

```text
新增 SME PostgreSQL 数据 <= 10GB
目标健康区间: 4GB ~ 8GB
硬上限: 10GB
超过 8GB 时需要进入 storage review
```

粗略预算：

| 层 | 默认范围 | 估计规模 | 控制策略 |
|---|---:|---:|---|
| `sme_stock_orderflow_daily` | 2021 至今 | 1.5GB-3.0GB | 紧凑列、金额 BIGINT、按年分区 |
| `sme_sector_orderflow_daily` | 2021 至今 | <200MB | SW L2 粒度很小 |
| `sme_sector_diffusion_daily` | 2021 至今 | <300MB | 只保留关键统计 |
| `sme_feature_panel_daily` | 2021 至今 | 300MB-1.0GB | 只保留生产特征，不保留所有实验列 |
| `sme_labels_daily` | 2021 至今 | <300MB | horizon 粒度 |
| `sme_predictions_daily` | 生产滚动 | <200MB | 可长期保留 |
| `sme_model_runs/report_runs/audit` | 长期 | <500MB | JSON 仅放元数据 |
| SME 自采 raw extra | 按需 | 0GB-2GB | 不默认全量拉宽表 |

关键结论：

- 不复制 `smartmoney.*` 已有 raw 大表。
- 从 `smartmoney.raw_moneyflow` 只读生成 SME 派生表。
- `stk_factor_pro` 不默认全字段入库；只拉生产需要字段，或只在增强阶段使用。
- 实验特征不进入长期生产表，训练矩阵有 TTL。

### 21.3 Backfill 数据分层

Backfill 分三类。

#### A 类：开发与调参必须先做

这些是 SME 的骨架，必须在调参前完成：

```text
smartmoney.raw_moneyflow          -- readonly
smartmoney.raw_daily              -- readonly
smartmoney.raw_daily_basic        -- readonly
smartmoney.raw_sw_daily           -- readonly
smartmoney.raw_sw_member          -- readonly
smartmoney.sw_member_monthly      -- readonly fallback
sme.sme_sw_member_daily           -- SME PIT materialization
sme.sme_source_audit_daily
sme.sme_stock_orderflow_daily
sme.sme_sector_orderflow_daily
sme.sme_sector_diffusion_daily
sme.sme_labels_daily
```

建议默认 backfill 范围：

```text
start: 2021-01-01
end: latest_completed_trade_date
```

原因：

- 当前本地 SmartMoney raw 覆盖 2021 起较完整。
- 足够覆盖多个 A 股风格阶段。
- 存储预算可控。
- 调参迭代速度较好。

#### B 类：调参增强数据

这些数据对模型有价值，但不应阻塞 MVP：

```text
smartmoney.raw_moneyflow_hsgt
smartmoney.raw_margin
smartmoney.raw_top_list
smartmoney.raw_top_inst
smartmoney.raw_block_trade
smartmoney.raw_limit_list_d
sme.sme_raw_stk_factor_pro selected fields
sme.sme_raw_margin_detail selected fields
```

策略：

- 先用于 OOC 和 ablation。
- 证明增益后再进入 production feature set。
- 不允许为了“看起来全”把所有宽表全字段长期入库。

#### C 类：未来生产减压数据

这些数据的目的不是立即提升模型，而是减少未来第三方使用时对 TuShare 的压力：

```text
sme.sme_predictions_daily
sme.sme_report_runs
sme.sme_export_artifacts
sme.sme_api_snapshot_daily
```

原则：

- 客户端、报告、第三方平台只读 SME 产物。
- 第三方集成不触发 TuShare。
- 每日生成稳定 snapshot，支持重复下载。

### 21.4 Backfill 执行策略

Backfill 必须按 source 分块、按日期幂等。

推荐命令：

```bash
uv run python -m ifa.cli sme etl backfill \
  --start 2021-01-01 \
  --end 2026-05-06 \
  --profile dev_full \
  --max-storage-gb 10 \
  --workers 4 \
  --resume
```

执行顺序：

1. 建立 `sme` schema 和分区。
2. 读取交易日历，生成目标日期集合。
3. 做 source audit，识别现有 smartmoney 覆盖。
4. 物化 `sme_sw_member_daily`。
5. 生成 `sme_stock_orderflow_daily`。
6. 生成 `sme_sector_orderflow_daily`。
7. 生成 `sme_sector_diffusion_daily`。
8. 生成 `sme_labels_daily`。
9. 生成 backfill summary。
10. 写入 `sme_etl_runs`。

Backfill 必须支持：

- `--resume`
- `--dry-run`
- `--force-date YYYY-MM-DD`
- `--force-source moneyflow`
- `--audit-only`
- `--fail-fast`
- `--allow-degraded`
- `--max-storage-gb`

### 21.5 高效率 ETL 原则

TuShare 调用原则：

- 能按 `trade_date` 拉全市场，就不按 `ts_code` 循环。
- 能用本地已有 smartmoney raw，就不重复请求 TuShare。
- 能一次性 backfill，就不要在 report-time 动态拉。
- 每个 source 有独立 watermark。
- 每次请求有 request log、row count、耗时、异常、重试次数。

数据库写入原则：

- 分区表按 year 或 month。
- dense daily panel 用 `BIGINT` / `DOUBLE PRECISION`，避免大面积 `NUMERIC`。
- JSONB 仅用于稀疏元数据，不用于核心因子列。
- 批量 upsert，不逐行 insert。
- 重算某日期时先写 staging，再原子 swap/upsert。
- 每个生产表都有 `computed_at`、`source_snapshot_id`、`quality_flag`。

索引原则：

```text
PRIMARY KEY (trade_date, ts_code)                 -- stock dense table
PRIMARY KEY (trade_date, l2_code)                 -- sector table
PRIMARY KEY (as_of_trade_date, horizon, l2_code)  -- predictions
BRIN (trade_date)                                 -- large date scans
BTREE (ts_code, trade_date)                       -- single stock debug
BTREE (l2_code, trade_date)                       -- sector history
```

### 21.6 存储治理

新增表：

`sme.sme_storage_audit_daily`

```text
audit_date
schema_name
table_name
row_count
total_bytes
table_bytes
index_bytes
toast_bytes
partition_count
retention_policy
storage_status
computed_at
```

规则：

- 总 SME 存储 > 8GB：warning。
- 总 SME 存储 > 10GB：block new optional backfill。
- 单表 index/table ratio > 0.8：index review。
- JSONB dense table 超过 500MB：schema review。
- 训练临时表默认 TTL 30 天。

---

## 22. 每日增量 ETL 与生产调度

### 22.1 北京时间 22:40 增量目标

SME 推荐每天北京时间 22:40 运行 incremental ETL，23:10 生成同一个观察交易日的客户简报：

```text
incremental schedule: 22:40 Asia/Shanghai
brief schedule:       23:10 Asia/Shanghai
target: same Beijing trading day if it is a trading day
```

生产脚本第一步检查 `smartmoney.trade_cal`：

```text
如果运行日是交易日: D = 运行日
如果运行日不是交易日: 输出 structured skip 并 exit 0
```

注意：

- `moneyflow`、`daily`、`daily_basic`、龙虎榜、大宗交易等多数盘后数据在夜间已可用。
- 部分源可能存在延迟或权限问题。
- 如果某增强源缺失，不应阻断核心预测，但必须标记降级。
- 可选增加北京时间 09:30 catch-up job，补齐两融等晚到数据。
- `scripts/sme_briefing_0400.sh` 仅保留给 legacy 凌晨 previous-trading-day 模式，不作为推荐第三方调度。

### 22.2 Incremental pipeline

每日任务：

```text
01 resolve_as_of_date
02 acquire_etl_lock
03 audit_existing_sources
04 pull_missing_tushare_sources
05 materialize_membership_if_needed
06 compute_stock_orderflow
07 compute_sector_orderflow
08 compute_diffusion
09 compute_state
10 mature_labels_for_old_dates
11 build_feature_panel_for_D
12 run_predictions
13 run_quality_gates
14 render_report
15 export_third_party_snapshot
16 release_lock
17 notify_status
```

对应命令：

```bash
TZ=Asia/Shanghai uv run python -m ifa.cli sme etl incremental \
  --as-of auto \
  --profile production \
  --compute \
  --predict \
  --report \
  --export \
  --fail-on-core-missing
```

### 22.3 幂等与锁

增量任务必须具备：

- 全局 job lock：同一 `as_of_trade_date` 不允许两个 production run 并发写。
- source-level lock：允许不同 source backfill 并行，但不允许同表同日期冲突。
- idempotent upsert：同一日期重复跑结果一致。
- run_id：每次执行唯一 ID。
- source_snapshot_id：同一批源数据快照 ID。
- retry：网络/API 临时错误指数退避。
- failover：增强源失败时可降级，核心源失败时阻断。

新增表：

`sme.sme_etl_runs`

```text
run_id
run_mode
profile
as_of_trade_date
started_at
finished_at
status
sources_requested_json
sources_completed_json
sources_failed_json
row_counts_json
storage_before_bytes
storage_after_bytes
quality_summary_json
error_json
```

### 22.4 Core missing vs optional missing

核心源缺失：

```text
raw_moneyflow
raw_daily
raw_daily_basic
sw_member
```

处理：

- 阻断 `production` 预测。
- 可生成“数据不可用”状态报告。
- 不允许输出正常榜单。

增强源缺失：

```text
hsgt
margin
top_list
top_inst
block_trade
limit_list
stk_factor_pro
```

处理：

- 标记 degraded。
- 降低相关特征权重或置为 missing。
- 报告 §10 显示“增强数据未完整更新”。
- 第三方 snapshot 中 `quality_flag = degraded`。

### 22.5 生产 SLA

默认 SLA：

| 任务 | 北京时间 | 要求 |
|---|---:|---|
| Incremental ETL 开始 | 22:40 | 交易日触发，非交易日 structured skip |
| 核心数据计算完成 | 23:00 | 失败告警 |
| 客户简报生成 | 23:10 | 写入 IFA 标准 production 输出目录 |
| 调参 artifact（周末/可选） | 23:00+ | 写入 `/Users/neoclaw/claude/ifaenv/out/sme_tuning/nightly/` |
| 可选 catch-up | 09:30 | 补迟到增强源 |

如果 23:10 未完成：

- 第三方 snapshot 保留上一成功版本。
- `freshness_status = stale`。
- 告警包含缺失源、失败步骤、是否影响核心预测。

---

## 23. 统一 CLI、调参与开发流程

### 23.1 统一入口原则

所有面向客户、生产、第三方集成的使用必须走：

```bash
uv run python -m ifa.cli sme ...
```

禁止生产依赖：

- repo 外散落脚本。
- 手动 Python one-liner。
- 报告运行时直接调用 TuShare。
- 第三方平台直接查询中间特征表。

### 23.2 CLI 命令树

建议命令：

```text
ifa.cli sme doctor
ifa.cli sme init-schema
ifa.cli sme etl backfill
ifa.cli sme etl incremental
ifa.cli sme etl audit
ifa.cli sme compute
ifa.cli sme labels
ifa.cli sme train
ifa.cli sme tune
ifa.cli sme validate
ifa.cli sme promote
ifa.cli sme predict
ifa.cli sme report
ifa.cli sme export
ifa.cli sme status
```

### 23.3 CLI 通用参数

所有命令统一支持：

```text
--run-mode test|manual|production
--as-of YYYY-MM-DD|auto
--start YYYY-MM-DD
--end YYYY-MM-DD
--profile dev_min|dev_full|production
--params path/to/yaml
--dry-run
--json
--output-dir PATH
--run-id UUID
--fail-fast
--allow-degraded
--log-level INFO|DEBUG
```

生产命令默认 `--json` 可输出机器可读状态，方便第三方调度系统解析。

### 23.4 Exit code 契约

```text
0  success
1  user/config error
2  data quality blocked
3  source unavailable
4  model validation failed
5  storage budget exceeded
6  lock conflict
7  unexpected runtime error
```

第三方系统只需要根据 exit code 和 JSON payload 判断是否可继续。

### 23.5 开发调试流程

开发者调试不直接污染 production：

```bash
uv run python -m ifa.cli sme etl backfill \
  --profile dev_min \
  --start 2025-01-01 \
  --end 2026-05-06 \
  --run-mode test

uv run python -m ifa.cli sme compute \
  --profile dev_min \
  --start 2025-01-01 \
  --end 2026-05-06 \
  --run-mode test
```

`dev_min`：

- 小窗口。
- 少量增强源。
- 可快速验证 schema 和计算。
- 不允许晋升生产。

`dev_full`：

- 与生产相同 schema。
- 完整核心源。
- 可用于调参和 OOS/OOC。

`production`：

- 只用已晋升参数。
- 只用已通过验证模型。
- 写入 production 标记的 predictions/report/export。

### 23.6 调参规划

调参必须作为正式实验运行：

新增表：

`sme.sme_experiment_runs`

```text
experiment_id
created_at
created_by
hypothesis
params_base_version
params_patch_json
feature_version
label_version
train_window_json
validation_window_json
test_window_json
ooc_buckets_json
metrics_json
decision
promoted_model_version
artifact_path
```

调参命令：

```bash
uv run python -m ifa.cli sme tune \
  --experiment-name flow_structure_v1 \
  --hypothesis "main-retail divergence predicts 5d heat migration" \
  --train-start 2021-01-01 \
  --train-end 2023-12-31 \
  --valid-start 2024-01-01 \
  --valid-end 2024-12-31 \
  --test-start 2025-01-01 \
  --test-end 2026-04-30 \
  --ooc-buckets regime,liquidity,l1,market_turnover \
  --search bayes \
  --max-trials 120
```

调参硬规则：

- 每个 experiment 必须有 hypothesis。
- 每次只能改变一个主要假设轴。
- 训练、验证、测试窗口固定写入数据库。
- OOC bucket 必须一起跑。
- 不允许直接把调参结果写入 production YAML。
- 只有 `sme promote` 可以晋升模型或参数。

晋升命令：

```bash
uv run python -m ifa.cli sme promote \
  --experiment-id SMEEXP-20260506-001 \
  --target production \
  --require-oos \
  --require-ooc \
  --require-calibration
```

---

## 24. 数据正确性、单位治理与 Wrong Result 防线

### 24.1 数据契约

每个 SME 表必须有 data contract：

```text
table_name
primary_key
not_null_columns
unit_columns
allowed_ranges
foreign_key_like_checks
freshness_requirement
quality_thresholds
blocking_rules
schema_version
```

建议落表：

`sme.sme_data_contracts`

```text
contract_id
table_name
schema_version
contract_json
is_active
created_at
```

### 24.2 单位 registry

新增模块：

```text
ifa/families/sme/data/units.py
```

新增表：

`sme.sme_unit_registry`

```text
source_name
source_field
source_unit
target_field
target_unit
conversion_factor
rounding_policy
example_value
last_verified_at
```

示例：

| source | field | source unit | target | factor |
|---|---|---|---|---:|
| `moneyflow` | `buy_elg_amount` | 万元 | `buy_elg_amount_yuan` | 10000 |
| `daily` | `amount` | 千元 | `amount_yuan` | 1000 |
| `daily_basic` | `total_mv` | 万元 | `total_mv_yuan` | 10000 |
| `moneyflow_hsgt` | `north_money` | 万元 | `north_money_yuan` | 10000 |

所有转换必须走 registry，不允许在业务代码里散落 `* 10000`。

### 24.3 数值类型规范

生产 dense table：

- 金额：`BIGINT`，单位元，允许四舍五入到元。
- 成交量：`BIGINT`。
- 比率：`DOUBLE PRECISION`。
- 排名/分位：`DOUBLE PRECISION`，范围 `[0, 1]`。
- 概率：`DOUBLE PRECISION`，范围 `[0, 1]`。
- 日期：`DATE`。
- 时间戳：`TIMESTAMPTZ`。

只有需要法律/财务精确小数的低频表才使用 `NUMERIC`。资金流因子和模型特征不使用大面积 `NUMERIC`，避免存储和计算成本膨胀。

### 24.4 Reconciliation checks

每次 compute 必须跑校验。

个股资金：

```text
sm_net = buy_sm - sell_sm
md_net = buy_md - sell_md
lg_net = buy_lg - sell_lg
elg_net = buy_elg - sell_elg
main_net = lg_net + elg_net
retail_net = sm_net + md_net
net_recomputed = sm_net + md_net + lg_net + elg_net
abs(net_recomputed - net_mf_amount) <= tolerance
```

板块资金：

```text
sector_main_net = SUM(member_main_net)
sector_amount = SUM(member_amount)
coverage_ratio = matched_members / total_members
main_net_ratio = sector_main_net / sector_amount
```

价格和成交：

```text
amount_yuan >= 0
turnover_rate >= 0
total_mv_yuan >= circ_mv_yuan when both available
abs(pct_chg) < sanity_threshold except special cases
```

预测：

```text
0 <= probability <= 1
rank is unique within (as_of_trade_date, horizon, model_version)
prediction uses feature rows with trade_date <= as_of_trade_date
model_version exists in sme_model_runs
```

### 24.5 PIT 和泄漏检查

训练前必须检查：

- 特征日期不晚于 `as_of_trade_date`。
- 标签窗口不与训练特征泄漏。
- walk-forward split 有 embargo。
- 行业成员用当日 `in_date/out_date`，不是最新成分。
- 股票上市前和退市后不出现。
- 财务/事件数据按披露日可见。

新增命令：

```bash
uv run python -m ifa.cli sme doctor --check leakage --start 2021-01-01 --end 2026-05-06
```

### 24.6 Blocking rules

阻断级别：

| 级别 | 处理 |
|---|---|
| `ok` | 正常输出 |
| `degraded` | 输出但降低置信度并展示原因 |
| `blocked` | 不输出预测榜单 |
| `stale` | 使用上一成功 snapshot，并明确标记 |

必须阻断的情况：

- 核心源缺失。
- 单位 registry 缺少字段映射。
- row count 暴跌超过阈值。
- sector coverage 低于阈值。
- `net_recomputed` 与源 `net_mf_amount` 系统性不一致。
- 预测概率越界。
- 模型版本不可追溯。
- 特征日期晚于 as-of。

SME 的产品原则：

> 宁可不输出，也不能输出错误结果。

### 24.7 Golden samples

建立固定 golden sample：

```text
10 个交易日
10 个 SW L2
30 个代表股票
覆盖大涨、大跌、震荡、涨停潮、低成交、北向大流入/流出
```

每次改单位、ETL、聚合、特征时跑 golden tests：

```bash
uv run pytest tests/sme/test_golden_orderflow.py -q
uv run pytest tests/sme/test_units.py -q
uv run pytest tests/sme/test_pit_membership.py -q
```

Golden tests 不追求模型收益，只防 wrong result。

---

## 25. 第三方平台集成设计

### 25.1 集成边界

第三方平台不直接访问：

- TuShare。
- SME 中间特征表。
- 训练 artifacts。
- LLM 原始 prompt。

第三方平台只访问：

- `sme.sme_predictions_daily`
- `sme.sme_report_runs`
- `sme.sme_api_snapshot_daily`
- CLI export artifact

### 25.2 Snapshot 表

新增表：

`sme.sme_api_snapshot_daily`

```text
snapshot_id
as_of_trade_date
generated_at
model_version
feature_version
quality_flag
freshness_status
payload_json
payload_schema_version
artifact_path
checksum
```

`payload_json` 是面向第三方的稳定契约，不暴露内部字段。

### 25.3 Export 命令

```bash
uv run python -m ifa.cli sme export \
  --as-of 2026-05-06 \
  --format json \
  --schema-version v1 \
  --output-dir /Users/neoclaw/claude/ifaenv/out/production/20260506/sme/export
```

支持：

```text
json
csv
parquet
html
```

JSON payload 示例：

```json
{
  "schema_version": "sme_snapshot_v1",
  "as_of_trade_date": "2026-05-06",
  "freshness_status": "fresh",
  "quality_flag": "ok",
  "model_version": "sme_xgb_heat_ranker_v2026_05",
  "top_heat_up": [
    {
      "l2_code": "801xxx.SI",
      "l2_name": "通信设备",
      "horizon": 5,
      "heat_up_probability": 0.72,
      "current_state": "ignition",
      "action_label": "watch_pullback",
      "evidence": [
        "主力净流入处于历史高分位",
        "板块内资金扩散上升",
        "龙头强，中军开始跟随"
      ],
      "risk_flags": ["拥挤度中等"]
    }
  ]
}
```

### 25.4 版本兼容

第三方契约：

- `schema_version` 只增不破坏。
- 字段删除必须提前一个版本 deprecate。
- 新字段必须可选。
- `action_label`、`quality_flag`、`freshness_status` 使用固定枚举。
- 每个 snapshot 有 checksum，防止半写文件被读取。

### 25.5 客户可见免责声明

SME 面向投顾和交易辅助，不输出确定性买卖承诺。第三方展示必须保留：

```text
本报告基于历史数据、资金流统计和模型预测生成，仅用于研究和辅助决策。
模型预测不保证未来结果，市场存在不确定性。
```

---

## 26. 更新后的实施优先级

### 26.1 P0：生产地基

必须先做：

1. `sme` schema migration。
2. `sme_unit_registry`。
3. `sme_data_contracts`。
4. `sme_etl_runs`。
5. `sme_source_audit_daily`。
6. `sme_storage_audit_daily`。
7. `sme_sw_member_daily`。
8. `ifa.cli sme doctor`。
9. `ifa.cli sme etl backfill`。
10. `ifa.cli sme etl incremental`。

验收标准：

- 2021 至今核心 backfill 可重跑。
- 新增存储 < 10GB。
- 核心源缺失会阻断。
- 单位校验可自动发现错误数量级。
- 22:40 incremental 与 23:10 brief 可在无人工干预下完成；非交易日必须 structured skip。

### 26.2 P1：资金画像和标签

交付：

- `sme_stock_orderflow_daily`
- `sme_sector_orderflow_daily`
- `sme_sector_diffusion_daily`
- `sme_sector_state_daily`
- `sme_labels_daily`

验收标准：

- 每个板块每日有 coverage。
- 每个资金字段可追溯到源字段和单位转换。
- 每个标签可追溯到未来窗口和收益计算口径。

### 26.3 P2：验证和调参

交付：

- OOS。
- OOC。
- walk-forward。
- placebo。
- ablation。
- calibration。
- experiment registry。

验收标准：

- 任何模型晋升都可追溯到 experiment。
- 调参结果不能绕过 `sme promote`。
- 所有 promoted model 都有 metrics artifact。

### 26.4 P3：生产预测和第三方集成

交付：

- `sme_predictions_daily`
- `sme_api_snapshot_daily`
- `sme export`
- HTML 资金雷达报告。

验收标准：

- 第三方不需要访问 TuShare。
- 第三方不需要理解内部特征表。
- snapshot 可复现、可校验、可降级。

---

## 27. Standalone 部署：没有 SmartMoney 时的预填充与增量 ETL

### 27.1 问题定义

前文的 `smartmoney.*` 只读依赖适用于当前 IFA 主环境，但第三方平台或全新生产环境可能完全没有 SmartMoney family，也没有历史 raw 表。

因此 SME 必须具备 standalone 能力：

```text
没有 smartmoney schema 也能安装
没有 smartmoney raw tables 也能 backfill
没有 SmartMoney Python 代码也能运行
不要求第三方先部署旧 SmartMoney
```

这不是可选增强，而是第三方集成的必要条件。

### 27.2 双源策略

SME 使用 logical source，不在业务逻辑里直接写死物理表。

```text
logical source -> physical source resolver
```

source resolver 策略：

| 策略 | 行为 | 适用 |
|---|---|---|
| `prefer_smartmoney` | 有 `smartmoney.*` 就只读复用，否则落到 `sme.sme_raw_*` | 当前 IFA 主环境 |
| `sme_only` | 只读 `sme.sme_raw_*`，忽略 `smartmoney.*` | 第三方生产 |
| `prefer_sme` | 优先 SME raw，缺口才读 SmartMoney | 迁移期 |
| `audit_compare` | 同时读取两边做差异审计，不用于生产输出 | 验证期 |

参数：

```yaml
data:
  source_mode: prefer_smartmoney  # prefer_smartmoney | sme_only | prefer_sme | audit_compare
  allow_smartmoney_readonly: true
  require_sme_raw_for_production: false
```

第三方 standalone 部署默认：

```yaml
data:
  source_mode: sme_only
  allow_smartmoney_readonly: false
  require_sme_raw_for_production: true
```

### 27.3 SME raw mirror 表

SME 需要维护一套最小 raw mirror，不是复制旧 SmartMoney 的全部 schema，而是为 SME 的资金雷达目标保留必要字段。

核心 raw mirror：

| SME 表 | TuShare API | 必要性 | 存储策略 |
|---|---|---|---|
| `sme.sme_raw_moneyflow` | `moneyflow` | P0 | 2021 至今默认；金额转元 BIGINT |
| `sme.sme_raw_daily` | `daily` | P0 | 2021 至今默认；成交额转元 |
| `sme.sme_raw_daily_basic` | `daily_basic` | P0 | 2021 至今默认；市值转元 |
| `sme.sme_raw_sw_member` | `index_member_all` | P0 | 全历史成员关系，低频 |
| `sme.sme_raw_sw_daily` | `sw_daily` | P0 | 2021 至今默认 |
| `sme.sme_raw_index_daily` | `index_daily` | P1 | 市场基准 |
| `sme.sme_raw_moneyflow_hsgt` | `moneyflow_hsgt` | P1 | 北向资金 |
| `sme.sme_raw_margin` | `margin` | P1 | 两融总量 |
| `sme.sme_raw_top_list` | `top_list` | P2 | 龙虎榜事件 |
| `sme.sme_raw_top_inst` | `top_inst` | P2 | 机构席位 |
| `sme.sme_raw_block_trade` | `block_trade` | P2 | 大宗交易 |
| `sme.sme_raw_limit_list_d` | `limit_list_d` | P2 | 涨跌停/炸板 |
| `sme.sme_raw_stk_factor_pro` | `stk_factor_pro` | P2 | 只拉 selected fields |

最小可生产集：

```text
sme_raw_moneyflow
sme_raw_daily
sme_raw_daily_basic
sme_raw_sw_member
sme_raw_sw_daily
sme_sw_member_daily
```

没有这些，SME 不允许输出 production 预测。

### 27.4 Raw mirror 字段原则

Raw mirror 不是无脑保存 TuShare 原始 DataFrame。原则：

- 只保存 SME 需要字段。
- 所有金额进入 SME 后统一转 `_yuan`。
- 保留 `source_trade_date_str` 或 `source_payload_hash` 便于追溯。
- 保留 `pulled_at`、`source_api`、`source_run_id`。
- 不保存重复的宽字段和 UI 无关字段。
- 不保存源 DataFrame 的无边界 JSON dump。

示例：`sme.sme_raw_moneyflow`

```text
trade_date
ts_code
buy_sm_vol
buy_sm_amount_yuan
sell_sm_vol
sell_sm_amount_yuan
buy_md_vol
buy_md_amount_yuan
sell_md_vol
sell_md_amount_yuan
buy_lg_vol
buy_lg_amount_yuan
sell_lg_vol
sell_lg_amount_yuan
buy_elg_vol
buy_elg_amount_yuan
sell_elg_vol
sell_elg_amount_yuan
net_mf_vol
net_mf_amount_yuan
source_api
source_run_id
pulled_at
```

### 27.5 Standalone prefill 命令

第三方新环境初始化：

```bash
uv run python -m ifa.cli sme init-schema \
  --run-mode production

uv run python -m ifa.cli sme etl prefill \
  --source-mode sme_only \
  --start 2021-01-01 \
  --end auto \
  --profile production_core \
  --max-storage-gb 10 \
  --resume \
  --workers 4
```

`prefill` 与 `backfill` 的区别：

```text
prefill:
  从 TuShare 或外部数据源填充 sme.sme_raw_*。

backfill:
  基于已经存在的 logical raw sources 生成 SME 派生表、标签、特征。
```

完整 standalone 初装流程：

```bash
uv run python -m ifa.cli sme doctor --check config,db,tushare
uv run python -m ifa.cli sme init-schema --run-mode production
uv run python -m ifa.cli sme etl prefill --profile production_core --start 2021-01-01 --end auto --resume
uv run python -m ifa.cli sme etl audit --source-mode sme_only --start 2021-01-01 --end auto
uv run python -m ifa.cli sme compute --source-mode sme_only --start 2021-01-01 --end auto
uv run python -m ifa.cli sme labels --source-mode sme_only --start 2021-01-01 --end auto
uv run python -m ifa.cli sme validate --source-mode sme_only --baseline-only
uv run python -m ifa.cli sme predict --source-mode sme_only --as-of auto
uv run python -m ifa.cli sme export --source-mode sme_only --as-of auto --format json
```

### 27.6 Standalone incremental ETL

没有 SmartMoney 的生产环境，每天 22:40 的 incremental 必须先更新 SME raw mirror：

```text
01 resolve_as_of_date
02 acquire_etl_lock
03 pull sme_raw_daily
04 pull sme_raw_daily_basic
05 pull sme_raw_moneyflow
06 pull sme_raw_sw_daily
07 refresh sme_raw_sw_member if scheduled
08 pull optional sme_raw_* sources
09 run source audit
10 compute SME derived tables
11 predict/report/export
```

命令：

```bash
TZ=Asia/Shanghai uv run python -m ifa.cli sme etl incremental \
  --source-mode sme_only \
  --as-of auto \
  --profile production \
  --pull-raw \
  --compute \
  --predict \
  --report \
  --export \
  --fail-on-core-missing
```

如果 TuShare 当天核心源失败：

- 不写新的 production prediction。
- 保留上一成功 snapshot。
- 标记 `freshness_status = stale`。
- exit code = `3 source unavailable` 或 `2 data quality blocked`。

### 27.7 Prefill 性能与 TuShare 压力控制

Standalone prefill 必须保护 TuShare：

- 按日期批量拉全市场。
- 对 `moneyflow/daily/daily_basic` 使用 trade_date 循环，不按股票循环。
- 对 `index_member_all` 低频全量拉后本地物化。
- 对 `stk_factor_pro` 默认不拉，除非 `--include-enhanced`.
- 有 rate limiter、指数退避、失败重试、断点续跑。
- 写 `sme_etl_request_log` 记录每次 API 调用。

新增表：

`sme.sme_etl_request_log`

```text
request_id
run_id
source_api
request_params_json
started_at
finished_at
status
row_count
retry_count
error_message
payload_hash
```

### 27.8 存储预算下的 standalone 取舍

在没有 SmartMoney 的第三方环境中，10GB 预算更紧，因为 SME 要自己保存 raw mirror。

默认策略：

```text
core window: 2021-01-01 至今
full research 2010-01-01 至今: 不默认入 PostgreSQL
older than 2021: 可选 Parquet cold archive
```

生产默认不保存：

- 全字段 `stk_factor_pro`。
- 全量逐笔/分钟数据。
- 宽 JSON payload。
- 旧 DC/THS 概念全量历史，除非 source comparison 需要。

如果用户需要 2010 至今全历史：

```text
PostgreSQL: 近 5 年生产热数据
Parquet cold archive: 2010-2020 研究冷数据
Feature/labels: 可按需重算，不长期保留所有实验列
```

### 27.9 Co-located 到 standalone 的迁移

当前 IFA 主环境可先用 `prefer_smartmoney` 快速开发。准备第三方部署前，必须跑一次迁移验证：

```bash
uv run python -m ifa.cli sme etl prefill \
  --source-mode sme_only \
  --start 2021-01-01 \
  --end 2026-05-06 \
  --profile production_core \
  --resume

uv run python -m ifa.cli sme doctor \
  --check source-parity \
  --left-source smartmoney \
  --right-source sme_raw \
  --start 2025-01-01 \
  --end 2026-05-06
```

source parity 检查：

- row count 一致。
- 关键金额字段数量级一致。
- `moneyflow` 重算净流入一致。
- SW L2 成员覆盖率一致。
- 派生 `sme_sector_orderflow_daily` 在两种 source mode 下结果一致或差异可解释。

只有 source parity 通过，才允许发布 standalone integration package。

### 27.10 文档影响

因此，本文中所有 “只读 `smartmoney.*`” 的表达都应理解为：

```text
当前 IFA 主环境的 source optimization
而不是 SME 的生产必要依赖
```

SME 的生产必要依赖是：

```text
TuShare Pro credentials
PostgreSQL sme schema
ifa.cli sme etl prefill / incremental
SME source resolver
SME data contracts and unit registry
```

---

## 28. 参考来源

- TuShare Pro `moneyflow`：个股资金流向，提供小/中/大/特大单买卖金额、净流入，数据开始于 2010 年，金额单位万元。
  <https://tushare.pro/document/2?doc_id=170>
- TuShare Pro `index_member_all`：申万行业 L1/L2/L3 成分，含 `in_date/out_date`。
  <https://tushare.pro/document/2?doc_id=335>
- TuShare Pro `stk_factor_pro`：股票每日技术面因子，含价格、成交额、换手、估值、市值、复权字段。
  <https://tushare.pro/document/2?doc_id=328>
- TuShare Pro 权限与数据列表：列示 `top_list`、`top_inst`、`margin`、`margin_detail`、`block_trade`、`moneyflow` 等接口更新节奏和权限要求。
  <https://tushare.pro/document/1?doc_id=108>
