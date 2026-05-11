from __future__ import annotations

import datetime as dt
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from jinja2 import Environment, FileSystemLoader

from ifa.core.report.timezones import BJT
from ifa.families.market import _common as common
from ifa.families.tech.focus import FocusStock


class FakeTuShare:
    def __init__(self, on_date: dt.date) -> None:
        self.on_date = on_date
        self.calls: list[tuple[str, dict]] = []

    def call(self, api: str, **params):
        self.calls.append((api, params))
        today_s = self.on_date.strftime("%Y%m%d")
        if api == "daily" and params.get("trade_date") == today_s:
            return pd.DataFrame(columns=["ts_code", "close", "pct_chg", "amount", "vol"])
        if api == "daily" and "trade_date" in params:
            return pd.DataFrame(
                [
                    {"ts_code": "300308.SZ", "close": 100.0, "pct_chg": 0.0, "amount": 9000.0, "vol": 1000.0},
                    {"ts_code": "300502.SZ", "close": 50.0, "pct_chg": 0.0, "amount": 8000.0, "vol": 900.0},
                ]
            )
        if api == "daily" and "ts_code" in params:
            prev = 100.0 if params["ts_code"] == "300308.SZ" else 50.0
            return pd.DataFrame([{"trade_date": (self.on_date - dt.timedelta(days=1)).strftime("%Y%m%d"), "close": prev}])
        if api == "daily_basic":
            return pd.DataFrame(columns=["ts_code", "turnover_rate", "pe_ttm"])
        if api == "moneyflow":
            return pd.DataFrame(columns=["ts_code", "net_mf_amount"])
        if api == "rt_min_daily":
            close_base = 100.0 if params["ts_code"] == "300308.SZ" else 50.0
            return pd.DataFrame(
                [
                    {
                        "ts_code": params["ts_code"],
                        "trade_time": f"{self.on_date:%Y-%m-%d} 09:35:00",
                        "close": close_base + 1.0,
                        "vol": 1000.0,
                        "amount": 120000.0,
                    },
                    {
                        "ts_code": params["ts_code"],
                        "trade_time": f"{self.on_date:%Y-%m-%d} 11:30:00",
                        "close": close_base + 5.0,
                        "vol": 2000.0,
                        "amount": 320000.0,
                    },
                    {
                        "ts_code": params["ts_code"],
                        "trade_time": f"{self.on_date:%Y-%m-%d} 13:00:00",
                        "close": close_base + 9.0,
                        "vol": 3000.0,
                        "amount": 520000.0,
                    },
                ]
            )
        raise AssertionError(f"Unexpected TuShare call: {api} {params}")


def test_enrich_market_focus_uses_noon_realtime_when_today_eod_empty():
    on_date = dt.datetime.now(BJT).date()
    client = FakeTuShare(on_date)
    important = [FocusStock("300308.SZ", "中际旭创", "infra", "光模块龙头")]
    regular = [FocusStock("300502.SZ", "新易盛", "infra", "光模块")]

    imp_data, reg_data = common.enrich_market_focus(
        tushare=client,
        on_date=on_date,
        important=important,
        regular=regular,
        slot="noon",
    )

    imp = imp_data["300308.SZ"]
    assert imp["close"] == pytest.approx(105.0)
    assert imp["pct_change"] == pytest.approx(5.0)
    assert imp["volume"] == pytest.approx(3000.0)
    assert imp["amount"] == pytest.approx(440000.0)
    assert imp["history_close"] == [101.0, 105.0]
    assert imp["quote_source"] == "rt_min_daily"
    assert imp["moneyflow_net"] is None
    assert imp["moneyflow_is_official"] is False
    assert imp["moneyflow_status"] == "unavailable_intraday"

    reg = reg_data["300502.SZ"]
    assert reg["close"] == pytest.approx(55.0)
    assert reg["pct_change"] == pytest.approx(10.0)
    assert reg["amount"] == pytest.approx(440000.0)
    assert any(api == "rt_min_daily" for api, _ in client.calls)


def test_focus_sections_render_realtime_close_pct_and_amount(monkeypatch):
    focus = FocusStock("300308.SZ", "中际旭创", "infra", "光模块龙头")
    focus_data = {
        "300308.SZ": {
            "close": 105.0,
            "pct_change": 5.0,
            "amount": 440000.0,
            "volume": 3000.0,
            "moneyflow_net": None,
            "moneyflow_is_official": False,
            "moneyflow_status": "unavailable_intraday",
            "history_close": [101.0, 105.0],
            "history_caption": "今日上午分时（5MIN）",
        }
    }

    def fake_chat_json(*args, **kwargs):
        return (
            {
                "results": [
                    {
                        "candidate_index": 0,
                        "status": "强势",
                        "state": "强势",
                        "today_observation": "上午量价同步。",
                        "today_hint": "观察午后承接。",
                    }
                ]
            },
            None,
            "parsed",
        )

    monkeypatch.setattr(common, "_safe_chat_json", fake_chat_json)
    ctx = common.MarketCtx(
        engine=None,
        llm=object(),
        tushare=object(),
        run=SimpleNamespace(report_run_id="test"),
        user="default",
        indices=[],
        breadth=None,
        flows=None,
        sw_rotation=[],
        main_lines=[],
        fund_top=[],
        dragon_tiger=[],
        news_df=None,
        aux_summaries={},
        important_focus=[focus],
        regular_focus=[focus],
        important_focus_data=focus_data,
        regular_focus_data=focus_data,
    )

    deep = common.build_focus_deep_section(ctx, order=6, title="重点关注股票午间更新 (10)", key="market_noon.s6_focus_deep")
    brief = common.build_focus_brief_section(ctx, order=7, title="普通关注股票午间简表 (20)", key="market_noon.s7_focus_brief")

    deep_row = deep["content_json"]["rows"][0]
    brief_row = brief["content_json"]["rows"][0]
    for row in (deep_row, brief_row):
        assert row["close_display"] == "105.00"
        assert row["pct_display"] == "+5.00%"
        assert row["amount_display"] == "44 万"
    assert deep_row["mf_display"] is None

    template_dir = Path(common.__file__).parents[2] / "core" / "render" / "templates"
    env = Environment(loader=FileSystemLoader(template_dir))
    deep_html = env.get_template("_focus_deep.html").render(s=deep)
    brief_html = env.get_template("_focus_brief.html").render(s=brief)

    assert "收盘 105.00" in deep_html
    assert "+5.00%" in deep_html
    assert "成交额 44 万" in deep_html
    assert "105.00" in brief_html
    assert "+5.00%" in brief_html
    assert "成交额 44 万" in brief_html
