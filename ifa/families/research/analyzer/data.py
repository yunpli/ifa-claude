"""Data loader — single boundary that converts raw api_cache rows into
    typed CompanyFinancialSnapshot with all values in base units (元 / 股 / 0-100 pct).

This is THE conversion boundary for the Research family. After this layer,
every downstream consumer (factors, sections, scoring) only sees base units.
Field naming convention: `_yuan` / `_share` / `_pct` suffixes (data-accuracy
guidelines Rule 1 / 2).

Reuses:
  · `macro/data.TimeSeries` for multi-period series (revenue, n_income, ROE, GPM …)
  · `asset/data.CommoditySnapshot` pattern for snapshot+history+data_status

Inputs:
  · engine — PostgreSQL engine (used to read api_cache, sw_member_monthly)
  · ts_code — canonical with suffix
  · CompanyRef — from resolver (provides exchange, sw_l1/l2)

Outputs:
  · CompanyFinancialSnapshot — bundle consumed by every factor module + section
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.units import (
    MoneyUnit,
    RatioUnit,
    ShareUnit,
    normalize_tushare_value,
    to_pct_0_100,
)
from ifa.families.macro.data import TimeSeries
from ifa.families.research.fetcher.cache import cache_get
from ifa.families.research.resolver import CompanyRef

log = logging.getLogger(__name__)


# ─── Snapshot dataclass ───────────────────────────────────────────────────────

@dataclass
class CompanyFinancialSnapshot:
    """Single-shot bundle of everything a factor or section needs.

    All money values are in 元 (Decimal). All percentages in 0-100. All dates ISO.
    Missing data is None — never 0 or NaN (Rule 5).
    """
    company: CompanyRef
    data_cutoff_date: date

    # Identity / classification
    sw_l1_code: str | None = None
    sw_l1_name: str | None = None
    sw_l2_code: str | None = None
    sw_l2_name: str | None = None
    list_date: date | None = None
    industry: str | None = None
    main_business: str | None = None
    introduction: str | None = None
    employees: int | None = None
    reg_capital_yuan: Decimal | None = None

    # ── Latest single-period values (most recent published) ───────────────
    latest_period: str | None = None             # '20240930' etc.

    # Income statement (元)
    revenue_yuan: Decimal | None = None
    oper_cost_yuan: Decimal | None = None
    n_income_yuan: Decimal | None = None
    profit_dedt_yuan: Decimal | None = None       # 扣非净利

    # Balance sheet (元)
    total_assets_yuan: Decimal | None = None
    total_liab_yuan: Decimal | None = None
    money_cap_yuan: Decimal | None = None
    goodwill_yuan: Decimal | None = None
    total_cur_assets_yuan: Decimal | None = None
    total_cur_liab_yuan: Decimal | None = None
    inventories_yuan: Decimal | None = None
    accounts_receiv_yuan: Decimal | None = None
    total_share: Decimal | None = None
    total_hldr_eqy_inc_min_int_yuan: Decimal | None = None  # 含少数股东权益

    # Cashflow (元)
    n_cashflow_act_yuan: Decimal | None = None       # CFO
    c_pay_acq_const_fiolta_yuan: Decimal | None = None  # 资本支出（构建固定资产等）

    # Indicators (already 0-100 pct from Tushare)
    roe_pct: Decimal | None = None
    eps_yuan: Decimal | None = None        # 元/股
    gross_margin_pct: Decimal | None = None
    debt_to_assets_pct: Decimal | None = None
    current_ratio: Decimal | None = None
    quick_ratio: Decimal | None = None

    # ── Multi-period series (for trends, sparklines) ───────────────────────
    revenue_series: TimeSeries | None = None
    n_income_series: TimeSeries | None = None
    profit_dedt_series: TimeSeries | None = None
    cfo_series: TimeSeries | None = None
    roe_series: TimeSeries | None = None
    gpm_series: TimeSeries | None = None
    npm_series: TimeSeries | None = None

    # ── Forecasts (业绩预告，元/股 pct mixed; 元 normalized) ────────────────
    forecasts: list[dict] = field(default_factory=list)
    expresses: list[dict] = field(default_factory=list)
    forecast_achievement_pct: Decimal | None = None  # 实际/预告中值 × 100

    # ── Audit / Governance ─────────────────────────────────────────────────
    audit_records: list[dict] = field(default_factory=list)
    holdertrades: list[dict] = field(default_factory=list)
    pledge_stat: list[dict] = field(default_factory=list)
    managers: list[dict] = field(default_factory=list)
    rewards: list[dict] = field(default_factory=list)
    disclosure_dates: list[dict] = field(default_factory=list)

    # ── Holders ────────────────────────────────────────────────────────────
    top10_holders: list[dict] = field(default_factory=list)
    top10_floatholders: list[dict] = field(default_factory=list)

    # ── Disclosures / events / IRM ─────────────────────────────────────────
    announcements: list[dict] = field(default_factory=list)
    research_reports: list[dict] = field(default_factory=list)
    irm_qa: list[dict] = field(default_factory=list)

    # ── Trades ─────────────────────────────────────────────────────────────
    block_trades: list[dict] = field(default_factory=list)

    # ── Data quality ──────────────────────────────────────────────────────
    data_status: dict[str, str] = field(default_factory=dict)   # api_name → 'ok' | 'empty' | 'missing'
    missing_apis: list[str] = field(default_factory=list)


# ─── Loader ───────────────────────────────────────────────────────────────────

# Field-level api unit registry shortcut: use TUSHARE_UNITS via normalize_tushare_value
def _norm(api: str, field_name: str, raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        return normalize_tushare_value(api, field_name, raw)
    except KeyError:
        # Field not registered → fall back to Decimal(str(raw)) (treat as base unit)
        try:
            return Decimal(str(raw))
        except Exception:
            return None


def _safe_dec(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        if isinstance(raw, str) and not raw.strip():
            return None
        return Decimal(str(raw))
    except Exception:
        return None


def _safe_int(raw: Any) -> int | None:
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def _read_all_cache(engine: Engine, ts_code: str) -> dict[str, list[dict]]:
    """Read all api_cache rows for a stock (any params_hash, latest per api)."""
    sql = text("""
        SELECT api_name, response_json, fetched_at
        FROM research.api_cache
        WHERE ts_code = :tc
        ORDER BY api_name, fetched_at DESC
    """)
    seen: set[str] = set()
    out: dict[str, list[dict]] = {}
    with engine.connect() as conn:
        for api_name, response_json, _ in conn.execute(sql, {"tc": ts_code}):
            if api_name in seen:
                continue
            seen.add(api_name)
            out[api_name] = response_json or []
    return out


def _build_series(
    rows: list[dict], value_field: str, name: str, unit: str, *,
    api_name: str | None = None, last_n: int = 8,
    period_field: str = "end_date",
) -> TimeSeries:
    """Build TimeSeries from list of period rows. Computes YoY against shift(4)."""
    # Sort by period ascending
    valid = [r for r in rows if r.get(period_field) and r.get(value_field) is not None]
    valid.sort(key=lambda r: r[period_field])
    valid = valid[-last_n:] if last_n else valid

    periods = [str(r[period_field]) for r in valid]
    values: list[float | None] = []
    for r in valid:
        raw = r.get(value_field)
        if api_name:
            v = _norm(api_name, value_field, raw)
        else:
            v = _safe_dec(raw)
        values.append(float(v) if v is not None else None)

    yoy: list[float | None] = []
    mom: list[float | None] = []
    for i, v in enumerate(values):
        if v is None or i < 4 or values[i - 4] in (None, 0):
            yoy.append(None)
        else:
            base = values[i - 4]
            yoy.append((v - base) / abs(base) * 100)
        # mom (QoQ here) — i-1
        if v is None or i < 1 or values[i - 1] in (None, 0):
            mom.append(None)
        else:
            base = values[i - 1]
            mom.append((v - base) / abs(base) * 100)

    ts = TimeSeries(name=name, periods=periods, values=values,
                    yoy_values=yoy, mom_values=mom, unit=unit)
    if periods:
        ts.latest_period = periods[-1]
        ts.latest_value = values[-1]
        ts.latest_yoy = yoy[-1]
        ts.latest_mom = mom[-1]
    return ts


def load_company_snapshot(
    engine: Engine,
    company: CompanyRef,
    *,
    data_cutoff_date: date,
) -> CompanyFinancialSnapshot:
    """Build snapshot from cached api responses. Caller must have already populated cache."""
    cache = _read_all_cache(engine, company.ts_code)

    snap = CompanyFinancialSnapshot(company=company, data_cutoff_date=data_cutoff_date)

    # Track what's available
    expected = [
        "stock_basic", "stock_company", "income", "balancesheet", "cashflow",
        "fina_indicator", "forecast", "express", "fina_audit", "anns_d",
        "research_report", "irm_qa", "top10_holders", "top10_floatholders",
        "stk_holdertrade", "pledge_stat", "share_float", "stk_managers",
        "stk_rewards", "block_trade", "disclosure_date", "cyq_perf",
    ]
    for api in expected:
        rows = cache.get(api, [])
        snap.data_status[api] = "ok" if rows else "empty"
        if not rows:
            snap.missing_apis.append(api)

    # ── Identity ──────────────────────────────────────────────────────────
    if (sb := cache.get("stock_basic")) and sb:
        first = sb[0]
        snap.industry = first.get("industry")
        ld = first.get("list_date")
        if ld:
            try:
                s = str(ld)
                snap.list_date = date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            except Exception:
                pass

    if (sc := cache.get("stock_company")) and sc:
        first = sc[0]
        snap.introduction = first.get("introduction")
        snap.main_business = first.get("main_business")
        snap.employees = _safe_int(first.get("employees"))
        snap.reg_capital_yuan = _norm("stock_company", "reg_capital", first.get("reg_capital"))

    # SW L1/L2 from existing smartmoney.sw_member_monthly (PIT)
    snap.sw_l1_code, snap.sw_l1_name, snap.sw_l2_code, snap.sw_l2_name = (
        _lookup_sw_membership(engine, company.ts_code, data_cutoff_date)
    )

    # ── Income statement: latest + series ─────────────────────────────────
    if income_rows := cache.get("income"):
        sorted_rows = sorted(income_rows, key=lambda r: r.get("end_date") or "", reverse=True)
        if sorted_rows:
            latest = sorted_rows[0]
            snap.latest_period = str(latest.get("end_date") or "")
            snap.revenue_yuan = _norm("income", "total_revenue", latest.get("total_revenue"))
            snap.oper_cost_yuan = _norm("income", "oper_cost", latest.get("oper_cost"))
            snap.n_income_yuan = _norm("income", "n_income", latest.get("n_income"))
            snap.profit_dedt_yuan = _norm("income", "profit_dedt", latest.get("profit_dedt"))

        snap.revenue_series = _build_series(
            income_rows, "total_revenue", "营收", "元", api_name="income",
        )
        snap.n_income_series = _build_series(
            income_rows, "n_income", "净利润", "元", api_name="income",
        )
        snap.profit_dedt_series = _build_series(
            income_rows, "profit_dedt", "扣非净利", "元", api_name="income",
        )

    # ── Balance sheet ─────────────────────────────────────────────────────
    if bs_rows := cache.get("balancesheet"):
        sorted_rows = sorted(bs_rows, key=lambda r: r.get("end_date") or "", reverse=True)
        if sorted_rows:
            latest = sorted_rows[0]
            snap.total_assets_yuan = _norm("balancesheet", "total_assets", latest.get("total_assets"))
            snap.total_liab_yuan = _norm("balancesheet", "total_liab", latest.get("total_liab"))
            snap.money_cap_yuan = _norm("balancesheet", "money_cap", latest.get("money_cap"))
            # goodwill 单位与 income 一致（元）— 未在 TUSHARE_UNITS 显式注册，sticky default to 元
            snap.goodwill_yuan = _safe_dec(latest.get("goodwill"))
            snap.total_cur_assets_yuan = _safe_dec(latest.get("total_cur_assets"))
            snap.total_cur_liab_yuan = _safe_dec(latest.get("total_cur_liab"))
            snap.inventories_yuan = _safe_dec(latest.get("inventories"))
            snap.accounts_receiv_yuan = _safe_dec(latest.get("accounts_receiv"))
            snap.total_share = _safe_dec(latest.get("total_share"))
            snap.total_hldr_eqy_inc_min_int_yuan = _safe_dec(latest.get("total_hldr_eqy_inc_min_int"))

    # ── Cashflow ──────────────────────────────────────────────────────────
    if cf_rows := cache.get("cashflow"):
        sorted_rows = sorted(cf_rows, key=lambda r: r.get("end_date") or "", reverse=True)
        if sorted_rows:
            latest = sorted_rows[0]
            snap.n_cashflow_act_yuan = _norm("cashflow", "n_cashflow_act", latest.get("n_cashflow_act"))
            snap.c_pay_acq_const_fiolta_yuan = _safe_dec(latest.get("c_pay_acq_const_fiolta"))
        snap.cfo_series = _build_series(
            cf_rows, "n_cashflow_act", "经营现金流", "元", api_name="cashflow",
        )

    # ── Fina indicators (already 0-100) ───────────────────────────────────
    if fi_rows := cache.get("fina_indicator"):
        sorted_rows = sorted(fi_rows, key=lambda r: r.get("end_date") or "", reverse=True)
        if sorted_rows:
            latest = sorted_rows[0]
            snap.roe_pct = _safe_dec(latest.get("roe"))
            snap.eps_yuan = _safe_dec(latest.get("eps"))
            snap.gross_margin_pct = _safe_dec(latest.get("grossprofit_margin"))
            snap.debt_to_assets_pct = _safe_dec(latest.get("debt_to_assets"))
            snap.current_ratio = _safe_dec(latest.get("current_ratio"))
            snap.quick_ratio = _safe_dec(latest.get("quick_ratio"))

        snap.roe_series = _build_series(fi_rows, "roe", "ROE", "%")
        snap.gpm_series = _build_series(fi_rows, "grossprofit_margin", "毛利率", "%")
        snap.npm_series = _build_series(fi_rows, "netprofit_margin", "净利率", "%")

    # ── Forecasts / express ────────────────────────────────────────────────
    snap.forecasts = cache.get("forecast", [])
    snap.expresses = cache.get("express", [])
    snap.forecast_achievement_pct = _compute_forecast_achievement(
        snap.forecasts, snap.expresses, cache.get("income", []),
    )

    # ── Governance / events ────────────────────────────────────────────────
    snap.audit_records = cache.get("fina_audit", [])
    snap.holdertrades = cache.get("stk_holdertrade", [])
    snap.pledge_stat = cache.get("pledge_stat", [])
    snap.managers = cache.get("stk_managers", [])
    snap.rewards = cache.get("stk_rewards", [])
    snap.disclosure_dates = cache.get("disclosure_date", [])

    # ── Holders / disclosures / events ─────────────────────────────────────
    snap.top10_holders = cache.get("top10_holders", [])
    snap.top10_floatholders = cache.get("top10_floatholders", [])
    snap.announcements = cache.get("anns_d", [])
    snap.research_reports = cache.get("research_report", [])
    snap.irm_qa = cache.get("irm_qa", [])
    snap.block_trades = cache.get("block_trade", [])

    return snap


def _lookup_sw_membership(
    engine: Engine, ts_code: str, on_date: date,
) -> tuple[str | None, str | None, str | None, str | None]:
    """PIT lookup of SW L1/L2 from existing smartmoney.sw_member_monthly."""
    snapshot_month = on_date.replace(day=1)
    sql = text("""
        SELECT l1_code, l1_name, l2_code, l2_name
        FROM smartmoney.sw_member_monthly
        WHERE ts_code = :tc AND snapshot_month = :sm
        LIMIT 1
    """)
    with engine.connect() as conn:
        row = conn.execute(sql, {"tc": ts_code, "sm": snapshot_month}).fetchone()
    if row:
        return tuple(row)
    return (None, None, None, None)


def _compute_forecast_achievement(
    forecasts: list[dict],
    expresses: list[dict],
    income_rows: list[dict],
) -> Decimal | None:
    """For the most recent forecast period, compute (actual / forecast_mid) × 100.

    actual prefers express (revenue/n_income filed before formal report),
    falls back to income.n_income for the same end_date.
    """
    if not forecasts:
        return None
    # Most recent forecast by end_date
    sorted_fcs = sorted(forecasts, key=lambda r: r.get("end_date") or "", reverse=True)
    fc = sorted_fcs[0]
    period = fc.get("end_date")
    if not period:
        return None

    fc_min = _norm("forecast", "net_profit_min", fc.get("net_profit_min"))
    fc_max = _norm("forecast", "net_profit_max", fc.get("net_profit_max"))
    if fc_min is None and fc_max is None:
        return None
    fc_mid = None
    if fc_min is not None and fc_max is not None:
        fc_mid = (fc_min + fc_max) / 2
    elif fc_min is not None:
        fc_mid = fc_min
    elif fc_max is not None:
        fc_mid = fc_max
    if fc_mid is None or fc_mid == 0:
        return None

    actual = None
    for ex in expresses:
        if ex.get("end_date") == period and ex.get("n_income") is not None:
            actual = _norm("express", "n_income", ex.get("n_income"))
            break
    if actual is None:
        for inc in income_rows:
            if inc.get("end_date") == period and inc.get("n_income") is not None:
                actual = _norm("income", "n_income", inc.get("n_income"))
                break
    if actual is None:
        return None

    return Decimal(actual) / Decimal(fc_mid) * 100
