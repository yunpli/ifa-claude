"""TuShare news source fetchers with windowed pagination.

`major_news` and `news` cap each call's row count, so a 90-day initial scan
must be chunked. We iterate week-by-week per (api, src) pair, dedup by url, and
return a tidy DataFrame the filter stage can consume.
"""
from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass

import pandas as pd

from ifa.core.tushare import TuShareClient


@dataclass(frozen=True)
class NewsSource:
    """One fetchable news source.

    `api` is the TuShare endpoint name; `src` is the source filter the API
    accepts (e.g. `'新华网'` for major_news, `'cls'` for news). For npr there
    is no per-source filter, so `src` is empty string.
    """

    api: str           # 'major_news' | 'news' | 'npr'
    src: str           # source filter ('' for npr)

    @property
    def label(self) -> str:
        return f"{self.api}.{self.src}" if self.src else self.api


# Default source bundles by job role.
# Order matters: higher-priority sources first, so the filter stage can
# preserve provenance ranking when dedup is applied later.
SOURCES_FOR_TEXT_CAPTURE: list[NewsSource] = [
    NewsSource("major_news", "新华网"),
    NewsSource("major_news", "财联社"),
    NewsSource("major_news", "第一财经"),
    NewsSource("major_news", "凤凰财经"),
    NewsSource("major_news", "财新网"),
    NewsSource("major_news", "华尔街见闻"),
    NewsSource("news",       "cls"),
    NewsSource("news",       "yicai"),
    NewsSource("news",       "wallstreetcn"),
    NewsSource("news",       "10jqka"),
    NewsSource("news",       "eastmoney"),
    NewsSource("news",       "sina"),
]

SOURCES_FOR_POLICY_MEMORY: list[NewsSource] = [
    NewsSource("npr", ""),  # official policy/regulation corpus — highest authority
    *SOURCES_FOR_TEXT_CAPTURE,
]


def _fmt_news_dt(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def _fmt_npr_date(d: dt.date) -> str:
    return d.strftime("%Y%m%d")


_BEIJING_OFFSET = dt.timedelta(hours=8)


def _to_beijing_naive(d: dt.datetime) -> dt.datetime:
    """Convert any datetime to Beijing wall-clock, naive."""
    if d.tzinfo is not None:
        d = d.astimezone(dt.timezone(_BEIJING_OFFSET))
    return d.replace(tzinfo=None)


def fetch_window(
    client: TuShareClient,
    source: NewsSource,
    *,
    start: dt.datetime,
    end: dt.datetime,
    chunk_days: int = 7,
    sleep_between: float = 0.0,
) -> pd.DataFrame:
    """Fetch one source's rows over [start, end), chunking by chunk_days.

    Returns a DataFrame with at minimum the columns `datetime, title, content,
    url, src`. Empty DataFrame if the source returned nothing.
    """
    chunks: list[pd.DataFrame] = []
    errors: list[str] = []
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + dt.timedelta(days=chunk_days), end)
        try:
            if source.api == "npr":
                df = client.call(
                    "npr",
                    start_date=_fmt_npr_date(cursor.date()),
                    end_date=_fmt_npr_date(chunk_end.date()),
                )
                df = _normalize_npr(df)
            else:
                # TuShare news APIs expect Beijing-time wall-clock strings, not UTC.
                # We pass naive Beijing-time strings; TuShare returns rows whose
                # `datetime` column is also Beijing-time wall-clock.
                start_local = _to_beijing_naive(cursor)
                end_local = _to_beijing_naive(chunk_end)
                df = client.call(
                    source.api,
                    src=source.src,
                    start_date=_fmt_news_dt(start_local),
                    end_date=_fmt_news_dt(end_local),
                )
                df = _normalize_news(df, source)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{source.label}@{cursor.isoformat()}: {type(exc).__name__}: {exc}")
            df = pd.DataFrame()
        if not df.empty:
            chunks.append(df)
        cursor = chunk_end
        if sleep_between:
            time.sleep(sleep_between)
    if errors and not chunks:
        # Surface the first error so the runner can log it instead of silently returning 0
        raise RuntimeError("; ".join(errors[:3]))

    if not chunks:
        return pd.DataFrame(columns=["publish_time", "title", "content", "url", "src", "source_label"])
    out = pd.concat(chunks, ignore_index=True)
    # Dedup: prefer URL when most rows have a real URL; otherwise (cls / sina / npr
    # often omit URL) dedup on (title, publish_time).
    has_real_url = "url" in out.columns and (out["url"].astype(str).str.len() > 0).mean() > 0.5
    if has_real_url:
        out = out.drop_duplicates(subset=["url"], keep="first")
    else:
        out = out.drop_duplicates(subset=["title", "publish_time"], keep="first")
    return out.reset_index(drop=True)


def _col(df: pd.DataFrame, name: str, default: str = "") -> pd.Series:
    """Always return a string Series, defaulting missing columns to `default`."""
    if name in df.columns:
        return df[name].fillna(default).astype(str)
    return pd.Series([default] * len(df), index=df.index, dtype=str)


def _normalize_news(df: pd.DataFrame, source: NewsSource) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    out["publish_time"] = pd.to_datetime(df["datetime"], errors="coerce") if "datetime" in df.columns else pd.NaT
    out["title"] = _col(df, "title")
    out["content"] = _col(df, "content")
    out["url"] = _col(df, "url")
    out["src"] = _col(df, "src", source.src)
    out["source_label"] = source.label
    return out


def _normalize_npr(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = pd.DataFrame(index=df.index)
    out["publish_time"] = pd.to_datetime(df["pubtime"], errors="coerce") if "pubtime" in df.columns else pd.NaT
    out["title"] = _col(df, "title")
    # Strip HTML tags from policy content_html
    raw = _col(df, "content_html")
    out["content"] = raw.str.replace(r"<[^>]+>", " ", regex=True).str.replace(r"\s+", " ", regex=True).str.strip()
    out["url"] = _col(df, "url")
    out["src"] = _col(df, "puborg")
    out["source_label"] = "npr"
    return out
