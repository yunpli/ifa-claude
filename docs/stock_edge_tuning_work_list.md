# Stock Edge 调参主工作列表（Living Plan）

> **更新规则**：每次 session 修改 status 列；新发现追加到对应 Tier；不删除已完成项。
>
> **当前 head commit**: `e630463` Phase 5 v2 (K-fold rolling)
>
> **最近更新**: 2026-05-05

---

## 0. 调参目标（Tuning Goal）

Stock Edge production 的 5d/10d/20d decision_score 对应 5/10/20 个交易日实现收益的排序需具备 **统计显著、跨 regime 稳定、可审计** 的预测力。YAML 参数演进必须通过 **全自动 gate** 治理，永远不需要人工 review-then-apply。

### 0.1 量化 success 标准

| 维度 | 目标 |
|---|---|
| 5d val rank IC, K-fold median | ≥ +0.03 且 ≥ 3/4 folds 正向 |
| 10d val rank IC, K-fold median | ≥ +0.04 且 ≥ 3/4 folds 正向 |
| **20d val rank IC, K-fold median** | **≥ +0.05 且 全部 folds 正向** |
| 跨 fold 稳定性 | per-horizon val IC std/median ≤ 1.5 |
| Bootstrap CI (95%) | 下界 > 0 on val |
| Regime-bucketed | ≥ 75% 桶改善 |
| Auto-promote 条件 | K-fold consistency + bootstrap + regime gate 全过 |
| Per-stock overlay 不污染 baseline | YAML promotion allowlist 强约束 |
| 决策可回滚 | git tag + backup YAML + 决策 ledger |

### 0.1.1 数据可用性（2026-05-05 audit）

| 数据层 | 范围 | 适用 |
|---|---|---|
| SmartMoney raw + computed（daily/moneyflow/sector/factor/market_state） | 2021-01 → 2026-04 FULL | ✅ Tier 1/2/3 全部覆盖 |
| ta.regime_daily | 2021-01 → 2026-04 FULL | ✅ G4 regime gate 即开即用 |
| TA family（candidates/setup_metrics/warnings/events/blacklist） | 2024-12 → 2026-04（17 个月）| ⚠️ panel dates 不能早于 2024-12 |

**T3.3 扩 panel 的 do/don't**：扩 stocks 数（30→100）✅，扩 dates 跨度回 2024 之前 ❌（撞 TA cliff）。

### 0.2 当前实测（2026-05-05，K=4 folds × 50 stocks × 12 dates 16-month panel）

| Horizon | Per-fold val_lift | Median | Positive folds | 距离目标 |
|---|---|---|---|---|
| 5d | -0.041 / +0.013 / -0.006 / +0.098 | +0.004 | 2/4 | 大 gap |
| 10d | +0.126 / -0.004 / -0.078 / +0.061 | +0.029 | 2/4 | 中 gap（不稳定）|
| **20d** | **+0.038 / +0.018 / +0.029 / +0.048** | **+0.034** | **4/4** | **接近，差 +0.016** |

---

## 1. 已完成（main line Phase 1-5 v2）

| Phase | 描述 | Commit |
|---|---|---|
| 1 | PIT 评估管线（panel + parquet cache + vectorized eval） | `46552f5` |
| 2 partial | rank_ic_quality 加入 objective + zero-floor weights | `bd4eeb1` |
| 3 partial | IC warmstart + 负权重 + multi-iteration | `c29e420` |
| 4 partial | Auto-gate G1/G2/G6/G7 + auto_promote_if_passing | `c29e420` |
| 5 | walk-forward OOS 单 split（暴露 overfit） | `f52cb19` |
| 5 v2 | K-fold rolling walk-forward（暴露 horizon-selective 真实性） | `e630463` |

---

## 2. Tier 1 — Production gate 必备（ship-blocking）

不做完不能信任 auto-promotion。完成后才有可发车的 production-grade variant YAML。

