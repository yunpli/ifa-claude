from __future__ import annotations

import datetime as dt

from ifa.families.stock.data.intraday import load_intraday_5min


def test_intraday_loader_degrades_when_duckdb_view_absent():
    result = load_intraday_5min(
        "300042.SZ",
        start_date=dt.date(2026, 4, 22),
        end_date=dt.date(2026, 4, 30),
    )

    assert result.name == "intraday_5min"
    assert result.status in {"ok", "missing"}
    if result.status == "missing":
        assert result.message
