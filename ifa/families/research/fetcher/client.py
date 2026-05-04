"""Research fetcher client — wraps all 23 Tushare APIs with cache + retry.

Each public function:
  · checks api_cache first (via cache.py)
  · on miss: calls Tushare with tenacity retry (3×, exp backoff)
  · stores result in api_cache
  · returns raw response as a list-of-dicts (JSON-safe)

Unit conversion (元/万元/万股 → base) is done in the analyzer layer, NOT here.
Here we just cache the raw Tushare response faithfully.

IRM routing:
  · exchange='SSE' → irm_qa_sh
  · exchange='SZSE' or unknown → irm_qa_sz
  · BSE (北交所) → returns []  (API not supported)
"""
from __future__ import annotations

import logging
import time
from typing import Any

import pandas as pd
import tushare as ts
from tenacity import retry, stop_after_attempt, wait_exponential

from ifa.config import get_settings
from ifa.families.research.fetcher.cache import cache_get, cache_set

log = logging.getLogger(__name__)

# Lazy singleton — avoids re-init on every call
_pro: Any = None


def _get_pro() -> Any:
    global _pro
    if _pro is None:
        settings = get_settings()
        ts.set_token(settings.tushare_token.get_secret_value())
        _pro = ts.pro_api()
    return _pro


def _df_to_records(df: pd.DataFrame | None) -> list[dict]:
    if df is None or df.empty:
        return []
    return df.where(df.notna(), other=None).to_dict(orient="records")


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _call_tushare(method: str, **kwargs: Any) -> list[dict]:
    pro = _get_pro()
    fn = getattr(pro, method)
    df = fn(**kwargs)
    return _df_to_records(df)


def _fetch(engine: Any, ts_code: str, api_name: str, tushare_method: str, **kwargs: Any) -> list[dict]:
    cached = cache_get(engine, ts_code, api_name, kwargs)
    if cached is not None:
        log.debug("cache hit: %s / %s", ts_code, api_name)
        return cached
    log.debug("cache miss: %s / %s — calling Tushare", ts_code, api_name)
    result = _call_tushare(tushare_method, **kwargs)
    cache_set(engine, ts_code, api_name, kwargs, result)
    return result


# ── Public API: one function per Tushare endpoint ────────────────────────────

def fetch_stock_basic(engine: Any, ts_code: str) -> list[dict]:
    return _fetch(engine, ts_code, "stock_basic", "stock_basic",
                  ts_code=ts_code, fields="ts_code,name,exchange,market,list_date,list_status,industry")


def fetch_stock_company(engine: Any, ts_code: str) -> list[dict]:
    return _fetch(engine, ts_code, "stock_company", "stock_company",
                  ts_code=ts_code)


def fetch_income(engine: Any, ts_code: str, *, period: str | None = None, limit: int = 8) -> list[dict]:
    kwargs: dict = {"ts_code": ts_code, "limit": limit}
    if period:
        kwargs["period"] = period
    return _fetch(engine, ts_code, "income", "income", **kwargs)


def fetch_balancesheet(engine: Any, ts_code: str, *, limit: int = 8) -> list[dict]:
    return _fetch(engine, ts_code, "balancesheet", "balancesheet",
                  ts_code=ts_code, limit=limit)


def fetch_cashflow(engine: Any, ts_code: str, *, limit: int = 8) -> list[dict]:
    return _fetch(engine, ts_code, "cashflow", "cashflow",
                  ts_code=ts_code, limit=limit)


def fetch_fina_indicator(engine: Any, ts_code: str, *, limit: int = 8) -> list[dict]:
    return _fetch(engine, ts_code, "fina_indicator", "fina_indicator",
                  ts_code=ts_code, limit=limit)


def fetch_forecast(engine: Any, ts_code: str, *, limit: int = 4) -> list[dict]:
    return _fetch(engine, ts_code, "forecast", "forecast",
                  ts_code=ts_code, limit=limit)


def fetch_express(engine: Any, ts_code: str, *, limit: int = 4) -> list[dict]:
    return _fetch(engine, ts_code, "express", "express",
                  ts_code=ts_code, limit=limit)


def fetch_fina_audit(engine: Any, ts_code: str, *, limit: int = 5) -> list[dict]:
    return _fetch(engine, ts_code, "fina_audit", "fina_audit",
                  ts_code=ts_code, limit=limit)


def fetch_anns(engine: Any, ts_code: str, *, start_date: str = "20230101", limit: int = 50) -> list[dict]:
    return _fetch(engine, ts_code, "anns_d", "anns_d",
                  ts_code=ts_code, start_date=start_date, limit=limit)


def fetch_research_report(engine: Any, ts_code: str, *, limit: int = 20) -> list[dict]:
    return _fetch(engine, ts_code, "research_report", "research_report",
                  ts_code=ts_code, limit=limit)


def fetch_irm_qa(engine: Any, ts_code: str, exchange: str, *, limit: int = 50) -> list[dict]:
    exch = (exchange or "").upper()
    if exch == "BSE":
        # 北交所互动易不支持
        log.debug("BSE stock %s: skipping IRM QA (not supported)", ts_code)
        return []
    if exch == "SSE":
        return _fetch(engine, ts_code, "irm_qa_sh", "irm_qa_sh",
                      ts_code=ts_code, limit=limit)
    # SZSE or unknown → use sz endpoint
    return _fetch(engine, ts_code, "irm_qa_sz", "irm_qa_sz",
                  ts_code=ts_code, limit=limit)


