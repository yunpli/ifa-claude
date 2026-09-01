from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from ifa.families._shared import focus_selection as fs


def _candidate(code: str, sector: str, name: str, *, score: float, pct: float) -> fs._Candidate:
    return fs._Candidate(
        ts_code=code,
        name=name,
        sector_code=sector,
        sector_name=sector,
        role="龙头",
        formal_score=score,
        pct_change=pct,
        amount_yuan=1_000_000_000 * score,
        turnover_rate=5.0,
        volume_ratio=1.4,
        main_net_yuan=100_000_000 * score,
        main_net_ratio=0.08,
        sector_state="acceleration",
        sector_state_score=0.8,
        sector_confidence=0.8,
        diffusion_score=0.75,
        quality_flag="ok",
    )


def test_market_focus_is_dynamic_disjoint_and_auditable(monkeypatch):
    candidates = {
        "000001.SZ": _candidate("000001.SZ", "S1", "甲公司", score=0.95, pct=6.0),
        "000002.SZ": _candidate("000002.SZ", "S1", "乙公司", score=0.75, pct=3.0),
        "600001.SH": _candidate("600001.SH", "S2", "丙公司", score=0.90, pct=5.0),
        "600002.SH": _candidate("600002.SH", "S2", "丁公司", score=0.70, pct=2.0),
    }
    monkeypatch.setattr(
        fs,
        "_load_member_candidates",
        lambda *args, **kwargs: (candidates, dt.date(2026, 8, 31), []),
    )
    monkeypatch.setattr(fs, "_apply_quote_overlay", lambda *args, **kwargs: None)
    monkeypatch.setattr(fs, "_resolve_names", lambda *args, **kwargs: None)

    selection = fs.select_market_focus(
        engine=object(),
        client=object(),
        on_date=dt.date(2026, 8, 31),
        slot="morning",
        main_lines=[
            SimpleNamespace(code="S2", name="板块二", pct_change=3.0),
            SimpleNamespace(code="S1", name="板块一", pct_change=2.0),
        ],
        important_limit=2,
        regular_limit=2,
    )

    assert [row.ts_code for row in selection.important] == ["600001.SH", "000001.SZ"]
    assert not ({row.ts_code for row in selection.important} & {row.ts_code for row in selection.regular})
    assert selection.audit["personalization"] == "disabled"
    assert selection.audit["uses_hardcoded_tickers"] is False
    assert selection.audit["logic_version"] == fs.LOGIC_VERSION


def test_market_focus_changes_when_market_sector_order_changes(monkeypatch):
    def load(*args, **kwargs):
        return (
            {
                "000001.SZ": _candidate("000001.SZ", "S1", "甲公司", score=0.90, pct=5.0),
                "600001.SH": _candidate("600001.SH", "S2", "乙公司", score=0.90, pct=5.0),
            },
            dt.date(2026, 8, 31),
            [],
        )

    monkeypatch.setattr(fs, "_load_member_candidates", load)
    monkeypatch.setattr(fs, "_apply_quote_overlay", lambda *args, **kwargs: None)
    monkeypatch.setattr(fs, "_resolve_names", lambda *args, **kwargs: None)

    first = fs.select_market_focus(
        engine=object(), client=object(), on_date=dt.date(2026, 8, 31), slot="morning",
        main_lines=[SimpleNamespace(code="S1", name="一", pct_change=3.0), SimpleNamespace(code="S2", name="二", pct_change=2.0)],
        important_limit=1, regular_limit=0,
    )
    second = fs.select_market_focus(
        engine=object(), client=object(), on_date=dt.date(2026, 8, 31), slot="morning",
        main_lines=[SimpleNamespace(code="S2", name="二", pct_change=3.0), SimpleNamespace(code="S1", name="一", pct_change=2.0)],
        important_limit=1, regular_limit=0,
    )

    assert first.important[0].ts_code == "000001.SZ"
    assert second.important[0].ts_code == "600001.SH"


def test_historical_noon_ignores_eod_main_lines_and_daily(monkeypatch):
    candidates = {
        "000001.SZ": _candidate("000001.SZ", "BASE", "甲公司", score=0.90, pct=5.0),
    }
    monkeypatch.setattr(
        fs,
        "_load_member_candidates",
        lambda *args, **kwargs: (candidates, dt.date(2026, 8, 28), []),
    )
    monkeypatch.setattr(fs, "_resolve_names", lambda *args, **kwargs: None)

    class NoEodClient:
        def call(self, api, **kwargs):
            raise AssertionError(f"historical noon must not call {api}")

    selection = fs.select_market_focus(
        engine=object(),
        client=NoEodClient(),
        on_date=dt.date(2026, 8, 31),
        slot="noon",
        # This represents an unsafe historical EOD result from the upstream
        # main-line fallback and must not become the noon active universe.
        main_lines=[SimpleNamespace(code="LATER_EOD", name="收盘主线", pct_change=8.0)],
        important_limit=1,
        regular_limit=0,
    )

    assert selection.important[0].ts_code == "000001.SZ"
    assert selection.audit["active_sectors"][0]["code"] == "BASE"
    assert selection.audit["base_feature_date"] == "2026-08-28"
    assert selection.audit["quality_flag"] == "degraded"
    assert "historical_no_intraday_replay" in selection.audit["notes"]
