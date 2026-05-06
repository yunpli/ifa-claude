# Stock Edge v2.2 三周期 Backfill 执行建议

> 本文基于当前数据盘点和 `scripts/stock_edge_data_backfill.py` dry-run。  
> 本轮建议仍不正式执行补数；正式执行应在用户确认后运行。

## 1. 是否需要跑各 family

| Family | 是否需要跑 | 判断 |
|---|---|---|
| `core` | 当前不需要 | `raw_daily`, `raw_daily_basic`, `raw_moneyflow` 已覆盖 2021-01-04 → 2026-04-30 |
| `event` | 当前不需要 | 龙虎榜、机构龙虎榜、涨停/炸板、涨跌停明细、大宗交易已覆盖 |
| `market` | 当前不需要 | 两融、北向市场已覆盖 |
| `sw` | 当前不需要 | SW L2 成员、资金、状态已覆盖 |
| `intraday` | 当前需要 | 5min/30min/60min 仅约 1 只股票，不能支持广泛 universe 的 5d 执行和调参 |

结论：当前只跑 `intraday` family。不要重复补日线、事件、市场、SW 数据。

## 2. Intraday 策略

| 问题 | 建议 |
|---|---|
| 是否只补 5min | 是，优先只从 TuShare 拉 5min |
| 30min / 60min | 从 5min 聚合派生，不重复拉数据源 |
| 默认窗口 | Top 500 最近 180 个交易日 |
| 扩展窗口 | 如后续需要，可 Top 800 × 180 或 Top 1200 × 252 |
| 当前目标股不在 Top 500 | 额外跑 target universe |
| focus/key focus/watchlist | 应支持独立 target-file 补齐 |
| 预算 | 当前估算远低于 10GB，但仍保留 `--max-new-data-gb 10` guard |

## 3. Universe 定义

Top-liquidity 应按当前 repo 的 `load_top_liquidity_universe()` 口径：基于本地日线/daily_basic 的成交额/流动性排名，选择最近 as-of 的高流动性股票。这个口径比全市场更适合 5d/10d 交易，因为低流动性股票的滑点、停牌、成交概率会污染短线调参。

推荐：

- 基础：`target + Top 500`；
- 若需要更高覆盖：Top 800；
- 若用于更完整 overnight 调参：Top 1200 × 252；
- 不建议第一轮全市场 5000+ 股票多年 5min。

## 4. 预算估算

来自 dry-run `codex_dryrun_top500_180d_v2`：

| 方案 | Universe | 窗口 | 预计行数 | 预计新增大小 | API 调用 | 预计时间 | 是否低于 10GB |
|---|---:|---:|---:|---:|---:|---:|---|
| A | Top 500 | 180 trading days 5min | 4,317,893 | 228.183 MB | 500 | 约 10 min | 是 |
| B | Top 800 | 180 trading days 5min | 约 6,912,000 | 约 0.36 GB | 800 | 约 16 min | 是 |
| C | Top 1200 | 252 trading days 5min | 约 14,515,200 | 约 0.73 GB | 1200 | 约 24 min | 是 |

实际运行时间会受 TuShare 限流、网络和 parquet 写入影响；预算 guard 仍以脚本实际估算为准。

## 5. 推荐命令表

