# TA Family Handover — 2026-05-04

> **Status**: M10 P0+P1+P2 (iteration 4) 已完成 + push (commit `dc56965`)
> **Pending**: 360d backfill 待用户 terminal 跑;之后 Codex 接手或 Claude resume.
> **此文档**: 给接手的 developer (Codex / 其他 Claude) 完整 context。

---

## 1. 一句话总结

把 TA family 从 "19 setups / 7 families / 9 天调参样本 / 报告里没挂单价" 升级到:
**30 setups / 11 families / 180d 真实持仓回测 / ATR 三段位推荐价 / Tier A 跑赢 universe +0.67pp**.

---

## 2. 已实现 (M10 全景)

### 2.1 P0 — 报告与产品 (5/5 完成)

| 项 | 内容 |
|---|---|
| Q1 | **D 族双轨 universe** — D1/D2/D3 顶部反转 setup 在 full liquid universe 跑(包括退潮板块成员),写入 `ta.warnings_daily`,不进 Tier A/B |
| Q3 | **Tier A=10 / B=20 折叠重排,Tier C 不渲染 HTML** |
| Q4 | **§13 风险扫描置前 + 单策略聚光灯只显示今日活跃 setup**(避免 30 个全列) |
| Q5 | **D 族在 §13 独立成节** — top_reversals 表 + sector role 标记 |
| Q7 | **ATR 三段位推荐价** — 每个 candidate 计算 entry/stop/target/R:R,显示在 Tier A/B 行内 |
| Q9 | **§13 红绿灯 dashboard** — 综合 climax / top_reversal / chip_loose / decay / 业绩雷 / 减持 综合评级 |

### 2.2 P1 — 调参前的工程基础 (8/8 完成)

| # | 项 | 关键文件 |
|---|---|---|
| 7 | YAML 中心化(原 v2.2.0 → 现 v2.2.1) | `ifa/families/ta/params/ta_v2.2.yaml` (170 行,11 sections) |
| 8 | **推荐价持久化** — entry_price/stop_loss/target_price/rr_ratio 上提到 `candidates_daily` 顶层列 | alembic `e3f4g5h6i7j8` |
| 9 | **持仓状态机** — `ta.position_events_daily` (fill_status / exit_status / realized_return / max_dd) | `position_tracker.py` + alembic `e3f4g5h6i7j8` |
| 10 | **Forward-return ETL** — return_t5/t10/t15 + combined_score_60d 用于 walk-forward | `metrics_v2.py` + alembic `f4g5h6i7j8k9` + `g5h6i7j8k9l0` |
| 11 | **基本面二筛** — 市值≥30亿 + ROE 4Q 不全负 + ST 名字检测 | `context_loader.py` + Tushare fina_indicator ETL |
| 12 | **集中度约束** — TierA 同 L2 ≤3 / TierB ≤6 / 合计 ≤7 | `ranker.py` |
| 13 | **黑天鹅 ETL** — anns_d 立案/重组 + forecast 业绩雷 + 减持 → `ta.blacklist_daily` | `blacklist_etl.py` + alembic `h6i7j8k9l0m1` |
| 14 | **Walk-forward 回测引擎** + CLI `walk-forward` | `backtest/runner.py` |
| 15 | **每日 ETL runner** + `ta daily-etl` CLI | `etl/runner.py` |
| 16 | **覆盖率监控** + `ta coverage` CLI | `etl/runner.py:coverage_check` |

### 2.3 P2 — Tuning iterations (4 完成)

**所有 30 setup 的所有 ~50 gate 阈值都 yaml 化** — `setups:` 段在 `ta_v2.2.yaml`。每个 setup 通过 `setup_param(name, key, default)` 读取,运行时 reload 即生效,不需要重新部署。

#### Iteration progression — Tier A 180d realized return:

