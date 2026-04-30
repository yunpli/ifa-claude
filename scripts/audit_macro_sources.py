"""Audit TuShare structured macro data + news keyword density.

Two purposes:
  1. Confirm which low-frequency macro indicators (M0/M1/M2, social financing,
     new RMB loans) are actually available as STRUCTURED TuShare endpoints.
     If any of them are, we should pull them directly and NOT route through
     text extraction.
  2. Estimate how dense M2/社融/新增贷款/policy keyword mentions are in
     `major_news` / `news` / `npr` over a recent window — to size the
     LLM call volume for the two pre-jobs.

Usage:
    uv run python scripts/audit_macro_sources.py [--lookback-days 30]

Read-only; touches no DB.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
from collections import Counter
from typing import Any

import tushare as ts
from rich.console import Console
from rich.table import Table

from ifa.config import get_settings

console = Console()


# ─── Indicators we want to capture (per PRD §2.1) ──────────────────────────
TEXT_KEYWORDS_MACRO = {
    "M0": [r"\bM0\b", r"\bM 0\b"],
    "M1": [r"\bM1\b", r"\bM 1\b"],
    "M2": [r"\bM2\b", r"\bM 2\b"],
    "社融": [r"社融", r"社会融资规模", r"社会融资"],
    "新增贷款": [r"新增人民币贷款", r"新增贷款"],
    "贷款余额": [r"人民币贷款余额", r"贷款余额"],
}

# Negative filters: false-positive guards so we don't tag PM2.5 / HBM2 as M2 etc.
NEGATIVE_FILTERS = [r"PM2\.5", r"PM\s*2\.5", r"HBM2", r"M2\.1", r"SQM\b"]

# Policy keywords (per PRD §2.2)
TEXT_KEYWORDS_POLICY = {
    "央行/货币": [r"中国人民银行", r"央行", r"货币政策", r"降准", r"降息", r"逆回购"],
    "财政": [r"财政部", r"财政政策", r"专项债", r"减税", r"国债"],
    "稳增长": [r"稳增长", r"刺激", r"扩大内需"],
    "新质生产力/科技": [r"新质生产力", r"科技自立", r"人工智能\+?", r"AI\+", r"半导体政策"],
    "地产/信用": [r"房地产", r"房贷", r"楼市", r"地产政策"],
    "资本市场": [r"证监会", r"资本市场", r"注册制", r"退市"],
    "监管": [r"银保监", r"金融监管", r"反垄断", r"国务院"],
}

# TuShare structured macro endpoints to probe.
#  (api_name, callable -> df-or-raises, narrative)
def _probes(pro: Any) -> list[tuple[str, Any, str]]:
    return [
        ("cn_gdp",        lambda: pro.cn_gdp(start_q="2024Q4"),                         "国内生产总值（季度）"),
        ("cn_cpi",        lambda: pro.cn_cpi(start_m="202501"),                         "居民消费价格指数 CPI"),
        ("cn_ppi",        lambda: pro.cn_ppi(start_m="202501"),                         "工业生产者价格指数 PPI"),
        ("cn_pmi",        lambda: pro.cn_pmi(start_m="202501"),                         "采购经理人指数 PMI"),
        ("cn_m",          lambda: pro.cn_m(start_m="202501"),                           "货币供应量 M0/M1/M2 (?)"),
        ("cn_sf",         lambda: pro.cn_sf(start_m="202501"),                          "社融数据 (?)"),
        ("sf_month",      lambda: pro.sf_month(start_m="202501"),                       "社融月度 (?)"),
        ("cn_l",          lambda: pro.cn_l(start_m="202501"),                           "贷款数据 (?)"),
        ("cn_newloan",    lambda: pro.cn_newloan(start_m="202501"),                     "新增贷款 (?)"),
        ("shibor",        lambda: pro.shibor(start_date="20260101", end_date="20260429"), "SHIBOR 上海银行间拆借利率"),
        ("shibor_lpr",    lambda: pro.shibor_lpr(start_date="20260101", end_date="20260429"), "LPR"),
        ("fx_daily",      lambda: pro.fx_daily(ts_code="USDCNH.FXCM",
                                              start_date="20260101", end_date="20260429"),  "USD/CNH 离岸人民币"),
    ]


# ─── Probes ────────────────────────────────────────────────────────────────

def probe_structured(pro: Any) -> None:
    table = Table(title="TuShare structured macro endpoints", show_lines=False)
    table.add_column("API", style="cyan")
    table.add_column("Status")
    table.add_column("Rows", justify="right")
    table.add_column("Latest period / first row sample", overflow="fold")
    table.add_column("Narrative", overflow="fold")

    for name, fn, narrative in _probes(pro):
        try:
            df = fn()
            rows = len(df)
            if rows == 0:
                status = "[yellow]ZERO[/yellow]"
                sample = "(0 rows)"
            else:
                status = "[green]OK[/green]"
                # try common period-like columns
                first = df.iloc[0]
                period_cols = [c for c in ("month", "quarter", "year", "date", "trade_date", "ts_code") if c in df.columns]
                if period_cols:
                    sample = ", ".join(f"{c}={first[c]}" for c in period_cols[:3])
                    extra = [f"{c}={first[c]}" for c in df.columns[:6] if c not in period_cols][:3]
                    if extra:
                        sample += " · " + ", ".join(extra)
                else:
                    sample = "; ".join(f"{c}={first[c]}" for c in df.columns[:4])
        except Exception as exc:  # noqa: BLE001
            status = "[red]FAIL[/red]"
            rows = 0
            msg = str(exc)
            sample = msg[:140] + ("…" if len(msg) > 140 else "")
        table.add_row(name, status, str(rows), str(sample), narrative)

    console.print(table)


def _has_negative(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in NEGATIVE_FILTERS)


def _count_hits(text: str, patterns: list[str]) -> int:
    if _has_negative(text):
        # negative filter only blocks the M-numeric keys; still allow social-financing
        # we'll let caller decide. Simpler: only suppress when text contains negative AND
        # no positive Chinese keyword.
        pass
    return sum(1 for p in patterns if re.search(p, text, re.IGNORECASE))


def probe_keyword_density(pro: Any, *, lookback_days: int) -> None:
    end = dt.datetime.now()
    start = end - dt.timedelta(days=lookback_days)
    s_str = start.strftime("%Y-%m-%d %H:%M:%S")
    e_str = end.strftime("%Y-%m-%d %H:%M:%S")

    sources_to_probe = [
        ("major_news", ["新华网", "财联社", "第一财经", "凤凰财经", "财新网", "华尔街见闻"]),
        ("news",       ["sina", "wallstreetcn", "10jqka", "eastmoney", "cls", "yicai"]),
    ]

    table = Table(
        title=f"News keyword density (last {lookback_days} days, {s_str} → {e_str})",
        show_lines=False,
    )
    table.add_column("Source", style="cyan")
    table.add_column("API.src")
    table.add_column("Rows", justify="right")
    macro_keys = list(TEXT_KEYWORDS_MACRO.keys())
    policy_keys = list(TEXT_KEYWORDS_POLICY.keys())
    for k in macro_keys:
        table.add_column(k, justify="right")
    for k in policy_keys[:4]:  # truncate column count
        table.add_column(k, justify="right")
    table.add_column("Other policy", justify="right")

    grand_macro: Counter[str] = Counter()
    grand_policy: Counter[str] = Counter()

    for api, srcs in sources_to_probe:
        for src in srcs:
            try:
                fn = getattr(pro, api)
                df = fn(src=src, start_date=s_str, end_date=e_str)
            except Exception as exc:  # noqa: BLE001
                table.add_row(api, src, "ERR", *(["—"] * (len(macro_keys) + 5)),
                              str(exc)[:60])
                continue
            rows = len(df)
            if rows == 0:
                table.add_row(api, src, "0", *(["—"] * (len(macro_keys) + 5)))
                continue
            # build a single text column = title + content (handle missing cols)
            import pandas as pd
            title_s = df["title"] if "title" in df.columns else pd.Series([""] * rows)
            content_s = df["content"] if "content" in df.columns else pd.Series([""] * rows)
            blob = (title_s.fillna("").astype(str) + "\n" + content_s.fillna("").astype(str))
            macro_hits: dict[str, int] = {}
            for k, pats in TEXT_KEYWORDS_MACRO.items():
                ct = sum(1 for t in blob if any(re.search(p, t, re.IGNORECASE) for p in pats)
                                              and not _has_negative(t))
                # Special handling: 社融/贷款 don't suffer from PM2.5 collisions, allow without negative check
                if k in {"社融", "新增贷款", "贷款余额"}:
                    ct = sum(1 for t in blob if any(re.search(p, t, re.IGNORECASE) for p in pats))
                macro_hits[k] = ct
                grand_macro[k] += ct
            policy_hits: dict[str, int] = {}
            for k, pats in TEXT_KEYWORDS_POLICY.items():
                ct = sum(1 for t in blob if any(re.search(p, t, re.IGNORECASE) for p in pats))
                policy_hits[k] = ct
                grand_policy[k] += ct
            other_policy = sum(v for k, v in policy_hits.items() if k not in policy_keys[:4])
            row = [api, src, str(rows)]
            row += [str(macro_hits[k]) for k in macro_keys]
            row += [str(policy_hits[k]) for k in policy_keys[:4]]
            row += [str(other_policy)]
            table.add_row(*row)

    console.print(table)
    console.print("\n[bold]Aggregate macro keyword totals (across all probed sources):[/bold]")
    for k, v in grand_macro.most_common():
        console.print(f"  {k}: {v}")
    console.print("\n[bold]Aggregate policy keyword totals:[/bold]")
    for k, v in grand_policy.most_common():
        console.print(f"  {k}: {v}")


def probe_npr(pro: Any, *, lookback_days: int) -> None:
    """npr is the official policy/regulation source."""
    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    try:
        df = pro.npr(start_date=start.strftime("%Y%m%d"), end_date=end.strftime("%Y%m%d"))
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]npr probe failed:[/red] {exc}")
        return
    if df is None or len(df) == 0:
        console.print(f"[yellow]npr returned 0 rows for {lookback_days}-day window[/yellow]")
        return
    table = Table(title=f"npr (政策法规) — last {lookback_days} days", show_lines=False)
    table.add_column("Total rows", justify="right")
    table.add_column("By type (top 8)", overflow="fold")
    table.add_column("By organisation (top 6)", overflow="fold")
    by_type = df["ptype"].value_counts().head(8) if "ptype" in df.columns else None
    by_org  = df["puborg"].value_counts().head(6) if "puborg" in df.columns else None
    table.add_row(
        str(len(df)),
        "; ".join(f"{k}={v}" for k, v in by_type.items()) if by_type is not None else "—",
        "; ".join(f"{k}={v}" for k, v in by_org.items()) if by_org is not None else "—",
    )
    console.print(table)
    # Sample 3 latest titles
    if "title" in df.columns:
        console.print("[bold]npr latest 3 titles:[/bold]")
        for t in df.sort_values("pubtime", ascending=False).head(3)["title"]:
            console.print(f"  · {t}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lookback-days", type=int, default=30)
    args = parser.parse_args()

    settings = get_settings()
    ts.set_token(settings.tushare_token.get_secret_value())
    pro = ts.pro_api()

    console.rule("[bold]Phase 1 — TuShare structured macro endpoints[/bold]")
    probe_structured(pro)

    console.rule(f"[bold]Phase 2 — news keyword density (last {args.lookback_days}d)[/bold]")
    probe_keyword_density(pro, lookback_days=args.lookback_days)

    console.rule(f"[bold]Phase 3 — policy/regulation source (npr)[/bold]")
    probe_npr(pro, lookback_days=args.lookback_days)


if __name__ == "__main__":
    main()
