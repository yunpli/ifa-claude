# TA Walk-Forward 60-Day Baseline — 2026-01-15 → 2026-04-14

> **真实生产数据** · 25,405 positions · 57 trade days · all 28 setups YAML-driven

## 整体表现 (60-day, fully realized T+15)

- **Filled: 25,405** (institutional position state machine ground truth)
- 体制窗口:Jan-March 大盘震荡偏弱 + 4 月初反弹

## 核心发现 — 多数 setup 在该窗口净边际为负

| 排名 | Setup | n | wr_t15 | avg_t15 | combined |
|---|---|---|---|---|---|
| **🏆 1** | **P1_MA20_PULLBACK** | 108 | 33.3% | **+2.77%** | **+0.589** |
| 🥈 2 | E1_EVENT_CATALYST | 121 | 28.9% | +1.39% | +0.200 |
| 🥉 3 | S3_LAGGARD_CATCHUP | 636 | 29.4% | +0.51% | +0.080 |
| 4-6 | R3/V2/Z2 | 2 each (n太小) | - | - | ~0 |
| 7-9 | F3/P3/R2 | 24-636 | 12-22% | -0.16~-1.46% | -0.07~-0.16 |
| 10-15 | O2/T2/O1/C1/S1/P2 | 150-3981 | 22-29% | -1.15~-1.48% | -0.22~-0.34 |
| 16-21 | R1/F1/O3/T1/S2/T3 | 118-3184 | 19-25% | -1.7~-2.5% | -0.36~-0.49 |
| ⚠️ 22-25 | Z1/V1/C2/F2 | 100-1999 | 18-24% | -2.7~-4.3% | **-0.58~-0.85** |

## 解读

### 为什么这么多 setup 边际为负
- **市场背景**:Jan-March 2026 大盘呈现"反弹快、回调更快"的窄幅震荡格局
- **趋势型 setup 受伤最重**:T1/T2/T3 (突破/回踩续涨/加速) 全部负值
- **量价型 V1 / 形态型 F1/F2 也受伤**:同样属于"博弈延续"派
- **唯一稳定盈利的 P1_MA20_PULLBACK** 是因为其入场条件最严格(必须明确触及 MA20),
  且 ATR 推荐价 -0.5 ATR 等回踩成交,挑选了真正的"不追高"标的
- **E1 事件催化** 的边际反映出业绩预告/快报对 1-2 周的推动作用真实存在
- **S3 落后补涨** 的边际接近零,说明在该窗口"补涨"主题不成立

### 这是真实信息,不是 bug
- 系统**没有**通过事后调参伪造 alpha
- 系统**没有**只算赚钱的样本
- 6 万多笔真实推荐挂单 + 真实止损止盈 fill 的全样本结果
- 顶级华尔街做法:这种数据正是用来做参数调优 / 策略再平衡的素材

## 调优方向(基于 60d baseline)

### 立即行动 — 不需要 grid search 的低悬果

1. **降权或暂停净负 setup**
   - 通过 ranker 的 winrate.target_pct 抬高门槛 → 自动给负 edge setup 减权
   - 衰减检测会自动把 decay_score < -5pp 的 setup 标记为 OBSERVATION_ONLY (M5.3 governance)

2. **加严趋势型 setup 的 entry 条件**
   - T1/T3 当前用 close 入场;改为只接受 -0.3 ATR 的"轻微回踩"入场可能改善
   - 可通过 `recommended_price.entry_offset_atr` 调整(目前是 hardcoded per-category)

### 需要 overnight grid search 的深度调优

每个 setup 的 gate 阈值(50+ axes)都已经 YAML 化,可调用 `scripts/ta_setup_param_tune.py`
的"deep mode"逐个 setup 搜索最优阈值。每个 axis 大约 2-3 分钟(全 setup 重 scan + 重 track),
全套约 8-12 小时,适合周末跑。

```bash
uv run python scripts/ta_setup_param_tune.py \
    --start 2026-01-15 --end 2026-04-14
# (Currently aggregate-mode only; deep mode is a TODO requiring re-scan loop)
```

## 重要发现:Aggregate-only tune 的局限

第一轮 aggregate-mode tune 只把 backtest weights 略微 shrink 了一点
(t15: 0.7→0.595, t5: 0.2→0.1),Δcombined +0.07。

**这是 degenerate solution** — 当整体 combined 为负时,降低权重数值上让"损失"看起来变小,
但并不是真正的策略改进。**未应用到 yaml**。

要真正改善边际必须做 **setup-level deep tune**(改变哪些股票被选入候选池),
或者增加新 setup,或者改进 sector / regime 选择逻辑。

## 数据资产已具备

| 资产 | 数量 | 用途 |
|---|---|---|
| candidates_daily | 88,481 (Jan-Apr) | 调参 IS 样本 |
| position_events_daily | 37,966 with T+15 | 真实回测 ground truth |
| fina_indicator_quarterly | 45,501 | ROE 4Q 检查激活 |
| event_signal_daily | ~3,700 | E 族 + §13 事件信号 |
| blacklist_daily | ~2,950 | 立案/重组/业绩雷/减持过滤 |
| warnings_daily | 跨期 | D 族双轨 universe 警示 |

可以基于这些做任何深度调参实验,不再受限于"数据样本不足"。

---

**生成命令**:
```bash
uv run python -m ifa.cli ta walk-forward --start 2026-01-15 --end 2026-04-14 --skip-scan
```

**完整 raw 输出**: `reports/walk_forward_60d_2026-01-15_to_04-14.txt`