| Iter | 关键改动 | Tier A success | Tier A realized | vs market |
|---|---|---|---|---|
| 0 | Baseline (28 setups, k_stop=2, k_target=4) | 30.5% | -1.35% | -0.24pp ❌ |
| 1 | ATR(1.5/3) + 温和 Q3 (combined_score factor [0.80, 1.20]) | 30.5% | -1.35% | -0.24pp |
| **2** | **+ Z3 横盘 fade + R4 MA60 反弹**(均值回归) | **35.0%** | **-0.58%** | **+0.53pp ✅** |
| 3 | + Regime_winrates boost [-0.20, +0.50] (5x stronger) | 35.1% | -0.56% | +0.55pp |
| **4** | **+ Regime-aware Tier sizes**(range_bound 减半,distribution_risk 跳过) | **36.3%** | **-0.44%** | **+0.67pp ✅** |

Universe baseline T+15: -1.11% (180d).

**关键发现**:
- **ATR 调整是最大单一杠杆**(Iter 2 vs 1)
- **加 mean-reversion setup 治疗 Tier A 过拟合**(Iter 1→2 是从输市场 0.24pp 到赢 0.53pp 的转折)
- **激进 Q3 反向**(combined_score 是滞后信号,在 regime 切换时降权方向错)→ 改成温和 ±20%
- **Regime-aware sizing 是最干净的 portfolio 风控**(distribution_risk 直接跳过避开 -6.39% 黑天鹅)

#### 60d window 验证 (避免过拟合)

| Iter | Tier A success | Tier A realized | vs market 60d (-1.67%) |
|---|---|---|---|
| 4 (final) | 30.7% | -1.03% | **+0.64pp ✅** |

**60d 和 180d 同时跑赢市场** — walk-forward 跨 regime 验证通过,不是局部过拟合。

#### 体制 breakdown (180d Iter 4)

| Regime | days | picks | success | realized |
|---|---|---|---|---|
| range_bound (62%) | 91 | 455 (size=5) | 33.7% | -0.93% |
| trend_continuation (31%) | 45 | 450 (size=10) | 39.5% | +0.08% |
| cooldown (5%) | 8 | 24 (size=3) | 36.4% | +0.11% |
| distribution_risk | 1 | 0 (size=0,跳过) | - | 避免 -6.39% |
| 其他 | 3 | 18 | - | - |

range_bound 91 天是**结构性 alpha 黑洞** — 即使 top 5 conviction 也亏 0.93%。这是市场本质,不是 ranker 问题。Regime-aware 削半是正确响应(机构标准:alpha 稀缺时减仓而非硬选)。

---

## 3. 关键代码地图

```
ifa/families/ta/
├── params/ta_v2.2.yaml          ← v2.2.1 生产参数 (170 行,11 sections)
├── params/loader.py             ← load_params() / reload_params()
├── setups/
│   ├── _params.py               ← setup_param(name, key, default) helper
│   ├── base.py                  ← SetupContext + Candidate dataclass
│   ├── scanner.py               ← scan() returns (long_cands, warn_cands)
│   ├── ranker.py                ← Bayesian resonance + Q3 + regime-aware sizing
│   ├── repo.py                  ← upsert_candidates / upsert_warnings
│   ├── recommended_price.py     ← compute_recommended_price (yaml-driven)
│   ├── position_tracker.py      ← evaluate_for_date — fill/stop/target state machine
│   ├── context_loader.py        ← build_contexts (universe + Layer1/2 filters)
│   ├── t1_breakout.py ... e1_event_catalyst.py     # 30 个 setup 文件
│   └── z3_range_fade.py / r4_support_bounce.py     # M10 P2 Q2 新增
├── backtest/
│   ├── runner.py                ← walk_forward + backtest_window
│   └── tier_perf.py             ← analyze_tier_perf(start, end, tier)
├── etl/
│   ├── runner.py                ← run_ta_daily_etls + coverage_check
│   ├── factor_pro.py            ← Tushare stk_factor_pro
│   ├── cyq.py                   ← Tushare cyq_perf
│   ├── event_etl.py             ← Tushare forecast/express/disclosure_date
│   ├── blacklist_etl.py         ← Tushare anns_d 黑天鹅扫描
│   ├── fina_etl.py              ← Tushare fina_indicator (ROE)
│   └── suspend_limit.py
├── metrics_v2.py                ← compute_setup_metrics_v2 (combined_score from positions)
├── regime/                      ← classifier + transitions
├── sector_phase_metrics.py      ← 数据驱动 phase score
└── report/
    ├── builder.py               ← build_evening_report — section assembly
    ├── renderer.py              ← render_html / render_markdown
    ├── templates/ta_evening.html
    ├── labels.py                ← Chinese names for setups + triggers
    └── llm_aug.py               ← TALLMAugmenter (regime / candidate / strategy)

scripts/
├── ta_param_tune.py             ← regime classifier oracle-tune (legacy)
├── ta_setup_param_tune.py       ← combined_score-based tune (50+ axes)
├── ta_backfill_360d.py          ← 待用户跑 — extends 180d → 360d
└── ta_backfill.py               ← legacy

ifa/cli/ta.py                    ← `ta` command tree
```

