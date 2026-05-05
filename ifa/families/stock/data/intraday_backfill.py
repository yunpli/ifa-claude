"""TuShare intraday backfill into Stock Edge DuckDB/Parquet storage.

This is target-stock first, not a full-market job. It writes under
`~/claude/ifaenv/duckdb/parquet/intraday_5min/` so report generation can reuse
the same DuckDB view without polluting the repo.
"""
from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from ifa.config import Settings, get_settings
from ifa.core.tushare import TuShareClient
from ifa.families.stock.db.duckdb_client import get_conn, init_duckdb, parquet_path_for, reset_conn

_SUPPORTED_FREQS = {"5min", "15min", "30min", "60min"}
_REQUIRED_COLS = ["ts_code", "trade_time", "open", "high", "low", "close", "vol", "amount"]


@dataclass(frozen=True)
class IntradayBackfillSpec:
    ts_code: str
    freq: str
    lookback_days: int


@dataclass
class IntradayBackfillResult:
    ts_code: str
    rows_written: int = 0
    files_written: list[str] = field(default_factory=list)
    estimated_uncompressed_mb: float = 0.0
    messages: list[str] = field(default_factory=list)


def default_intraday_sweep(ts_code: str) -> list[IntradayBackfillSpec]:
    """Fast Stock Edge sweep for one target stock.

    5min is the execution/T+0 workhorse; 30min and 60min are fetched so we can
    later compare multi-timeframe structure without exploding data volume.
    """
    return [
        IntradayBackfillSpec(ts_code=ts_code, freq="5min", lookback_days=30),
        IntradayBackfillSpec(ts_code=ts_code, freq="30min", lookback_days=60),
        IntradayBackfillSpec(ts_code=ts_code, freq="60min", lookback_days=90),
    ]


def estimate_intraday_storage(specs: list[IntradayBackfillSpec]) -> dict[str, float]:
    """Rough per-stock storage budget.

    A-share has 240 trading minutes/day. We assume 16 numeric/object columns
    after normalization and Snappy Parquet compression around 4-8x. The estimate
    is intentionally conservative so the user can size sweeps before running.
    """
    bars_per_day = {"5min": 48, "15min": 16, "30min": 8, "60min": 4}
    row_bytes_uncompressed = 160
    total_rows = 0
    for spec in specs:
        total_rows += bars_per_day.get(spec.freq, 48) * spec.lookback_days
    uncompressed_mb = total_rows * row_bytes_uncompressed / 1_000_000
    parquet_mb = uncompressed_mb / 5.0
    return {
        "rows": float(total_rows),
        "uncompressed_mb": round(uncompressed_mb, 3),
        "parquet_mb_estimate": round(parquet_mb, 3),
    }


def backfill_intraday_sweep(
    specs: list[IntradayBackfillSpec],
    *,
    end_date: dt.date | None = None,
    settings: Settings | None = None,
    on_log: Callable[[str], None] = print,
) -> IntradayBackfillResult:
    if not specs:
        raise ValueError("At least one IntradayBackfillSpec is required.")
    ts_codes = {spec.ts_code for spec in specs}
    if len(ts_codes) != 1:
        raise ValueError("One sweep should target exactly one stock.")
    ts_code = next(iter(ts_codes)).strip().upper()
    result = IntradayBackfillResult(ts_code=ts_code)
    budget = estimate_intraday_storage(specs)
    result.estimated_uncompressed_mb = budget["uncompressed_mb"]
    on_log(
        f"[intraday] sweep {ts_code}: rows≈{budget['rows']:.0f}, "
        f"parquet≈{budget['parquet_mb_estimate']:.3f} MB"
    )

    client = TuShareClient(settings or get_settings())
    end = end_date or dt.date.today()
    frames = []
    for spec in specs:
        _validate_spec(spec)
        start = end - dt.timedelta(days=max(spec.lookback_days * 2, spec.lookback_days + 7))
        on_log(f"[intraday] fetching {spec.ts_code} {spec.freq} {start} → {end}")
        df = _fetch_stk_mins(client, spec.ts_code, spec.freq, start, end)
        if df.empty:
            msg = f"{spec.freq}: TuShare returned 0 rows"
            result.messages.append(msg)
            on_log(f"[intraday] {msg}")
            continue
        df = _normalize_intraday(df, spec.freq)
        cutoff = pd.Timestamp(end - dt.timedelta(days=spec.lookback_days * 2))
        df = df[pd.to_datetime(df["trade_time"]) >= cutoff].copy()
        on_log(f"[intraday] {spec.freq}: normalized {len(df)} rows")
        frames.append(df)

    if not frames:
        result.messages.append("No intraday rows fetched.")
        return result

    all_rows = pd.concat(frames, ignore_index=True)
    for (year, month, freq), group in all_rows.groupby(
        [all_rows["trade_time"].dt.year, all_rows["trade_time"].dt.month, "freq"],
        sort=True,
    ):
        path = parquet_path_for(int(year), int(month), prefix=f"{ts_code.replace('.', '_')}_{freq}")
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = _read_existing(path)
        merged = _merge_intraday(existing, group)
        merged.to_parquet(path, index=False, compression="snappy")
        result.files_written.append(str(path))
        result.rows_written += len(group)
        on_log(f"[intraday] wrote {len(merged)} rows → {path}")

    _write_manifest(ts_code, specs, result)
    reset_conn()
    init_duckdb()
    _register_freq_views()
    return result


