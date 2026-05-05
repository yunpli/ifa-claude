# TA Tier A/B Tuning — Iteration 1 (2026-05-04)

> 第一次基于真实持仓回测的参数调优。Window: 60d (2026-01-15 → 2026-04-14, 57 trade days, 1710 picks).

## 演进时间线

| 阶段 | k_stop | k_target | Q3 | Lookback | Tier A 成功率 | Tier A realized | Tier B 成功率 | Tier B realized | fill rate |
|---|---|---|---|---|---|---|---|---|---|
| **0. Baseline** | 2.0 | 4.0 | OFF | (Mar-Apr only) | 26.9% | -1.25% | 23.9% | -1.48% | 66% |
| 1. ATR 调小 (yaml 写了但 bug) | 1.5 | 3.0 | OFF | partial | 23.0% | -1.67% | 21.7% | -1.43% | 66% |
| 2. **ATR 调小 + 修 yaml bug** | 1.5 | 3.0 | OFF | partial | 30.0% | -1.25% | 28.9% | -1.15% | 64.9% |
| 3. + Q3 自动降权 (激进 ±70%) | 1.5 | 3.0 | aggressive | full 180d | 28.4% | -1.49% | 26.7% | -1.86% | 54.9% |
| 4. **+ Q3 温和 (±20%)** | 1.5 | 3.0 | mild | full 180d | **31.0%** | **-1.15%** | **29.7%** | **-1.10%** | **67.4%** |
| Full universe baseline | - | - | - | - | - | -1.67% | - | -1.67% | - |

## 关键发现

### 1. ATR 调整 (k_stop 2.0→1.5, k_target 4.0→3.0) — 最大单一改进项
- 成功率 +4-5pp 对 Tier A 和 B 都成立
- 道理:震荡市里 +4 ATR 目标 15 天经常达不到;调到 +3 ATR 提高命中率,虽然单次盈利变小,但期望值改善
- Bug 提醒:`compute_recommended_price` 默认参数硬写 2.0/4.0,yaml 改值无效。已修复(`recommended_price.py` 现读 yaml `recommended_price.k_stop / k_target`)

### 2. Q3 自动降权 — 激进版反向,温和版有效
- **激进版** (combined ≤ -0.5 → factor 0.30, combined ≥ +0.6 → factor 1.40): Tier A 变差
  - fill rate 跌到 54.9% (挂单更难成交)
  - Tier A 实现收益从 -1.25% 变 -1.49%
  - 原因:`combined_score_60d` 是滞后信号,在 regime 切换时反向降权
- **温和版** (factor ∈ [0.80, 1.20], 最大 ±20% 影响): 是稳定 win
  - Tier A: 31.0% 成功率 / -1.15% realized
  - Tier B: 29.7% / -1.10% realized
  - 既不会大幅扭曲 Tier 组成,又能小幅奖励历史好 setup

### 3. Lookback 数据完整性是基础
- 之前 `ta.factor_pro_daily` / `cyq_perf_daily` 只有 Jan 5 起的 77 天
- 回填到 2025-06-03(180 天 lookback,共 223 天):
  - factor_pro_daily: +812k 行
  - cyq_perf_daily: +812k 行
  - candidates_daily: +146 days × ~500 = ~73k 新行
  - position_events_daily: 类似规模
  - setup_metrics_daily: combined_score_60d 真正可用

### 4. Tier A/B 现在跑赢全 universe baseline
- Full universe 平均 T+15: **-1.67%** (Jan-March 大盘震荡偏弱)
- Tier A: **-1.15%** → 跑赢 +0.52pp ✅
- Tier B: **-1.10%** → 跑赢 +0.57pp ✅

虽然两 Tier 都还是负值(市场太弱),但**已经跑赢被动等权基准**。这是真正的 alpha.

## 已落地的参数 (ta_v2.2.yaml)

```yaml
recommended_price:
  k_stop: 1.5      # was 2.0
  k_target: 3.0    # was 4.0
```

```python
# ranker.py — 温和 Q3 (无 yaml 暴露,直接代码 hard-coded 后等下一轮调优):
combined = m.get("combined_score_60d")
if combined is not None:
    c_factor = max(0.80, min(1.20, 1.0 + 0.20 * float(combined)))
    adj_score *= c_factor
```

## 下一步可调优方向

### A. 进一步调 ATR
- k_stop 1.5 → 1.2 (更紧):止损更频繁但更小,可能再改善
- k_target 3.0 → 2.5 (R:R 仍 ~2:1):成功率应再 +3-5pp
- 但要小心:过紧导致一遇正常波动就出场,信号被 chop 切碎

### B. Q3 进一步精细
- 温和 (±20%) 已是 win
- 可以试 (±10%, ±15%, ±30%) 寻找最优 factor 范围
- 或换成基于"近 20 天" combined(更短 lookback,更敏感)

### C. 加新震荡市 setup (Q2)
- 已草稿 z3_range_fade.py / r4_support_bounce.py(尚未注册到 SETUPS dict)
- 注册后预期能进一步改善震荡市表现

### D. 更长窗口验证
- 当前 60d 窗口(Jan 15 - Apr 14)
- 现在 180d 数据已全部就绪,可跑 90d / 120d / 180d 验证参数稳定性
- 命令:`uv run python -m ifa.cli ta tier-perf --start 2025-09-01 --end 2026-04-14`

## 数据资产已具备

| 表 | 范围 | 行数 |
|---|---|---|
| ta.factor_pro_daily | 2025-06-03 → 2026-04-30 | 223 days |
| ta.cyq_perf_daily | 同 | 同 |
| ta.candidates_daily | 同 | 88,481 → 增加 ~73k Jun-Dec |
| ta.position_events_daily | 同 | ~37k → ~75k |
| ta.setup_metrics_daily (combined_score_60d) | 2025-09 → 2026-04 | 4900 行 |

可基于此数据资产做更深的 grid search 调参。