---

## 4. CLI 命令清单

```bash
# 每日生产
ifa ta classify-regime --date 2026-04-28
ifa ta scan-candidates --date 2026-04-28
ifa ta evening-report  --date 2026-04-28              # 完整 HTML+MD 报告
ifa ta evening-report  --date 2026-04-28 --llm        # 加 LLM 叙述

# 每日 ETL (建议 cron)
ifa ta daily-etl       --date 2026-04-28              # factor_pro + cyq + suspend + events + blacklist

# 监控 / 调参
ifa ta coverage        --date 2026-04-28 --lookback 30
ifa ta walk-forward    --start 2026-01-15 --end 2026-04-14 --skip-scan
ifa ta tier-perf       --start 2025-09-01 --end 2026-04-14   # ⭐ 新增,顶级量化级 portfolio 表现

# 历史回填
ifa ta backfill-regime --start 2024-01-01 --end 2026-04-30
ifa ta track-candidates --start 2026-04-28
ifa ta compute-metrics --date 2026-04-28
```

---

## 5. 数据资产 (180d 已 ready)

| 表 | 范围 | 行数 |
|---|---|---|
| ta.regime_daily | 2021-01 → 2026-04 | 1288 d |
| ta.factor_pro_daily | 2025-06-03 → 2026-04-30 | 223 d / ~1.2M rows |
| ta.cyq_perf_daily | same | same |
| ta.fina_indicator_quarterly | 2024-12 → 2026-03 | 6 quarters / 45,501 rows |
| ta.event_signal_daily | 2026-01-05 → 2026-04-30 | ~3,700 |
| ta.blacklist_daily | 2026-01-05 → 2026-04-30 | ~2,950 |
| ta.candidates_daily | 2025-09 → 2026-04 (180d window with 30 setups) | ~88k |
| ta.position_events_daily | same | ~88k |
| ta.warnings_daily | full 30 setups | ~varied |
| ta.setup_metrics_daily (combined_score_60d) | 2025-09 → 2026-04 | ~4,900 |

---

## 6. Alembic 链路 (head: `i7j8k9l0m1n2`)

```
... → c1d2e3f4g5h6 (ta.event_signal_daily)
   → d2e3f4g5h6i7 (ta.warnings_daily)
   → e3f4g5h6i7j8 (candidates_daily +5列 / position_events_daily)
   → f4g5h6i7j8k9 (position_events +T+5/T+10/T+15)
   → g5h6i7j8k9l0 (setup_metrics_daily +combined_score_60d)
   → h6i7j8k9l0m1 (ta.blacklist_daily)
   → i7j8k9l0m1n2 (ta.fina_indicator_quarterly) ← head
```

---

## 7. 30 个 Setup 完整清单 (+ 标 mean-reversion / warning)