def _validate_spec(spec: IntradayBackfillSpec) -> None:
    if spec.freq not in _SUPPORTED_FREQS:
        raise ValueError(f"Unsupported freq {spec.freq!r}; expected one of {sorted(_SUPPORTED_FREQS)}")
    if spec.lookback_days <= 0:
        raise ValueError("lookback_days must be positive.")


def _fetch_stk_mins(
    client: TuShareClient,
    ts_code: str,
    freq: str,
    start: dt.date,
    end: dt.date,
) -> pd.DataFrame:
    start_s = f"{start:%Y-%m-%d} 09:00:00"
    end_s = f"{end:%Y-%m-%d} 15:30:00"
    try:
        return client.call("stk_mins", ts_code=ts_code, freq=freq, start_date=start_s, end_date=end_s)
    except TypeError:
        return client.call("stk_mins", ts_code=ts_code, freq=freq, start_time=start_s, end_time=end_s)


def _normalize_intraday(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    out = df.copy()
    if "trade_time" not in out.columns and "trade_date" in out.columns:
        out["trade_time"] = out["trade_date"]
    missing = [col for col in _REQUIRED_COLS if col not in out.columns]
    if missing:
        raise ValueError(f"stk_mins missing expected columns: {missing}; columns={list(out.columns)}")
    out = out[_REQUIRED_COLS].copy()
    out["trade_time"] = pd.to_datetime(out["trade_time"])
    out["trade_date"] = out["trade_time"].dt.date
    out["freq"] = freq
    for col in ["open", "high", "low", "close", "vol", "amount"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=["trade_time", "open", "high", "low", "close"])
    return out.sort_values(["ts_code", "freq", "trade_time"]).reset_index(drop=True)


def _read_existing(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def _merge_intraday(existing: pd.DataFrame, new_rows: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        merged = new_rows.copy()
    else:
        merged = pd.concat([existing, new_rows], ignore_index=True)
    merged["trade_time"] = pd.to_datetime(merged["trade_time"])
    return (
        merged.sort_values(["ts_code", "freq", "trade_time"])
        .drop_duplicates(["ts_code", "freq", "trade_time"], keep="last")
        .reset_index(drop=True)
    )


def _write_manifest(ts_code: str, specs: list[IntradayBackfillSpec], result: IntradayBackfillResult) -> None:
    manifest_dir = Path.home() / "claude" / "ifaenv" / "duckdb" / "manifests"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts_code": ts_code,
        "specs": [spec.__dict__ for spec in specs],
        "rows_written": result.rows_written,
        "files_written": result.files_written,
        "messages": result.messages,
        "computed_at": dt.datetime.now().isoformat(),
    }
    (manifest_dir / f"intraday_{ts_code.replace('.', '_')}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _register_freq_views() -> None:
    conn = get_conn()
    tables = {row[0] for row in conn.execute("SHOW TABLES").fetchall()}
    if "intraday_5min" not in tables:
        return
    conn.execute("CREATE OR REPLACE VIEW intraday_30min AS SELECT * FROM intraday_5min WHERE freq = '30min'")
    conn.execute("CREATE OR REPLACE VIEW intraday_60min AS SELECT * FROM intraday_5min WHERE freq = '60min'")
