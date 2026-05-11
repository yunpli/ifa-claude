from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from ifa.families.market import _common as common
from ifa.families.market import _sw_realtime
from ifa.families.market import data as mdata


class FakeTuShare:
    def __init__(self, on_date: dt.date) -> None:
        self.on_date = on_date

    def call(self, api: str, **params):
        if api == "daily_basic":
            return pd.DataFrame(
                [
                    {"ts_code": "000001.SZ", "total_mv": 1.0},
                    {"ts_code": "000002.SZ", "total_mv": 1.0},
                    {"ts_code": "000003.SZ", "total_mv": 1.0},
                    {"ts_code": "000004.SZ", "total_mv": 1.0},
                    {"ts_code": "000005.SZ", "total_mv": 1.0},
                ]
            )
        if api == "sw_daily":
            return pd.DataFrame(
                [
                    {
                        "ts_code": params["ts_code"],
                        "trade_date": (self.on_date - dt.timedelta(days=1)).strftime("%Y%m%d"),
                        "close": 1000.0,
                    }
                ]
            )
        raise AssertionError(f"Unexpected TuShare call: {api} {params}")


class FakeBreadthTuShare:
    def __init__(self, *, rt_by_pattern: dict[str, pd.DataFrame], limits: pd.DataFrame, anchor_date: dt.date) -> None:
        self.rt_by_pattern = rt_by_pattern
        self.limits = limits
        self.anchor_date = anchor_date

    def call(self, api: str, **params):
        if api == "rt_k":
            return self.rt_by_pattern[params["ts_code"]].copy()
        if api == "stk_limit":
            return self.limits.copy()
        if api == "limit_list_d":
            if params.get("trade_date") == self.anchor_date.strftime("%Y%m%d"):
                return pd.DataFrame(
                    [
                        {"ts_code": "600010.SH", "limit": "U", "open_times": 0, "up_stat": "1/1"},
                        {"ts_code": "600011.SH", "limit": "U", "open_times": 1, "up_stat": "1/2"},
                    ]
                )
            return pd.DataFrame(columns=["ts_code", "limit", "open_times", "up_stat"])
        if api == "daily":
            return pd.DataFrame()
        raise AssertionError(f"Unexpected TuShare call: {api} {params}")


def _make_rt_chunk(prefix: str, suffix: str, count: int) -> pd.DataFrame:
    rows = []
    for i in range(count):
        rows.append(
            {
                "ts_code": f"{prefix}{i:03d}.{suffix}",
                "pre_close": 10.0,
                "open": 10.0,
                "high": 10.05,
                "low": 9.95,
                "close": 10.0,
                "vol": 1000.0,
                "amount": 100_000.0,
            }
        )
    return pd.DataFrame(rows)


def _engine_with_empty_eod_sector_tables(on_date: dt.date):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("ATTACH DATABASE ':memory:' AS smartmoney")
        conn.exec_driver_sql(
            """
            CREATE TABLE smartmoney.sw_member_monthly (
                snapshot_month DATE,
                l2_code TEXT,
                l2_name TEXT,
                ts_code TEXT
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE smartmoney.sector_moneyflow_sw_daily (
                trade_date DATE,
                l2_code TEXT,
                l2_name TEXT,
                net_amount REAL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE smartmoney.raw_sw_daily (
                trade_date DATE,
                ts_code TEXT,
                name TEXT,
                close REAL,
                pct_change REAL,
                amount REAL
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE smartmoney.raw_daily (
                trade_date DATE,
                ts_code TEXT,
                close REAL,
                pct_chg REAL
            )
            """
        )
        rows = [
            {"sm": on_date.replace(day=1), "code": "801001", "name": "强势主线", "stock": "000001.SZ"},
            {"sm": on_date.replace(day=1), "code": "801001", "name": "强势主线", "stock": "000002.SZ"},
            {"sm": on_date.replace(day=1), "code": "801001", "name": "强势主线", "stock": "000003.SZ"},
            {"sm": on_date.replace(day=1), "code": "801002", "name": "次强主线", "stock": "000004.SZ"},
            {"sm": on_date.replace(day=1), "code": "801002", "name": "次强主线", "stock": "000005.SZ"},
        ]
        conn.execute(
            text(
                """
                INSERT INTO smartmoney.sw_member_monthly
                    (snapshot_month, l2_code, l2_name, ts_code)
                VALUES (:sm, :code, :name, :stock)
                """
            ),
            rows,
        )
    return engine


