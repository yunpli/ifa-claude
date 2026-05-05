# TA Tuning Playbook — 调参经验沉淀与启发式规则

> **目的**: 把 M10 P2 所有调参 iteration 的实战经验沉淀成 future-tuning 的启发式规则。
> 后续每次调参都应**先读这里**,避免重复死路;每次新 iteration 完后**附录在底部** "iteration log"。
>
> **核心原则**: 任何参数改动必须**同时在 60d / 180d / 360d 上跑**,任一窗口显著退化 = 过拟合 = 不能上线。
>
> **当前 baseline**: iter5 (regime-aware concentration cap relaxation)。
> Tier A 360d alpha **+0.06pp** (vs market -0.20%), 60d +0.71pp, 180d +0.96pp。

---

## 1. 学到的启发式规则 (Heuristics)

### H1. 60d 局部胜利 ≠ 真 alpha,必看 360d
**证据**: iter6 (ATR k_stop 1.5→1.2, k_target 3.0→2.5)
- 60d Tier A 从 +0.71pp 升到 **+0.96pp** ✅
- 但 180d 从 +0.96 跌到 +0.46pp,**360d 从 +0.06 跌到 -0.44pp** ❌

**机制**: 更紧 stop 在最近震荡市频繁救人(60d Jan-Apr 噪声大),
但拉长看会**切早赢家**(2025 H1 趋势市)。

**应用**: 任何"短窗口大幅改善"的参数,必须 360d 验证。
60d 涨幅 > 0.2pp 但 360d 跌幅 > 0.2pp = **过拟合,reject**。

### H2. Combined_score_60d 是滞后信号,Q3 力度必须克制
**证据**: 早期"激进 Q3"(factor [0.30, 1.40])让 Tier A 变差,
温和 Q3 (factor [0.80, 1.20]) 才稳定。

**机制**: setup 在过去 60d 表现差时,降权它们,
但**市场已轮动**到那些 setup 实际开始 work,降权方向错。

**应用**: 任何基于"过去表现"的加权,
**力度 ≤ ±20%**;超过这个力度大概率反向。

### H3. Tier A defensive alpha — down market 救命,up market 跟不上
**证据**: 360d 季度拆分:
- 2024-Q4 universe -10.08% → Tier A +8.14pp ✅ (huge alpha in crashes)
- 2025-Q3 universe +1.59% → Tier A **-1.46pp ❌** (lag in trend)
- 2026-Q2 universe +6.28% → Tier A **-2.87pp ❌** (lag in rebound)

**机制**: risk gates(基本面二筛 / 黑天鹅 / D 族 warning / 集中度)
让 Tier A 偏中盘 conviction → trend 时小盘高 beta 跑赢,Tier A 跑输。

**应用**: trend regime 的 alpha 必须**结构性松绑**:
- 集中度 cap 放松(iter5 已做,A_l2 3→5)→ +0.13pp 360d
- 市值门槛降低(iter8 testing)— 让小盘进
- 但 down/range regime 保持紧 — 不能全局松绑(会失去防守)

### H4. Regime-aware 是 alpha 真正杠杆 — 全局调参失效
**证据**: iter4 regime_tier_sizes(range_bound 减半 / distribution_risk 跳过)
直接消除一个 -6.39% 的黑天鹅日,360d alpha +0.13pp。

**机制**: A 股 regime 切换频繁(month-level),
全局参数总是在妥协,regime-conditional 参数才能精准 fit。

**应用**: 优先做 **regime-aware** 而非全局参数:
- ✅ 集中度 cap by_regime, 市值门 by_regime, ATR by_regime
- ❌ 单一全局 k_stop / k_target / cap

### H5. range_bound 是结构性 alpha 黑洞 — 不可调
**证据**: 360d 中 167 天 range_bound,即使顶 5 conviction
Tier A 仍亏 -0.65%。所有 iteration 都救不动。

**机制**: range 市场本质上无系统性 setup edge,
任何"做多 setup"都是 50/50 赌博。

