# Ningbo Deep Dive — 宁波派短线策略报告

> **状态**：Phase 1–3.D 完整闭环，可用于生产
> **当前 active 模型**：aggressive=ensemble_meanrank · conservative=xgb_ndcg
> **覆盖范围**：A 股全市场（2024-01-02 起 174k 历史候选 + 标签）

---

## 1. 背景与定位

宁波派是 A 股短线流派的代表，强调：
- **24 日均线生命线**（跌破即止损）
- **+20% 累计止盈**
- **5–15 个交易日持仓窗口**
- **多策略共振**（神枪手 / 聚宝盆 / 半年翻倍）

iFA Ningbo 是该策略的算法化实现 + ML 增强系统：

```
完整候选池（每天 ~310 只触发任意策略的股票）
  → 启发式 confidence_score（可解释规则）
  → 双轨 ML 排序（aggressive：求收益；conservative：求 Sharpe）
  → ★1-★5 共识矩阵（融合三方排名给客户参考）
  → top-5 投递（客户按风险偏好选）
  → 15 日追踪 + take_profit/stop_loss/expired 终态
```

---

## 2. 数据 schema（`ningbo` 库）

| 表 | 用途 | 行数（截至 2026-05-03） |
|---|---|---|
| `candidates_daily` | 每日全部策略命中（**不限 top-N**），ML 训练原料 | ~230k |
| `candidate_outcomes` | candidates 的 15 日前向标签（take_profit / stop_loss / expired） | ~219k |
| `recommendations_daily` | 每日 top-10 投放推荐（含 3 个 scoring_mode：heuristic / ml_aggressive / ml_conservative） | ~3k |
| `recommendation_tracking` | 每日追踪表（每条推荐 × 15 天）| ~50k |
| `recommendation_outcomes` | 推荐的最终 outcome | ~3k |
| `model_registry` | ML 模型注册表（slot-aware：aggressive / conservative / heuristic） | 历次训练版本 |
| `promotion_log` | Champion-Challenger 晋升日志 | 每周 1-2 行 |
| `strategy_params` | 策略参数版本（神枪手等的阈值） | — |

---

## 3. 三个策略

### 3.1 神枪手（Sniper）
**核心模式**：5 日均线上穿 24 日均线后回踩到 24MA 的"狙击点"。
- A. MA5 在过去 N≤20 天上穿 MA24（`days_since_cross`）
- B. 自上穿后无收盘跌破 MA24（最多容忍 2 天回探）
- C. 今日最低价触及 MA24 ±0.5%
- D. 今日收盘 ≥ MA24 × 0.99
- E. 量能收缩（今日 vol < 5 日均量）
- F. MA5 仍 > MA24

触发分类（按"清洁触摸次数"）：
- `strike_1` — 首次触摸（早 / 信号弱）
- `strike_2` — 二次触摸（黄金信号，原版规格首推）
- `strike_3p` — 三次或以上（支撑疲弱）

Confidence score 加权（5 个分量，每个 0-1）：
- 0.30 trigger_w（strike_2 = 1.0）
- 0.20 touch_precision（low 距 MA24 多近）
- 0.20 rebound_strength（close 在 H-L 中位置）
- 0.15 vol_contraction（量能收缩程度）
- 0.15 cross_freshness（cross 多新鲜）

### 3.2 聚宝盆（Treasure Basin）
**核心模式**：K 线形态 — T-2 / T-1 / T 三日组合识别。
- 红三兵 / 启明星 / 锤子线 / 倒锤子等中国 A 股常见底部反转形态
- 配合量能验证 + MA24 附近收敛

### 3.3 半年翻倍（Half-Year Double / hyd）
**核心模式**：周线级别 MACD + KDJ 共振 + 日线确认。
- 周线 MACD 金叉 + 周线 KDJ < 50 + 日线收阳
- 看 6 个月（半年）翻倍可能性

---