def test_fetch_main_lines_noon_uses_realtime_constituent_proxy_when_eod_empty(monkeypatch):
    on_date = dt.date(2026, 5, 11)
    engine = _engine_with_empty_eod_sector_tables(on_date)
    client = FakeTuShare(on_date)
    rt = pd.DataFrame(
        [
            {"ts_code": "000001.SZ", "pre_close": 100.0, "close": 110.0, "amount": 200_000_000.0},
            {"ts_code": "000002.SZ", "pre_close": 100.0, "close": 104.0, "amount": 100_000_000.0},
            {"ts_code": "000003.SZ", "pre_close": 100.0, "close": 98.0, "amount": 50_000_000.0},
            {"ts_code": "000004.SZ", "pre_close": 100.0, "close": 102.0, "amount": 10_000_000.0},
            {"ts_code": "000005.SZ", "pre_close": 100.0, "close": 99.0, "amount": 20_000_000.0},
        ]
    )
    monkeypatch.setattr(mdata, "_today_bjt", lambda: on_date)
    monkeypatch.setattr(_sw_realtime, "_load_whole_a_rt_k", lambda _client, *, on_date: rt)

    lines = mdata.fetch_main_lines(
        engine,
        on_date=on_date,
        client=client,
        slot="noon",
        top_n=2,
    )

    assert [line.code for line in lines] == ["801001", "801002"]
    top = lines[0]
    assert top.name == "强势主线"
    assert top.pct_change == pytest.approx(4.0)
    assert top.amount_yuan == pytest.approx(350_000_000.0)
    assert top.up_ratio == pytest.approx(2 / 3)
    assert top.source_method == "constituent_rt_k_proxy"
    assert top.source_label == "成分股实时代理"
    assert top.source_confidence == "medium"


def test_rotation_section_surfaces_intraday_main_line_amount_breadth_and_source(monkeypatch):
    on_date = dt.date(2026, 5, 11)
    main_lines = [
        mdata.SectorBar(
            code="801001",
            name="强势主线",
            close=1040.0,
            pct_change=4.0,
            trade_date=on_date,
            rank=1,
            amount_yuan=350_000_000.0,
            up_count=2,
            down_count=1,
            flat_count=0,
            up_ratio=2 / 3,
            member_count=3,
            covered_count=3,
            source_method="constituent_rt_k_proxy",
            source_label="成分股实时代理",
            source_confidence="medium",
        )
    ]

    def fake_chat_json(*args, **kwargs):
        return ({"results": [{"candidate_index": 0, "strength_label": "强", "commentary": "上午扩散较好。"}]}, None, "parsed")

    monkeypatch.setattr(common, "_safe_chat_json", fake_chat_json)
    ctx = common.MarketCtx(
        engine=None,
        llm=object(),
        tushare=object(),
        run=SimpleNamespace(report_run_id="test"),
        user="default",
        indices=[],
        breadth=mdata.BreadthSnap(
            trade_date=on_date,
            total_amount=None,
            total_amount_prev=None,
            up_count=None,
            down_count=None,
            flat_count=None,
            avg_pct_change=None,
            limit_up_count=None,
            limit_down_count=None,
            broke_limit_count=None,
            broke_limit_pct=None,
            max_consec_streak=None,
        ),
        flows=mdata.FlowsSnap(
            north_money=None,
            south_money=None,
            hsgt_date=None,
            margin_total=None,
            margin_change=None,
            margin_date=None,
        ),
        sw_rotation=[],
        main_lines=main_lines,
        fund_top=[],
        dragon_tiger=[],
        news_df=None,
        aux_summaries={},
        important_focus=[],
        regular_focus=[],
    )

    section = common.build_rotation_section(
        ctx,
        order=4,
        title="上午板块轮动与主线状态",
        key="market_noon.s4_rotation",
    )

    row = section["content_json"]["rows"][0]
    assert row["avg_pct_display"] == "+4.00%"
    assert row["amount_display"] == "4 亿"
    assert row["up_share_display"] == "67%"
    assert row["source_method"] == "constituent_rt_k_proxy"
    assert "成分股实时代理" in row["source_label"]
    assert "中置信" in row["source_label"]

    template_dir = Path(common.__file__).parents[2] / "core" / "render" / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))
    html = env.get_template("_category_strength.html").render(s=section)
    assert "成交额 4 亿" in html
    assert "成分股实时代理" in html
    assert "67%" in html


