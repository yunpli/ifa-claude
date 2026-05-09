from __future__ import annotations

import datetime as dt

from ifa.families.stock.diagnostic.models import EvidencePoint, PerspectiveEvidence
from ifa.families.stock.diagnostic.delivery import build_telegram_delivery_payload
from ifa.families.stock.diagnostic.persistence import persist_diagnostic_run
from ifa.families.stock.diagnostic.service import diagnostic_manifest_payload, render_html, render_markdown, synthesize_diagnostic
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


def test_synthesis_lowers_confidence_when_key_evidence_is_stale():
    perspectives = [
        PerspectiveEvidence(
            "stock_edge_sector_cycle",
            "Stock Edge",
            "available",
            "positive",
            "sector flow positive",
            freshness={"status": "fresh"},
        ),
        PerspectiveEvidence(
            "ta",
            "TA",
            "available",
            "positive",
            "TA setup positive",
            freshness={"status": "stale"},
        ),
        PerspectiveEvidence("risk", "Risk", "available", "neutral", "no hard risk", freshness={"status": "fresh"}),
    ]

    synthesis = synthesize_diagnostic(perspectives)

    assert synthesis.conclusion == "short-term tradable"
    assert synthesis.confidence == "low"
    assert "stale/unavailable" in synthesis.rationale[-1]


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

    assert "## Top Summary" in md
    assert "Key conflict" in md
    assert "Status/View: unavailable / unknown" in md
    assert "ningbo.recommendations_daily" in md
    assert "avg amount 7d yuan" in md


def test_report_dict_exposes_customer_contract_aliases():
    report = DiagnosticReport(
        ts_code="300042.SZ",
        name="朗科科技",
        as_of_trade_date=dt.date(2026, 5, 6),
        generated_at_bjt="2026-05-08T10:00:00+08:00",
        data_cutoff_bjt="2026-05-06T15:00:00+08:00",
        perspectives=[
            PerspectiveEvidence(
                "risk",
                "Risk",
                "available",
                "neutral",
                "未命中硬性风险。",
                points=[EvidencePoint("avg amount 7d yuan", 123.0, "smartmoney.raw_daily", "2026-05-06")],
                freshness={"latest_as_of": "2026-05-06", "evidence_count": 1},
            ),
        ],
        synthesis=synthesize_diagnostic([
            PerspectiveEvidence("risk", "Risk", "available", "neutral", "ok"),
        ]),
    )

    data = report.to_dict()
    perspective = data["perspectives"][0]

    assert perspective["stance"] == "neutral"
    assert perspective["evidence"][0]["label"] == "avg amount 7d yuan"
    assert perspective["missing_evidence"] == []
    assert perspective["freshness"]["latest_as_of"] == "2026-05-06"
    assert perspective["freshness_status"] == "fresh"


def test_html_renderer_includes_summary_and_missing_evidence():
    report = DiagnosticReport(
        ts_code="300042.SZ",
        name="朗科科技",
        as_of_trade_date=dt.date(2026, 5, 6),
        generated_at_bjt="2026-05-08T10:00:00+08:00",
        data_cutoff_bjt="2026-05-06T15:00:00+08:00",
        perspectives=[
            PerspectiveEvidence(
                "stock_edge_sector_cycle",
                "Stock Edge / Sector-Cycle-Leader",
                "partial",
                "neutral",
                "板块证据不完整。",
                missing=["stock.sector_cycle_leader_daily"],
            ),
        ],
        synthesis=synthesize_diagnostic([
            PerspectiveEvidence("stock_edge_sector_cycle", "Stock Edge", "partial", "neutral", "ok"),
        ]),
    )

    html = render_html(report)

    assert "<h2>Top Conclusion</h2>" in html
    assert "Missing Evidence" in html
    assert "stock.sector_cycle_leader_daily" in html


def test_manifest_payload_persists_run_contract():
    report = DiagnosticReport(
        ts_code="300042.SZ",
        name="朗科科技",
        as_of_trade_date=dt.date(2026, 5, 6),
        generated_at_bjt="2026-05-08T10:00:00+08:00",
        data_cutoff_bjt="2026-05-06T15:00:00+08:00",
        perspectives=[
            PerspectiveEvidence(
                "risk",
                "Risk",
                "available",
                "neutral",
                "未命中硬性风险。",
                points=[EvidencePoint("avg amount 7d yuan", 123.0, "smartmoney.raw_daily", "2026-05-06")],
                freshness={"status": "fresh", "latest_as_of": "2026-05-06"},
            ),
        ],
        synthesis=synthesize_diagnostic([
            PerspectiveEvidence("risk", "Risk", "available", "neutral", "ok", freshness={"status": "fresh"}),
        ]),
    )

    payload = diagnostic_manifest_payload(report, output_paths={"html": "/tmp/report.html"})

    assert payload["artifact_type"] == "stock_edge_diagnostic_run"
    assert payload["logic_version"] == "stock_diagnostic_synthesis_v1"
    assert payload["ts_code"] == "300042.SZ"
    assert payload["conclusion"] == report.synthesis.conclusion
    assert payload["output_paths"]["html"] == "/tmp/report.html"
    assert payload["perspective_statuses"]["risk"]["sources"] == ["smartmoney.raw_daily"]