## 4. Phase 演化简史

| Phase | 完成时间 | 关键产出 |
|---|---|---|
| **Phase 1** | 2026-04 早期 | 三策略实现 + 启发式打分 + 单一 evening 报告 + 15 日追踪 |
| **Phase 2** | 2026-04 中期 | 历史回填（2024-01 → 2026-04，~2.8k top-5 推荐 + 41k 追踪行）+ 向量化指标（10x 提速）|
| **Phase 3.A** | 2026-05-02 | ML 训练管线（LR + RF + XGB）on top-5 数据 |
| **Phase 3.B** | 2026-05-02 | **全候选池**（174k → 233k 行）替代 top-5，解决样本选择偏差 |
| **Phase 3.C** | 2026-05-02 | 9 模型矩阵（LR/RF/XGB/LGBM/CatBoost + XGB-pair/ndcg + LGBM-LambdaRank + ensemble）+ Kronos 预训练 embedding 实验 |
| **Phase 3.D** | 2026-05-02 | Champion-Challenger 双 slot 自动晋升 + ★1-★5 共识矩阵 + 三轨追踪 |

---

## 5. ML 架构核心

### 5.1 模型库（9 个候选）

| 模型 | 类型 | 角色 |
|---|---|---|
| `lr` | LogisticRegression + StandardScaler | 线性 baseline |
| `rf` | RandomForestClassifier (max_depth=6, min_leaf=50) | 鲁棒 baseline |
| `xgb_clf` | XGBoost binary classifier | GBM 主力 |
| `lgbm_clf` | LightGBM binary classifier | GBM 替代 |
| `cat_clf` | CatBoost binary classifier | GBM 替代（类别友好）|
| `xgb_pair` | XGBRanker rank:pairwise | 排序学习 |
| `xgb_ndcg` | XGBRanker rank:ndcg | **直接优化 NDCG@5** |
| `lgbm_lamda` | LGBMRanker LambdaRank | 排序学习 |
| `ensemble_meanrank` | 5 个 GBM + ranker 的 mean-rank ensemble | 融合 |

### 5.2 特征矩阵（39 维 base）

| 类 | 数量 | 字段 |
|---|---|---|
| A. 启发式基线 | 4 | confidence_score, n_hits, resonance_boost, best_individual_score |
| B. 策略 one-hot | 4 | has_sniper, has_basin, has_hyd, is_multi |
| C. Sniper 特化 | 5 | sniper_strike_code, touch_precision, rebound_strength, vol_contraction, cross_freshness |
| D. Basin 特化 | 1 | basin_pattern_strength |
| E. HYD 特化 | 2 | hyd_weekly_score, hyd_daily_score |
| F. 股票上下文 | 8 | log_rec_price, vol_20d, return_20d, turnover_5d_avg, log_market_cap, vol_surge, dist_60d_high, dist_60d_low |
| G. 市场上下文 | 6 | index_pct_chg, index_5d_return, index_above_ma20, index_5d_vol, index_10d_return, index_above_ma60 |
| H. 日历 | 3 | day_of_week, month, quarter |
| I. SW L2 板块动量 | 3 | sector_l2_5d_return, sector_l2_5d_breadth, sector_l2_inflow_5d_norm |
| J. 截面排名 | 3 | cs_rank_confidence, cs_n_picks_day, cs_n_multi_day |

**Kronos 实验结论**：256 维 OHLCV embedding（来自 NeoQuasar/Kronos-Tokenizer-2k 预训练）加进特征矩阵后，**8/10 模型表现下降**。维度诅咒 + 任务特化导致预训练通用 K 线表征对宁波派 +20%/15日 任务无帮助。**已禁用**（如需未来再试，季度 refresh 会自动重新评估）。

### 5.3 Champion-Challenger 双 slot