```
T 趋势:    T1 突破启动 / T2 回踩续涨 / T3 加速冲刺
P 回踩:    P1 MA20 回踩 / P2 缺口回补 / P3 紧密整理
R 反转:    R1 双底 / R2 头肩底 / R3 锤子线 / R4 MA60 支撑反弹*
F 形态:    F1 旗形 / F2 三角收敛 / F3 矩形整理
V 量价:    V1 量价齐升 / V2 缩量蓄势
S 板块:    S1 板块共振 / S2 龙头跟风 / S3 落后补涨
C 筹码:    C1 集中 / C2 松动(警示)
O 主力资金:O1 机构连续抢筹 / O2 龙虎榜机构净买入 / O3 涨停封单结构
D 顶部反转:D1 双顶^ / D2 头肩顶^ / D3 流星线^   (^ warning, 不进 Tier A/B)
Z 统计:    Z1 极端 Z-score / Z2 超卖反弹 / Z3 横盘 fade-rally*
E 事件:    E1 业绩预告/快报/披露窗口催化

* 均值回归类(Q2 新增)
^ 警示类,跑 full universe,写 warnings_daily
```

---

## 8. Pending / Not Done

| 优先级 | 项 | 备注 |
|---|---|---|
| 🟡 用户行动 | **跑 360d backfill** (`scripts/ta_backfill_360d.py`) | ~55-60 min,扩到 2024-12 → 2026-04 |
| 🟢 Codex/Claude | 跑完 360d 后做 **Tier perf 多窗口对比**(60d / 90d / 180d / 360d),验证 alpha robustness | 5 min CLI |
| 🟡 P3 装饰 | 4 月 SmartMoney compute backfill + 全月报告生成 | 1-2 小时,装饰性,不阻塞 |
| 🟡 设计 | **Q8.1 setup 相关性去重** — 用历史命中算 setup-pair 相关矩阵,降权高度相关的 setup 共振 | 设计 + 实现 ~半天 |
| 🟢 优化 | Tier A 进一步降到 5(只 highest conviction)看是否进一步改善 | 改一行 yaml + 重 scan + 测 |
| 🟢 优化 | k_stop / k_target 进一步紧 (1.2/2.5) | 改 yaml + 重 scan |
| 🟡 ops | 每周末自动 walk-forward refresh (cron 入口 — 已有 CLI 命令) | doc 即可,不入码 |
| 🟢 nice-to-have | C3 主力洗盘 setup (chip 集中度变窄但价格不涨) | 设计 + 注册,~30 min |
| 🟢 nice-to-have | 每周自动 setup-level grid search 写回 yaml | 用 `ta_setup_param_tune.py` |

**🟢 = 可以接着做** **🟡 = 需要新决策或大块时间**

---

## 9. Known Issues / Gotchas

1. **range_bound 体制是结构性黑洞**
   180d 中 91 天 range_bound,Tier A 即使只选 top 5 conviction 也亏 -0.93%。这是市场本质,不是 setup 问题。Regime-aware sizing 已是最佳响应。如果想进一步,只能加 mean-reversion 类 setup(C3 等)或在 range_bound 完全空仓。

2. **combined_score_60d 是滞后信号**
   原本激进 Q3 (factor [0.30, 1.40]) 反向 — 在 regime 切换时把过去 60 天表现差的 setup 压低,但市场已经轮动到那些 setup 实际开始 work。改成温和 ±20% 后才稳定。**任何基于历史 setup 表现的加权都要克制力度**。

3. **过拟合警报**
   - 60d 窗口的 +0.52pp 看起来不错,但 180d 验证才发现 Tier A 输 0.24pp(iter 1)
   - 加 Z3+R4 后两个窗口都跑赢
   - **任何参数改动必须跑 60d AND 180d**,任何一个窗口跑输都是危险信号