def test_telegram_delivery_payload_is_dry_run_contract_only():
    manifest = {
        "artifact_type": "stock_edge_diagnostic_run",
        "ts_code": "300042.SZ",
        "name": "朗科科技",
        "as_of_trade_date": "2026-05-06",
        "conclusion": "watch only",
        "confidence": "low",
        "synthesis": {
            "horizon_suitability": {"5d": "watch", "10d": "watch", "20d": "neutral"},
            "trigger": "放量突破",
            "invalidation": "跌破支撑",
            "conflicts": ["sector positive but TA unconfirmed"],
        },
        "output_paths": {"html": "/tmp/report.html"},
        "perspective_statuses": {
            "ta": {"status": "available", "freshness_status": "fresh", "latency_ms": 12.3},
            "research_news": {
                "status": "unavailable",
                "freshness_status": "unavailable",
                "latency_ms": 3.0,
                "missing_required": ["stock.theme_heat_weekly"],
            },
        },
    }

    payload = build_telegram_delivery_payload(manifest, recipient_placeholder="telegram:<chat_id>")

    assert payload["artifact_type"] == "stock_edge_diagnostic_telegram_delivery_payload"
    assert payload["dry_run"] is True
    assert payload["external_send_performed"] is False
    assert payload["delivery_mode"] == "ifa_direct_send_preferred_if_enabled"
    assert "结论: watch only" in payload["short_text"]
    assert payload["attachments"][0]["path"] == "/tmp/report.html"
    assert payload["failure_context"]["unavailable"] == ["research_news"]
    assert payload["latency"]["total_latency_ms"] == 15.3


def test_synthesis_tags_conflict_taxonomy_for_hard_risk():
    perspectives = [
        PerspectiveEvidence("stock_edge_sector_cycle", "Stock Edge", "available", "positive", "sector flow positive"),
        PerspectiveEvidence("ta", "TA", "available", "neutral", "no TA setup"),
        PerspectiveEvidence("risk", "Risk", "available", "risk", "blacklist hit"),
    ]

    synthesis = synthesize_diagnostic(perspectives)

    assert synthesis.logic_version == "stock_diagnostic_synthesis_v1"
    assert "hard_risk_precedence" in synthesis.conflict_taxonomy
    assert "sector_positive_ta_unconfirmed" in synthesis.conflict_taxonomy


def test_persist_diagnostic_run_uses_transaction_and_evidence_rows(monkeypatch):
    report = DiagnosticReport(
        ts_code="300042.SZ",
        name="朗科科技",
        as_of_trade_date=dt.date(2026, 5, 6),
        generated_at_bjt="2026-05-08T10:00:00+08:00",
        data_cutoff_bjt="2026-05-06T15:00:00+08:00",
        perspectives=[
            PerspectiveEvidence(
                "risk",
                "Risk",
                "available",
                "neutral",
                "未命中硬性风险。",
                points=[EvidencePoint("avg amount 7d yuan", 123.0, "smartmoney.raw_daily", "2026-05-06")],
                freshness={"status": "fresh", "latest_as_of": "2026-05-06"},
            ),
        ],
        synthesis=synthesize_diagnostic([
            PerspectiveEvidence("risk", "Risk", "available", "neutral", "ok", freshness={"status": "fresh"}),
        ]),
    )

    class FakeTx:
        def __init__(self):
            self.calls = []
            self.entered = False
            self.exited = False

        def __enter__(self):
            self.entered = True
            return self

        def __exit__(self, *args):
            self.exited = True
            return None

        def execute(self, sql, params):
            self.calls.append((str(sql), params))

    class FakeEngine:
        def __init__(self):
            self.tx = FakeTx()

        def begin(self):
            return self.tx

    import ifa.families.stock.diagnostic.persistence as persistence

    monkeypatch.setattr(persistence, "Engine", FakeEngine)
    engine = FakeEngine()
    run_id = persist_diagnostic_run(
        report,
        engine=engine,
        output_paths={"html": "/tmp/report.html"},
        requested_at=dt.datetime(2026, 5, 8, 9, 0, tzinfo=dt.timezone.utc),
    )

    assert run_id["status"] == "persisted"
    assert engine.tx.entered and engine.tx.exited
    assert len(engine.tx.calls) == 2
    assert "stock.diagnostic_runs" in engine.tx.calls[0][0]
    assert engine.tx.calls[0][1]["logic_version"] == "stock_diagnostic_synthesis_v1"
    assert "stock.diagnostic_perspective_evidence" in engine.tx.calls[1][0]
    assert engine.tx.calls[1][1]["perspective_key"] == "risk"
