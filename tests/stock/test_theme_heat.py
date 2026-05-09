from __future__ import annotations

import datetime as dt

from ifa.families.stock.theme_heat import default_stub_themes, week_start


def test_week_start_uses_monday():
    assert week_start(dt.date(2026, 5, 8)) == dt.date(2026, 5, 4)


def test_default_stub_themes_are_explicit_top5_placeholders():
    rows = default_stub_themes(dt.date(2026, 5, 8))

    assert len(rows) == 5
    assert [row.theme_rank for row in rows] == [1, 2, 3, 4, 5]
    assert {row.quality_flag for row in rows} == {"stub"}
    assert all(0.0 <= row.heat_score <= 1.0 for row in rows)
