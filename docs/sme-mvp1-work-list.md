# SME MVP-1 工作清单 — 基于本地 SmartMoney 只读数据

> **状态**: Implementation Work List
> **日期**: 2026-05-06
> **目标阶段**: SME MVP-1 / 核心资金画像与每日生产级流水线
> **数据模式**: `co-located` / `source_mode=prefer_smartmoney`
> **核心约束**: 本阶段优先复用本地 `smartmoney.*` 原始表，只读，不写旧表；所有 SME 新产物写入 `sme.*`。
> **质量目标**: 虽然是 MVP-1，但按 daily production-grade 标准建设：幂等、可重跑、可审计、可阻断 wrong result、可被后续模型和第三方集成稳定消费。

---

## 1. MVP-1 范围定义

### 1.1 本阶段必须完成

MVP-1 不是模型调参阶段，而是 SME 的生产数据地基和核心资金画像阶段。必须交付：

1. `sme` schema 与最小生产表。
2. 只读 `smartmoney.*` source resolver。
3. 单位 registry 和数据 contract。
4. source audit / storage audit / ETL run audit。
5. PIT SW daily membership materialization。
6. 个股资金画像：`sme.sme_stock_orderflow_daily`。
7. SW L2 板块资金画像：`sme.sme_sector_orderflow_daily`。
8. SW L2 扩散画像：`sme.sme_sector_diffusion_daily`。
9. SW L2 状态机：`sme.sme_sector_state_daily`。
10. 前向标签：`sme.sme_labels_daily`。
11. 统一 CLI：`ifa.cli sme doctor / init-schema / etl backfill / etl incremental / compute / labels / status`。
12. 北京时间 22:40 daily incremental 和 23:10 same-day brief 可跑。
13. 单元测试、golden tests、数据质量阻断规则。

### 1.2 本阶段暂不做

以下不进入 MVP-1 主交付，可预留接口：

- XGBoost / RF 正式训练。
- 模型调参和 promote。
- 第三方 standalone `sme_raw_*` prefill。
- 复杂 HTML 资金雷达报告。
- LLM 解释。
- Stock Edge/TA 正式接入。

但 MVP-1 必须为这些后续阶段提供稳定表、数据口径和 CLI 契约。

---

## 2. 生产级验收标准

MVP-1 完成后必须满足：

```text
1. 从 2021-01-01 到最新交易日完成核心派生表 backfill。
2. 新增 SME PostgreSQL 存储 < 10GB。
3. 每日 22:40 incremental 可幂等运行，23:10 简报可读取同一观察交易日。
4. 同一日期重复运行结果稳定。
5. 核心源缺失或单位异常会阻断 production 输出。
6. 所有金额字段统一为 _yuan。
7. 所有训练标签有 horizon 和 as-of 边界。
8. 所有表可追溯 source_snapshot_id / run_id / computed_at。
9. `uv run pytest tests/sme -q` 通过。
10. `ifa.cli sme status --json` 能输出机器可读健康状态。
```

---

## 3. 工作分解总览

| 优先级 | 模块 | 目标 | 完成标志 |
|---|---|---|---|
| P0 | Schema | 建立 `sme` 生产表 | Alembic upgrade 通过 |
| P0 | Units | 单位 registry | 所有金额转换集中管理 |
| P0 | Data contracts | 数据契约和阻断规则 | `sme doctor` 可检查 |
| P0 | Source resolver | 只读 smartmoney gateway | 不 import SmartMoney 代码 |
| P0 | Audit | ETL/source/storage audit | 每次运行有记录 |
| P1 | Membership | PIT SW daily member | 覆盖率可查 |
| P1 | Stock flow | 个股资金画像 | 2021 至今可 backfill |
| P1 | Sector flow | SW L2 聚合资金画像 | 每日每板块可追溯 |
| P1 | Diffusion | 扩散指标 | 识别龙头/中军/尾部 |
| P1 | State | 状态机 | 板块状态每日可查 |
| P1 | Labels | 前向标签 | 1/3/5/10/20d 可生成 |
| P1 | CLI | 统一入口 | daily run 一条命令 |
| P1 | Tests | 阻断 wrong result | golden tests 通过 |
| P1 | Ops | 22:40 增量 / 23:10 简报 | 可每天无人值守运行，非交易日 structured skip |

