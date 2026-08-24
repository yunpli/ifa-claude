from __future__ import annotations

import datetime as dt
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from ifa.core.report.timezones import BJT
from ifa.families.market import _common as common
from ifa.families.market import data as mdata
from ifa.families.market import evening as market_evening
from ifa.families.market import morning as market_morning
from ifa.families.market import noon as market_noon


def _report_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.exec_driver_sql(
            """
            CREATE TABLE report_runs (
                report_run_id TEXT PRIMARY KEY,
                report_family TEXT NOT NULL,
                report_type TEXT NOT NULL,
                report_date DATE NOT NULL,
                status TEXT NOT NULL,
                template_version TEXT,
                completed_at TIMESTAMP
            )
            """
        )
        conn.exec_driver_sql(
            """
            CREATE TABLE report_sections (
                section_id TEXT PRIMARY KEY,
                report_run_id TEXT NOT NULL,
                section_key TEXT NOT NULL,
                content_json TEXT
            )
            """
        )
    return engine


def _insert_aux_run(
    engine,
    *,
    family: str,
    report_type: str,
    report_date: dt.date,
    content_json: dict | None = None,
    section_key: str | None = None,
    run_id: str | None = None,
    completed_at: dt.datetime | None = None,
) -> None:
    run_id = run_id or str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO report_runs (
                    report_run_id, report_family, report_type, report_date,
                    status, template_version, completed_at
                ) VALUES (
                    :rid, :family, :report_type, :report_date,
                    'succeeded', 'test-template', :completed_at
                )
                """
            ),
            {
                "rid": run_id,
                "family": family,
                "report_type": report_type,
                "report_date": report_date,
                "completed_at": completed_at or dt.datetime(2026, 5, 12, 1, 0, 0),
            },
        )
        if section_key is not None:
            conn.execute(
                text(
                    """
                    INSERT INTO report_sections (
                        section_id, report_run_id, section_key, content_json
                    ) VALUES (
                        :sid, :rid, :section_key, :content_json
                    )
                    """
                ),
                {
                    "sid": str(uuid.uuid4()),
                    "rid": run_id,
                    "section_key": section_key,
                    "content_json": json.dumps(content_json or {}, ensure_ascii=False),
                },
            )


def _prefetch_payload() -> dict:
    return {
        "indices": [],
        "breadth": SimpleNamespace(),
        "flows": SimpleNamespace(),
        "sw_rotation": [],
        "main_lines": [],
        "fund_top": [],
        "dragon_tiger": [],
        "news_df": pd.DataFrame(),
        "aux_summaries": {},
        "important_focus": [],
        "regular_focus": [],
    }


def _section(key: str, order: int) -> dict:
    return {
        "key": key,
        "title": key,
        "order": order,
        "type": "commentary",
        "content_json": {},
    }


def _patch_market_orchestrator(monkeypatch, module, *, prefetch_calls: list[dict], prev_trade_date: dt.date | None = None) -> None:
    settings = SimpleNamespace(run_mode=SimpleNamespace(value="test"))
    engine = object()

    monkeypatch.setattr(module, "get_settings", lambda: settings)
    monkeypatch.setattr(module, "get_engine", lambda _settings: engine)
    monkeypatch.setattr(module, "LLMClient", lambda _settings: object())
    monkeypatch.setattr(module, "TuShareClient", lambda _settings: object())
    monkeypatch.setattr(module, "insert_report_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "insert_section", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "finalize_report_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(module, "_render_and_save", lambda *_args, **_kwargs: Path("/tmp/test-market.html"))
    monkeypatch.setattr(module, "enrich_market_focus", lambda **_kwargs: ({}, {}))

    def fake_prefetch_market_data(**kwargs):
        prefetch_calls.append(kwargs)
        return _prefetch_payload()

    monkeypatch.setattr(module, "prefetch_market_data", fake_prefetch_market_data)
    monkeypatch.setattr("ifa.core.report.freshness.preflight_freshness_check", lambda *args, **kwargs: [])
    monkeypatch.setattr("ifa.core.render.staleness.compute_staleness_warning", lambda **kwargs: None)
    if prev_trade_date is not None:
        monkeypatch.setattr("ifa.core.calendar.prev_trading_day", lambda _engine, _date: prev_trade_date)

    if module is market_morning:
        monkeypatch.setattr(module, "_build_s1_tone", lambda ctx: _section("market_morning.s1_tone", 1))
        monkeypatch.setattr(module, "build_three_aux_section", lambda *args, **kwargs: _section("market_morning.s2_three_aux", 2))
        monkeypatch.setattr(module, "build_index_panel_section", lambda *args, **kwargs: _section("market_morning.s3_index_panel", 3))
        monkeypatch.setattr(module, "build_rotation_section", lambda *args, **kwargs: _section("market_morning.s4_rotation", 4))
        monkeypatch.setattr(module, "build_sentiment_section", lambda *args, **kwargs: _section("market_morning.s5_sentiment", 5))
        monkeypatch.setattr(module, "build_dragon_tiger_section", lambda *args, **kwargs: _section("market_morning.s6_dragon_tiger", 6))
        monkeypatch.setattr(module, "build_news_section", lambda *args, **kwargs: _section("market_morning.s7_news", 7))
        monkeypatch.setattr(module, "_build_s8_main_line", lambda ctx: _section("market_morning.s8_main_line", 8))
        monkeypatch.setattr(module, "build_focus_deep_section", lambda *args, **kwargs: _section("market_morning.s9_focus_deep", 9))
        monkeypatch.setattr(module, "build_focus_brief_section", lambda *args, **kwargs: _section("market_morning.s10_focus_brief", 10))
        monkeypatch.setattr(module, "_build_s11_risk", lambda ctx, prior: _section("market_morning.s11_risk", 11))
        monkeypatch.setattr(module, "_build_s12_hypotheses", lambda ctx, prior: _section("market_morning.s12_hypotheses", 12))
        monkeypatch.setattr(module, "_build_s13_disclaimer", lambda: _section("market_morning.s13_disclaimer", 13))
        return

    if module is market_noon:
        monkeypatch.setattr(module, "_load_morning_hypotheses", lambda *_args, **_kwargs: [])
        monkeypatch.setattr(module, "_build_n1_tone", lambda ctx, hyps: _section("market_noon.s1_tone", 1))
        monkeypatch.setattr(module, "build_index_panel_section", lambda *args, **kwargs: _section("market_noon.s2_index_panel", 2))
        monkeypatch.setattr(module, "_build_n3_review", lambda ctx, hyps: _section("market_noon.s3_review", 3))
        monkeypatch.setattr(module, "build_rotation_section", lambda *args, **kwargs: _section("market_noon.s4_rotation", 4))
        monkeypatch.setattr(module, "build_sentiment_section", lambda *args, **kwargs: _section("market_noon.s5_sentiment", 5))
        monkeypatch.setattr(module, "build_focus_deep_section", lambda *args, **kwargs: _section("market_noon.s6_focus_deep", 6))
        monkeypatch.setattr(module, "build_focus_brief_section", lambda *args, **kwargs: _section("market_noon.s7_focus_brief", 7))
        monkeypatch.setattr(module, "_build_n10_scenarios", lambda ctx, prior: _section("market_noon.s10_scenarios", 10))
        monkeypatch.setattr(module, "_build_n11_review_hooks", lambda ctx, prior: _section("market_noon.s11_review_hooks", 11))
        monkeypatch.setattr(module, "_build_n12_disclaimer", lambda: _section("market_noon.s12_disclaimer", 12))
        return

    monkeypatch.setattr(module, "_load_hypotheses", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(module, "_build_e1_headline", lambda ctx, morning_hyps, noon_hyps: _section("market_evening.s1_headline", 1))
    monkeypatch.setattr(module, "build_index_panel_section", lambda *args, **kwargs: _section("market_evening.s2_index_panel", 2))
    monkeypatch.setattr(module, "build_rotation_section", lambda *args, **kwargs: _section("market_evening.s3_rotation", 3))
    monkeypatch.setattr(module, "build_sentiment_section", lambda *args, **kwargs: _section("market_evening.s4_sentiment", 4))
    monkeypatch.setattr(module, "build_dragon_tiger_section", lambda *args, **kwargs: _section("market_evening.s5_dragon_tiger", 5))
    monkeypatch.setattr(module, "build_three_aux_section", lambda *args, **kwargs: _section("market_evening.s6_three_aux", 6))
    monkeypatch.setattr(module, "_build_review", lambda *args, **kwargs: _section(kwargs["key"], kwargs["order"]))
    monkeypatch.setattr(module, "build_focus_deep_section", lambda *args, **kwargs: _section("market_evening.s9_focus_deep", 9))
    monkeypatch.setattr(module, "build_focus_brief_section", lambda *args, **kwargs: _section("market_evening.s10_focus_brief", 10))
    monkeypatch.setattr(module, "_build_e11_attribution", lambda ctx: _section("market_evening.s11_attribution", 11))
    monkeypatch.setattr(module, "_build_e12_sticky", lambda ctx, prior: _section("market_evening.s12_sticky", 12))
    monkeypatch.setattr(module, "_build_e13_watchlist", lambda ctx, prior: _section("market_evening.s13_watchlist", 13))
    monkeypatch.setattr(module, "_build_e14_disclaimer", lambda: _section("market_evening.s14_disclaimer", 14))


@pytest.mark.parametrize(
    ("report_date", "prev_trade_date"),
    [
        (dt.date(2026, 5, 11), dt.date(2026, 5, 8)),
        (dt.date(2026, 10, 9), dt.date(2026, 9, 30)),
    ],
)
def test_run_market_morning_splits_market_and_aux_dates(monkeypatch, report_date: dt.date, prev_trade_date: dt.date):
    prefetch_calls: list[dict] = []
    _patch_market_orchestrator(monkeypatch, market_morning, prefetch_calls=prefetch_calls, prev_trade_date=prev_trade_date)

    market_morning.run_market_morning(
        report_date=report_date,
        data_cutoff_at=dt.datetime.combine(report_date, dt.time(9, 0), tzinfo=BJT),
    )

    assert len(prefetch_calls) == 1
    assert prefetch_calls[0]["market_observation_date"] == prev_trade_date
    assert prefetch_calls[0]["aux_report_date"] == report_date
    assert prefetch_calls[0]["aux_report_type"] == "morning_long"
    assert prefetch_calls[0]["slot"] == "morning"


def test_prefetch_market_data_logs_missing_aux_soft_dependency(monkeypatch):
    engine = _report_engine()
    report_date = dt.date(2026, 5, 12)
    _insert_aux_run(
        engine,
        family="macro",
        report_type="morning_long",
        report_date=report_date,
        section_key="macro_morning.s1_tone",
        content_json={
            "tone": "偏积极",
            "headline": "宏观环境偏稳。",
            "summary": "流动性和政策信号未见显著恶化。",
            "top3": ["盯汇率方向", "看地产量价", "跟踪北向反馈"],
        },
    )

    monkeypatch.setattr(common.mdata, "fetch_index_family", lambda *args, **kwargs: [])
    monkeypatch.setattr(common.mdata, "fetch_breadth", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(common.mdata, "fetch_sw_rotation", lambda *args, **kwargs: [])
    monkeypatch.setattr(common.mdata, "fetch_main_lines", lambda *args, **kwargs: [])
    monkeypatch.setattr(common.mdata, "fetch_market_news", lambda *args, **kwargs: pd.DataFrame())

    logs: list[str] = []
    payload = common.prefetch_market_data(
        tushare=object(),
        engine=engine,
        market_observation_date=dt.date(2026, 5, 9),
        aux_report_date=report_date,
        aux_report_type="morning_long",
        end_bjt=dt.datetime(2026, 5, 12, 12, 30, tzinfo=BJT),
        on_log=logs.append,
        slot="noon",
    )

    assert set(payload["aux_summaries"]) == {"macro"}
    assert any("aux_report_date=2026-05-12" in line for line in logs)
    assert any("asset, tech" in line for line in logs)
    assert any("soft dependency missing" in line for line in logs)


@pytest.mark.parametrize(
    ("slot", "market_observation_date"),
    [
        ("morning", dt.date(2026, 5, 9)),
        ("evening", dt.date(2026, 5, 12)),
    ],
)
def test_prefetch_market_data_uses_market_observation_date_for_flows(monkeypatch, slot: str, market_observation_date: dt.date):
    engine = _report_engine()
    report_date = dt.date(2026, 5, 12)
    calls: dict[str, object] = {}

    monkeypatch.setattr(common.mdata, "fetch_index_family", lambda *args, **kwargs: [])
    monkeypatch.setattr(common.mdata, "fetch_breadth", lambda *args, **kwargs: SimpleNamespace())
    monkeypatch.setattr(common.mdata, "fetch_sw_rotation", lambda *args, **kwargs: [])
    monkeypatch.setattr(common.mdata, "fetch_main_lines", lambda *args, **kwargs: [])
    monkeypatch.setattr(common.mdata, "fetch_market_news", lambda *args, **kwargs: pd.DataFrame())
    monkeypatch.setattr(common.mdata, "fetch_three_aux_summaries", lambda *args, **kwargs: mdata.AuxSummaryFetchResult({}, report_date, "morning_long"))

    def fake_fetch_flows(_client, *, on_date):
        calls["flows_on_date"] = on_date
        return mdata.FlowsSnap(
            north_money=None,
            south_money=None,
            hsgt_date=None,
            margin_total=None,
            margin_change=None,
            margin_date=None,
        )

    monkeypatch.setattr(common.mdata, "fetch_flows", fake_fetch_flows)

    if slot == "evening":
        monkeypatch.setattr(common.mdata, "fetch_fund_flow_top", lambda *args, **kwargs: [])
        monkeypatch.setattr(common.mdata, "fetch_dragon_tiger", lambda *args, **kwargs: [])
        monkeypatch.setattr(common.mdata, "enrich_stocks", lambda *args, **kwargs: None)

    common.prefetch_market_data(
        tushare=object(),
        engine=engine,
        market_observation_date=market_observation_date,
        aux_report_date=report_date,
        aux_report_type="evening_long" if slot == "evening" else "morning_long",
        end_bjt=dt.datetime(2026, 5, 12, 17, 0, tzinfo=BJT),
        on_log=lambda _msg: None,
        slot=slot,
    )

    assert calls["flows_on_date"] == market_observation_date


def test_fetch_three_aux_summaries_accepts_headline_top3_only_payload():
    engine = _report_engine()
    report_date = dt.date(2026, 5, 12)
    _insert_aux_run(
        engine,
        family="macro",
        report_type="morning_long",
        report_date=report_date,
        section_key="macro_morning.s1_tone",
        content_json={
            "headline": "宏观环境偏稳。",
            "top3": ["盯汇率方向", "看地产量价", "跟踪北向反馈"],
        },
    )

    result = mdata.fetch_three_aux_summaries(
        engine,
        report_date=report_date,
        report_type="morning_long",
    )

    summary = result.summaries["macro"]
    assert summary.headline == "宏观环境偏稳。"
    assert summary.tone_or_state is None
    assert summary.summary == "盯汇率方向；看地产量价；跟踪北向反馈"
    assert summary.bullets == [{"text": "盯汇率方向"}, {"text": "看地产量价"}, {"text": "跟踪北向反馈"}]


def test_fetch_three_aux_summaries_fails_when_succeeded_run_lacks_required_contract():
    engine = _report_engine()
    report_date = dt.date(2026, 5, 12)
    _insert_aux_run(
        engine,
        family="macro",
        report_type="morning_long",
        report_date=report_date,
        section_key="macro_morning.s1_tone",
        content_json={
            "headline": "宏观环境偏稳。",
            "top3": ["盯汇率方向", "看地产量价"],
        },
    )

    with pytest.raises(ValueError, match="incomplete: top3"):
        mdata.fetch_three_aux_summaries(
            engine,
            report_date=report_date,
            report_type="morning_long",
        )


def test_fetch_three_aux_summaries_selects_latest_succeeded_run_deterministically():
    engine = _report_engine()
    report_date = dt.date(2026, 5, 12)
    completed_at = dt.datetime(2026, 5, 12, 1, 0, 0)
    _insert_aux_run(
        engine,
        family="macro",
        report_type="evening_long",
        report_date=report_date,
        run_id="macro-run-a",
        completed_at=completed_at,
        section_key="macro_evening.s1_headline",
        content_json={
            "headline": "旧版本结论",
            "top3": ["旧条目1", "旧条目2", "旧条目3"],
        },
    )
    _insert_aux_run(
        engine,
        family="macro",
        report_type="evening_long",
        report_date=report_date,
        run_id="macro-run-b",
        completed_at=completed_at,
        section_key="macro_evening.s1_headline",
        content_json={
            "headline": "新版本结论",
            "top3": ["新条目1", "新条目2", "新条目3"],
        },
    )

    result = mdata.fetch_three_aux_summaries(
        engine,
        report_date=report_date,
        report_type="evening_long",
    )

    assert result.summaries["macro"].headline == "新版本结论"
    assert result.summaries["macro"].summary == "新条目1；新条目2；新条目3"


@pytest.mark.parametrize(
    ("module", "runner_name", "report_type"),
    [
        (market_noon, "run_market_noon", "morning_long"),
        (market_evening, "run_market_evening", "evening_long"),
    ],
)
def test_noon_and_evening_keep_same_day_aux_behavior(monkeypatch, module, runner_name: str, report_type: str):
    prefetch_calls: list[dict] = []
    _patch_market_orchestrator(monkeypatch, module, prefetch_calls=prefetch_calls, prev_trade_date=dt.date(2026, 5, 8))

    getattr(module, runner_name)(
        report_date=dt.date(2026, 5, 11),
        data_cutoff_at=dt.datetime(2026, 5, 11, 12 if module is market_noon else 17, 30, tzinfo=BJT),
    )

    assert len(prefetch_calls) == 1
    assert prefetch_calls[0]["market_observation_date"] == dt.date(2026, 5, 11)
    assert prefetch_calls[0]["aux_report_date"] == dt.date(2026, 5, 11)
    assert prefetch_calls[0]["aux_report_type"] == report_type
