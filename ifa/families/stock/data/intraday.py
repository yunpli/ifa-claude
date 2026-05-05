"""Optional DuckDB-backed intraday loaders for Stock Edge."""
from __future__ import annotations

import datetime as dt

import pandas as pd

from ifa.families.stock.db.duckdb_client import get_conn

from .availability import LoadResult


def load_intraday_5min(
    ts_code: str,
    *,
    start_date: dt.date,
    end_date: dt.date,
    required: bool = False,
) -> LoadResult[pd.DataFrame]:
    """Load target-stock 5min bars if the local DuckDB view exists."""
    try:
        conn = get_conn(read_only=True)
        tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
        if "intraday_5min" not in tables:
            return LoadResult(
                name="intraday_5min",
                data=None,
                source="missing",
                status="missing",
                required=required,
                message="DuckDB intraday_5min view is not available.",
            )
        cols = {row[0] for row in conn.execute("DESCRIBE intraday_5min").fetchall()}
        freq_filter = "AND freq = '5min'" if "freq" in cols else ""
        df = conn.execute(
            f"""
            SELECT *
            FROM intraday_5min
            WHERE ts_code = ?
              AND CAST(trade_time AS DATE) BETWEEN ? AND ?
              {freq_filter}
            ORDER BY trade_time
            """,
            [ts_code, start_date, end_date],
        ).df()
    except Exception as exc:  # noqa: BLE001
        return LoadResult(
            name="intraday_5min",
            data=None,
            source="missing",
            status="missing",
            required=required,
            message=f"DuckDB intraday_5min unavailable: {type(exc).__name__}: {exc}",
        )

    if df.empty:
        return LoadResult(
            name="intraday_5min",
            data=None,
            source="missing",
            status="missing",
            required=required,
            message=f"No local 5min bars for {ts_code} between {start_date} and {end_date}.",
        )
    return LoadResult(
        name="intraday_5min",
        data=df,
        source="duckdb",
        status="ok",
        rows=len(df),
        as_of=pd.to_datetime(df["trade_time"]).max().to_pydatetime(),
        required=required,
    )
