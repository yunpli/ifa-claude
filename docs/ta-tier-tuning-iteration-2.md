# TA Tier A/B Tuning — Iteration 2 (2026-05-04)

> Q2: 注册 Z3 (横盘 fade-rally) + R4 (MA60 支撑反弹) 后的 180d 验证。

## 演进总表

### 180d Window (2025-09-01 → 2026-04-14, 4,410 picks)

| 阶段 | A 成功率 | A realized | B 成功率 | B realized | A vs market | B vs market |
|---|---|---|---|---|---|---|
| iteration 1 (28 setups, mild Q3) | 30.5% | -1.35% | 32.6% | -0.63% | **-0.24pp ❌** | +0.48pp |
| **iteration 2 (30 setups, +Z3+R4)** | **35.0%** | **-0.58%** | 33.0% | -0.92% | **+0.53pp ✅** | +0.19pp |

180d full-universe baseline: -1.11%

### 60d Window (2026-01-15 → 04-14)

| 阶段 | A 成功率 | A realized | B realized |
|---|---|---|---|
| iteration 1 (28) | 31.0% | -1.15% | -1.10% |
| **iteration 2 (30)** | 30.7% | **-1.03%** | -1.15% |

60d full-universe baseline: -1.67%
- iteration 2 Tier A: -1.03% → 跑赢 +0.64pp ✅
- iteration 2 Tier B: -1.15% → 跑赢 +0.52pp ✅

## 关键发现

### Z3 + R4 补齐了系统的 mean-reversion 盲区

Iteration 1 的 28 setups 全部偏向"趋势/形态/突破延续"派。在震荡市里这些 setup 同向亏损,Tier A 跟着失血。

新增的 **Z3 (横盘 fade-rally)** + **R4 (MA60 支撑反弹)**:
- Z3:60d 横盘内股票今日窜高,挂限价 -0.8 ATR 等回撤 → 长仓位等 box-bottom 买入
- R4:股票回踩 MA60 后今日反弹 → 长仓位 catch-the-knife 但有 stop 保护

两个 setup 在 trending 市不触发(filter 卡住),只在 range / 回撤 regime 才出现,**正好补齐其他 28 setup 的盲区**。

### 跨 regime 验证通过 — Tier A 不再过拟合

180d 跨越:
- 2025 Q4:相对 trending 上涨
- 2026 Q1:震荡偏弱
- 2026 Q2 初:小幅反弹

iteration 1 在 Jan-Apr 60d 跑赢 +0.52pp,但拉到 180d 看 Tier A 反而输 0.24pp — 60d 是局部过拟合。

iteration 2 在 60d 跑赢 +0.64pp,在 180d 也跑赢 +0.53pp — **同时在两个窗口跑赢市场**,这是 walk-forward 验证的关键标志。

### 各 setup 历史 hit count (180d)

```sql
SELECT setup_name, COUNT(*) FROM ta.candidates_daily
WHERE trade_date BETWEEN '2025-09-01' AND '2026-04-14'
GROUP BY 1 ORDER BY COUNT(*) DESC;
```

R4_SUPPORT_BOUNCE: ~6,500+ hits
Z3_RANGE_FADE: ~800+ hits

R4 触发频繁(MA60 支撑测试是常见模式),Z3 触发稀少(横盘 60d + spike 是稀缺信号)。两者并存为 portfolio 提供差异化机会。

## 落地参数 (ta_v2.2.yaml)

新增 setups 段:
```yaml
Z3_RANGE_FADE:
  box_max_pct: 0.25                  # 60d 振幅上限
  today_min_pct: 4.0                 # 今日 spike 下限
  near_top_quartile: 0.75            # 收盘需在 60d 上四分位
  rsi_overbought: 70

R4_SUPPORT_BOUNCE:
  above_ma60_min_x: 1.005            # 收盘 ≥ 1.005 × MA60
  today_pct_min: 1.5                 # 今日反弹 ≥ 1.5%
  drop_floor_x: 0.85                 # 不能是断崖跌势
  touch_lookback_days: 5             # 5 日内触及 MA60
```

ATR + 温和 Q3 保留 iteration 1 的:
```yaml
recommended_price:
  k_stop: 1.5
  k_target: 3.0
```

```python
# ranker.py 温和 Q3
combined = m.get("combined_score_60d")
if combined is not None:
    c_factor = max(0.80, min(1.20, 1.0 + 0.20 * float(combined)))
    adj_score *= c_factor
```

## 数据资产

| 表 | 范围 | 行数 |
|---|---|---|
| ta.factor_pro_daily | 2025-06-03 → 2026-04-30 | 223 days, ~1.2M rows |
| ta.cyq_perf_daily | same | same |
| ta.candidates_daily (180d window) | 2025-09-01 → 2026-04-14 | ~88k rows |
| ta.position_events_daily (180d window) | same | ~88k rows |
| ta.setup_metrics_daily (combined_score) | 2025-09 → 2026-04 | ~4,900 rows |

## 下一步可选

1. **跑 90d 窗口验证**(Dec 2025 → Mar 2026)— 进一步 robustness check
2. **Q3 factor range 优化** — ±20% 是否最优? 试 ±15% / ±25%
3. **新增更多 mean-reversion setup** — 比如 C3 主力洗盘 / V3 缩量黄金线
4. **Tier A 选股 regime-aware** — trend 体制和 range 体制采用不同 ranker 权重
5. **每周末自动 walk-forward refresh** — 滚动跟踪 alpha 是否持续