---

## 4. 详细任务清单

### 4.1 Schema 与 migration

**目标**: 新建 `sme` schema 和 MVP-1 所需表。

文件建议：

```text
alembic/versions/<rev>_sme_mvp1_schema.py
ifa/families/sme/db/schema.py
```

表清单：

```text
sme.sme_unit_registry
sme.sme_data_contracts
sme.sme_etl_runs
sme.sme_source_audit_daily
sme.sme_storage_audit_daily
sme.sme_sw_member_daily
sme.sme_stock_orderflow_daily
sme.sme_sector_orderflow_daily
sme.sme_sector_diffusion_daily
sme.sme_sector_state_daily
sme.sme_labels_daily
```

DDL 要求：

- dense daily 表按年分区，或至少预留分区策略。
- 金额字段用 `BIGINT`，单位元。
- 比率、分位、概率用 `DOUBLE PRECISION`。
- 核心主键：

```text
sme_stock_orderflow_daily:   (trade_date, ts_code)
sme_sector_orderflow_daily:  (trade_date, l2_code)
sme_sector_diffusion_daily:  (trade_date, l2_code)
sme_sector_state_daily:      (trade_date, l2_code)
sme_labels_daily:            (trade_date, l2_code, horizon)
```

索引：

```text
BRIN(trade_date) for dense daily tables
BTREE(ts_code, trade_date)
BTREE(l2_code, trade_date)
BTREE(run_id)
BTREE(quality_flag)
```

验收：

```bash
uv run alembic upgrade head
uv run alembic current
uv run python -m ifa.cli sme doctor --check schema
```

### 4.2 SME package scaffold

**目标**: 新建独立 SME family，不依赖 SmartMoney Python 代码。

目录：

```text
ifa/families/sme/
  __init__.py
  db/
    read_gateway.py
    write_gateway.py
    audit.py
    locks.py
  data/
    units.py
    contracts.py
    calendar.py
    source_resolver.py
  features/
    membership.py
    stock_orderflow.py
    sector_orderflow.py
    diffusion.py
    state_machine.py
  labels/
    forward.py
  etl/
    runner.py
    backfill.py
    incremental.py
  cli.py
  params/
    sme_v0.1.yaml
```

硬性检查：

```bash
rg "ifa\\.families\\.smartmoney" ifa/families/sme tests/sme
```

验收：无结果。

### 4.3 Source resolver 与只读 gateway

**目标**: 当前阶段优先读 `smartmoney.*`，但通过 logical source，不写死业务 SQL。

Logical sources：

```text
moneyflow
daily
daily_basic
sw_daily
sw_member
moneyflow_hsgt
margin
top_list
top_inst
block_trade
limit_list_d
```

MVP-1 source mode：

```yaml
data:
  source_mode: prefer_smartmoney
  allow_smartmoney_readonly: true
  require_sme_raw_for_production: false
```

Gateway 要求：

- 只暴露 `SELECT`。
- 所有日期条件必须显式。
- 所有结果带 source table metadata。
- 若 `smartmoney.*` 不存在，本阶段给出清晰错误；standalone 留到后续。

验收：

```bash
uv run python -m ifa.cli sme doctor --check sources --source-mode prefer_smartmoney --json
```

### 4.4 单位 registry

**目标**: 所有单位转换集中管理，杜绝散落 `* 10000`。

文件：

```text
ifa/families/sme/data/units.py
```

表：

```text
sme.sme_unit_registry
```

最小 registry：

