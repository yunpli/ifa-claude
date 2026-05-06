from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

import pandas as pd

from ifa.core.report.timezones import BJT
from ifa.families.stock.analysis import StockEdgeAnalysis
from ifa.families.stock.context import StockEdgeRequest, build_context
from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.data.gateway import _build_sector_leaders
from ifa.families.stock.data.snapshot import StockEdgeSnapshot
from ifa.families.stock.report import build_report_model, render_report_assets
from ifa.families.stock.report.charts import build_peer_context_charts
from ifa.families.stock.strategies import build_rule_baseline_plan
from tests.stock.test_context import FakeCalendar


class RunMode(str, Enum):
    manual = "manual"


@dataclass(frozen=True)
class FakeSettings:
    output_root: Path
    run_mode: RunMode = RunMode.manual


def _analysis() -> StockEdgeAnalysis:
    ctx = build_context(
        StockEdgeRequest(ts_code="300042.SZ", requested_at=dt.datetime(2026, 5, 5, 15, 1, tzinfo=BJT)),
        calendar=FakeCalendar({dt.date(2026, 5, 5)}),
    )
    rows = []
    for i in range(60):
        close = 10 + i * 0.1
        rows.append({
            "trade_date": dt.date(2026, 1, 1) + dt.timedelta(days=i),
            "open": close - 0.05,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "amount": 100000,
        })
    snapshot = StockEdgeSnapshot(
        ctx=ctx,
        daily_bars=LoadResult("daily_bars", pd.DataFrame(rows), "postgres", "ok", rows=60, as_of=ctx.as_of.as_of_trade_date, required=True),
        daily_basic=LoadResult("daily_basic", {}, "postgres", "ok", rows=7, as_of=ctx.as_of.as_of_trade_date, required=True),
        moneyflow=LoadResult("moneyflow", pd.DataFrame({"net_mf_amount": [10.0] * 7}), "postgres", "ok", rows=7),
        sector_membership=LoadResult("sector_membership", {}, "postgres", "ok", rows=1),
        ta_context=LoadResult(
            "ta_context",
            {
                "candidates": [],
                "warnings": [],
                "regime": {},
                "setup_metrics": [
                    {
                        "setup_name": "T1",
                        "triggers_count": 12,
                        "winrate_60d": 58.3,
                        "avg_return_60d": 3.2,
                        "pl_ratio_60d": 1.8,
                        "winrate_250d": 51.0,
                        "decay_score": 7.3,
                        "combined_score_60d": 0.42,
                    }
                ],
            },
            "postgres",
            "ok",
            rows=2,
        ),
        research_lineup=LoadResult("research_lineup", {}, "postgres", "ok", rows=1),
    )
    plan = build_rule_baseline_plan(snapshot)
    return StockEdgeAnalysis(ctx=ctx, snapshot=snapshot, plan=plan)


def test_build_report_model_contains_plan_and_freshness():
    report = build_report_model(_analysis())

    assert report["ts_code"] == "300042.SZ"
    assert report["plan"]["probability"]["model_version"] == "prediction_surface_v1"
    assert report["plan"]["probability"]["prob_hit_20_40d"] is not None
    assert report["freshness"]
    assert report["record_status_degraded_reasons"] == []
    assert report["price_context"]["nearest_support"] is not None
    assert report["price_context"]["nearest_resistance"] is not None
    assert report["price_context"]["recent_20d_high"] is not None
    assert report["price_context"]["levels"]
    assert report["chart_context"]["daily_kline_svg"].startswith("<svg")
    assert "MACD" in report["chart_context"]["macd_svg"]
    assert report["strategy_validation"]["available"] is True
    assert report["strategy_matrix"]["aggregate_score"] > 0
    assert report["strategy_matrix"]["signals"]
    assert report["decision_layer"]["decision_5d"]["decision"]
    assert report["decision_layer"]["decision_10d"]["decision"]
    assert report["decision_layer"]["decision_20d"]["decision"]
    assert report["decision_layer"]["decision_5d"]["score_type"] == "execution_score"
    assert report["decision_layer"]["decision_5d"]["data_quality"]["status"] == "partial"
    assert report["prediction_context"]["decision"]
    assert report["prediction_context"]["next_5d"]
    assert report["prediction_context"]["sell_targets"]
    assert report["prediction_context"]["entry_fill_probability"] is not None
    assert report["prediction_context"]["return_quantiles"]["p90"] is not None
    assert report["prediction_context"]["best_opportunity"] is not None
    assert report["prediction_context"]["opportunities"]
    assert report["scenario_tree"]["available"] is True
    assert report["scenario_tree"]["branches"]
    assert "LLMClient" in report["scenario_tree"]["llm_tool"]
    assert report["sector_leaders"]["available"] is False