def fetch_top10_holders(engine: Any, ts_code: str, *, limit: int = 4) -> list[dict]:
    return _fetch(engine, ts_code, "top10_holders", "top10_holders",
                  ts_code=ts_code, limit=limit)


def fetch_top10_floatholders(engine: Any, ts_code: str, *, limit: int = 4) -> list[dict]:
    return _fetch(engine, ts_code, "top10_floatholders", "top10_floatholders",
                  ts_code=ts_code, limit=limit)


def fetch_stk_holdertrade(engine: Any, ts_code: str, *, limit: int = 20) -> list[dict]:
    return _fetch(engine, ts_code, "stk_holdertrade", "stk_holdertrade",
                  ts_code=ts_code, limit=limit)


def fetch_pledge_stat(engine: Any, ts_code: str) -> list[dict]:
    return _fetch(engine, ts_code, "pledge_stat", "pledge_stat",
                  ts_code=ts_code)


def fetch_share_float(engine: Any, ts_code: str, *, limit: int = 10) -> list[dict]:
    return _fetch(engine, ts_code, "share_float", "share_float",
                  ts_code=ts_code, limit=limit)


def fetch_stk_managers(engine: Any, ts_code: str) -> list[dict]:
    return _fetch(engine, ts_code, "stk_managers", "stk_managers",
                  ts_code=ts_code)


def fetch_stk_rewards(engine: Any, ts_code: str, *, limit: int = 10) -> list[dict]:
    return _fetch(engine, ts_code, "stk_rewards", "stk_rewards",
                  ts_code=ts_code, limit=limit)


def fetch_block_trade(engine: Any, ts_code: str, *, start_date: str = "20240101", limit: int = 30) -> list[dict]:
    return _fetch(engine, ts_code, "block_trade", "block_trade",
                  ts_code=ts_code, start_date=start_date, limit=limit)


def fetch_disclosure_date(engine: Any, ts_code: str, *, end_date: str | None = None) -> list[dict]:
    kwargs: dict = {"ts_code": ts_code}
    if end_date:
        kwargs["end_date"] = end_date
    return _fetch(engine, ts_code, "disclosure_date", "disclosure_date", **kwargs)


def fetch_cyq_perf(engine: Any, ts_code: str, *, limit: int = 30) -> list[dict]:
    return _fetch(engine, ts_code, "cyq_perf", "cyq_perf",
                  ts_code=ts_code, limit=limit)


def fetch_all(engine: Any, ts_code: str, exchange: str, *, verbose: bool = False) -> dict[str, list[dict]]:
    """Pull all 23 APIs for a single stock. Returns dict keyed by api_name."""
    apis = [
        ("stock_basic", lambda: fetch_stock_basic(engine, ts_code)),
        ("stock_company", lambda: fetch_stock_company(engine, ts_code)),
        ("income", lambda: fetch_income(engine, ts_code)),
        ("balancesheet", lambda: fetch_balancesheet(engine, ts_code)),
        ("cashflow", lambda: fetch_cashflow(engine, ts_code)),
        ("fina_indicator", lambda: fetch_fina_indicator(engine, ts_code)),
        ("forecast", lambda: fetch_forecast(engine, ts_code)),
        ("express", lambda: fetch_express(engine, ts_code)),
        ("fina_audit", lambda: fetch_fina_audit(engine, ts_code)),
        ("anns_d", lambda: fetch_anns(engine, ts_code)),
        ("research_report", lambda: fetch_research_report(engine, ts_code)),
        ("irm_qa", lambda: fetch_irm_qa(engine, ts_code, exchange)),
        ("top10_holders", lambda: fetch_top10_holders(engine, ts_code)),
        ("top10_floatholders", lambda: fetch_top10_floatholders(engine, ts_code)),
        ("stk_holdertrade", lambda: fetch_stk_holdertrade(engine, ts_code)),
        ("pledge_stat", lambda: fetch_pledge_stat(engine, ts_code)),
        ("share_float", lambda: fetch_share_float(engine, ts_code)),
        ("stk_managers", lambda: fetch_stk_managers(engine, ts_code)),
        ("stk_rewards", lambda: fetch_stk_rewards(engine, ts_code)),
        ("block_trade", lambda: fetch_block_trade(engine, ts_code)),
        ("disclosure_date", lambda: fetch_disclosure_date(engine, ts_code)),
        ("cyq_perf", lambda: fetch_cyq_perf(engine, ts_code)),
    ]
    results: dict[str, list[dict]] = {}
    for name, fn in apis:
        t0 = time.perf_counter()
        try:
            results[name] = fn()
            if verbose:
                log.info("  %-25s %3d rows  %.2fs", name, len(results[name]), time.perf_counter() - t0)
        except Exception as exc:
            log.warning("fetch_all: %s / %s failed: %s", ts_code, name, exc)
            results[name] = []
    return results
