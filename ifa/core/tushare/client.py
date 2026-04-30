"""TuShare Pro client wrapper.

Token never leaves the secrets file. Every call is logged with its window and row
count so the caller can build `report_inputs` rows downstream.

This is intentionally minimal in this iteration — only enough to (a) verify the
token works in healthcheck, and (b) give Macro report sections a typed entry
point. Per-table convenience methods will be added as Macro sections need them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import tushare as ts

from ifa.config import Settings, get_settings


@dataclass
class TuShareCallLog:
    api: str
    params: dict[str, Any]
    row_count: int


class TuShareClient:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        ts.set_token(self.settings.tushare_token.get_secret_value())
        self._pro = ts.pro_api()
        self.call_log: list[TuShareCallLog] = []

    # ---- generic ----------------------------------------------------------

    def call(self, api: str, **params: Any) -> pd.DataFrame:
        """Call any TuShare Pro endpoint by name."""
        fn = getattr(self._pro, api)
        df = fn(**params)
        self.call_log.append(TuShareCallLog(api=api, params=params, row_count=len(df)))
        return df

    # ---- light convenience for healthcheck --------------------------------

    def trade_calendar(self, *, exchange: str = "SSE", start: str, end: str) -> pd.DataFrame:
        return self.call("trade_cal", exchange=exchange, start_date=start, end_date=end)

    def stock_basic_sample(self, *, n: int = 5) -> pd.DataFrame:
        df = self.call("stock_basic", exchange="", list_status="L")
        return df.head(n)