| source | field | source unit | target field | factor |
|---|---|---|---|---:|
| `moneyflow` | `buy_sm_amount` | 万元 | `buy_sm_amount_yuan` | 10000 |
| `moneyflow` | `sell_sm_amount` | 万元 | `sell_sm_amount_yuan` | 10000 |
| `moneyflow` | `buy_md_amount` | 万元 | `buy_md_amount_yuan` | 10000 |
| `moneyflow` | `sell_md_amount` | 万元 | `sell_md_amount_yuan` | 10000 |
| `moneyflow` | `buy_lg_amount` | 万元 | `buy_lg_amount_yuan` | 10000 |
| `moneyflow` | `sell_lg_amount` | 万元 | `sell_lg_amount_yuan` | 10000 |
| `moneyflow` | `buy_elg_amount` | 万元 | `buy_elg_amount_yuan` | 10000 |
| `moneyflow` | `sell_elg_amount` | 万元 | `sell_elg_amount_yuan` | 10000 |
| `moneyflow` | `net_mf_amount` | 万元 | `net_mf_amount_yuan` | 10000 |
| `daily` | `amount` | 千元 | `amount_yuan` | 1000 |
| `daily_basic` | `total_mv` | 万元 | `total_mv_yuan` | 10000 |
| `daily_basic` | `circ_mv` | 万元 | `circ_mv_yuan` | 10000 |

验收：

```bash
uv run pytest tests/sme/test_units.py -q
rg "\\* *10000|\\* *1000" ifa/families/sme
```

允许的乘法只能在 `units.py` 和测试 fixture 中出现。

### 4.5 Data contracts 与 doctor

**目标**: 每个表有 contract，能阻断 wrong result。

文件：

```text
ifa/families/sme/data/contracts.py
```

表：

```text
sme.sme_data_contracts
```

最小 checks：

```text
not_null primary keys
amount_yuan >= 0
probability/rank/percentile in [0,1]
coverage_ratio in [0,1]
trade_date <= as_of_trade_date when applicable
row_count above min threshold
sector coverage above threshold
unit registry mappings exist
net_mf_amount reconciliation within tolerance
```

CLI：

```bash
uv run python -m ifa.cli sme doctor --check schema,sources,units,contracts
uv run python -m ifa.cli sme doctor --check data --date 2026-05-06
```

验收：

- 正常数据返回 exit code `0`。
- 缺核心源返回 exit code `2` 或 `3`。
- 单位 registry 缺失返回 exit code `2`。

### 4.6 ETL run / source / storage audit

**目标**: 每次 backfill/incremental 都可追溯。

表：

```text
sme.sme_etl_runs
sme.sme_source_audit_daily
sme.sme_storage_audit_daily
```

每次运行记录：

```text
run_id
run_mode
source_mode
as_of_trade_date
date_range
row_counts
quality_summary
storage_before/after
status
errors
```

source audit 指标：

```text
row_count
distinct_stock_count
distinct_l2_count
min_date/max_date
null_rate_json
coverage_status
```

storage audit 指标：

```text
schema bytes
table bytes
index bytes
row count
storage status: ok/warn/block
```

验收：

```bash
uv run python -m ifa.cli sme etl audit --date 2026-05-06 --json
uv run python -m ifa.cli sme status --json
```

### 4.7 PIT SW daily membership

**目标**: 用 `smartmoney.raw_sw_member` / `sw_member_monthly` 生成 SME 自己的 daily PIT 成员表。

表：

```text
sme.sme_sw_member_daily
```