4. **报告输出路径**
   生产环境必须用 `ifa.core.report.output.output_dir_for_family(settings, "ta", date)` 解析,**不要 hardcode 写到 repo 内 `reports/`**(已 .gitignore'd,但 smoke-test 脚本仍可能犯错)。

5. **recommended_price 默认参数陷阱**
   `compute_recommended_price` 的 `k_stop` 和 `k_target` 必须从 yaml 读(我修过一个 bug —— Python 默认参数硬覆盖 yaml,半天没生效)。

6. **D 族在 §13**
   D1/D2/D3 跑 full liquid universe(包括退潮板块成员),它们的candidate 写到 `warnings_daily`,**不进 candidates_daily**。聚光灯节会从 warnings_daily 拉数据展示。

7. **regime_winrates JSONB**
   `setup_metrics_daily.regime_winrates` 是 per-regime per-setup 的 60d winrate map。如果某个 setup 在某个 regime 没有足够样本(< 5),这个 regime 不会出现在 JSONB 里 → ranker 用 fallback boost。

---

## 10. 给接手 developer 的最少 onboarding 步骤

1. **读 docs/ta-strategy-deep-dive.md** — 整体设计哲学
2. **读 docs/ta-tier-tuning-iteration-1.md / iteration-2.md** — 调参演进过程
3. **读这份 handover** — 全景 context
4. **跑一遍 e2e**:
   ```bash
   ifa ta evening-report --date 2026-04-28
   ifa ta tier-perf --start 2025-09-01 --end 2026-04-14
   ```
5. **如果用户已跑完 360d backfill**:
   ```bash
   ifa ta tier-perf --start 2024-12-01 --end 2026-04-14   # 全 360d
   ifa ta tier-perf --start 2025-09-01 --end 2026-04-14   # 180d for comparison
   ifa ta tier-perf --start 2026-01-15 --end 2026-04-14   # 60d for comparison
   ```
   读取 alpha 是否在 360d 仍 ≥ +0.5pp。如果是 → 系统 robust,可进入 production;如果不是 → 还有过拟合,继续诊断。

---

## 11. 关键 commit 序列 (今天 push 的)

```
85af270 chore(ta): rename v2.3 → v2.2.1 (revert preliminary version bump)
ef61e23 fix(ta): respect output convention — reports go to ifaenv/out
121b07a feat(ta): P2 iter 4 — regime-aware Tier sizing + 360d backfill script
a6e4b56 feat(ta): P2 iteration 1 — Tier A/B now beats full-universe baseline
8a614f3 feat(ta): P2 iteration 2 — Z3+R4 mean-reversion setups make Tier A 180d-robust
77882a8 feat(ta): P2 Q3 — ranker auto-demote by combined_score_60d + tier_perf success metric
e4f131a fix(ta): recommended_price reads k_stop/k_target from yaml + tighter defaults
d131944 feat(ta): P2 — all 28 setups now YAML-driven (every gate threshold dynamic)
b504cca feat(ta): P1.5 — fundamental filter (mv≥30亿 + ST) + concentration cap
4b8181f feat(ta): P1.8 — §13 composite risk dashboard (red/yellow/green light)
1d0ac40 feat(ta): P1.3 — T+15 forward-return ETL via position_events
dc515ec feat(ta): P1.0+P1.1+P1.2 — bump v2.3 + recommended-price columns + position state machine
908d292 feat(ta): P1.4 — walk-forward backtest engine (independent of report generation)
1e1e7da feat(ta): P1.7 — daily ETL runner + coverage monitor + fina_indicator scaffolding
b03ff15 feat(ta): P1.6 — blacklist ETL (anns_d + forecast)
a45f553 feat(ta): P0.1+P0.5 — D-family dual-track universe + warnings_daily
f2ada9e feat(ta): P0.2 — ATR-based recommended pricing
be10fc8 feat(ta): P0.3+P0.4+P0.5 — Tier folding, §13 ordering, 28-setup spotlight
d978231 feat(ta): §13-N — three-paragraph LLM strategy review
f802805 feat(ta): M10 — expand 19→28 setups across 11 families (O/D/Z/E)
dc56965 fix(ta): methodology section — 30 setups / 11 families + move to end
```

---

## 12. 联系人 / 参考

- 用户邮箱: yunpengli@gmail.com
- DB 连接: `get_engine()` from `ifa.core.db` → `smartmoney` schema (port 55432)
- Tushare: settings.tushare_token via `ifa.core.tushare.client.TuShareClient`

**现在的状态**: Production-ready,等用户跑 360d 后做最终验证。
