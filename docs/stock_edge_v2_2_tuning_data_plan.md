# Stock Edge v2.2 三周期调参与验证数据计划

## 1. 当前数据盘点结论

根据已执行的 inventory-only / dry-run 盘点：

| 数据族 | 当前状态 | 对 5/10/20 的判断 |
|---|---|---|
| 日线 OHLCV | `smartmoney.raw_daily` 覆盖 2021-01-04 → 2026-04-30，约 654.8 万行 | 足够，不需要重复补 |
| daily_basic | 覆盖 2021-01-04 → 2026-04-30 | 足够 |
| moneyflow | 覆盖 2021-01-04 → 2026-04-30 | 足够 |
| 龙虎榜 / 机构龙虎榜 | 覆盖 2021-01-04 → 2026-04-30 | 足够 |
| 涨停/炸板/涨跌停明细 | 覆盖 2021-01-04 → 2026-04-30 | 足够 |
| 大宗交易 | 覆盖 2021-01-04 → 2026-04-30 | 足够 |
| SW L2 成员/资金/状态 | 已覆盖到 2026-04-30/2026-05-01 | 足够 |
| SmartMoney 因子/市场状态 | 已覆盖 2021-01-04 → 2026-04-30 | 足够 |
| TA candidates/setup/warnings | 覆盖 2024-12-02 → 2026-04-30 | 对当前 v2.2 足够，历史更长可后续增强 |
| 交易日历 | 覆盖 2015-01-01 → 2026-12-31 | 足够 |
| 5min/30min/60min | 当前约 1 只股票，13 个 parquet | 不足以支持 Top universe 短线执行和调参 |
| Research/Fundamental cache | 有少量 | 不阻塞三周期，20d 辅助 |

完整 inventory 文件位置：

`/Users/neoclaw/claude/ifaenv/manifests/stock_edge_data_backfill/codex_dryrun_top500_180d_v2/inventory.md`

## 2. 各策略类型数据需求

| 策略类型 | 必需数据 | 当前是否足够 | 缺口 |
|---|---|---|---|
| 日线规则 / K线 / 趋势 | OHLCV, MA, ATR, 支撑压力 | 足够 | 无 |
| 统计 replay | 日线 OHLCV 360-900 行 | 足够 | 三周期 label 需重算 |
| 目标/止损路径 | high/low/close forward path | 足够 | 5/10/20 target/stop label 需扩展 |
| entry fill | 日线 high/low；增强版需要 5min | 日线够，分钟不足 | Top universe 5min |
| gap/open risk | 日线 open/prev close | 足够 | 无 |
| intraday/VWAP/volume profile | 5min OHLCV/amount | 不足 | 需补 intraday family |
| T+0 | 5min + 是否有底仓 | 数据不足；持仓由用户输入 | 需补 5min，报告约束底仓 |
| moneyflow/orderflow | raw_moneyflow | 足够 | 无 |
| 龙虎榜/涨停/炸板 | raw_top_list/raw_top_inst/raw_kpl_list/raw_limit_list_d | 足够 | 事件稀疏不是缺数 |
| SW L2/SmartMoney | sw_member_monthly, sector_moneyflow, factor/state | 足够 | 无 |
| peer relative momentum | SW 成员 + raw_daily/daily_basic/moneyflow | 足够 | 同行财务 Research 不全 |
| ML/DL 即时模型 | 日线特征 + labels；Kronos cache 可选 | 日线足够，Kronos cache 未确认广泛可用 | Kronos 不阻塞 |
| LLM cache | smartmoney.llm_* / research memory | 部分 | 只做解释，不阻塞 |
| Research/Fundamental | research.report_runs/factors/pdf cache | 部分 | deferred 深化 |

## 3. Label 需求

| Horizon | 必须生成的 labels |
|---|---|
| 5d | `return_5d_pct`, `positive_5d`, `target_first_5d`, `stop_first_5d`, `max_drawdown_5d_pct`, `mfe_5d_pct`, `mae_5d_pct`, `entry_fill_5d`, `adverse_gap_next_open`, `slippage_bucket` |
| 10d | `return_10d_pct`, `positive_10d`, `target_first_10d`, `stop_first_10d`, `max_drawdown_10d_pct`, `mfe_10d_pct`, `mae_10d_pct`, `moneyflow_persistence_10d`, `sector_persistence_10d` |
| 20d | `return_20d_pct`, `positive_20d`, `target_first_20d`, `stop_first_20d`, `max_drawdown_20d_pct`, `mfe_20d_pct`, `mae_20d_pct`, `position_loss_budget_hit`, `strategy_decay_bucket` |

## 4. Training / validation 数据窗口

| 用途 | 建议窗口 | 理由 |
|---|---|---|
| 全市场 preset | 2021-01-04 至最新可用交易日 | 覆盖多轮 A 股风格切换 |
| 单股 pre-report overlay | 最近 360-900 根日线 | 当前 YAML 已有此范围，足以单股局部调参 |
| 5min intraday | Top500 最近 180 个交易日 | 支持 5d/10d 执行与 VWAP，不超过 10GB |
| 30min/60min | 从 5min 派生 | 避免重复拉取和存储 |
| TA validation | 当前 2024-12 起可用 | 先用现有；如需更长期再扩展 TA backfill |
| Research/Fundamental | 现有缓存 | 20d 辅助，不作为 tuning 主标签 |

## 5. Post-run validation 数据检查

正式补 intraday 后必须检查：

- Top universe 实际覆盖股票数；
- 每只股票最近 180 个交易日 5min rows；
- 30min/60min view 是否从 5min 派生成功；
- parquet 文件数和总大小；
- DuckDB `intraday_5min`, `intraday_30min`, `intraday_60min` 是否可查询；
- `freq` 字段是否一致；
- 是否存在重复 `(ts_code, freq, trade_time)`。

## 6. 不需要补的数据

本轮不跑：

- `core family`，除非未来发现目标股本地日线缺口；
- `event family`，当前已覆盖；
- `market family`，当前已覆盖；
- `sw family`，当前已覆盖；
- Research/财报全文；
- 40d/长期额外数据；
- 全市场多年 5min。