字段：

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
source_mode
source_snapshot_id
quality_flag
computed_at
```

逻辑：

```text
in_date <= trade_date
AND (out_date IS NULL OR out_date > trade_date)
```

MVP fallback：

- 如果 `raw_sw_member` 缺失但 `sw_member_monthly` 存在，允许 fallback。
- fallback 必须 `quality_flag = degraded`。
- production 默认优先 exact `in_date/out_date`。

验收：

```bash
uv run python -m ifa.cli sme compute membership --start 2021-01-01 --end 2026-05-06
uv run pytest tests/sme/test_pit_membership.py -q
```

### 4.8 个股资金画像

**目标**: 生成 `sme.sme_stock_orderflow_daily`。

输入：

```text
smartmoney.raw_moneyflow
smartmoney.raw_daily
smartmoney.raw_daily_basic
```

核心计算：

```text
sm_net_yuan = buy_sm_amount_yuan - sell_sm_amount_yuan
md_net_yuan = buy_md_amount_yuan - sell_md_amount_yuan
lg_net_yuan = buy_lg_amount_yuan - sell_lg_amount_yuan
elg_net_yuan = buy_elg_amount_yuan - sell_elg_amount_yuan
main_net_yuan = lg_net_yuan + elg_net_yuan
retail_net_yuan = sm_net_yuan + md_net_yuan
net_recomputed_yuan = sm_net_yuan + md_net_yuan + lg_net_yuan + elg_net_yuan
main_net_ratio = main_net_yuan / amount_yuan
retail_net_ratio = retail_net_yuan / amount_yuan
elg_net_ratio = elg_net_yuan / amount_yuan
```

行为标签：

```text
true_accumulation
silent_accumulation
retail_chase
distribution
panic_absorb
fake_inflow
```

质量要求：

- `amount_yuan = 0` 时 ratio 为 `NULL`，不能填 0。
- `net_recomputed_yuan` 与 `net_mf_amount_yuan` 系统性不一致则阻断。
- 每日 row count 低于阈值则阻断。

验收：

```bash
uv run python -m ifa.cli sme compute stock-flow --date 2026-05-06
uv run pytest tests/sme/test_stock_orderflow.py -q
```

### 4.9 SW L2 板块资金画像

**目标**: 生成 `sme.sme_sector_orderflow_daily`。

输入：

```text
sme.sme_stock_orderflow_daily
sme.sme_sw_member_daily
smartmoney.raw_sw_daily
```

聚合原则：

- 金额字段先求和。
- ratio 用聚合金额除以聚合成交额。
- 统计 `member_count`、`matched_stock_count`、`coverage_ratio`。
- 保留 equal-weight return、amount-weight return、SW index return。

关键字段：

```text
sector_amount_yuan
sm_net_yuan
md_net_yuan
lg_net_yuan
elg_net_yuan
main_net_yuan
retail_net_yuan
net_mf_amount_yuan
main_net_ratio
retail_net_ratio
flow_breadth
main_positive_breadth
top5_main_net_share
leader_ts_code
leader_name
```

验收：

```bash
uv run python -m ifa.cli sme compute sector-flow --date 2026-05-06
uv run pytest tests/sme/test_sector_orderflow.py -q
```

### 4.10 扩散画像

**目标**: 生成 `sme.sme_sector_diffusion_daily`，识别资金是否从龙头扩散到中军和后排。

输入：

```text
sme_stock_orderflow_daily
sme_sector_orderflow_daily
sme_sw_member_daily
```

计算：

```text
leader_return_1d/3d/5d
median_member_return_5d
tail_member_return_5d
leader_to_median_spread
median_to_tail_spread
flow_breadth_1d/3d/5d/10d
diffusion_slope_5_20
diffusion_acceleration
main_flow_dispersion
diffusion_phase
diffusion_score
```

阶段：

```text
leader_only
leader_confirmed
midcap_following
broad_diffusion
tail_chase
diffusion_breakdown
```

验收：

```bash
uv run python -m ifa.cli sme compute diffusion --date 2026-05-06
uv run pytest tests/sme/test_diffusion.py -q
```

### 4.11 状态机

**目标**: 生成 `sme.sme_sector_state_daily`。

状态：

```text
dormant
ignition
diffusion
acceleration
climax
distribution
cooldown
rebound
```

输入：

```text
sme_sector_orderflow_daily
sme_sector_diffusion_daily
smartmoney.raw_sw_daily
```

输出：

```text
current_state
state_score
state_confidence
transition_hint
risk_flags_json
evidence_json
quality_flag
```

MVP 状态机先用规则，参数 YAML 化。

验收：

```bash
uv run python -m ifa.cli sme compute state --date 2026-05-06
uv run pytest tests/sme/test_state_machine.py -q
```

### 4.12 前向标签

**目标**: 生成 `sme.sme_labels_daily`，为后续 OOS/OOC 和模型训练准备。

horizon：

```text
1, 3, 5, 10, 20
```

标签：

```text
future_return
future_excess_return_vs_market
future_excess_return_vs_l1
future_rank_pct
future_top_quantile_label
future_heat_delta
future_heat_up_label
future_drawdown
future_max_runup
turnover_adjusted_return
label_quality_flag
```

重要规则：

- 标签只对已经 mature 的日期生成。
- 如果 horizon=20，则最近 20 个交易日不能生成完整标签。
- 标签表不能被 report-time 使用为当前预测证据。

验收：

```bash
uv run python -m ifa.cli sme labels --start 2021-01-01 --end 2026-04-10
uv run pytest tests/sme/test_labels.py -q
```

### 4.13 统一 CLI

**目标**: 所有日常操作走 `ifa.cli sme`。

命令树 MVP：

```text
ifa.cli sme doctor
ifa.cli sme init-schema
ifa.cli sme etl audit
ifa.cli sme etl backfill
ifa.cli sme etl incremental
ifa.cli sme compute membership
ifa.cli sme compute stock-flow
ifa.cli sme compute sector-flow
ifa.cli sme compute diffusion
ifa.cli sme compute state
ifa.cli sme labels
ifa.cli sme status
```

通用参数：

```text
--run-mode test|manual|production
--source-mode prefer_smartmoney
--start
--end
--date
--as-of auto|YYYY-MM-DD
--run-id
--dry-run
--json
--fail-fast
--allow-degraded
```

验收：

```bash
uv run python -m ifa.cli sme --help
uv run python -m ifa.cli sme etl incremental --help
uv run python -m ifa.cli sme status --json
```

### 4.14 Backfill pipeline

**目标**: 一条命令完成 MVP-1 2021 至今派生表 backfill。

命令：

```bash
uv run python -m ifa.cli sme etl backfill \
  --source-mode prefer_smartmoney \
  --start 2021-01-01 \
  --end auto \
  --run-mode manual \
  --workers 4 \
  --resume \
  --max-storage-gb 10
