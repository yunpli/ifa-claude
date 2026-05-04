# SmartMoney Deep Dive — 主力资金流晚报

> **状态**：V2.1.2+ — 已迁移到 SW L2 主路径，需要 recompute + retrain 才能用
> **核心价值**：揭示当日主力（超大单+大单）资金流向、龙头股、ML 预测信号
>
> **打分原则**：`factors/liquidity.py` 的 attack/retreat/defense 评分已切到
> **连续 strength function**（2026-05-04, commit `ec3df2d`）；factors/flow/role/cycle
> 原本就是 rank-based × 权重的连续设计。详见 [`scoring-principles.md`](scoring-principles.md)。

---

## 1. 报告定位

SmartMoney 是 iFA 的「资金面深度」晚报，独立于一主三辅，专注 3 个问题：
1. **资金流向哪里？** — SW L1/L2 板块净流入排名
2. **龙头是谁？** — 各板块涨幅最大、量能最强的代表股
3. **明日机会？** — RF（短线 1-3 天）+ XGBoost（中长线 1-2 个月）双 ML 模型预测

---

## 2. 数据 schema（`smartmoney` 库）

### 2.1 Raw 层（TuShare 原始数据）
| 表 | 用途 | 覆盖 |
|---|---|---|
| `raw_daily` | 个股日 OHLCV + 涨跌幅 | 2021-01 → 今天 ✓ |
| `raw_daily_basic` | 个股基本面（换手率、市值）| 同上 |
| `raw_moneyflow` | 个股资金流（**核心！** 主力 / 超大单 / 大单 buy/sell）| 同上 |
| `raw_sw_member` | SW 成员全历史（含 in_date / out_date） | 1993 起 |
| `sw_member_monthly` | SW 月度快照（PIT-correct） | 2021-01 → 65 个月 |
| `raw_index_daily` | 指数日行情（上证 / 深证 / 创业板等 8 个）| 2021-01 → 今天 |
| `raw_kpl_list` | 涨停池 | 同上 |
| `raw_top_list` | 龙虎榜 | 同上 |
| `raw_block_trade` | 大宗交易 | 同上 |
| `raw_moneyflow_hsgt` | 北向资金 | 2021 起（每年约 5-10 天缺口）|
| `trade_cal` | 交易日历 | 2015 → 2026-12 |

### 2.2 计算层（每日 ETL + Compute 输出）
| 表 | 用途 |
|---|---|
| `sector_moneyflow_sw_daily` | SW L2 板块资金流日聚合（V2.1.2+ 主源）|
| `factor_daily` | 个股每日因子 |
| `sector_state_daily` | 板块状态（phase: 启动/加速/高潮/衰退/冷却/蛰伏/反弹 7 种）|
| `market_state_daily` | 市场整体 state |
| `stock_signals_daily` | ML 预测信号（RF + XGBoost）|

### 2.3 ML 训练层
| 路径 | 内容 |
|---|---|
| `~/ifaenv/models/smartmoney/<version>/rf.joblib` | RandomForest 短线模型（horizon=1d）|
| `~/ifaenv/models/smartmoney/<version>/xgb.joblib` | XGBoost 中长线模型（horizon=20d）|

---

## 3. 关键架构演化

### 3.1 V2.0 → V2.1（SW 统一）
**问题**：早期使用东财概念（DC）+ 同花顺（THS）作为板块源，但：
- DC 只有 ~18 天近期数据，无法 PIT 回溯
- THS 没有 in_date/out_date，会引入 forward-looking bias

**解决**：所有板块逻辑切换到申万（SW），唯一有完整 in_date/out_date 历史的源（1993 起）。

### 3.2 V2.1.2（L2 pct_change 修正 — **这是 recompute 必须的原因**）
**Bug**：所有 5 个 SmartMoney factor SQL JOIN 都用 `l1_code` 而非 `l2_code`，导致 L2 板块 inheriting L1 父级的均值。

**实测**：2026-04-30 当天，电子 L1 下 6 个 L2 子板块 pct_change 实际离散度 5.73%，但 ML 模型看到的全是 L1 均值（0% spread），导致信号严重失真。

**修复 + 影响**：
- 所有 factor JOIN 改为 L2-with-L1-fallback
- **历史数据必须 recompute**（覆盖旧的 L1-proxy 值）
- ML 模型必须 **retrain**（picking up 新的 L2 spread 信号）
- → `bash scripts/recompute_smartmoney_required.sh` 是 V2.1.2+ 部署后**首次必跑**

---

## 4. 两层 recompute 体系（重要）

### 4.1 Layer A — Daily Light Recompute（每日生产，~10-15 min）
为今晚的 smartmoney evening 服务，仅计算**当天单日**：

