from __future__ import annotations

import datetime as dt

import scripts.stock_edge_data_backfill as data_backfill


def test_forward_label_table_lag_is_maturing_not_stale(monkeypatch) -> None:
    monkeypatch.setattr(data_backfill, "_trading_lag_to_today", lambda engine, max_date: 1)

    status = data_backfill._postgres_freshness_status(
        object(),
        "sme.sme_strategy_eval_daily",
        dt.date(2026, 8, 20),
        100,
    )

    assert status["supports_5d"] == "是"
    assert status["needs_backfill"].startswith("否（forward labels mature")


def test_legacy_ningbo_tables_are_not_daily_staleness_failures() -> None:
    status = data_backfill._postgres_freshness_status(
        object(),
        "ningbo.candidates_daily",
        dt.date(2026, 4, 30),
        100,
    )

    assert status["supports_20d"] == "历史"
    assert status["needs_backfill"] == "否（legacy，不按日刷新）"