def test_render_report_assets_writes_under_output_root(tmp_path):
    rendered = render_report_assets(_analysis(), FakeSettings(tmp_path))  # type: ignore[arg-type]

    assert rendered.html_path.exists()
    assert rendered.md_path.exists()
    assert rendered.html_path.parts[-3:-1] == ("20260505", "stock_edge")
    html = rendered.html_path.read_text(encoding="utf-8")
    assert "个股作战室" in html
    assert "今日结论" in html
    assert "三周期决策" in html
    assert "价格作战地图" in html
    assert "系统评分用于比较机会质量和风险，不等同于确定性上涨概率" in html
    assert "未来5日执行路线" in html
    assert "为什么这样判断" in html
    assert "40日+" not in html.split("策略矩阵")[0]
    assert "最近支撑" in html
    assert "日线 K 线与关键价位" in html
    assert "策略矩阵" in html
    assert "同板块位置" in html
    assert "策略验证摘要" not in html
    assert "60日胜率" not in html
    assert "目标价格兼容审计" not in html
    assert "参数哈希" not in html
    assert "数据新鲜度" not in html
    assert "免责声明" in html
    assert "不构成投资建议" in html
    md = rendered.md_path.read_text(encoding="utf-8")
    assert "## 今日结论" in md
    assert "## 三周期决策" in md
    assert "## 价格作战地图" in md
    assert "## 未来5日执行路线" in md
    assert "## 风险纪律" in md
    assert "40 日内触及" not in md
    assert "兼容概率面" not in md
    assert "## 数据新鲜度" not in md


def test_peer_charts_keep_target_when_target_is_not_top_ranked():
    peers = [
        {"ts_code": f"00000{i}.SZ", "name": f"同行{i}", "return_5d_pct": 20 - i, "return_10d_pct": 18 - i, "return_15d_pct": 15 - i, "total_mv": 500 - i * 10}
        for i in range(12)
    ]
    peers.append(
        {
            "ts_code": "300042.SZ",
            "name": "朗科科技",
            "is_target": True,
            "return_5d_pct": -8.5,
            "return_10d_pct": -3.2,
            "return_15d_pct": 1.1,
            "total_mv": 80.0,
        }
    )
    peers.append({"ts_code": "002618.SZ", "name": "丹邦退(退市)", "return_5d_pct": 88.0, "return_10d_pct": 90.0, "return_15d_pct": 100.0, "total_mv": 4.0})

    charts = build_peer_context_charts(peers)

    assert "朗科科技" in charts["peer_size_return_svg"]
    assert "目标股黑色外圈" in charts["peer_size_return_svg"]
    assert "朗科科技" in charts["peer_return_ladder_svg"]
    assert "丹邦退" not in charts["peer_size_return_svg"]
    assert "丹邦退" not in charts["peer_return_ladder_svg"]


def test_sector_leaders_filter_delisted_peers_from_comparison_universe():
    peers = [
        {"ts_code": "002938.SZ", "name": "鹏鼎控股", "return_5d_pct": -4.5, "total_mv": 1670.0},
        {"ts_code": "002618.SZ", "name": "丹邦退(退市)", "return_5d_pct": 43.2, "total_mv": 4.2},
        {"ts_code": "002288.SZ", "name": "*ST超华(退市)", "return_5d_pct": 99.0, "total_mv": 3.0},
        {"ts_code": "688655.SH", "name": "迅捷兴", "return_5d_pct": 26.1, "total_mv": 30.0},
        {"ts_code": "002463.SZ", "name": "沪电股份", "return_5d_pct": -3.8, "total_mv": 1000.0, "list_status": "L"},
        {"ts_code": "000000.SZ", "name": "非上市样本", "return_5d_pct": 88.0, "total_mv": 10.0, "list_status": "D"},
    ]

    leaders = _build_sector_leaders(peers, "002938.SZ")
    names = {row["name"] for rows in leaders.values() for row in rows}

    assert "鹏鼎控股" in names
    assert "迅捷兴" in names
    assert "丹邦退(退市)" not in names
    assert "*ST超华(退市)" not in names
    assert "非上市样本" not in names
