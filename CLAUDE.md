# IFA SmartMoney — Claude Code 上下文

> **更新**: 2026-05-01
> **当前阶段**: A 阶段全部完成 → 下一步 B1

---

## 项目概览

**目标**: 将 SmartMoney 晚报的板块源从 DC（东财概念）全面迁移到 SW（申万 L2），并完成报告各节的重构与 LLM/ML 集成。

**技术栈**: Python 3.12 · uv · PostgreSQL 16 (port 55432) · SQLAlchemy 2.0 · Jinja2

**DB 连接**: `get_engine()` from `ifa.core.db` → `smartmoney` schema

---

## 为什么要换源（关键背景）

| 源 | 问题 |
|----|------|
| DC (东财概念) `raw_dc_member` | 只有 ~18 天历史，无法做时间正确的 PIT 查询 |
| THS (同花顺) `ths_member` | 只有当前快照，无 in_date/out_date，会引入前视偏差 |
| **SW (申万) `index_member_all`** | ✅ 完整历史 in_date/out_date，回溯至 1993 年 |

---

## 三阶段路线图

```
A 阶段（数据原料）✅      B 阶段（改配方）⬅ 当前    C 阶段（用新配方加工）
─────────────────────   ─────────────────────        ─────────────────────
A1. SW 成员 ETL ✅      B1. sector_flow_sw_l2 ⬅ 起点  C1. 跑板块资金流聚合
A2. 拉 SW 成员数据 ✅   B2. factors/flow.py            C2. 跑 compute（因子/状态/信号）
A3. raw backfill  ✅    B3. factors/leader.py           C3. 训练回测 2021-2025
A4. raw全覆盖    ✅     B4. data.py                    C4. 训练 RF + XGB 模型
                         B5. transition_matrix          C5. OOS 验证 2025-2026
                         B6. evening.py 重构            C6. 生成最终晚报
                         B7. LLM aug 集成
                         B8. ML §10 双模型
                         B9. run-mode badge
```

---

## A 阶段完成状态（全部 ✅）

### ✅ A1: SW 成员 ETL + 迁移
- `ifa/families/smartmoney/etl/sw_member_fetcher.py` — 完整 ETL
- `alembic/versions/c2e8f1a40b56_smartmoney_sw_member_tables.py` — 建表
- `alembic/versions/2d0c597983b9_merge_*.py` — 合并 heads
- `alembic/versions/a9f3c2e17d84_widen_kpl_list_numerics.py` — 放宽 NUMERIC 精度（已 apply）

### ✅ A2: 初次拉取 SW 成员数据
- `smartmoney.raw_sw_member`: 5,847 行（含完整 in_date/out_date）
- `smartmoney.sw_member_monthly`: 327,547 行，65 个月快照（2021-01 → 2026-05）
- 月度快照逻辑: `in_date <= snapshot_month AND (out_date IS NULL OR out_date > snapshot_month)`

### ✅ A3+A4: Raw backfill（2021-01 → 2025-10-31）
- 使用 `scripts/fast_backfill.py` 完成（877天，1585万行，195分钟）
- 跳过 `raw_dc_member`（已被 SW 替代）
- `raw_sw_daily` / `raw_index_daily` 按 code 批量拉（31+8次 API 而非 39×N_days）

### Alembic 当前 head
```
a9f3c2e17d84 (head)
```

---

## 数据库覆盖率矩阵（2026-05-01 实测）

### 原始数据层 — 核心（2021全覆盖）✅

| 表 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| raw_daily | 242 | 242 | 242 | 242 | 243 | 77 |
| raw_daily_basic | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_moneyflow | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_margin | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_top_inst | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_sw_daily | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_index_daily | 243 | 242 | 242 | 242 | 243 | 77 |
| raw_kpl_list | 241 | 242 | 242 | 242 | 243 | 77 |
| raw_top_list | 242 | 242 | 241 | 242 | 243 | 77 |
| raw_limit_list_d | 241 | 242 | 241 | 242 | 243 | 77 |
| raw_block_trade | 240 | 242 | 242 | 242 | 243 | 77 |
| raw_moneyflow_hsgt | 233 | 236 | 231 | 233 | 237 | 75 |

### 原始数据层 — TuShare 无历史（只有近期，非缺失）

