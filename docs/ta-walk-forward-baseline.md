# TA Walk-Forward Baseline — 2026-05-04

> **首次真正基于真实 fill price + T+15 出场的 setup 边际测量**。
> 数据窗:2026-04-01 → 2026-04-14 (9 trade days, 4395 candidates → 3130 filled positions).

## 关键设置

| 项 | 值 |
|---|---|
| 窗口 | 9 trade days (2026-04-01 → 2026-04-14) |
| Horizon | T+15 (主目标 70%) + T+10 (10%) + T+5 (20%) |
| 入场价 | ATR 三段位 (P1.2 推荐价: -0.5 ATR for pullback / -0.8 ATR for reversal / 0.0 for breakout) |
| 止损 | -2 ATR |
| 目标 | +4 ATR (R:R 2:1) |
| Fill 检测 | T+1 low ≤ entry & T+1 open ≤ entry × 1.005 |

## 整体表现

- 9,452 positions evaluated (April full month, all candidates)
- **Filled: 6,475 (68.5%)** / Unfilled 2,977 (31.5%)
- Stop_hit: 1,652 @ avg **-10.53%**
- Target_hit: 1,607 @ avg **+18.45%**
- Time_exit (T+15): 873 @ avg +2.33%
- Win/loss ratio (target/stop): 49.3%
- **Avg gain : avg loss = 1.75** (positive expectancy)

## Setup 边际排序 (combined = 0.7×T+15 + 0.2×T+5 + 0.1×T+10)

### 🏆 Tier-1 强 edge (combined > 4)

| Setup | n | wr_t15 | avg_ret_t15 | combined | 评注 |
|---|---|---|---|---|---|
| F1_FLAG | 5 | 60.0% | +18.3% | **10.48** | 旗形完美 setup,but n 太小 |
| P1_MA20_PULLBACK | 15 | 46.7% | +12.1% | **4.94** | 经典回踩,稳定 |
| P2_GAP_FILL | 66 | 45.5% | +11.2% | **4.67** | 缺口回补在趋势体制下表现极好 |

### Tier-2 中等 edge (combined 2-4)

| Setup | n | wr_t15 | avg_ret | combined |
|---|---|---|---|---|
| R1_DOUBLE_BOTTOM | 149 | 32.9% | +10.1% | 3.13 |
| S2_LEADER_FOLLOWTHROUGH | 206 | 39.8% | +8.2% | 2.87 |
| F2_TRIANGLE | 29 | 44.8% | +6.8% | 2.67 |
| S3_LAGGARD_CATCHUP | 188 | 42.5% | +7.1% | 2.62 |
| O1_INST_PERSISTENT_BUY | 179 | 33.5% | +8.3% | 2.47 |
| S1_SECTOR_RESONANCE | 375 | 35.5% | +7.1% | 2.40 |
| T2_PULLBACK_RESUME | 548 | 33.2% | +7.5% | 2.31 |
| C1_CHIP_CONCENTRATED | 366 | 30.3% | +7.7% | 2.19 |

### Tier-3 弱 edge (combined 1-2)

| Setup | n | wr_t15 | avg_ret | combined |
|---|---|---|---|---|
| V1_VOL_PRICE_UP | 222 | 31.1% | +6.5% | 1.92 |
| O3_LIMIT_SEAL_STRENGTH | 26 | 42.3% | +5.0% | 1.68 |
| Z1_ZSCORE_EXTREME | 46 | 26.1% | +5.9% | 1.59 |
| T1_BREAKOUT | 332 | 24.7% | +6.0% | 1.43 |
| T3_ACCELERATION | 285 | 22.5% | +4.0% | 1.06 |

### ⚠️ Tier-4 边际接近零 (combined < 1)

| Setup | n | wr_t15 | avg_ret | combined | 决策 |
|---|---|---|---|---|---|
| O2_LHB_INST_BUY | 38 | 23.7% | +3.1% | **0.68** | 触发条件过松,需加严 |
| R2_HS_BOTTOM | 148 | 23.6% | +2.7% | **0.58** | 头肩底检测假信号多 |
| C2_CHIP_LOOSE | 48 | 16.7% | +3.5% | **0.52** | 警示型 setup,边际本就该弱 |
| F3_RECTANGLE | 4 | 0% | -1.5% | -0.00 | 样本太少,无判断 |
| V2_QUIET_COIL | 1 | - | - | - | 样本太少 |

## 观察 + 调参方向

### 1. 形态 / 回踩 setup 的 ATR 推荐价非常成功
F1 / P1 / P2 都用了 -0.5 / -0.8 ATR 的"等回踩入场",平均收益 11-18%,远超只看突破的 setup (T1=6%, T3=4%)。**保留并加权**。

### 2. T1/T3 突破型 setup edge 偏弱
原因可能:
- 突破后立即按 close 入场,容易追高
- 或者:T1 信号太松,过多噪声

调参方向:
- 提高 T1/T3 的 `volume_ratio` 阈值(比如 >=1.8 → >=2.5)
- 或加 sector_quality > 0.6 的硬条件

### 3. R2 头肩底检测低效
仅 23.6% 胜率。检测规则可能太宽容(允许低肩部不对称)。
调参方向:
- 加严 R2 的肩部对称性约束(当前 5%, 改为 3%)
- 或要求 neckline 突破 ≥ 2% (当前 1%)

### 4. O2 龙虎榜机构净买入触发偏频繁
n=38, wr 仅 24%, avg 3%。单纯"机构净买入"信号噪声大。
调参方向:
- 提高 LHB net_buy 占流通市值的下限 (当前 0.5%, 改为 1.0%)
- 或要求至少 2 个机构席位同向

### 5. Fill rate 68.5% 偏低,改进空间
- 31.5% 候选挂单未成交(主要是 pullback / reversal 类的"等回踩"被忽略)
- 可考虑放宽 unfilled_max_premium_pct (当前 0.5%, 改为 1.0%)

## 下一步建议

**P2 调参的具体动作**:

1. 用 `walk-forward` CLI 扫 60-120 天回测(等更多 T+15 数据)
2. 针对 T1/T3/R2/O2 各自做 1-axis greedy search:
   - T1: volume_ratio 阈值 [1.5, 1.8, 2.0, 2.5, 3.0]
   - R2: shoulder_symmetry [0.03, 0.04, 0.05, 0.06]
   - O2: lhb_net_buy_pct_float [0.3, 0.5, 0.8, 1.0, 1.5]
3. 每个候选参数集跑 backtest,选 combined 最大的
4. **Walk-forward 验证**: 90d-IS 选 → 30d-OOS 看稳定性
5. 写回 `ta_v2.2.yaml`

**P3 装饰回放**:
当前 4 月报告样本(~/claude/ifaenv/out/<mode>/20260428/ta/ifa_TA_evening_20260428_*.html)已能展示完整新功能,
不必再批量回放历史报告。

---

**生成命令**:
```bash
uv run python -m ifa.cli ta walk-forward --start 2026-04-01 --end 2026-04-14 --skip-scan
```

**完整输出**: `/tmp/walk_forward_baseline_2026-04-01_to_04-14.txt`
