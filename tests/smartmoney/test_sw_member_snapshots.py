from __future__ import annotations

import datetime as dt
from contextlib import AbstractContextManager

from ifa.families.smartmoney.etl import sw_member_fetcher


class _Result:
    def __init__(self, rows=None, rowcount: int = 0) -> None:
        self._rows = rows or []
        self.rowcount = rowcount

    def fetchall(self):
        return self._rows


class _Conn:
    def __init__(self) -> None:
        self.queries: list[tuple[str, dict | None]] = []

    def execute(self, statement, params=None):
        sql = str(statement)
        self.queries.append((sql, params))
        if "GROUP BY snapshot_month" in sql:
            return _Result(rows=[(dt.date(2026, 7, 1), 100)])
        if "INSERT INTO smartmoney.sw_member_monthly" in sql:
            return _Result(rowcount=88)
        return _Result()


class _Context(AbstractContextManager):
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    def __enter__(self) -> _Conn:
        return self.conn

    def __exit__(self, *exc_info) -> None:
        return None


class _Engine:
    def __init__(self) -> None:
        self.conn = _Conn()

    def connect(self) -> _Context:
        return _Context(self.conn)

    def begin(self) -> _Context:
        return _Context(self.conn)


def test_ensure_monthly_snapshots_materialises_only_missing_months() -> None:
    engine = _Engine()

    status = sw_member_fetcher.ensure_monthly_snapshots(
        engine,
        start_date=dt.date(2026, 7, 15),
        end_date=dt.date(2026, 8, 21),
    )

    assert status == {
        "start_month": dt.date(2026, 7, 1),
        "end_month": dt.date(2026, 8, 1),
        "expected_months": 2,
        "materialised_months": 1,
        "rows_inserted": 88,
    }
    insert_params = [params for sql, params in engine.conn.queries if "INSERT INTO smartmoney.sw_member_monthly" in sql]
    assert insert_params == [{"sm": dt.date(2026, 8, 1)}]