| ID | 任务 | 估时 | Acceptance Criteria | 依赖 | Status |
|---|---|---|---|---|---|
| **T1.1** | K-fold 一致性 gate | 半天 | `evaluate_promotion_gates` 接受 `kfold_results: list[fold_metrics]`；新增 G9 gate：要求 ≥`min_positive_folds`(default 3/4) folds 在每 horizon 上 lift > 0；CLI `--k-fold-min-positive` 参数；K-fold 路径下默认调用 G9 | none | ✅ done (`d2e86ed`) |
| **T1.2** | G5 Bootstrap CI gate | 半天 | 每 horizon val rank IC 做 1000 次 bootstrap（np.random sampling with replacement）；新增 G5 gate：95% CI 下界 > 0；vectorized；artifact metrics 暴露 `bootstrap_ci` 字段；CLI 默认开 G5 | none | ✅ done (`2cc1e0c`) |
| **T1.3** | G4 Regime-bucketed gate | 半天 | 按 `ta.regime_daily` 把 val rows 分桶（panel 已有 regime 字段）；每桶 ≥30 样本时算 rank IC；新增 G4 gate：要求 ≥75% 桶改善（lift > 0）；artifact metrics 暴露 `regime_breakdown` | none | ✅ done (`67530b1`) |
| **T1.4** | Horizon-selective promotion | 半天 | `auto_promote_if_passing` 接受 `per_horizon_decisions: dict`；只把 pass 的 horizon 的 weights 写到 variant YAML；其他 horizon 保留 baseline；CLI 报告每 horizon 的 promotion 决定（applied / kept_baseline） | T1.1 | ✅ done (`5ff90ec`) |

**T1 完成验收**：在当前 50×12 cached panel 上跑 K-fold + 全 gate（G1+G2+G4+G5+G6+G7+G9），输出：
- 20d weights ✅ promoted
- 10d weights ❌ kept baseline（G4 regime-bucketed 或 G9 K-fold consistency 拦下）
- 5d weights ❌ kept baseline（G5 bootstrap CI 下界 < 0 拦下）

---

## 3. Tier 2 — 搜索器升级（拓宽 alpha 上限）

| ID | 任务 | 估时 | Acceptance Criteria | 依赖 | Status |
|---|---|---|---|---|---|
| **T2.1** | Optuna TPE 取代随机搜索 | 半天 | 引入 `optuna` 依赖；`fit_global_preset_via_panel` 加 `search_algo` 参数（`'random' | 'tpe'`）；TPE 模式下用 IC priors 作为 enqueue 的 first trial；K-fold 下每 fold 独立 study；CLI `--search-algo tpe` | none | ✅ done (`c139568`) |
| **T2.2** | Successive halving | 半天 | 三阶段：广搜 N → top 25% 进精搜 N/2 → top 5% 进 fine N/4；CLI `--search-stages a,b,c`；artifact 暴露每阶段最优 | T2.1（基于 TPE）| ✅ done (`faf6155`) |
| **T2.3** | G3 Sharpe-like gate | 半天 | 计算 `avg_return / max_drawdown` per horizon；新增 G3 gate：tuned ratio ≥ baseline ratio - 0.05（容差 5%）；artifact 暴露 sharpe-like ratio | none | ✅ done (`871cca4`) |
| **T2.4** | G8 收敛 stat 形式化 | 1 小时 | search_history 已存 per-iter best；新增 G8 gate：最近 3 轮 best 的 std/mean < 5%；artifact 暴露 convergence_stat | none | ✅ done (`ad6e3d0`) |

---

## 4. Tier 3 — Scale & 工程化

