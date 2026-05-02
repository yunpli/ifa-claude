# SmartMoney Backfill 审计报告 + B8/C3/C4 实施计划

**审计日期**: 2026-05-01  
**审计范围**: SW L2 主路径 (B1-B7+B9 完成后) + 现有 ML 基础设施  
**目的**: 在 B8 训练前确认数据完整性和方案可行性

---

## Phase 1: 单位一致性 (B8 训练前必须确认)

| 字段 | 表 | 单位 | 校验状态 |
|------|-----|------|---------|
| `raw_daily.amount` | raw_daily | **千元** | ✓ 由 `vol×close×100/1000` 校验通过 |
| `raw_daily.vol` | raw_daily | 手 (1手=100股) | ✓ |
| `raw_daily.close` | raw_daily | 元 | ✓ |
| `raw_daily.pct_chg` | raw_daily | % | ✓ |
| `circ_mv` / `total_mv` | raw_daily_basic | **万元** | ✓ |
| `turnover_rate` / `pe_ttm` | raw_daily_basic | % / 倍 | ✓ |
| `net_mf_amount` 等 | raw_moneyflow | **万元** | ✓ |
| `sector_moneyflow_sw_daily.net_amount` | sw_l2 agg | **万元** | ✓ 反算与 raw_moneyflow SUM 完全一致 |
| **`market_state_daily.total_amount`** | market_state | **🔴 真 BUG** | 全表常量 = 775,891,494 (不是日聚合) |

### 行动项

- ✅ B6c 已修：`load_sector_flows(sw_l2)` 在 SQL 层 ×10000 (万→元) 与 `_fmt_amt(scale=1e8)` 兼容
- ✅ B6c 已修：`candidate.min_amount/min_circ_mv` 单位修正 (千元/万元)
- 🟡 待修: `market_state_daily.total_amount` bug — **B8 训练不依赖**, evening 显示问题留 B6 后续修复
- 🟢 ML 训练规则: **永远不直接使用 `market_state_daily.total_amount`**, 用 `raw_daily SUM` 实时计算

---

## Phase 2: ML 特征覆盖率 (按年非空率)

### 个股级特征 (训练 RF/XGB 的 features)

| 字段 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|------|------|------|------|------|------|------|
| **raw_daily**: pct_chg / amount / vol / close | 100% | 100% | 100% | 100% | 100% | 100% |
| **raw_daily_basic**: turnover_rate / circ_mv / total_mv | 100% | 100% | 100% | 100% | 100% | 100% |
| **raw_daily_basic**: pe_ttm | 84% | 82% | 78% | 76% | 73% | 72% |
| **raw_moneyflow**: net_mf / buy_elg / sell_elg / buy_lg | 100% | 100% | 100% | 100% | 100% | 100% |
| **raw_limit_list_d**: limit_ / open_times | 100% | 100% | 100% | 100% | 100% | 100% |
| **raw_limit_list_d**: limit_times | 78% | 76% | 75% | 77% | 77% | 77% |
| **raw_kpl_list**: lu_desc / theme | 100% | 100% | 100% | 100% | 100% | 100% |

### 板块级特征 (sector-level)

| 字段 | 2021 | 2022 | 2023 | 2024 | 2025 | 2026 |
|------|------|------|------|------|------|------|
| `factor_daily(sw_l2)` heat/trend/persist/crowding | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `sector_state_daily(sw_l2)` cycle_phase 有效率 | 98% | 100% | 98% | 99% | 100% | 94% |
| `sector_moneyflow_sw_daily` net/elg/lg | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `sw_member_monthly` 月快照覆盖 (avg 股/L2) | 12 mo (30股) | 12 mo (35股) | 12 mo (40股) | 12 mo (42股) | 12 mo (44股) | 5 mo (44股) |

### 北向资金 (体制识别 / 日级特征)

| 年 | rows | non-null | avg (万元) | 异常? |
|----|------|----------|------------|-------|
| 2021 | 233 | 232 | 1,880 | ✓ |
| 2022 | 236 | 232 | 388 | ✓ (年内正负相抵) |
| 2023 | 231 | 230 | 211 | ✓ (同上) |
| 2024 | 233 | 233 | 71,013 | ✓ |
| 2025 | 237 | 237 | 212,375 | ✓ (北向活跃度上升) |

> **错误警报已排除**: 2022-2023 不是 bug, 是单日正负流相抵导致年均接近 0. 2025 单日见 +21亿元 (217,612 万元) 正常.

### 数据缺口 (训练集需特别处理)

1. **2023 raw_kpl_list 数据短**: 10K rows vs 18-20K (其他年). 2023 早期 ETL 失败, 不是结构性. 处理: NaN forward-fill 或剔除 2023-Q1-Q3 涨停特征
2. **2023 raw_top_inst 数据短**: 152K vs 220-262K. 同上
3. **`raw_kpl_concept` 仅 2024+** (有 z_t_num): RF 短线 "连板热度" 特征 2021-2023 无, 用 raw_kpl_list 推算 (非 perfect 但可用)

