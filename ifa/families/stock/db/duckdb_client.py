"""Stock Intel DuckDB client — time-series engine for 5min intraday + Kronos embeddings.

DB location: ~/claude/ifaenv/duckdb/stock.duckdb
Parquet layout:
  parquet/intraday_5min/year=YYYY/month=MM/<prefix>.parquet
  parquet/kronos/year=YYYY/<prefix>.parquet

Usage:
    from ifa.families.stock.db.duckdb_client import get_conn, init_duckdb

    conn = get_conn()
    df = conn.execute("SELECT * FROM stock.intraday_5min WHERE ts_code='001339.SZ' LIMIT 10").df()
"""
from __future__ import annotations

import os
from pathlib import Path

import duckdb

_DUCKDB_DIR = Path(os.environ.get("IFA_DUCKDB_DIR", "~/claude/ifaenv/duckdb")).expanduser()
_DUCKDB_PATH = _DUCKDB_DIR / "stock.duckdb"
_PARQUET_ROOT = _DUCKDB_DIR / "parquet"

_conn: duckdb.DuckDBPyConnection | None = None


def get_conn(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Return (or create) the singleton DuckDB connection."""
    global _conn
    if _conn is None:
        _DUCKDB_DIR.mkdir(parents=True, exist_ok=True)
        _conn = duckdb.connect(str(_DUCKDB_PATH), read_only=read_only)
        _init_views(_conn)
    return _conn


def reset_conn() -> None:
    """Close and reset the singleton (for tests / reinitialization)."""
    global _conn
    if _conn is not None:
        try:
            _conn.close()
        except Exception:
            pass
        _conn = None


def init_duckdb() -> None:
    """Initialize DuckDB: create Parquet directories, register views, create tables."""
    _DUCKDB_DIR.mkdir(parents=True, exist_ok=True)
    (_PARQUET_ROOT / "intraday_5min").mkdir(parents=True, exist_ok=True)
    (_PARQUET_ROOT / "kronos").mkdir(parents=True, exist_ok=True)

    conn = get_conn()
    _init_views(conn)
    _init_tables(conn)


def _init_views(conn: duckdb.DuckDBPyConnection) -> None:
    """Register Parquet-backed views (idempotent — CREATE OR REPLACE).

    Views are created in the default (main) schema of this DuckDB file.
    Only created when Parquet files exist — empty glob raises BinderException.
    """
    intraday_glob = str(_PARQUET_ROOT / "intraday_5min" / "**" / "*.parquet")
    kronos_glob = str(_PARQUET_ROOT / "kronos" / "**" / "*.parquet")

    # Only create views if Parquet files exist (otherwise DuckDB errors on empty glob)
    intraday_files = list((_PARQUET_ROOT / "intraday_5min").rglob("*.parquet"))
    if intraday_files:
        conn.execute(f"""
            CREATE OR REPLACE VIEW intraday_5min AS
            SELECT * FROM read_parquet('{intraday_glob}', hive_partitioning=true)
        """)

    kronos_files = list((_PARQUET_ROOT / "kronos").rglob("*.parquet"))
    if kronos_files:
        conn.execute(f"""
            CREATE OR REPLACE VIEW kronos_embeddings AS
            SELECT * FROM read_parquet('{kronos_glob}', hive_partitioning=true)
        """)


def _init_tables(conn: duckdb.DuckDBPyConnection) -> None:
    """Create persistent DuckDB tables (metadata caches)."""
    # DuckDB: use main schema (default) for tables inside a single-file DB
    conn.execute("""
        CREATE TABLE IF NOT EXISTS timeframe_snapshot (
            ts_code      VARCHAR NOT NULL,
            trade_date   DATE NOT NULL,
            timeframe    VARCHAR NOT NULL,
            snapshot     JSON,
            computed_at  TIMESTAMP,
            PRIMARY KEY (ts_code, trade_date, timeframe)
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS analog_cache (
            ts_code         VARCHAR NOT NULL,
            end_date        DATE NOT NULL,
            top_k_json      JSON,
            computed_at     TIMESTAMP,
            PRIMARY KEY (ts_code, end_date)
        )
    """)


def parquet_path_for(year: int, month: int, prefix: str = "all") -> Path:
    """Return the Parquet file path for a given year/month/prefix."""
    return _PARQUET_ROOT / "intraday_5min" / f"year={year}" / f"month={month:02d}" / f"{prefix}.parquet"


def kronos_path_for(year: int, prefix: str = "all") -> Path:
    return _PARQUET_ROOT / "kronos" / f"year={year}" / f"{prefix}.parquet"