| ID | 任务 | 估时 | Acceptance Criteria | 依赖 | Status |
|---|---|---|---|---|---|
| **T3.1** | DB I/O batching（per-as_of pre-load） | 1 天 | 主进程对每个 as_of 一次性 SELECT all-stocks 的 daily / moneyflow / sector flow / regime；通过 multiprocessing `initializer` 把字典传给 worker；worker 内 gateway 优先读 in-memory cache | none | ✅ done (commit pending, in-worker preload) |
| **T3.2** | ML 模型跨日期复用 | 2 天 | 同股票邻近 PIT 日期共享 sklearn fit（如 RF / HGBM）；PIT 正确性：训练数据严格 ≤ as_of；缓存 key = (ts_code, fit_window_start..fit_window_end)；命中率监控 | T3.1（数据准备到位）| ⏳ todo |
| **T3.3** | 扩 panel 到 Top 100 × 24 dates | 数小时 wall time | 在 T3.1+T3.2 后跑 100×24 = 2400 rows；估时 30-45min；K=6 folds；产出更稳定的真信号判断（特别是 5d/10d 是否有真 alpha） | T3.1, T3.2 | ⏳ todo |
| **T3.4** | 决策 ledger DB 表 + git tag 自动化 | 半天 | 新表 `stock.tuning_promotion_log`：列 (timestamp, artifact_path, gates_passed_json, variant_yaml_path, git_tag, applied_to_baseline)；每次 auto_promote_if_passing 写一行；git tag 命名 `stock-edge-tune-YYYYMMDD-HHMMSS` | T1 完成 | ✅ done (`b394fdb`) |

---

## 5. 推荐执行顺序

```
Phase A (Tier 1) — Ship-blocking gate 完成
  T1.1 K-fold consistency
  T1.2 Bootstrap CI
  T1.3 Regime-bucketed
  T1.4 Horizon-selective promotion
  → 验收：在 cached panel 上跑出"只 promote 20d、5d/10d 拒绝"的 variant YAML
  → commit + tag

Phase B (Tier 2) — Search 智能化
  T2.4 G8 convergence (1h)
  T2.3 G3 Sharpe-like (半天)
  T2.1 Optuna TPE (半天)
  T2.2 Successive halving (半天)
  → 验收：相同 candidate budget 下 K-fold median val_lift 提升 ≥ 50%
  → commit + tag

Phase C (Tier 3) — Scale & Production
  T3.1 DB batching → panel build wall time 减半
  T3.4 Ledger 表 + git tag 自动化（不依赖 T3.2）
  T3.2 ML 模型跨日期复用（深度优化）
  T3.3 扩 panel 到 100×24 跑最终 production tuning
  → 验收：5d/10d alpha 在大 panel + K-fold + 全 gate 下要么通过、要么明确 documented 为 noise
  → commit + tag + apply to baseline
```

---

## 6. 执行约定

每次 session 操作：

1. **开始前**：读这份文档第 0、1 节明确目标 + 当前状态
2. **执行中**：picked up 一个 Tier 内的 task，按 acceptance criteria 完成
3. **完成后**：
   - 跑现有 tests（`uv run pytest tests/stock -q`），保证 68 全过
   - 在 cached panel 上做 smoke 验证 acceptance criteria
   - 修改本文档：把 task status 从 `⏳ todo` 改为 `✅ done (commit <hash>)`，第 0.2 节 metric 表更新
   - Commit 引用 task ID（如 commit message 含 `T1.1`）
4. **拒绝事项**：
   - 不跨 Tier 顺序（T2 之前不动 T1 未完成项）
   - 不私自调整 success 标准（0.1 节）
   - 不 silent 改动 base YAML（T1.4 后只写 variant）
   - 不引入新 ML/DL 框架（除非 task explicitly says so，比如 T2.1 引入 optuna）

---

## 7. 关键文件索引

```
ifa/families/stock/backtest/
  replay_panel.py        # T1.1/T1.3 用得到（regime 字段已经在 panel 里）
  panel_evaluator.py     # T1.1/T1.2/T1.3/T2.4 主战场
  optimizer.py           # T2.1/T2.2 主战场
  promotion.py           # T1.1/T1.2/T1.3/T1.4/T2.3/T2.4 主战场
  objectives.py          # T2.3 加 sharpe metric

scripts/
  stock_edge_panel_tune.py   # CLI；每个 task 都加 flag

docs/
  stock_edge_tuning_work_list.md       # 本文档（living plan）
  stock_edge_v2_2_tuning_governance_handover_2026_05_05.md  # 上轮 handover（已 superseded）
```

---

*Living document — 最后更新由 commit `e630463` 之后 session 维护。*
