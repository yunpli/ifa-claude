from __future__ import annotations

import datetime as dt
import json

from ifa.families.stock.theme_heat import _aggregate_theme_rows, default_stub_themes, week_start
from scripts.stock_edge_theme_heat_stub import _load_theme_rows


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