| 用途 | 命令 | 为什么需要 | 预计数据量 | 是否必须 | 备注 |
|---|---|---|---|---|---|
| 盘点当前数据 | `uv run python scripts/stock_edge_data_backfill.py --inventory-only` | 确认本地数据状态，不触发补数 | 0 | 是 | 输出 inventory 到 ifaenv |
| Top500 dry-run | `uv run python scripts/stock_edge_data_backfill.py --universe top-liquidity --limit 500 --family intraday --intraday-days 180 --dry-run --run-id stockedge_top500_180d` | 估算任务、预算、API 调用 | 0 | 是 | 正式执行前必须跑 |
| Top500 正式执行 | `uv run python scripts/stock_edge_data_backfill.py --universe top-liquidity --limit 500 --family intraday --intraday-days 180 --execute --run-id stockedge_top500_180d` | 补齐 5d 执行模型所需分钟线 | 约 0.22GB | 是，进入调参前 | 只补 5min，派生 30/60 |
| Resume | `uv run python scripts/stock_edge_data_backfill.py --run-id stockedge_top500_180d --resume --execute` | 中断后继续 | 仅剩余任务 | 是 | 使用 checkpoint |
| 目标股 dry-run | `uv run python scripts/stock_edge_data_backfill.py --target 300042.SZ --family intraday --intraday-days 180 --dry-run --run-id stockedge_target_300042_180d` | 目标股不在 Top500 或需立即分析 | 0 | 条件必须 | 替换代码 |
| 目标股正式执行 | `uv run python scripts/stock_edge_data_backfill.py --target 300042.SZ --family intraday --intraday-days 180 --execute --run-id stockedge_target_300042_180d` | 单股补齐分钟线 | 很小 | 条件必须 | 报告前可跑 |
| Watchlist dry-run | `uv run python scripts/stock_edge_data_backfill.py --target-file /path/to/watchlist.txt --family intraday --intraday-days 180 --dry-run --run-id stockedge_watchlist_180d` | focus/key focus/watchlist 补齐 | 取决于股票数 | 建议 | 文件一行一个 ts_code |
| 扩展 Top800 | `uv run python scripts/stock_edge_data_backfill.py --universe top-liquidity --limit 800 --family intraday --intraday-days 180 --dry-run --run-id stockedge_top800_180d` | 扩大短线调参 universe | 约 0.36GB | 非必须 | 先 dry-run |
| 扩展 Top1200×252 | `uv run python scripts/stock_edge_data_backfill.py --universe top-liquidity --limit 1200 --family intraday --intraday-days 252 --dry-run --run-id stockedge_top1200_252d` | 更充分 overnight 调参 | 约 0.73GB | 非必须 | 仍需预算检查 |
| 超预算显式执行 | `... --allow-over-budget --max-new-data-gb 20` | 只有用户明确同意时使用 | >10GB | 非默认 | 日志会提示风险 |

## 6. Post-run validation 命令

当前脚本已输出 manifest/checkpoint，可先用轻量命令验证文件和视图：

```bash
uv run python scripts/stock_edge_data_backfill.py --inventory-only
```

建议追加一次 DuckDB 视图检查：

```bash
uv run python - <<'PY'
from ifa.families.stock.db.duckdb_client import init_duckdb, get_conn
init_duckdb()
conn = get_conn(read_only=True)
for view in ["intraday_5min", "intraday_30min", "intraday_60min"]:
    try:
        row = conn.execute(f"""
            SELECT COUNT(*) AS rows,
                   COUNT(DISTINCT ts_code) AS stocks,
                   MIN(CAST(trade_time AS DATE)) AS min_date,
                   MAX(CAST(trade_time AS DATE)) AS max_date
            FROM {view}
        """).fetchone()
        print(view, row)
    except Exception as exc:
        print(view, type(exc).__name__, exc)
PY
```

重复键检查：

```bash
uv run python - <<'PY'
from ifa.families.stock.db.duckdb_client import init_duckdb, get_conn
init_duckdb()
conn = get_conn(read_only=True)
print(conn.execute("""
    SELECT COUNT(*) FROM (
      SELECT ts_code, freq, trade_time, COUNT(*) c
      FROM intraday_5min
      GROUP BY 1,2,3
      HAVING COUNT(*) > 1
    )
""").fetchone()[0])
PY
```

## 7. 路径

| 类型 | 路径 |
|---|---|
| 日志 | `/Users/neoclaw/claude/ifaenv/logs/stock_edge_data_backfill/<run_id>.log` |
| manifest | `/Users/neoclaw/claude/ifaenv/manifests/stock_edge_data_backfill/<run_id>/manifest.json` |
| checkpoint | `/Users/neoclaw/claude/ifaenv/manifests/stock_edge_data_backfill/<run_id>/checkpoint.json` |
| retry queue | `/Users/neoclaw/claude/ifaenv/manifests/stock_edge_data_backfill/<run_id>/retry_queue.jsonl` |
| parquet | `/Users/neoclaw/claude/ifaenv/duckdb/parquet/intraday_5min/` |
| DuckDB | `/Users/neoclaw/claude/ifaenv/duckdb/stock.duckdb` |

## 8. 降级规则

如果 dry-run 预计超过 10GB，按顺序降级：

1. Top1200 → Top800；
2. Top800 → Top500；
3. 252 trading days → 180；
4. 180 → 120；
5. 只补 target + watchlist；
6. 保留日线版本，5d execution confidence 降级为 medium/low。

禁止用全市场多年 5min 作为默认方案。