---

## Phase 3: 信号→次日收益验证 (这是 B8 设计最关键的一节)

### in-sample 2025-01-01 → 2025-10-31 信号有效性

| Role | n | 1d 收益 | 1d 命中率 | 5d 收益 | 20d 收益 | 评价 |
|------|----|---------|-----------|---------|----------|------|
| **龙头** | 4,318 | **+0.62%** | **48.0%** | +0.45% | +1.05% | ✅ 信号有效 (3.7x 市场基线) |
| 补涨 | 7,175 | -0.02% | 45.1% | +0.31% | +1.72% | ⚠️ 1d 弱, 20d 转正 |
| 中军 | 10,223 | -0.13% | 41.8% | +0.06% | +1.57% | ⚠️ 短线弱, 长线略胜 |
| **趋势** | 1,708 | **-0.35%** | **38.5%** | -0.35% | +1.05% | 🔴 **反向信号!** 需 ML 重学 |
| 全市场基线 | 1,074,430 | +0.168% | — | +0.906% | — | 参考值 |

### 关键诊断

1. **rule-based 信号 ≠ 训练目标** — 不能直接把 role 当 label, 因为"趋势"反向, "补涨"弱
2. **正确做法**: forward return (回归) 或 top-quantile 二分类作为 label, role/sector 信号作为 **特征**
3. **B8 的 ML 模型应该比 rule-based 更准** (或至少与 龙头 +0.62% / 48% 命中持平), 否则没必要训练

### B8 训练样本量 (in-sample 2021-01 → 2025-10)

| 项目 | 数量 |
|------|------|
| 全市场 stock-day 行 | ~6,300,000 (5,432 股票 × ~1,168 天) |
| sector-day 行 (sw_l2) | 152,452 |
| 短线信号 (补涨/龙头/情绪先锋) | 75,922 |
| 中长线信号 (趋势) | 13,240 |

> **关键决策**: 13K 趋势样本太少 → XGB 训练应基于全市场 stock-day, 用 forward 20d return 作 label, 把"趋势"作 feature 而非 label.

---

## Phase 4: 现有 ML/Backtest 基础设施盘点

| 模块 | 行数 | 现状 | sw_l2 兼容? |
|------|------|------|-------------|
| `ml/features.py` | 247 | sector-level 特征工程, 支持 source 筛选 | ⚠️ 需测 sw_l2; `_extract_dc_extras` DC-only |
| `ml/dataset.py` | 277 | 含 `_load_sw_returns` / `_load_dc_returns` / `_load_ths_returns` | 🔴 **缺 `_load_sw_l2_returns`** (B8 必须新增) |
| `ml/random_forest.py` | 97 | RF 包装类, 二分类 | ✓ 通用 |
| `ml/xgboost_model.py` | 121 | XGB 包装类 | ✓ 通用 |
| `ml/persistence.py` | 188 | 模型保存/加载 | ✓ |
| `backtest/engine.py` | 501 | 因子 IC/RankIC + walk-forward ML | 🟡 含 sw/dc/ths 三源, 需加 sw_l2 |
| `backtest/runner.py` | 312 | 编排 + 持久化到 backtest_runs/metrics | ✓ |

### sw_l2 适配清单 (B8 第一步)

1. 在 `ml/dataset.py` 新增 `_load_sw_l2_returns()`: 从 `raw_daily` 经 `sw_member_monthly` 等权聚合 L2 sector daily return
2. 在 `ml/features.py` 验证 `_extract_dc_extras` 不会破坏 sw_l2 流程 (条件已是 `if row["sector_source"] != "dc"`)
3. 在 `backtest/engine.py` 同上加载 sw_l2 returns
4. 默认 `sector_source` 全部 sw_l2

---

## B8 实施计划: SW L2 ML 模型训练 (替代 rule-based candidate)

### 设计原则

1. **任务分两个**: 
   - **RF 短线 (1-3 天 forward return)**: 二分类 / 回归. 用于晚报 §10 "短线池"
   - **XGB 中长线 (20 天 forward return)**: 二分类 / 回归. 用于晚报 §10 "中长线池"
2. **预测对象**: SW L2 sector (与现有 sector-level infra 一致). 然后通过 `sector_signals_daily` × `sector membership` 推到个股
3. **Label 构造**: forward return (或 top-quantile = 1, bottom-quantile = 0 二分类)
4. **Feature**: sector-level (heat/trend/persistence/crowding + cycle_phase + role + 资金流) + market-level (北向 + 量能水位 + 体制)

### 步骤

#### B8.1 sw_l2 sector return 构造 (首要)
- 实现 `dataset.py::_load_sw_l2_returns(engine, start, end)`:
  ```sql
  SELECT m.trade_date, sm.l2_code AS sector_code, 'sw_l2' AS sector_source,
         AVG(m.pct_chg) AS pct_chg
  FROM raw_daily m
  JOIN sw_member_monthly sm 
    ON m.ts_code=sm.ts_code 
   AND sm.snapshot_month=date_trunc('month', m.trade_date)::date
  WHERE m.trade_date BETWEEN :start AND :end
  GROUP BY m.trade_date, sm.l2_code
  ```
