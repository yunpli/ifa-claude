from __future__ import annotations

import datetime as dt
import json

from ifa.families.stock.theme_heat import (
    _aggregate_theme_rows,
    _keyword_theme_label,
    _quality_flag_for_sources,
    _tushare_row_importance,
    daily_theme_heat_artifact_from_llm_response,
    daily_theme_heat_llm_messages,
    daily_theme_heat_response_schema,
    default_stub_themes,
    weekly_theme_heat_llm_messages,
    weekly_theme_heat_response_schema,
    weekly_theme_heat_rows_from_llm_response,
    week_start,
)
from scripts.stock_edge_theme_heat_stub import _load_theme_rows
from scripts.stock_edge_theme_heat_llm import _parse_window_days


def test_week_start_uses_monday():
    assert week_start(dt.date(2026, 5, 8)) == dt.date(2026, 5, 4)


def test_default_stub_themes_are_explicit_top5_placeholders():
    rows = default_stub_themes(dt.date(2026, 5, 8))

    assert len(rows) == 5
    assert [row.theme_rank for row in rows] == [1, 2, 3, 4, 5]
    assert {row.quality_flag for row in rows} == {"stub"}
    assert all(0.0 <= row.heat_score <= 1.0 for row in rows)


def test_theme_heat_cli_loads_batch_cache_json(tmp_path):
    path = tmp_path / "themes.json"
    path.write_text(
        json.dumps({
            "themes": [
                {
                    "theme_rank": 1,
                    "theme_label": "AI端侧应用",
                    "category": "AI",
                    "heat_score": 0.82,
                    "affected_sectors": [{"l2_code": "801081.SI", "l2_name": "半导体"}],
                    "representative_stocks": [{"ts_code": "300042.SZ", "name": "朗科科技"}],
                    "quality_flag": "batch_llm_cache",
                }
            ]
        }),
        encoding="utf-8",
    )

    rows = _load_theme_rows(path, dt.date(2026, 5, 4), "manual")

    assert len(rows) == 1
    assert rows[0].theme_label == "AI端侧应用"
    assert rows[0].representative_stocks[0]["ts_code"] == "300042.SZ"
    assert rows[0].quality_flag == "batch_llm_cache"


def test_theme_heat_local_source_aggregation_outputs_non_stub_rows():
    rows = _aggregate_theme_rows(
        [
            {
                "source_table": "research.company_event_memory",
                "capture_date": dt.date(2026, 5, 4),
                "event_type": "AI应用",
                "title": "端侧AI更新",
                "polarity": "positive",
                "importance": "high",
                "source_url": "https://example.test/a",
                "ts_code": "300042.SZ",
            },
            {
                "source_table": "ta.catalyst_event_memory",
                "capture_date": dt.date(2026, 5, 5),
                "event_type": "AI应用",
                "polarity": "neutral",
                "importance": "medium",
                "source_url": "https://example.test/b",
                "target_ts_codes": ["002888.SZ"],
                "target_sectors": ["计算机应用"],
            },
            {
                "source_table": "ta.catalyst_event_memory",
                "capture_date": dt.date(2026, 5, 6),
                "event_type": "半导体",
                "polarity": "positive",
                "importance": "medium",
                "target_sectors": ["半导体"],
            },
        ],
        week=dt.date(2026, 5, 4),
        max_themes=5,
        run_mode="manual",
    )

    assert rows[0].theme_label == "AI应用"
    assert rows[0].quality_flag == "local_source_cache"
    assert rows[0].prompt_version == "stock_theme_heat_local_sources_v1"
    assert rows[0].representative_stocks[0]["ts_code"] in {"300042.SZ", "002888.SZ"}
    assert rows[0].evidence["source_rows"] == 2


def test_theme_heat_keyword_mapping_for_tushare_cache_rows():
    assert _keyword_theme_label("公司签署端侧AI算力合作协议") == "AI与算力"
    assert _keyword_theme_label("拟收购半导体封测资产") == "半导体与国产替代"
    assert _tushare_row_importance("关于重大资产重组的公告") == "high"
    assert _quality_flag_for_sources({"research.api_cache.anns_d"}) == "tushare_cached"