| 表 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|---|---|
| raw_moneyflow_ind_dc | 0 | 0 | 73 | 242 | 243 | 77 |
| raw_ths_hot | 0 | 0 | 62 | 241 | 243 | 77 |
| raw_moneyflow_ind_ths | 0 | 0 | 0 | 73 | 242 | 77 |
| raw_kpl_concept | 0 | 0 | 0 | 53 | 242 | 77 |
| raw_kpl_concept_cons | 0 | 0 | 0 | 57 | 242 | 77 |
| raw_dc_hot | 0 | 0 | 0 | 192 | 239 | 77 |
| raw_dc_index | 0 | 0 | 0 | 8 | 243 | 77 |

### 已弃用 / 无数据

| 表 | 说明 |
|---|---|
| raw_dc_member | 已弃用 → SW 替代（仅 18 天近期数据，勿用） |
| raw_cyq_chips | 筹码分布，未启用 |

### 计算层（B+C 阶段产出，当前仅有近期）

| 表 | 现状 | 目标 |
|---|---|---|
| factor_daily | 2023部分+2025近期 | C2 跑全 2021-2026 |
| sector_state_daily | 2025近期 | C2 跑全 |
| market_state_daily | 2025近期 | C2 跑全 |
| stock_signals_daily | 空 | C4 训练后 C5 产出 |
| predictions_daily | 空 | C4 训练后产出 |
| **sector_moneyflow_sw_daily** | **不存在** | **B1 建表+聚合** |

---

## B 阶段详细规格（B1–B9）

### B1: `sector_flow_sw_l2.py`（新建）⬅ 从这里开始
**路径**: `ifa/families/smartmoney/etl/sector_flow_sw_l2.py`

**Step 1 — 新 Alembic migration 建表** `sector_moneyflow_sw_daily`:
```sql
CREATE TABLE smartmoney.sector_moneyflow_sw_daily (
    trade_date     DATE        NOT NULL,
    l2_code        VARCHAR(12) NOT NULL,
    l2_name        VARCHAR(64),
    l1_code        VARCHAR(12),
    l1_name        VARCHAR(64),
    net_amount     NUMERIC,          -- SUM(net_mf_amount) 单位: 万元
    buy_elg_amount NUMERIC,          -- SUM(buy_elg_amount) 超大单买入
    sell_elg_amount NUMERIC,         -- SUM(sell_elg_amount)
    buy_lg_amount  NUMERIC,          -- SUM(buy_lg_amount) 大单买入
    sell_lg_amount NUMERIC,
    stock_count    INTEGER,          -- COUNT(DISTINCT ts_code)
    PRIMARY KEY (trade_date, l2_code)
);
CREATE INDEX ON smartmoney.sector_moneyflow_sw_daily (trade_date);
```

**Step 2 — 聚合函数** (idempotent，支持按日或批量):
```sql
INSERT INTO smartmoney.sector_moneyflow_sw_daily
    (trade_date, l2_code, l2_name, l1_code, l1_name,
     net_amount, buy_elg_amount, sell_elg_amount,
     buy_lg_amount, sell_lg_amount, stock_count)
SELECT
    m.trade_date,
    s.l2_code, s.l2_name, s.l1_code, s.l1_name,
    SUM(m.net_mf_amount)     AS net_amount,
    SUM(m.buy_elg_amount)    AS buy_elg_amount,
    SUM(m.sell_elg_amount)   AS sell_elg_amount,
    SUM(m.buy_lg_amount)     AS buy_lg_amount,
    SUM(m.sell_lg_amount)    AS sell_lg_amount,
    COUNT(DISTINCT m.ts_code) AS stock_count
FROM smartmoney.raw_moneyflow m
JOIN smartmoney.sw_member_monthly s
  ON m.ts_code = s.ts_code
  AND s.snapshot_month = date_trunc('month', m.trade_date)::date
WHERE m.trade_date = ANY(:dates)   -- 或 BETWEEN :start AND :end
GROUP BY m.trade_date, s.l2_code, s.l2_name, s.l1_code, s.l1_name
ON CONFLICT (trade_date, l2_code) DO UPDATE SET
    net_amount      = EXCLUDED.net_amount,
    buy_elg_amount  = EXCLUDED.buy_elg_amount,
    sell_elg_amount = EXCLUDED.sell_elg_amount,
    buy_lg_amount   = EXCLUDED.buy_lg_amount,
    sell_lg_amount  = EXCLUDED.sell_lg_amount,
    stock_count     = EXCLUDED.stock_count,
    l2_name         = EXCLUDED.l2_name,
    l1_code         = EXCLUDED.l1_code,
    l1_name         = EXCLUDED.l1_name
```

