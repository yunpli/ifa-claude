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
