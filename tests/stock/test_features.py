from __future__ import annotations

import pandas as pd

from ifa.families.stock.features import build_support_resistance, compute_technical_summary


def _bars(n: int = 60) -> pd.DataFrame:
    rows = []
    for i in range(n):
        close = 10.0 + i * 0.1
        rows.append(
            {
                "trade_date": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
                "open": close - 0.05,
                "high": close + 0.2,
                "low": close - 0.2,
                "close": close,
                "amount": 100000 + i,
            }
        )
    return pd.DataFrame(rows)


def test_compute_technical_summary_detects_uptrend_and_amount_units():
    summary = compute_technical_summary(_bars())

    assert summary.trend_label == "uptrend"
    assert summary.ma5 is not None
    assert summary.ma20 is not None
    assert summary.ma60 is not None
    assert summary.atr14 is not None
    assert summary.avg_amount_7d_yuan and summary.avg_amount_7d_yuan > 100_000_000


def test_build_support_resistance_returns_auditable_levels():
    levels = build_support_resistance(_bars())

    assert levels
    assert any(level.source == "20d_low" for level in levels)
    assert any(level.source == "20d_high" for level in levels)
    assert all(level.price > 0 for level in levels)