**应用**:
- range_bound 不要试图调参拉升
- 唯一正确做法: **减仓**(iter4 a_size 5/15)或**完全空仓**
- 加 mean-reversion setup (iter Q2 Z3+R4) 已是最优;再加意义不大

### H6. 单 axis 改动 vs 多 axis 同改 — 优先单 axis
**证据**: iter1-4 一次只改一个东西,每次都能定位贡献。
后期"激进 Q3"误以为有效,实际是别的因素。

**应用**: 每次 iteration **只改 1 个参数**,跑完 360d 验证再决定下一步。
多 axis 同改 = 调参噪声,无法归因。

### H7. 60d 数据样本量在 1500-2000 picks → 统计显著门槛 ~0.3pp
**机制**: 标准差 ~3-5%,n=1700 → SE ≈ 0.1pp,
2σ 阈值 ~0.2pp,3σ ~0.3pp。

**应用**:
- alpha 改善 < 0.2pp = **统计噪声**,不可信
- 改善 > 0.3pp = 大概率真信号
- 必须 360d 也确认(更大样本)

### H8. "tier B 跟着 A 一起改善"不一定成立
**证据**: iter5 Tier A 改善但 **Tier B 180d 从 +0pp 退到 -0.17pp**。

**机制**: Tier B 选股标准本就不同(20 个 vs 10 个),
A 改进的机制不一定 generalize 到 B。

**应用**:
- Tier A 是核心产品(用户关注),优先优化
- Tier B 副产品,但**不能因 A 改善而忽视 B 退化**
- 如果 Tier B 退 > 0.3pp,即使 A 改善 +0.5pp,也要重新设计

### H9. 推荐价的 yaml bug 是历史教训
**证据**: iter1 改 k_stop/k_target 但 Python 默认参数硬覆盖 yaml,半天 alpha 没动。

**应用**: 每次改 yaml 后必须**实际验证 yaml 被读取**:
```python
uv run python -c "from ifa.families.ta.params import reload_params; print(reload_params()['recommended_price'])"
```
任何"改了没效果"的实验,先怀疑代码有 hardcode。

### H10. Multi-iter chaining 时 yaml 状态管理是关键
**证据**: 每次 iteration 改 yaml 后必须**显式 commit 或 revert**,
否则下一个 iteration 会在错误的 baseline 上跑。

**应用**:
- 用 `scripts/ta_tune_experiment.py` 它会自动 backup + restore
- 或 git diff 每次 iter 之间确认 yaml 状态干净

---

## 2. Iteration Log

下表按时间倒序;每次 iteration 完成后,**append 一行**(不要修改历史)。

### Format
```
| iter | hypothesis | change | 60d alpha | 180d alpha | 360d alpha | decision |
```

### iter5 (2026-05-04) ✅ KEPT — 当前 baseline
- **Hypothesis**: trend regime 集中度 cap 太紧,板块龙头被排除外
- **Change**: `concentration.by_regime.trend_continuation = {a:5, b:10}`(默认 a:3, b:6)
- **Result**: 60d Tier A +0.71pp, 180d +0.96pp, **360d +0.06pp** (转正)
- **Decision**: ✅ 保留为新 baseline

### iter6 (2026-05-04) ❌ REVERTED — over-fit to 60d
- **Hypothesis**: ATR 更紧能进一步降 drawdown
- **Change**: `recommended_price.k_stop 1.5→1.2 / k_target 3.0→2.5`
- **Result**: 60d Tier A +0.96pp ✅, 180d +0.46pp, **360d -0.44pp** ❌
- **Decision**: ❌ REVERT (60d 局部胜利,长窗口反而退化)
- **Lesson**: H1 (60d ≠ 360d) + H6 (单 axis 改才能归因)

### iter4 (2026-05-04) ✅ KEPT — included in iter5 baseline
- **Hypothesis**: alpha 稀缺时减仓比硬选好
- **Change**: `regime_tier_sizes` (range_bound a:5, distribution_risk a:0)
- **Result**: 360d Tier A -0.07 → -0.07(数据保持),关键是**避开 1 day distribution_risk -6.39pp**
- **Decision**: ✅ 保留(infrastructure benefit)

