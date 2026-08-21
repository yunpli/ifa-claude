from __future__ import annotations

import datetime as dt
from contextlib import AbstractContextManager

import pandas as pd

from ifa.families.ta.etl import suspend_limit


class _Conn:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.params: list[object] = []

    def execute(self, statement, params=None):
        self.statements.append(str(statement))
        self.params.append(params)
        return None


class _Begin(AbstractContextManager):
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Conn:
        return self.conn

    def __exit__(self, *exc_info) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.conn = _Conn()

    def begin(self) -> _Begin:
        return _Begin(self.conn)


def test_fetch_and_store_limit_quotes_reserved_limit_column(monkeypatch) -> None:
    monkeypatch.setattr(
        suspend_limit,
        "_pull_limit",
        lambda trade_date: pd.DataFrame([
            {
                "ts_code": "000001.SZ",
                "trade_date": trade_date,
                "name": "平安银行",
                "close": 10.2,
                "pct_chg": 9.99,
                "limit": "U",
            }
        ]),
    )
    engine = _Engine()

    rows = suspend_limit.fetch_and_store_limit(engine, dt.date(2026, 8, 21))

    assert rows == 1
    sql = engine.conn.statements[0]
    assert 'strth, "limit")' in sql
    assert '"limit" = EXCLUDED."limit"' in sql
    assert "strth, limit" not in sql
