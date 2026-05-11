from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader

from ifa.families.market import _common as common
from ifa.families.market import data as mdata
from ifa.families.market.hypothesis_review import build_noon_hypothesis_reviews


def _sector(
    *,
    code: str,
    name: str,
    pct: float | None,
    rank: int,
    level: str = "L1",
    amount: float | None = 800_000_000.0,
    up_ratio: float | None = 0.65,
) -> mdata.SectorBar:
    source = "申万官方日线" if level == "L1" else "成分股实时代理"
    method = "official_sw_daily_eod" if level == "L1" else "constituent_rt_k_proxy"
    return mdata.SectorBar(
        code=code,
        name=name,
        close=1000.0,
        pct_change=pct,
        trade_date=dt.date(2026, 5, 11),
        rank=rank,
        amount_yuan=amount,
        up_ratio=up_ratio,
        member_count=20,
        covered_count=18,
        source_method=method,
        source_label=source,
        source_confidence="medium",
    )


def _ctx(*, with_structured_inputs: bool = True) -> common.MarketCtx:
    on_date = dt.date(2026, 5, 11)
    sw_rotation = []
    main_lines = []
    important_focus_data = {}
    regular_focus_data = {}
    if with_structured_inputs:
        sw_rotation = [
            _sector(code="801770.SI", name="通信", pct=2.1, rank=1),
            _sector(code="801080.SI", name="电子", pct=1.4, rank=3),
            _sector(code="801740.SI", name="国防军工", pct=-1.2, rank=27, up_ratio=0.32),
            _sector(code="801780.SI", name="银行", pct=0.8, rank=8, up_ratio=0.58),
        ]
        main_lines = [
            _sector(code="801102", name="通信设备", pct=3.5, rank=1, level="L2", up_ratio=0.72),
            _sector(code="801081", name="半导体", pct=2.3, rank=2, level="L2", up_ratio=0.68),
        ]
        important_focus_data = {
            "300308.SZ": {
                "close": 128.6,
                "pct_change": 3.2,
                "amount": 1_250_000_000.0,
                "quote_source": "rt_min_daily",
            },
        }
        regular_focus_data = {
            "002463.SZ": {
                "close": 42.5,
                "pct_change": 2.4,
                "amount": 920_000_000.0,
                "quote_source": "rt_min_daily",
            },
        }

    breadth = mdata.BreadthSnap(
        trade_date=on_date,
        total_amount=0.72 if with_structured_inputs else None,
        total_amount_prev=0.81 if with_structured_inputs else None,
        up_count=3600 if with_structured_inputs else None,
        down_count=1100 if with_structured_inputs else None,
        flat_count=120 if with_structured_inputs else None,
        avg_pct_change=0.74 if with_structured_inputs else None,
        limit_up_count=75 if with_structured_inputs else None,
        limit_down_count=4 if with_structured_inputs else None,
        broke_limit_count=10 if with_structured_inputs else None,
        broke_limit_pct=0.125 if with_structured_inputs else None,
        max_consec_streak=5 if with_structured_inputs else None,
        touched_limit_up_count=80 if with_structured_inputs else None,
        limit_source_method="computed_rt_proxy" if with_structured_inputs else None,
        limit_source_label="rt_k+stk_limit实时代理" if with_structured_inputs else None,
        limit_source_confidence="medium" if with_structured_inputs else None,
    )
    return common.MarketCtx(
        engine=None,
        llm=object(),
        tushare=object(),
        run=SimpleNamespace(report_run_id="test"),
        user="default",
        indices=[
            mdata.IndexSnap("000001.SH", "上证指数", "权重", 3400.0, 0.46, None, on_date),
            mdata.IndexSnap("399006.SZ", "创业板指", "成长", 2200.0, 1.15, None, on_date),
        ],
        breadth=breadth,
        flows=mdata.FlowsSnap(
            north_money=None,
            south_money=None,
            hsgt_date=None,
            margin_total=None,
            margin_change=None,
            margin_date=None,
        ),
        sw_rotation=sw_rotation,
        main_lines=main_lines,
        fund_top=[],
        dragon_tiger=[],
        news_df=None,
        aux_summaries={},
        important_focus=[
            SimpleNamespace(ts_code="300308.SZ", display_name="中际旭创", layer="infra", sub_theme="光模块"),
        ],
        regular_focus=[
            SimpleNamespace(ts_code="002463.SZ", display_name="沪电股份", layer="infra", sub_theme="AI PCB"),
        ],
        important_focus_data=important_focus_data,
        regular_focus_data=regular_focus_data,
    )


def test_noon_hypothesis_review_uses_structured_inputs_to_judge_formerly_unable_items():
    hyps = [
        {"hypothesis": "AI科技链上午需要由通信、半导体扩散，中际旭创和沪电股份要有承接。"},
        {"hypothesis": "风险偏好修复成立需要炸板率不能明显抬升，涨停家数维持。"},
        {"hypothesis": "军工链上午需要扩散，国防军工不能掉到后排。"},
    ]

    rows = build_noon_hypothesis_reviews(_ctx(with_structured_inputs=True), hyps)

    assert [row["review_result"] for row in rows] == ["validated", "validated", "falsified"]
    assert rows[0]["missing_inputs"] == []
    assert "通信设备" in rows[0]["evidence_text"]
    assert "中际旭创午间收盘" in rows[0]["evidence_text"]
    assert "rt_k+stk_limit实时代理" in rows[1]["evidence_text"]
    assert "炸板率 12%" in rows[1]["evidence_text"]
    assert "国防军工" in rows[2]["evidence_text"]
    assert rows[2]["review_result_display"] == "证伪"


def test_noon_hypothesis_review_reports_specific_missing_dependencies_and_template_reason():
    hyps = [
        {"hypothesis": "军工链上午需要扩散并进入 L2 主线。"},
        {"hypothesis": "风险偏好修复成立需要炸板率不能明显抬升。"},
    ]

    rows = build_noon_hypothesis_reviews(_ctx(with_structured_inputs=False), hyps)

    assert [row["review_result"] for row in rows] == ["unable_to_judge", "unable_to_judge"]
    assert "国防军工申万 L1/L2 实时涨幅" in rows[0]["missing_inputs"][0]
    assert "缺少国防军工" in rows[0]["missing_reason"]
    assert "触板/封板/炸板率实时代理" in rows[1]["missing_inputs"]
    assert "无法验证炸板率/风险偏好" in rows[1]["missing_reason"]
    assert rows[1]["evidence_text"] != "无法判断"

    template_dir = Path(common.__file__).parents[2] / "core" / "render" / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))
    section = {"content_json": {"rows": rows}, "title": "早报假设初步验证", "order": 3}
    html = env.get_template("_review_table.html").render(s=section)
    assert "暂无法判断" in html
    assert "触板/封板/炸板率实时代理" in html
    assert "missing_inputs" not in html
