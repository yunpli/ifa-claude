from __future__ import annotations

import datetime as dt

import pandas as pd

from ifa.families.stock.backtest import compute_forward_labels


def test_forward_labels_include_three_horizon_decision_fields():
    rows = []
    for i in range(26):
        close = 10.0 + i * 0.08
        rows.append(
            {
                "trade_date": dt.date(2026, 1, 1) + dt.timedelta(days=i),
                "open": close - 0.02,
                "high": close + 0.20,
                "low": close - 0.18,
                "close": close,
                "amount": 120000.0,
            }
        )
    labels = compute_forward_labels(
        pd.DataFrame(rows),
        as_of_trade_date=dt.date(2026, 1, 5),
        entry_price=10.32,
        stop_price=9.70,
        target_return_by_horizon_pct={5: 4.0, 10: 7.0, 20: 12.0},
        entry_zone_low=10.10,
        entry_zone_high=10.45,
    )

    payload = labels.to_dict()
    assert payload["return_5d_pct"] is not None
    assert payload["return_10d_pct"] is not None
    assert payload["return_20d_pct"] is not None
    assert payload["positive_5d"] is True
    assert payload["mfe_5d_pct"] is not None
    assert payload["mae_20d_pct"] is not None
    assert payload["target_first_5d"] in {True, False, None}
    assert payload["stop_first_10d"] in {True, False, None}
    assert payload["entry_fill_5d"] is not None
    assert payload["adverse_gap_next_open"] is not None
    assert payload["slippage_bucket"] in {"low", "medium", "high", "unknown"}
    assert payload["hit_50pct_40d"] in {True, False}