**Step 3 — CLI 入口**（加入 runner.py 的每日 ETL 链）:
- `aggregate_sector_flow_sw(engine, dates: list[date]) -> int` — 批量
- `aggregate_sector_flow_sw_for_date(engine, trade_date: date) -> int` — 单日

**C1 回填命令**（B1 完成后执行）:
```bash
uv run python -c "
from ifa.families.smartmoney.etl.sector_flow_sw_l2 import aggregate_sector_flow_sw
from ifa.core.db import get_engine
import datetime as dt
# 拉所有有 raw_moneyflow 的交易日
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    dates = [r[0] for r in c.execute(text(
        'SELECT DISTINCT trade_date FROM smartmoney.raw_moneyflow ORDER BY trade_date'
    ))]
n = aggregate_sector_flow_sw(eng, dates)
print(f'Done: {n} rows')
"
```

---

### B2: `factors/flow.py` 修改
- 现有: 从 `raw_moneyflow_ind_dc` 取板块资金流（DC 源）
- 新增: 从 `sector_moneyflow_sw_daily` 取 SW L2 路径
- 参数: `sector_source: str = 'sw_l2'`（默认换 SW）
- 保留 DC 路径作为 fallback（`sector_source='dc'`）

---

### B3: `factors/leader.py` + `factors/candidate.py` 修改
- 现有: 从 `raw_dc_member` 查板块成员
- 新增: 从 `sw_member_monthly` 查（PIT 正确）
  ```python
  snapshot_month = date_trunc('month', trade_date)
  WHERE snapshot_month = :sm AND l2_code = :sector_code
  ```

---

### B4: `data.py` 修改
- 所有 `load_sector_*` 函数默认 `sector_source='sw_l2'`
- `load_sector_structures()`: 已有 kpl fallback，改为优先用 SW L2 成员

---

### B5: `transition_matrix.py`（新建）
**路径**: `ifa/families/smartmoney/transition_matrix.py`

**逻辑**:
1. 从 `sector_state_daily` 读历史 phase 序列（7 种 phase）
2. 构建经验转移矩阵（7×7）
3. Bayesian 每板块调整（per-sector 历史 vs 全局先验）
4. LLM ±10% 微调钩子
5. 输出: `predict_next_phase(sector_code, current_phase, trade_date) -> dict[phase, prob]`

---

### B6: `evening.py` 各节重构

已完成的改动（勿重复）:
- ✅ 金额单位: 万→亿（`_fmt_amt` 默认 scale=1e8）
- ✅ intro 去重（`_section_head.html` 已渲染，模板不再输出）
- ✅ §05 高质净流入加 LLM 解读列
- ✅ §07 周期网格加 leader_name 注释
- ✅ run-mode badge（TEST/MANUAL/PRODUCTION）

**待做**（严格按规格）:

§02 — 10日资金面水位迷你折线图:
- 从 `factor_daily` 取最近 10 个交易日 `north_flow` + `net_amount` (SW L2 汇总)
- 渲染 SVG 迷你折线（嵌入 HTML inline）

§03/§04 — 每个板块 top-5 个股钻取:
- 排除非 A 股板块（富时罗素/MSCI/沪深300 成分是个股标签，不是行业）
- SW L2 板块 → 查 `sw_member_monthly` 成员 → 从 `factor_daily` 取个股数据 → top-5 by net_amount

§05 — 高质净流入门槛提高:
- 门槛: 净流入 ≥ 10亿 AND 超大单占比 ≥ 2%
- 去重: 同一板块只保留最高分那条

§06 — 拥挤度风险卡片改表格:
- 现有: 分散的 card UI
- 改为: 紧凑表格（板块 | 拥挤度分 | 资金分布 | 风险描述）