def test_fetch_breadth_noon_computes_open_board_from_rt_high_when_official_missing(monkeypatch):
    on_date = dt.date(2099, 1, 5)
    anchor_date = on_date - dt.timedelta(days=1)
    rt_by_pattern = {
        "6*.SH": _make_rt_chunk("600", "SH", 1001),
        "0*.SZ": _make_rt_chunk("000", "SZ", 1001),
        "3*.SZ": _make_rt_chunk("300", "SZ", 1001),
    }
    # sealed涨停、触板后炸板、接近涨停价封住、跌停各一只；其余样本未触板。
    rt_by_pattern["6*.SH"].loc[0, ["high", "close"]] = [11.0, 11.0]
    rt_by_pattern["0*.SZ"].loc[1, ["high", "close"]] = [11.0, 10.50]
    rt_by_pattern["3*.SZ"].loc[2, ["high", "close"]] = [11.0, 10.996]
    rt_by_pattern["6*.SH"].loc[1, ["low", "close"]] = [9.0, 9.0]

    all_codes = pd.concat(rt_by_pattern.values(), ignore_index=True)["ts_code"]
    limits = pd.DataFrame(
        {
            "ts_code": all_codes,
            "up_limit": 11.0,
            "down_limit": 9.0,
        }
    )
    client = FakeBreadthTuShare(rt_by_pattern=rt_by_pattern, limits=limits, anchor_date=anchor_date)
    monkeypatch.setattr(mdata, "_today_bjt", lambda: on_date)

    snap = mdata.fetch_breadth(client, on_date=on_date, slot="noon", engine=None)

    assert snap.limit_up_count == 2
    assert snap.limit_down_count == 1
    assert snap.touched_limit_up_count == 3
    assert snap.broke_limit_count == 1
    assert snap.broke_limit_pct == pytest.approx(1 / 3)
    assert snap.limit_source_method == "computed_rt_proxy"
    assert snap.limit_source_label == "rt_k+stk_limit实时代理"
    assert snap.limit_source_confidence == "medium"
    assert snap.limit_anchor_date == anchor_date
    assert snap.limit_anchor_limit_up_count == 2
    assert snap.limit_anchor_broke_limit_pct == pytest.approx(1 / 3)


def test_sentiment_section_renders_limit_proxy_metadata(monkeypatch):
    on_date = dt.date(2099, 1, 5)

    def fake_chat_json(*args, **kwargs):
        return ({"cycle_phase": "分歧", "ladder_health": "偏弱", "commentary": "炸板率升高。", "risk_note": ""}, None, "parsed")

    monkeypatch.setattr(common, "_safe_chat_json", fake_chat_json)
    ctx = common.MarketCtx(
        engine=None,
        llm=object(),
        tushare=object(),
        run=SimpleNamespace(report_run_id="test"),
        user="default",
        indices=[],
        breadth=mdata.BreadthSnap(
            trade_date=on_date,
            total_amount=None,
            total_amount_prev=None,
            up_count=1200,
            down_count=2800,
            flat_count=100,
            avg_pct_change=-0.35,
            limit_up_count=2,
            limit_down_count=1,
            broke_limit_count=1,
            broke_limit_pct=1 / 3,
            max_consec_streak=None,
            touched_limit_up_count=3,
            limit_source_method="computed_rt_proxy",
            limit_source_label="rt_k+stk_limit实时代理",
            limit_source_confidence="medium",
            limit_anchor_date=on_date - dt.timedelta(days=1),
            limit_anchor_limit_up_count=2,
            limit_anchor_broke_limit_pct=1 / 3,
        ),
        flows=mdata.FlowsSnap(
            north_money=None,
            south_money=None,
            hsgt_date=None,
            margin_total=None,
            margin_change=None,
            margin_date=None,
        ),
        sw_rotation=[],
        main_lines=[],
        fund_top=[],
        dragon_tiger=[],
        news_df=None,
        aux_summaries={},
        important_focus=[],
        regular_focus=[],
    )

    section = common.build_sentiment_section(
        ctx,
        order=5,
        title="市场情绪 · 午间状态",
        key="market_noon.s5_sentiment",
    )

    cells = {cell["label"]: cell for cell in section["content_json"]["cells"]}
    assert cells["炸板率"]["value"] == "33"
    assert cells["炸板率"]["source_method"] == "computed_rt_proxy"
    assert "触板 3 家，炸板 1 家" in cells["炸板率"]["note"]
    assert "rt_k+stk_limit实时代理" in cells["炸板率"]["note"]
    assert "中置信" in cells["炸板率"]["note"]
    assert "官方锚" in cells["炸板率"]["note"]

    template_dir = Path(common.__file__).parents[2] / "core" / "render" / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))
    html = env.get_template("_sentiment_grid.html").render(s=section)
    assert "炸板率" in html
    assert "33" in html
    assert "rt_k+stk_limit实时代理" in html
    assert "中置信" in html