```

内部顺序：

```text
doctor schema/sources/units
audit source coverage
compute membership
compute stock-flow
compute sector-flow
compute diffusion
compute state
compute mature labels
storage audit
status summary
```

验收：

- 中断后 `--resume` 可继续。
- 重跑相同日期不重复插入。
- 每个步骤 row count 进入 `sme_etl_runs`。

### 4.15 每日 22:40 incremental / 23:10 brief

**目标**: 每天自动处理北京时间运行日对应的交易日；非交易日 clean skip。

命令：

```bash
TZ=Asia/Shanghai uv run python -m ifa.cli sme etl incremental \
  --source-mode prefer_smartmoney \
  --as-of auto \
  --run-mode production \
  --compute \
  --labels \
  --fail-on-core-missing \
  --json
```

处理日期：

```text
交易日北京时间 22:40：处理当天已完成交易日。
交易日北京时间 23:10：生成当天 SME 简报。
非交易日：输出 `status=non_trade_day` / `action=skip` 并 exit 0。
```

质量门：

- 核心 source 缺失：blocked。
- row count 异常：blocked。
- coverage 低于阈值：blocked 或 degraded，按配置。
- storage > 10GB：block optional work。

验收：

```bash
uv run python -m ifa.cli sme etl incremental --as-of auto --dry-run --json
```

### 4.16 Tests

**目标**: MVP-1 每个关键口径都有测试。

测试目录：

```text
tests/sme/
  test_units.py
  test_contracts.py
  test_source_resolver.py
  test_pit_membership.py
  test_stock_orderflow.py
  test_sector_orderflow.py
  test_diffusion.py
  test_state_machine.py
  test_labels.py
  test_cli.py
  test_incremental_idempotency.py
  test_golden_orderflow.py