§07 — 7×N 相位轨迹矩阵 + 转移概率预测:
- 7 种 phase: 启动/加速/高潮/衰退/冷却/蛰伏/反弹
- 矩阵展示当前活跃板块最近 N 天的 phase 轨迹
- 每格加转移概率（来自 B5 transition_matrix）

§08 — 明日→下个交易日; 加板块内股票; 标注算法来源:
- 标题: "下个交易日操作建议"
- 每个推荐板块展开显示候选股
- 注明: "(RF模型)" 或 "(XGB模型)"

§09/§10 — 加术语定义解释框

§10 — 拆分双模型:
- 短线池 (1-3天): RandomForest
- 中长线池 (1-2月, 目标 +30~50%): XGBoost
- 分开展示，各标注算法和预期持仓周期

§11 — 加章节定义 + 术语词汇表

---

### B7: LLM aug 模块集成
6 个已写好但未集成的模块（路径待确认）:
- `concept_cluster` — 概念聚类
- `regime_classifier` — 市场体制识别
- `hypothesis_grader` — 假设评分
- `backtest_forensics` — 回测归因
- `policy_polarity` — 政策极性
- `counterfactual` — 反事实分析

集成到 `evening.py` 对应节，通过 `ctx.llm_aug` 传入。

---

### B8: 双 ML 模型 §10
- RandomForest: 短线因子（1-3日动量、资金流方向、连板热度）
- XGBoost: 中长线因子（周期位置、资金趋势、基本面代理）
- 模型参数文件: `models/params_v2026_05_{rf,xgb}.json`（C4 训练后冻结）

---

### B9: run-mode badge 解耦
- 新增环境变量 `IFA_REPORT_RUN_BADGE`（值: `test`/`manual`/`production`）
- 优先级: env var > DB profile 推断
- 默认: 无 env var 时从 DB URL 推断（localhost=test，其余=production）

---

## C 阶段规格

### C1: 跑 sector_moneyflow_sw_daily 回填（B1 完成后）
```bash
# 见 B1 的 C1 回填命令
# 预期: ~1169天 × 约100个SW L2板块 = ~116,900行，几分钟内完成（纯SQL聚合）
```

### C2: 跑 compute 全量回填（B2-B4 完成后）
```bash
# 从 2021-01-04 到今天，补跑 factor_daily / sector_state_daily / market_state_daily
uv run python -m ifa.cli backfill --family smartmoney --start 2021-01-04 --end 2026-04-30
```
⚠️ 注意: compute 历史数据有 `'content_type'` / `'trade_date'` KeyError 问题，B6 修 evening.py 时一并处理。

### C3: 训练回测 2021-2025
- OOS 窗口: 2021-01 → 2025-10（in-sample training）
- 滚动验证窗口设置待定

### C4: 训练 RF + XGB 模型，冻结 v2026_05
- 特征工程见 B8 规格
- 输出: `models/params_v2026_05_rf.json` + `models/params_v2026_05_xgb.json`

### C5: OOS 验证 2025-11 → 2026-04
- 用冻结模型跑 stock_signals_daily / predictions_daily

### C6: 生成最终晚报 2026-04-30
```bash
IFA_REPORT_RUN_BADGE=production uv run python -m ifa.cli report --family smartmoney --date 2026-04-30
```

---

## 数据库 Schema 快速参考

```
smartmoney 库:
  raw_daily               — 个股日行情
  raw_daily_basic         — 个股基本面日数据
  raw_moneyflow           — 个股资金流（主力/超大单/大单）核心！
  raw_sw_member           — SW成员全历史 PK(l1_code,ts_code,in_date)
  sw_member_monthly       — SW月度快照 PK(snapshot_month,l2_code,ts_code) 65个月
  sector_moneyflow_sw_daily — SW L2 板块日资金流汇总 ← B1 建
  factor_daily            — 每日因子（north_flow,net_amount,vol_ratio等）
  sector_state_daily      — 板块状态（phase,role,cycle_phase等）
  market_state_daily      — 市场整体状态
  stock_signals_daily     — 个股信号（ML输出）
  predictions_daily       — ML预测结果
  raw_kpl_list            — 涨停池（leader fallback）
  raw_sw_daily            — SW板块价格/成交（非资金流）
  raw_index_daily         — 指数日行情（上证/深证/创业板/科创板等8个）
  backtest_runs / backtest_metrics — 回测结果
  report_runs / report_judgments   — 报告记录
  etl_watermarks          — ETL 水位线
```