def test_weekly_theme_heat_llm_prompt_is_batch_and_auditable():
    messages = weekly_theme_heat_llm_messages(
        dt.date(2026, 5, 4),
        [
            {
                "source_table": "research.api_cache.anns_d",
                "capture_date": dt.date(2026, 5, 6),
                "title": "低空经济订单落地",
                "summary": "多家公司披露无人机订单。",
                "source_url": "https://example.test/news",
                "target_ts_codes": ["000001.SZ"],
            }
        ],
        max_themes=5,
        evidence_quality="local_evidence",
    )
    schema = weekly_theme_heat_response_schema(max_themes=5)

    user_payload = json.loads(messages[1]["content"])
    assert "per-news tagging" in messages[0]["content"]
    assert user_payload["local_cached_facts"][0]["fact_id"] == 1
    assert user_payload["evidence_quality"] == "local_evidence"
    assert any("主力资金" in item for item in user_payload["instructions"])
    assert schema["properties"]["themes"]["maxItems"] == 5


def test_weekly_theme_heat_rows_from_llm_response_preserves_evidence_fields():
    rows = weekly_theme_heat_rows_from_llm_response(
        {
            "themes": [
                {
                    "theme_label": "低空经济",
                    "category": "policy_growth",
                    "heat_score": 0.84,
                    "persistence_score": 0.71,
                    "freshness": "new_acceleration",
                    "affected_sectors": [{"sector_name": "航空装备"}],
                    "representative_stocks": [{"ts_code": "000001.SZ", "name": "样本"}],
                    "leader_candidates": [{"ts_code": "000001.SZ", "reason": "订单披露"}],
                    "one_day_wonder_risk": 0.22,
                    "validation_signals": ["主力净流入延续"],
                    "evidence_refs": [{"fact_id": 1}],
                    "quality_flag": "batch_llm_cache",
                    "main_logic": "订单落地改善持续性。",
                }
            ]
        },
        week=dt.date(2026, 5, 4),
        run_mode="manual",
        model_name="fake-model",
        source_rows=[
            {
                "source_table": "research.api_cache.anns_d",
                "capture_date": dt.date(2026, 5, 6),
                "title": "低空经济订单落地",
                "source_url": "https://example.test/news",
            }
        ],
        evidence_quality="local_evidence",
        max_themes=5,
    )

    assert rows[0].quality_flag == "batch_llm_cache"
    assert rows[0].model_name == "fake-model"
    assert rows[0].source_urls == ["https://example.test/news"]
    assert rows[0].evidence["persistence_score"] == 0.71
    assert rows[0].evidence["validation_signals"] == ["主力净流入延续"]
    assert rows[0].evidence["main_money_judgement"] == ""


def test_daily_theme_heat_llm_prompt_asks_required_batch_questions():
    messages = daily_theme_heat_llm_messages(
        dt.date(2026, 5, 8),
        [],
        window_days=7,
        max_themes=8,
        evidence_quality="needs_local_evidence",
    )
    schema = daily_theme_heat_response_schema(max_themes=8)

    user_payload = json.loads(messages[1]["content"])
    assert user_payload["source_marker"] == "llm_daily_theme_scan"
    assert user_payload["window_start"] == "2026-05-02"
    assert any("真正影响资金行为" in item for item in user_payload["questions"])
    assert any("未来1/3/5/10/20" in item for item in user_payload["questions"])
    assert "per-news" in user_payload["instructions"][0]
    assert schema["properties"]["themes"]["maxItems"] == 8


def test_daily_theme_heat_artifact_preserves_quality_and_flow_fields():
    artifact = daily_theme_heat_artifact_from_llm_response(
        {
            "market_summary": "资金围绕低空经济扩散。",
            "themes": [
                {
                    "theme_label": "低空经济",
                    "category": "policy_growth",
                    "heat_score": 0.77,
                    "persistence_score": 0.66,
                    "freshness": "accelerating",
                    "main_money_judgement": "主力净流入扩散但未到极端。",
                    "retail_chase_judgement": "散户追涨升温。",
                    "crowding_distribution_risk": "高位分歧需防出货。",
                    "validation_signals_by_horizon": {"1d": "成交额不缩", "5d": "龙头不破位"},
                    "quality_flag": "needs_local_evidence",
                }
            ],
        },
        as_of=dt.date(2026, 5, 8),
        window_days=7,
        run_mode="manual",
        model_name="fake-model",
        endpoint="primary",
        source="all-cache",
        source_rows=[],
        evidence_quality="needs_local_evidence",
        max_themes=8,
    )

    assert artifact["source_marker"] == "llm_daily_theme_scan"
    assert artifact["quality_flag"] == "llm_prior_only"
    assert artifact["themes"][0]["quality_flag"] == "needs_local_evidence"
    assert artifact["themes"][0]["main_money_judgement"].startswith("主力")
    assert artifact["themes"][0]["validation_signals_by_horizon"]["5d"] == "龙头不破位"
    assert _parse_window_days("7d") == 7