```

最重要的测试：

- 单位转换。
- PIT 成员。
- 个股 net flow reconciliation。
- 板块聚合金额一致性。
- ratio denominator 为 0 时不误填。
- 同一日期 incremental 重跑幂等。
- 缺核心源阻断。

验收：

```bash
uv run pytest tests/sme -q
```

### 4.17 文档和 runbook

**目标**: 每天跑的人知道怎么处理失败。

新增/更新：

```text
docs/sme-mvp1-runbook.md
docs/sme-mvp1-work-list.md
```

Runbook 必须包含：

- 首次 backfill 命令。
- 每日 incremental 命令。
- 常见失败和处理。
- 如何看 `sme status`。
- 如何判断 blocked/degraded/stale。
- 如何查某日某板块资金聚合。
- 如何重算单日。

---

## 5. 推荐实施顺序

### Day 1：地基

1. 新建 SME package scaffold。
2. 新建 Alembic migration。
3. 实现 unit registry。
4. 实现 source resolver。
5. 实现 `sme doctor --check schema,sources,units`。
6. 加基础测试。

完成标志：

```bash
uv run alembic upgrade head
uv run python -m ifa.cli sme doctor --check schema,sources,units
uv run pytest tests/sme/test_units.py tests/sme/test_source_resolver.py -q
```

### Day 2：membership + audit

1. 实现 `sme_sw_member_daily`。
2. 实现 source audit。
3. 实现 storage audit。
4. 实现 `sme status --json`。

完成标志：

```bash
uv run python -m ifa.cli sme compute membership --start 2021-01-01 --end auto
uv run python -m ifa.cli sme etl audit --start 2021-01-01 --end auto
uv run python -m ifa.cli sme status --json
```

### Day 3：stock orderflow

1. 实现个股资金画像。
2. 实现 reconciliation checks。
3. 建 golden samples。

完成标志：

```bash
uv run python -m ifa.cli sme compute stock-flow --date 2026-05-06
uv run pytest tests/sme/test_stock_orderflow.py tests/sme/test_golden_orderflow.py -q
```

### Day 4：sector orderflow

1. 实现 SW L2 聚合。
2. 实现 coverage。
3. 实现 leader/top5 concentration。

完成标志：

```bash
uv run python -m ifa.cli sme compute sector-flow --date 2026-05-06
uv run pytest tests/sme/test_sector_orderflow.py -q
```

### Day 5：diffusion + state

1. 实现扩散画像。
2. 实现规则状态机。
3. YAML 化阈值。

完成标志：

```bash
uv run python -m ifa.cli sme compute diffusion --date 2026-05-06
uv run python -m ifa.cli sme compute state --date 2026-05-06
uv run pytest tests/sme/test_diffusion.py tests/sme/test_state_machine.py -q
```

### Day 6：labels + backfill

1. 实现 labels。
2. 实现 backfill orchestrator。
3. 跑小窗口。

完成标志：

```bash
uv run python -m ifa.cli sme etl backfill --start 2026-04-01 --end 2026-05-06 --resume
uv run pytest tests/sme/test_labels.py -q
```

### Day 7：production-grade daily run

1. 实现 incremental。
2. 实现 lock 和 idempotency。
3. 实现 runbook。
4. 跑 2021 至今 backfill。

完成标志：

```bash
uv run python -m ifa.cli sme etl backfill --start 2021-01-01 --end auto --resume --max-storage-gb 10
uv run python -m ifa.cli sme etl incremental --as-of auto --dry-run --json
uv run pytest tests/sme -q
```

---

## 6. Definition of Done

MVP-1 完成的硬标准：

```text
[ ] Alembic migration merged and applied
[ ] SME package has no SmartMoney Python imports
[ ] Unit registry exists and tests pass
[ ] Source resolver reads local smartmoney.* readonly
[ ] Data contracts block core wrong result cases
[ ] sme_sw_member_daily backfilled
[ ] sme_stock_orderflow_daily backfilled
[ ] sme_sector_orderflow_daily backfilled
[ ] sme_sector_diffusion_daily backfilled
[ ] sme_sector_state_daily backfilled
[ ] sme_labels_daily generated for mature dates
[ ] etl_runs/source_audit/storage_audit populated
[ ] CLI help and JSON status work
[ ] Incremental dry-run works
[ ] Incremental real run works for one date
[ ] Re-running same date is idempotent
[ ] tests/sme passes
[ ] Storage audit < 10GB
[ ] Runbook exists
[ ] `ifa sme market-structure` can explain daily market structure from orderflow/breadth/state
[ ] `sme_market_structure_daily` persists strategy snapshots for walk-forward tuning
[ ] `sme_strategy_eval_daily` joins persisted strategy buckets to forward labels
```

---

## 7. Daily Run 目标命令

MVP-1 完成后，每天生产命令应固定为：

```bash
TZ=Asia/Shanghai uv run python -m ifa.cli sme etl incremental \
  --source-mode prefer_smartmoney \
  --as-of auto \
  --run-mode production \
  --compute \
  --labels \
  --fail-on-core-missing \
  --json