---

## 文件结构快速参考

```
ifa/families/smartmoney/
  evening.py              — 晚报主逻辑（B6 主战场）
  data.py                 — 数据加载层（B4 修改）
  transition_matrix.py    — 相位转移矩阵（B5 新建）
  etl/
    runner.py             — 每日 ETL runner
    raw_fetchers.py       — 所有原始数据拉取函数
    sw_member_fetcher.py  — SW成员 ETL（A1 完成）
    sector_flow_sw_l2.py  — SW L2 板块流聚合（B1 新建）
  factors/
    flow.py               — 资金流因子（B2 修改）
    leader.py             — 龙头识别（B3 修改）
    candidate.py          — 候选股（B3 修改）

ifa/core/render/templates/
  report.html             — 含 run-mode badge（已更新）
  styles.css              — badge 样式（已更新）
  _sm_quality_flow.html   — §05 含解读列（已更新）
  _sm_cycle_grid.html     — §07 含 leader_name（已更新）
  _sm_sector_structure.html — §08 板块结构（已更新）

scripts/
  fast_backfill.py        — 优化 raw 回填脚本（A4 用，已完成）
  check_raw_coverage.py   — raw backfill 进度查询

alembic/versions/
  c2e8f1a40b56_*.py       — raw_sw_member + sw_member_monthly
  2d0c597983b9_*.py       — merge heads
  a9f3c2e17d84_*.py       — 放宽 raw_kpl_list NUMERIC 精度（已 apply）
```

---

## 常用命令

```bash
# 查 raw 数据覆盖率
uv run python scripts/check_raw_coverage.py

# 查所有表年度覆盖（快速）
uv run python -c "
from ifa.core.db import get_engine
from sqlalchemy import text
eng = get_engine()
with eng.connect() as c:
    for tbl in ['raw_moneyflow','raw_daily','factor_daily','sector_state_daily','sector_moneyflow_sw_daily']:
        try:
            rows = c.execute(text(f'''
                SELECT EXTRACT(YEAR FROM trade_date)::int, COUNT(DISTINCT trade_date)
                FROM smartmoney.{tbl} GROUP BY 1 ORDER BY 1
            ''')).fetchall()
            print(f'{tbl}: {dict(rows)}')
        except Exception as e:
            print(f'{tbl}: {e}')
"

# 重新拉 SW 成员（季度更新）
uv run python -c "
from ifa.families.smartmoney.etl.sw_member_fetcher import run_sw_member_full_refresh
from ifa.core.db import get_engine
print(run_sw_member_full_refresh(get_engine()))
"

# 生成晚报（手动模式）
uv run python -m ifa.cli report --family smartmoney --date 2026-04-30

# Alembic 迁移
uv run alembic upgrade head
uv run alembic current
```

---

## 已知问题 / 注意事项

1. **compute `'content_type'` / `'trade_date'` KeyError**: compute 阶段对历史数据报错，原因是某个 LLM 返回字段名不一致。B6 修 `evening.py` 时一并处理。

2. **DC sector codes vs SW sector codes**: DC 用 `BK*.DC`，SW 用 `801xxx.SI`，两套代码系统不互通。B3 之后统一走 SW，`raw_dc_member` 不再使用。

3. **kpl fallback**: `load_sector_structures()` 在 `stock_signals_daily` 为空时自动 fallback 到 `raw_kpl_list` 关键词匹配（已实现），是临时方案，C 阶段有真实 ML 信号后自然失效。

4. **`raw_dc_member`**: 只有 18 天近期数据，已弃用，勿用于任何历史分析。

5. **TuShare 无历史的表**: `raw_moneyflow_ind_dc`（2023起）、`raw_moneyflow_ind_ths`（2024起）、`raw_kpl_concept`（2024起）等，早年确实无数据，非 bug。B 阶段主路径不依赖这些表。

6. **`raw_moneyflow_hsgt` 缺口**: 北向资金数据略有缺口（每年约 5-10 天），是交易所不开放日（MSCI 审议等），正常现象。
