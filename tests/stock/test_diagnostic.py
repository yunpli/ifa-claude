from __future__ import annotations

import datetime as dt

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence
from ifa.families.stock.diagnostic.service import render_markdown, synthesize_diagnostic
from ifa.families.stock.diagnostic.models import DiagnosticReport


def test_synthesis_reports_conflicts_without_forcing_consensus():
    perspectives = [
        PerspectiveEvidence("stock_edge_sector_cycle", "Stock Edge", "available", "positive", "sector flow positive"),
        PerspectiveEvidence("ta", "TA", "available", "neutral", "no TA setup"),
        PerspectiveEvidence("ningbo", "Ningbo", "available", "positive", "ningbo hit"),
        PerspectiveEvidence("risk", "Risk", "available", "risk", "blacklist hit"),
    ]

    synthesis = synthesize_diagnostic(perspectives)

    assert synthesis.conclusion == "avoid"
    assert synthesis.confidence == "high"
    assert synthesis.horizon_suitability == {"5d": "avoid", "10d": "avoid", "20d": "avoid"}
    assert synthesis.conflicts


def test_synthesis_can_mark_short_term_tradable_on_independent_positive_evidence():
    perspectives = [
        PerspectiveEvidence("stock_edge_sector_cycle", "Stock Edge", "available", "positive", "sector flow positive"),
        PerspectiveEvidence("ta", "TA", "available", "positive", "TA setup positive"),
        PerspectiveEvidence("risk", "Risk", "available", "neutral", "no hard risk"),
    ]

    synthesis = synthesize_diagnostic(perspectives)

    assert synthesis.conclusion == "short-term tradable"
    assert synthesis.confidence == "medium"


def test_markdown_marks_unavailable_perspective_explicitly():
    report = DiagnosticReport(
        ts_code="300042.SZ",
        name="朗科科技",
        as_of_trade_date=dt.date(2026, 5, 6),
        generated_at_bjt="2026-05-08T10:00:00+08:00",
        data_cutoff_bjt="2026-05-06T15:00:00+08:00",
        perspectives=[
            PerspectiveEvidence(
                "ningbo",
                "Ningbo",
                "unavailable",
                "unknown",
                "宁波短线策略近期未命中目标股。",
                missing=["ningbo.recommendations_daily"],
            ),
            PerspectiveEvidence(
                "risk",
                "Risk",
                "available",
                "neutral",
                "未命中硬性风险。",
                points=[EvidencePoint("avg amount 7d yuan", 123.0, "smartmoney.raw_daily")],
            ),
        ],
        synthesis=synthesize_diagnostic([
            PerspectiveEvidence("ningbo", "Ningbo", "unavailable", "unknown", "missing"),
            PerspectiveEvidence("risk", "Risk", "available", "neutral", "ok"),
        ]),
    )

    md = render_markdown(report)

    assert "Status/View: unavailable / unknown" in md
    assert "ningbo.recommendations_daily" in md
    assert "avg amount 7d yuan" in md