```

成功 JSON 至少包含：

```json
{
  "status": "success",
  "run_id": "...",
  "as_of_trade_date": "2026-05-06",
  "source_mode": "prefer_smartmoney",
  "quality_flag": "ok",
  "freshness_status": "fresh",
  "row_counts": {
    "sme_stock_orderflow_daily": 5300,
    "sme_sector_orderflow_daily": 100,
    "sme_sector_diffusion_daily": 100,
    "sme_sector_state_daily": 100
  },
  "storage_gb": 4.8
}
```

失败 JSON 至少包含：

```json
{
  "status": "blocked",
  "exit_code": 2,
  "as_of_trade_date": "2026-05-06",
  "blocking_reason": "core source raw_moneyflow missing or low row count",
  "safe_to_use_previous_snapshot": true
}
```

日频增量成功后，可直接生成资金结构策略快照：

```bash
uv run python -m ifa.cli sme market-structure --date auto --json
uv run python -m ifa.cli sme market-structure --date auto --persist --json
uv run python -m ifa.cli sme compute strategy-eval --start 2026-01-01 --end auto --json
uv run python -m ifa.cli sme tuning-ready --start 2026-01-01 --end auto --json
uv run python -m ifa.cli sme tune bucket-review --start 2026-01-01 --end auto --json
```

第三方平台可以接入两个无调度假设的脚本：

```bash
scripts/sme_incremental_2240.sh
scripts/sme_nightly_tune_2300.sh
```

可通过环境变量覆盖 nightly 调参窗口：

```bash
SME_TUNE_START=2026-01-01 SME_TUNE_MIN_SAMPLE_DAYS=60 scripts/sme_nightly_tune_2300.sh
SME_MARKET_STRUCTURE_PROFILE=mvp1_ytd_candidate scripts/sme_nightly_tune_2300.sh
SME_MARKET_STRUCTURE_PROFILE=mvp1_ytd_candidate SME_TUNE_PROMOTE_PROFILE=mvp1_ytd_candidate SME_TUNE_APPLY_PROMOTION=1 scripts/sme_nightly_tune_2300.sh
```

最终给客户/投顾界面的报告只输出结论，不展示推导过程：

```bash
uv run python -m ifa.cli sme market-structure --date auto --client
uv run python -m ifa.cli sme market-structure --date auto --client --json
```

`--client` 输出层只保留：

- 一句话底线判断
- 当前重点方向
- 二级观察方向
- 防御/脱敏方向
- 修复弹性方向
- 回避/减仓方向
- 拥挤风险
- 未来 1-3 个交易日三情景结论

如果当日已有 LLM/联网外部变量摘要，作为可复现输入传入，不写入核心 SME 数据层：

```bash
uv run python -m ifa.cli sme market-structure \
  --date auto \
  --external-summary "美元走强、油价上行、政策窗口临近，市场关注风险资产定价和顺周期修复。" \
  --json
```

MVP1 解释范围：

- 用指数涨跌、全市场成交额、涨跌家数说明市场温度，但不把指数涨跌作为强弱唯一依据。
- 用 SW L2 主力净流入/流出、主力净流入率、价格涨跌、扩散、状态、头部集中度分类资金行为。
- 输出主流入、主流出、涨幅强但流入不足、跌幅较大但流出收敛/压制后修复、一级/二级/脱敏/修复弹性方向、资金状态和未来 1-3 个交易日情景推演。
- 暂不持久化真实分时走势；需要分钟/实时快照入库后再升级。

---

## 8. 风险清单

| 风险 | 影响 | 防线 |
|---|---|---|
| 单位错 | wrong result | unit registry + golden tests |
| PIT 成员错 | 前视偏差 | daily member + PIT tests |
| raw_moneyflow 行数异常 | 资金画像失真 | source audit blocking |
| 重跑不幂等 | daily job 污染 | PK/upsert + idempotency tests |
| 存储超过 10GB | 生产不可控 | storage audit |
| 标签泄漏 | 模型无效 | mature horizon only |
| CLI 分散 | 第三方难集成 | only `ifa.cli sme` |
| SmartMoney 代码耦合 | 未来 standalone 难 | import scan |