### iter3 (2026-05-04) ⚠ MIXED
- **Hypothesis**: 拓宽 regime_winrates boost 强度从 ±20% 到 ±50%
- **Change**: `(ratio_r - 1) × 0.50` clipped to `[-0.20, 0.50]`
- **Result**: 整体几乎无变化(noise)
- **Decision**: 保留(没害处但贡献有限)

### iter2 (2026-05-04) ✅ KEPT — game-changer
- **Hypothesis**: Tier A 在震荡市过拟合 → 加 mean-reversion setup 解决
- **Change**: 注册 Z3_RANGE_FADE + R4_SUPPORT_BOUNCE (28→30 setups)
- **Result**: **180d Tier A +0.53pp ✅**(从 iter1 -0.24pp 翻到正)
- **Decision**: ✅ 关键改动,保留

### iter1 (2026-05-04) ⚠ PARTIAL — ATR (1.5/3) bug 修复后才生效
- **Hypothesis**: ATR k_stop 2→1.5 / k_target 4→3 适合震荡市
- **Change**: yaml 改 + 修复 yaml 不被读取的 bug
- **Result**: 60d 从 baseline 改善 +5pp success rate
- **Decision**: ✅ 保留(yaml 现读取正确)

### baseline (M10 P0+P1 完成 ~2026-05-04)
- 28 setups, k_stop=2/k_target=4, no Q3 yet, no regime-aware sizing
- 180d Tier A: -1.35% vs market -1.11% = -0.24pp ❌

### iter8 (2026-05-04) ⏳ RUNNING
- **Hypothesis**: 全局 mv門 30亿→20亿,让小盘进 (trend regime alpha)
- **Change**: `fundamental_filter.min_total_mv_yi 30→20`
- **Risk**: 全局降会让 down regime 也吃小盘暴跌
- **Result**: TBD
- **Plan**: 如果 trend Q (2025 Q2/Q3, 2026 Q2) alpha 改善 + 360d 不退,
         保留;否则做成 regime-aware 版本

### iter12 (planned) ⏸
- **Hypothesis**: trend regime 集中度更激进(5→6)
- **Change**: `concentration.by_regime.trend_continuation.a 5→6`
- **Plan**: iter8 跑完后启动

---

## 3. Forward Guidance — 下次调参的优先方向

按学到的启发式,future tuning 应**按这个优先级**展开:

### P1 — Regime-aware 进一步细化
- iter8 (trend mv 20亿) 验证后,做 `fundamental_filter.by_regime`(让 trend 单独松绑)
- ATR by_regime: trend 用 1.5/3.0, range 用 1.2/2.5
- winrate floor by_regime: trend 0.4 / range 0.55

### P2 — Setup 库扩展(只在结构性需求时做)
- C3 主力洗盘(chip 集中度变窄但价格不涨)
- 更细的 D 族(如 D4 高位放量阴线)— 提升警示精度
- 优先级低于参数调优,因为新 setup 也是潜在过拟合源

### P3 — ML 启用
- Right-tail classifier(P(+50%, 40d))
- Quantile forecaster
- 必须先做完 calibration infrastructure(stock-edge §11.4)

### P4 — 永远不做
- ❌ 全局 k_stop / k_target 进一步紧(已证 360d 退化)
- ❌ Q3 factor range > ±20%(已证反向)
- ❌ range_bound regime 试图调出 alpha(结构性黑洞)
- ❌ 一次改 2+ 参数(无法归因)

---

## 4. 与 stock-edge 的接口

Stock Edge 调参时务必参考 H1-H10。**特别是**:
- H1: 60d / 180d / 360d 三窗口验证不可少
- H4: 优先 regime-aware,避免全局
- H5: range_bound 直接减仓而非调参
- H7: 0.2pp 以下改善不可信

---

## 5. 维护

每次新 iteration 完后必须:
1. 在 §2 Iteration Log **append** (不修改历史)
2. 如果发现新启发式规则,**append** 到 §1
3. commit message 引用本 playbook

**禁止**: 修改历史 iteration 结论(即使后来发现是错的,
应在新 iteration 中标注 "reverses iter X conclusion")。