```bash
ifa smartmoney etl     --report-date <today_BJT> --mode production   # 5-10 min
ifa smartmoney compute --report-date <today_BJT> --mode production   # 1-3 min
```

**做了什么**：
- ETL 拉 TuShare 当天数据（A 股 15:30 收盘 → TuShare 17:00 publish → 我们 18:00 跑）
- Compute 算今天的 factor / sector_state / leader / candidate
- **复用现有训练好的 ML 模型**做推理，不重训

**调度**：每个交易日 18:00 BJT（在 smartmoney evening 18:20 之前留 20 分钟 buffer）

### 4.2 Layer B — Weekly Heavy Recompute（每周日，~30-90 min）
全量历史回填 + 模型重训：

```bash
bash scripts/recompute_smartmoney_required.sh
```

**做了什么**：
1. `ifa smartmoney compute --start 2021-01-04 --end <today>` — 重算 factor / sector_state / leader / candidate **整个 5 年范围**（idempotent UPSERT，覆盖旧 V2.1.1 L1-proxy 残留）
2. `ifa smartmoney train --in-sample-start 2021-01-04 --in-sample-end 2025-10-31 --oos-start 2025-11-01 --oos-end <today>` — 重训 RF (horizon=1d) + XGBoost (horizon=20d)，OOS 评估，写新版本到 `~/ifaenv/models/smartmoney/<version>/`

**调度**：每周日 22:30 BJT（在 ningbo refresh weekly 22:00 之后跑，错开 CPU）

### 4.3 关系图
```
T-1 evening 18:00  ──────  Daily light recompute（用 T-1 close 数据）
                           ↓
T evening 18:20    ──────  smartmoney evening report（用 T-1 信号 + T 收盘行情）

每周日 22:30       ──────  Weekly heavy recompute + retrain（更新模型版本）
                           ↓
下周一起的 daily light recompute 自动加载新模型
```

---

## 5. CLI 命令参考

```bash
# 日常生产
ifa smartmoney etl     --report-date <today> --mode production
ifa smartmoney compute --report-date <today> --mode production
ifa smartmoney evening --report-date <today> --mode production --generate-pdf

# 周度全量
bash scripts/recompute_smartmoney_required.sh
bash scripts/recompute_smartmoney_required.sh --version v2026_05_v2  # A/B 测试新版本
bash scripts/recompute_smartmoney_required.sh --skip-compute         # 只重训不重算

# Compute 范围模式
ifa smartmoney compute --start 2024-01-01 --end 2024-12-31 --mode production

# 单独训练（一般通过上面的 .sh 脚本调用，不直接用）
ifa smartmoney train --in-sample-start 2021-01-04 --in-sample-end 2025-10-31 \
                     --oos-start 2025-11-01 --oos-end 2026-04-30 \
                     --version v2026_05 --short-horizon 1 --long-horizon 20 \
                     --source sw_l2 --mode production
```

---

## 6. 报告 sections（晚报）

完整列表见 [`family-reference.md`](./family-reference.md) 的 SmartMoney 章节。要点：
- §02 — 10 日资金面水位迷你折线图
- §03/04 — 各板块 top-5 个股钻取
- §05 — 高质净流入门槛（≥10亿 AND 超大单占比 ≥2%）
- §06 — 拥挤度风险卡片
- §07 — 7×N 相位轨迹矩阵 + 转移概率预测
- §08 — 明日操作建议（含板块内股票 + 算法标注）
- §10 — 双 ML 模型推荐（短线 RF + 中长线 XGB）
- §11 — 章节定义 + 术语词汇表

---

## 7. 已知限制

- **TuShare 部分接口有历史延迟**：`raw_dc_member` 已弃用（仅 18 天近期），`raw_moneyflow_ind_dc`/`ths` 早年无数据但近期可用
- **北向资金（hsgt）每年约 5-10 天缺口** — 是交易所不开放日，正常现象
- **Layer B recompute 是 CPU-bound + 30-90 分钟** — 不要在生产高峰跑
- **每次 recompute 会创建新模型版本** — 旧版本不自动清理（注意磁盘）

---

## 8. 故障排查

| 症状 | 解决 |
|---|---|
| smartmoney evening 报"模型未找到" | `bash scripts/recompute_smartmoney_required.sh` 至少跑一次 |
| 当天 sector_state_daily 缺数据 | 先跑 daily light recompute（compute --report-date <today>）|
| ETL 拉数据失败 | 检查 TuShare token 是否有效 / 是否限流（等 30 分钟）|
| 因子 SQL 报 NULL 异常 | 通常是 trade_cal 缺当天 — 跑 `python scripts/is_trading_day.py --refresh` |

---

完整运维流程见 [`OPERATIONS.md`](./OPERATIONS.md)。