- 等权 L2 sector return (member 平均). 也可用 vol-weighted 如果对比更优

#### B8.2 Feature engineering for sw_l2
- 复用 `features.py::build_feature_matrix(source='sw_l2')`
- 新增 sw_l2 特定特征:
  - `net_amount_zscore_5d`: net_amount 在板块 60d 历史中的 z-score
  - `elg_buy_rate`: buy_elg / (buy_elg + sell_elg)
  - `member_pct_chg_dispersion`: 成员股 pct_chg 标准差 (主线分歧度)
  - `top_member_concentration`: top-3 成员 net_mf 占整体比例

#### B8.3 训练 RF / XGB

| 模型 | 目标 | window | label |
|------|------|--------|-------|
| RF 短线 | 选出未来 1-3 天 SW L2 板块 top-decile | 1d / 3d forward | 二分类: top 20% = 1 |
| XGB 中长线 | 选出未来 20 天 top-decile | 20d forward | 二分类: top 20% = 1 |

- Walk-forward CV: 12-month rolling training, 3-month test
- 超参 search: RF (n_estimators, max_depth), XGB (eta, max_depth, n_rounds)
- 保存为 `models/params_v2026_05_rf.json` / `_xgb.json`

#### B8.4 接入 candidate.py
- 给 candidate.py 加 `model_source='RF'/'XGB'/'rule'` 参数
- 默认 ML 加载冻结模型预测, fallback 到 rule

---

## C3 实施计划: 训练回测 (in-sample 2021-01 → 2025-10)

### 任务

1. 跑 B8.1-B8.3 训练流程, 在 in-sample 期间做 walk-forward
2. 保存 backtest_run_id 和 metrics:
   - factor IC / RankIC / TopN hit rate (因子单测)
   - ML AUC mean / std (RF + XGB)
3. 对比基线: 
   - 龙头规则: 1d +0.62%
   - 补涨规则: 1d -0.02%
   - 趋势规则: 20d +1.05%
   - 全市场基线: 1d +0.168% / 5d +0.906%
4. ML 必须 > 基线 5-10% 超额才算"有效"

### 命令

```bash
uv run python -m ifa.cli smartmoney backtest \
  --start 2021-01-04 --end 2025-10-31 \
  --windows 1,3,5,20 --topn 5 \
  --notes "B8 in-sample sw_l2 walk-forward"
```

### 输出

`backtest_runs.run_id` + `backtest_metrics` 表, 用于 C4 决定哪一组超参冻结.

---

## C4 实施计划: 冻结 v2026_05 RF + XGB 模型

### 任务

1. 从 C3 backtest 中选 IC/AUC 最佳的超参组合
2. 用 in-sample 全期 (2021-01 → 2025-10) 重训, **不再 walk-forward, 一次性 fit**
3. 保存模型文件:
   ```
   models/params_v2026_05_rf.json
   models/params_v2026_05_xgb.json
   models/v2026_05_rf.pkl  (joblib dump)
   models/v2026_05_xgb.pkl
   ```
4. 用 `params freeze` 命令更新 DB 的 active params 版本
5. 元数据: backtest_run_id, IC, AUC, 训练日期, OOS 预期窗口

### 命令

```bash
# 取得最佳 backtest run
uv run python -m ifa.cli smartmoney bt list

# 冻结
uv run python -m ifa.cli smartmoney params freeze \
  --name v2026_05 --from-backtest <run_id> \
  --notes "B8/C3 完成: SW L2 RF + XGB 冻结, IC=X.XX, AUC=X.XX"
```

---

## C5/C6 (后续)

- **C5 OOS 验证 (2025-11 → 2026-04)**: 用 v2026_05 模型跑 stock_signals_daily / predictions_daily, 评估 OOS 表现
- **C6 最终晚报 2026-04-30**: `IFA_REPORT_RUN_BADGE=production` 跑生产报告

---

## 总结: 进入 B8 的"绿灯检查清单"

- [x] sector_moneyflow_sw_daily 全覆盖 2021-2026 (1,289 trading days, 168K rows)
- [x] factor_daily(sw_l2) 全覆盖 (152K in-sample + 10K OOS, 131 sectors/day)
- [x] sector_state_daily(sw_l2) 98%+ 有效 cycle_phase
- [x] stock_signals_daily 2021-2026 全覆盖 (4500-5000 龙头/年; candidates 后台补跑中)
- [x] 单位审计: 所有金额字段单位明确, market_state 总额 bug 已隔离不影响 ML
- [x] 信号有效性诊断: 龙头 ✓, 趋势 ✗ → 必须 ML 重新筛选
- [x] 现有 ML/backtest 基础设施可复用, 主要新增 sw_l2 returns loader

**结论: 可以开始 B8.**