| Slot | 优化目标 | Risk floor | 当前 active |
|---|---|---|---|
| **aggressive** | Top5_AvgReturn 最高 | T5_Med ≥ -3.0% | `ensemble_meanrank` (T5_Mean +2.18%) |
| **conservative** | Sharpe 最高 | T5_Mean ≥ +1.0%, MaxDD ≥ -65% | `xgb_ndcg` (Sharpe 0.27) |
| **heuristic** | 固定 confidence_score | — | 永远在线，做 baseline |

**晋升规则**（5 条全满足才替换 active）：
1. 主指标提升 ≥ +0.5pp（aggressive: T5_Mean / conservative: Sharpe）
2. Bootstrap p-value < 0.10 vs 当前 active
3. Risk floor（见上表）
4. 最近 3 个 monthly bucket 都 ≥ +1.0%
5. Active 已上线 ≥ 14 天（冷却期）

**触发频率**：每周日 22:00 BJT。

---

## 6. 报告 5 个 section（晚报）

| # | Section | 内容 |
|---|---|---|
| 1 | 市场简报 + 扫描漏斗 | 上证 / 中证 1000 / 创业板涨跌；六步曲扫描 / sniper / basin / hyd 各命中数 |
| 2 | **共识矩阵 ★1-★5** | top-15 候选股，按三轨排名加总打星，前 5 行高亮 |
| 3 | 持仓警报 | 今日触发 stop_loss / take_profit 的过往推荐 |
| 4 | **15 日历史追踪**（按日期 click-to-expand）| 每个过去日期的 top-5 共识 picks，含 sparkline 趋势 + 终态 |
| 5 | 风险提示与免责声明 | 中英对照（继承 iFA 通用 disclaimer）|

### ★1-★5 评分公式

每个轨道（heuristic / ml_aggressive / ml_conservative）的 top-5 picks 给分：
- rank #1 → 5 分
- rank #2 → 4 分
- ...
- rank #5 → 1 分
- 不在 top-5 → 0 分

3 个轨道加总（满分 15）：
| 总分 | ★ |
|---|---|
| 13–15 | ★★★★★（三方一致 + 至少两方 top-2）|
| 10–12 | ★★★★ |
| 7–9 | ★★★ |
| 4–6 | ★★ |
| 1–3 | ★ |

---

## 7. CLI 命令完整参考

```bash
# 日常生产
ifa ningbo evening --scoring dual --mode production --generate-pdf

# 历史回填（一次性）
ifa ningbo backfill                                 # 启发式 top-5 历史填充
ifa ningbo backfill-candidates --start 2024-01-02 --end 2026-04-30   # ML 候选池
ifa ningbo candidate-outcomes --start 2024-01-02 --end 2026-04-30    # ML 标签

# Champion-Challenger（每周日）
ifa ningbo refresh weekly                           # 训练 + 晋升判断
ifa ningbo backfill-dual --days 7                   # 用新 active 模型刷新历史推荐

# 健康检查
ifa ningbo refresh monthly                          # 每月 1 号 walk-forward
ifa ningbo refresh quarterly                        # 每季度模型族重审

# Registry 管理
ifa ningbo registry status                          # 看当前 active + 历史
ifa ningbo registry promote aggressive <version>    # 手动晋升
ifa ningbo registry rollback aggressive             # 紧急回退
```

---

## 8. 性能基准（OOS 2025-11 → 2026-04，136 天）

| 模型 | T5_Prec | T5_Mean | T5_Med | Sharpe | WinRate | MaxDD |
|---|---|---|---|---|---|---|
| heuristic baseline | 9.9% | +0.41% | -2.02% | 0.03 | 43% | -75.5% |
| **ensemble_meanrank** ⭐ aggressive | 10.8% | **+2.52%** | -2.08% | 0.22 | 51% | -82.5% |
| **xgb_ndcg** ⭐ conservative | 11.5% | +1.82% | **-0.46%** | **0.25** | **62%** | **-57.1%** |
| xgb_clf | 12.7% | +1.82% | -1.88% | 0.17 | 53% | -84.3% |

