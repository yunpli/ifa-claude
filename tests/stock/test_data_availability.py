from __future__ import annotations

import pandas as pd

from ifa.families.stock.data import LoadResult


def test_load_result_ok_and_degraded_flags():
    result = LoadResult(name="daily_bars", data=pd.DataFrame({"x": [1]}), source="postgres", status="ok", rows=1)

    assert result.ok is True
    assert result.degraded is False
    assert result.require().iloc[0]["x"] == 1


def test_load_result_required_missing_raises_clear_error():
    result = LoadResult(
        name="daily_bars",
        data=None,
        source="missing",
        status="missing",
        required=True,
        message="No local data.",
    )

    assert result.ok is False
    assert result.degraded is True
    try:
        result.require()
    except RuntimeError as exc:
        assert "No local data" in str(exc)
    else:
        raise AssertionError("Expected missing required data to raise")
