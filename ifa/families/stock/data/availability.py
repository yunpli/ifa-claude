"""Data availability primitives for Stock Edge.

Every loader must report where data came from and whether it is complete enough
for the current phase. Strategy code should never infer data quality from an
empty DataFrame alone.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Generic, Literal, TypeVar

T = TypeVar("T")

LoadSource = Literal["postgres", "duckdb", "parquet", "tushare_backfill", "missing"]
LoadStatus = Literal["ok", "partial", "missing", "stale"]


@dataclass(frozen=True)
class LoadResult(Generic[T]):
    name: str
    data: T | None
    source: LoadSource
    status: LoadStatus
    rows: int = 0
    as_of: dt.date | dt.datetime | None = None
    required: bool = False
    message: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def degraded(self) -> bool:
        return self.status in ("partial", "missing", "stale")

    def require(self) -> T:
        if self.data is None or not self.ok:
            raise RuntimeError(self.message or f"Required data is not available: {self.name}")
        return self.data
