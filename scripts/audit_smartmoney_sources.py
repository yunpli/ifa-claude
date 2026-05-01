"""SmartMoney-specific TuShare capability audit.

Probes the 16 endpoints that are most directly relevant to a capital-flow /
sentiment-cycle / leader-detection engine, organised by the 4-factor / 6-role /
7-stage logic in smartmoney.txt §2.

Usage:
    uv run python scripts/audit_smartmoney_sources.py

Read-only; touches no DB.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import tushare as ts
from rich.console import Console
from rich.table import Table

from ifa.config import get_settings

console = Console()

# Use a recent confirmed-trading date so the probes return real rows.
RECENT_DATE = "20260430"
RECENT_RANGE_START = "20260420"

# Use a known liquid stock + index for stk-level probes.
SAMPLE_STOCK = "000001.SZ"   # 平安银行
SAMPLE_INDEX = "000001.SH"   # 上证综指
SAMPLE_INDUSTRY = "801080.SI"  # 申万电子


def _row_summary(df, narrative_cols: int = 4) -> str:
    if df is None or len(df) == 0:
        return "(empty)"
    cols = list(df.columns)[:narrative_cols]
    if len(df) > 0:
        first = df.iloc[0]
        sample = "; ".join(f"{c}={first[c]}" for c in cols if c in df.columns)
    else:
        sample = ""
    return f"{len(df)} rows; cols={list(df.columns)[:8]}{'…' if len(df.columns) > 8 else ''}; sample: {sample}"


def _probe(name: str, fn, narrative: str, priority: str) -> tuple[str, str, str, str, str]:
    """Returns (name, status, sample, narrative, priority)."""
    try:
        df = fn()
        if df is None or (hasattr(df, "empty") and df.empty):
            return name, "ZERO", "(0 rows)", narrative, priority
        return name, "OK", _row_summary(df), narrative, priority
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        return name, "FAIL", msg[:140] + ("…" if len(msg) > 140 else ""), narrative, priority


def _probes(pro: Any) -> list:
    return [
        # ─── 高优先级：4 因子 + 6 角色 + 龙头识别 ──────────────────────────
        ("moneyflow_ind_dc",
         lambda: pro.moneyflow_ind_dc(trade_date=RECENT_DATE),
         "东财板块级资金流（板块直接给净流入，比个股聚合更准）",
         "P0-HIGH"),
        ("moneyflow_ind_ths",
         lambda: pro.moneyflow_ind_ths(trade_date=RECENT_DATE),
         "同花顺板块级资金流（与东财跨源校验）",
         "P0-HIGH"),
        ("kpl_concept",
         lambda: pro.kpl_concept(trade_date=RECENT_DATE),
         "开盘啦概念榜（专做炒作热点 / 主线识别，对短线最重要）",
         "P0-CRITICAL"),
        ("kpl_concept_cons",
         lambda: pro.kpl_concept_cons(trade_date=RECENT_DATE),
         "开盘啦概念成分（板块内股票）",
         "P0-HIGH"),
        ("kpl_list",
         lambda: pro.kpl_list(trade_date=RECENT_DATE),
         "开盘啦榜单（涨停 / 连板 / 炸板细分，比 limit_list_d 更细）",
         "P0-CRITICAL"),
        ("top_inst",
         lambda: pro.top_inst(trade_date=RECENT_DATE),
         "龙虎榜机构席位（机构 vs 游资 vs 北向；先前 doc-only）",
         "P0-HIGH"),
        ("ths_hot",
         lambda: pro.ths_hot(trade_date=RECENT_DATE),
         "同花顺热榜（情绪 / 关注度）",
         "P0-MED"),
        ("dc_hot",
         lambda: pro.dc_hot(trade_date=RECENT_DATE),
         "东财热榜",
         "P0-MED"),
        ("dc_index",
         lambda: pro.dc_index(trade_date=RECENT_DATE),
         "东财概念指数",
         "P0-MED"),
        ("dc_member",
         lambda: pro.dc_member(),
         "东财概念成分（无 trade_date 参数，全集）",
         "P0-MED"),

        # ─── 中优先级：拥挤 / 主力意图 / 风险过滤 ─────────────────────────
        ("cyq_chips",
         lambda: pro.cyq_chips(ts_code=SAMPLE_STOCK, start_date=RECENT_RANGE_START, end_date=RECENT_DATE),
         "筹码分布（识别高位套牢盘 → 拥挤板块判断）",
         "P1-HIGH"),
        ("cyq_perf",
         lambda: pro.cyq_perf(ts_code=SAMPLE_STOCK, start_date=RECENT_RANGE_START, end_date=RECENT_DATE),
         "筹码胜率",
         "P1-HIGH"),
        ("block_trade",
         lambda: pro.block_trade(trade_date=RECENT_DATE),
         "大宗交易（主力批量动作）",
         "P1-MED"),
        ("stk_holdertrade",
         lambda: pro.stk_holdertrade(ts_code=SAMPLE_STOCK, start_date="20260101", end_date=RECENT_DATE),
         "股东 / 高管增减持",
         "P1-MED"),
        ("pledge_stat",
         lambda: pro.pledge_stat(ts_code=SAMPLE_STOCK),
         "质押统计（风险股过滤）",
         "P1-LOW"),
        ("share_float",
         lambda: pro.share_float(ts_code=SAMPLE_STOCK),
         "解禁（拥挤 / 限售解锁风险）",
         "P1-LOW"),

        # ─── 已知可用，作为基线确认（quick re-verify）─────────────────────
        ("moneyflow",
         lambda: pro.moneyflow(trade_date=RECENT_DATE),
         "（基线）个股主力资金流",
         "BASELINE"),
        ("limit_list_d",
         lambda: pro.limit_list_d(trade_date=RECENT_DATE),
         "（基线）涨跌停明细",
         "BASELINE"),
        ("top_list",
         lambda: pro.top_list(trade_date=RECENT_DATE),
         "（基线）龙虎榜个股",
         "BASELINE"),
        ("daily",
         lambda: pro.daily(trade_date=RECENT_DATE),
         "（基线）全市场日线",
         "BASELINE"),
        ("daily_basic",
         lambda: pro.daily_basic(trade_date=RECENT_DATE),
         "（基线）日级指标（换手 / 估值 / 市值）",
         "BASELINE"),
        ("sw_daily",
         lambda: pro.sw_daily(ts_code=SAMPLE_INDUSTRY, start_date=RECENT_RANGE_START, end_date=RECENT_DATE),
         "（基线）申万行业日线",
         "BASELINE"),
    ]


def _print_results(probes: list[tuple]) -> None:
    table = Table(title="SmartMoney TuShare audit", show_lines=False)
    table.add_column("Endpoint", style="cyan")
    table.add_column("Priority", style="magenta")
    table.add_column("Status")
    table.add_column("Sample / Error", overflow="fold")
    table.add_column("Narrative", overflow="fold")

    for name, status, sample, narrative, priority in probes:
        if status == "OK":
            tag = "[green]OK[/green]"
        elif status == "ZERO":
            tag = "[yellow]ZERO[/yellow]"
        else:
            tag = "[red]FAIL[/red]"
        table.add_row(name, priority, tag, sample, narrative)
    console.print(table)


def main() -> None:
    settings = get_settings()
    ts.set_token(settings.tushare_token.get_secret_value())
    pro = ts.pro_api()

    console.rule("[bold]SmartMoney TuShare audit (sample date = 2026-04-30)[/bold]")
    results = []
    for name, fn, narrative, priority in _probes(pro):
        results.append(_probe(name, fn, narrative, priority))
    _print_results(results)

    # Aggregate counters
    ok = sum(1 for r in results if r[1] == "OK")
    zero = sum(1 for r in results if r[1] == "ZERO")
    fail = sum(1 for r in results if r[1] == "FAIL")
    console.print(f"\n[bold]Summary:[/bold] {ok} OK, {zero} ZERO, {fail} FAIL out of {len(results)} endpoints.")


if __name__ == "__main__":
    main()