**对比启发式**：ensemble Top5_Mean 翻 6 倍（+0.41% → +2.52%），xgb_ndcg 中位数提升 4 倍（-2.02% → -0.46%）。

---

## 9. 文件结构

```
ifa/families/ningbo/
├── data.py                    # bulk OHLCV / weekly bars / index 加载
├── strategies/
│   ├── _indicators.py         # MA / MACD / KDJ / RSI / WR + bulk vectorized
│   ├── sniper.py              # 神枪手
│   ├── treasure_basin.py      # 聚宝盆
│   ├── half_year_double.py    # 半年翻倍
│   └── six_step.py            # 选股六步曲（baseline filter）
├── signals/
│   ├── confidence.py          # HeuristicScorer / MLScorer protocol
│   ├── selection.py           # select_top_n with per_strategy_cap
│   └── alerts.py              # detect_today_alerts / fetch_in_progress_summary
├── tracking/
│   ├── batch.py               # run_tracking_batch / insert_recommendations
│   └── sparkline.py           # SVG sparkline render（红涨绿跌中国习惯）
├── llm/
│   └── narrative.py           # 推荐叙事（127-140 chars per pick）
├── ml/
│   ├── candidates.py          # full candidate pool backfill
│   ├── features.py            # 39 维特征 + 共享辅助
│   ├── features_v2.py         # full pool 版本
│   ├── trainer_v3.py          # 9 模型矩阵 + bootstrap + ensemble
│   ├── champion_challenger.py # slot-aware 晋升规则
│   ├── refresh.py             # weekly/monthly/quarterly 入口
│   ├── dual_scorer.py         # 三轨打分 + EnsembleWrapper + ★ 共识矩阵
│   ├── backfill_dual.py       # 历史 ml_* 推荐回填
│   ├── kronos_features.py     # Kronos embedding 提取（已禁用）
│   └── kronos_lib/            # 内嵌的 Kronos 模型代码
├── backfill.py                # Phase 2 回填
├── evening.py                 # 主入口（dual mode）
└── ...
```

---

## 10. 已知限制 + 未来工作

**当前限制**：
- 只用 EOD 数据，无 intraday → 理论 AUC ceiling 约 0.6-0.65
- 模型每周日重训，工作日不学习新数据（避免 overfitting）
- 特征工程偏 handcrafted，未尝试 deep learning representation（Kronos 已实验失败）
- 中国节假日处理依赖 `smartmoney.trade_cal`（来自 TuShare）

**Roadmap**：
- 季度 refresh 自动重审 Kronos / TabNet 等先前被拒模型族
- 引入分钟级数据可能突破 AUC 0.65 上限（需新数据源 + ETL）
- 多周期叠加（日 + 周 + 月信号融合）
- 跨市场迁移（港股 / 美股策略复用）

---

## 11. 故障排查

| 症状 | 可能原因 | 解决 |
|---|---|---|
| 晚报报错 numeric overflow | ML scores 未归一化 | 已修（`_picks_from_ml_scores` 内做了 [0,1] 归一化） |
| 共识矩阵历史日期没有 ★★★+ | 历史推荐只有 heuristic | 跑 `ifa ningbo backfill-dual --days 30 --mode manual` |
| `ifa ningbo refresh weekly` 跑 15+ 分钟仍未晋升 | Bootstrap p-value 不显著 | 正常，"NO CHANGE" 是合理输出 |
| Active 模型最近 30 天 T5_Mean < 0% | 模型衰退或 regime shift | 跑 `refresh monthly` 看 walk-forward；必要时 `registry rollback` |
| 特征矩阵 build 慢（>3 分钟） | L2 sector momentum SQL 重 | 正常，单 chunk 10s/月 |

---

完整运维流程见 [`OPERATIONS.md`](./OPERATIONS.md)。
