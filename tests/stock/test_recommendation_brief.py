import datetime as dt

from ifa.families.stock.recommendation.models import RecommendationBriefReport
from ifa.families.stock.recommendation.render import render_markdown
from ifa.families.stock.recommendation.service import _candidate_from_row


def _row(**overrides):
    base = {
        "trade_date": dt.date(2026, 5, 6),
        "ts_code": "300042.SZ",
        "name": "朗科科技",
        "l1_name": "电子",
        "l2_name": "半导体",
        "rank_in_sector": 1,
        "sector_rank_count": 50,
        "leader_score": 0.76,
        "sector_score": 0.62,
        "stock_score": 0.72,
        "quality_flag": "computed",
        "evidence_json": {
            "main_net_yuan": 123_000_000,
            "diffusion_phase": "broad_diffusion",
            "risk_flags_json": [],
        },
        "hard_veto": False,
        "veto_categories": None,
        "veto_reasons": None,
        "ta_score": 0.81,
        "ta_setups": ["S2_LEADER_FOLLOWTHROUGH"],
        "ta_watchlist": True,
        "ningbo_score": None,
        "ningbo_modes": None,
        "close": 12.3,
        "high": 12.8,
        "low": 11.9,
        "pct_chg": 2.1,
    }
    base.update(overrides)
    return base


def test_sector_cycle_leader_becomes_strong_candidate():
    candidate = _candidate_from_row(_row(), dt.date(2026, 5, 6))

    assert candidate.group == "strong"
    assert candidate.horizon_suitability["5d"] == "适合"
    assert "12.80" in candidate.trigger
    assert any(e.source == "stock.sector_cycle_leader_daily" for e in candidate.evidence)


def test_hard_veto_forces_avoid_candidate():
    candidate = _candidate_from_row(
        _row(hard_veto=True, veto_categories="suspension", veto_reasons="停牌"),
        dt.date(2026, 5, 6),
    )

    assert candidate.group == "avoid"
    assert candidate.horizon_suitability == {"5d": "不适合", "10d": "不适合", "20d": "不适合"}
    assert candidate.risk_notes == ["停牌"]


def test_markdown_includes_source_status_and_disclaimer():
    candidate = _candidate_from_row(_row(), dt.date(2026, 5, 6))
    report = RecommendationBriefReport(
        title="Stock Edge 推荐简报 · 2026年05月06日",
        as_of_trade_date=dt.date(2026, 5, 6),
        generated_at_bjt="2026-05-09 18:00:00 CST",
        data_cutoff_bjt="2026-05-06 15:00:00 CST",
        run_mode="manual",
        as_of_rule="after_close_cutoff",
        logic_version="test",
        groups={"strong": [candidate], "watchlist": [], "avoid": []},
        source_status={"stock.sector_cycle_leader_daily": {"available": True, "rows": 1, "latest": "2026-05-06"}},
        disclaimer={
            "short_header_zh": "仅供信息汇总与研究参考。",
            "paragraphs_zh": ["中文免责声明"],
            "paragraphs_en": ["English disclaimer"],
        },
    )

    md = render_markdown(report)

    assert "强候选" in md
    assert "stock.sector_cycle_leader_daily" in md
    assert "中文免责声明" in md
