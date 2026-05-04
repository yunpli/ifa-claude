"""Unit conversion and formatting — single source of truth for monetary,
share, percentage, and price quantities across all iFA families.

Why this module exists
----------------------
Tushare Pro returns the same conceptual quantity in different units across
endpoints — `income.total_revenue` is 元, `forecast.net_profit_min` is 万元,
`daily.amount` is 千元, `stk_factor_pro.total_mv` is 万元. Mixing these has
caused production bugs in V2.0/V2.1 (e.g. "营收显示成 40 万亿" was a 10000×
unit-conversion mistake). This module forces every fetcher to declare the
source unit explicitly so the conversion is auditable.

Convention
----------
Database storage is always in BASE units:
    · Money: 元 (yuan)
    · Share count: 股 (shares)
    · Ratio / pct_change: 0-100 percent (NOT 0-1 decimal)
    · Price: 元/股
    · Time: TIMESTAMPTZ UTC; business date is BJT DATE

Conversion happens at TWO boundaries only:
    1. Fetcher in: `to_base()` — convert Tushare native unit → base unit
    2. Renderer out: `fmt_*()` — convert base unit → human-readable string

NEVER do unit math in middle layers. NEVER assume a number is in a particular
unit without checking against `TushareUnit`.

See `docs/tushare-units-reference.md` for the empirical unit table.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Final


# ─── Tushare native units ───────────────────────────────────────────────────

class MoneyUnit(str, Enum):
    """Native monetary units returned by Tushare endpoints."""

    YUAN = "yuan"            # 元
    QIAN_YUAN = "qian_yuan"  # 千元
    WAN_YUAN = "wan_yuan"    # 万元
    BAI_WAN_YUAN = "bai_wan_yuan"  # 百万元 (rarely used; kept for completeness)
    YI_YUAN = "yi_yuan"      # 亿元


class ShareUnit(str, Enum):
    """Native share-count units."""

    SHARE = "share"          # 股
    SHOU = "shou"            # 手 (1 手 = 100 股)
    WAN_SHARE = "wan_share"  # 万股


class RatioUnit(str, Enum):
    """Native ratio / percentage units."""

    PCT_0_100 = "pct_0_100"  # 0-100 percentage (e.g. 15.56 means 15.56%)
    DECIMAL_0_1 = "decimal_0_1"  # 0-1 decimal (e.g. 0.1556 means 15.56%)


# ─── Conversion factors (to base units) ─────────────────────────────────────

_MONEY_TO_YUAN: Final[dict[MoneyUnit, Decimal]] = {
    MoneyUnit.YUAN: Decimal(1),
    MoneyUnit.QIAN_YUAN: Decimal(1_000),
    MoneyUnit.WAN_YUAN: Decimal(10_000),
    MoneyUnit.BAI_WAN_YUAN: Decimal(1_000_000),
    MoneyUnit.YI_YUAN: Decimal(100_000_000),
}

_SHARE_TO_SHARE: Final[dict[ShareUnit, Decimal]] = {
    ShareUnit.SHARE: Decimal(1),
    ShareUnit.SHOU: Decimal(100),
    ShareUnit.WAN_SHARE: Decimal(10_000),
}


# ─── Conversion functions ───────────────────────────────────────────────────

def to_yuan(value: float | int | Decimal | None, unit: MoneyUnit) -> Decimal | None:
    """Convert a Tushare-native monetary value to 元 (BASE).

    Use this at the fetcher boundary, ONCE per value, and store the result.
    Never call this in middle layers.
    """
    if value is None:
        return None
    factor = _MONEY_TO_YUAN[unit]
    return Decimal(str(value)) * factor


def to_share(value: float | int | Decimal | None, unit: ShareUnit) -> Decimal | None:
    """Convert a Tushare-native share count to 股 (BASE)."""
    if value is None:
        return None
    factor = _SHARE_TO_SHARE[unit]
    return Decimal(str(value)) * factor


def to_pct_0_100(value: float | int | Decimal | None, unit: RatioUnit) -> Decimal | None:
    """Convert a Tushare-native ratio to 0-100 percentage (BASE)."""
    if value is None:
        return None
    v = Decimal(str(value))
    if unit == RatioUnit.PCT_0_100:
        return v
    if unit == RatioUnit.DECIMAL_0_1:
        return v * 100
    raise ValueError(f"Unknown ratio unit: {unit}")


# ─── Per-Tushare-endpoint unit declarations ─────────────────────────────────

@dataclass(frozen=True)
class TushareFieldUnit:
    """Authoritative unit for a specific Tushare interface field.

    Maintained in sync with docs/tushare-units-reference.md. Fetcher code
    SHOULD reference these constants rather than hard-coding scale factors.
    """

    api: str
    field: str
    unit: MoneyUnit | ShareUnit | RatioUnit


# Common ones — extend as new endpoints are integrated.
# Cross-reference: docs/tushare-units-reference.md
TUSHARE_UNITS: Final[dict[str, TushareFieldUnit]] = {
    # income / balancesheet / cashflow — 元
    "income.total_revenue":     TushareFieldUnit("income", "total_revenue", MoneyUnit.YUAN),
    "income.n_income":          TushareFieldUnit("income", "n_income", MoneyUnit.YUAN),
    "income.profit_dedt":       TushareFieldUnit("income", "profit_dedt", MoneyUnit.YUAN),
    "income.oper_cost":         TushareFieldUnit("income", "oper_cost", MoneyUnit.YUAN),
    "balancesheet.total_assets": TushareFieldUnit("balancesheet", "total_assets", MoneyUnit.YUAN),
    "balancesheet.total_liab":  TushareFieldUnit("balancesheet", "total_liab", MoneyUnit.YUAN),
    "balancesheet.money_cap":   TushareFieldUnit("balancesheet", "money_cap", MoneyUnit.YUAN),
    "cashflow.n_cashflow_act":  TushareFieldUnit("cashflow", "n_cashflow_act", MoneyUnit.YUAN),

    # forecast — 万元 (DIFFERENT FROM income — easy bug)
    "forecast.net_profit_min":  TushareFieldUnit("forecast", "net_profit_min", MoneyUnit.WAN_YUAN),
    "forecast.net_profit_max":  TushareFieldUnit("forecast", "net_profit_max", MoneyUnit.WAN_YUAN),
    "forecast.last_parent_net": TushareFieldUnit("forecast", "last_parent_net", MoneyUnit.WAN_YUAN),

    # express — 元
    "express.revenue":          TushareFieldUnit("express", "revenue", MoneyUnit.YUAN),
    "express.n_income":         TushareFieldUnit("express", "n_income", MoneyUnit.YUAN),

    # daily — amount 千元, vol 手
    "daily.amount":             TushareFieldUnit("daily", "amount", MoneyUnit.QIAN_YUAN),
    "daily.vol":                TushareFieldUnit("daily", "vol", ShareUnit.SHOU),

    # stk_mins — amount 元, vol 股 (DIFFERENT FROM daily — easy bug)
    "stk_mins.amount":          TushareFieldUnit("stk_mins", "amount", MoneyUnit.YUAN),
    "stk_mins.vol":             TushareFieldUnit("stk_mins", "vol", ShareUnit.SHARE),

    # daily_basic / stk_factor_pro — 万元, 万股
    "daily_basic.total_mv":     TushareFieldUnit("daily_basic", "total_mv", MoneyUnit.WAN_YUAN),
    "daily_basic.circ_mv":      TushareFieldUnit("daily_basic", "circ_mv", MoneyUnit.WAN_YUAN),
    "stk_factor_pro.total_mv":  TushareFieldUnit("stk_factor_pro", "total_mv", MoneyUnit.WAN_YUAN),
    "stk_factor_pro.circ_mv":   TushareFieldUnit("stk_factor_pro", "circ_mv", MoneyUnit.WAN_YUAN),
    "stk_factor_pro.amount":    TushareFieldUnit("stk_factor_pro", "amount", MoneyUnit.QIAN_YUAN),
    "stk_factor_pro.vol":       TushareFieldUnit("stk_factor_pro", "vol", ShareUnit.SHOU),
    "stk_factor_pro.total_share": TushareFieldUnit("stk_factor_pro", "total_share", ShareUnit.WAN_SHARE),
    "stk_factor_pro.float_share": TushareFieldUnit("stk_factor_pro", "float_share", ShareUnit.WAN_SHARE),
    "stk_factor_pro.free_share":  TushareFieldUnit("stk_factor_pro", "free_share", ShareUnit.WAN_SHARE),

    # moneyflow / hsgt — 万元, 手
    "moneyflow.buy_lg_amount":  TushareFieldUnit("moneyflow", "buy_lg_amount", MoneyUnit.WAN_YUAN),
    "moneyflow.sell_lg_amount": TushareFieldUnit("moneyflow", "sell_lg_amount", MoneyUnit.WAN_YUAN),
    "moneyflow.buy_elg_amount": TushareFieldUnit("moneyflow", "buy_elg_amount", MoneyUnit.WAN_YUAN),
    "moneyflow.sell_elg_amount": TushareFieldUnit("moneyflow", "sell_elg_amount", MoneyUnit.WAN_YUAN),
    "moneyflow.net_mf_amount":  TushareFieldUnit("moneyflow", "net_mf_amount", MoneyUnit.WAN_YUAN),
    "moneyflow_hsgt.north_money": TushareFieldUnit("moneyflow_hsgt", "north_money", MoneyUnit.WAN_YUAN),
    "moneyflow_hsgt.south_money": TushareFieldUnit("moneyflow_hsgt", "south_money", MoneyUnit.WAN_YUAN),
    "moneyflow_hsgt.hgt":       TushareFieldUnit("moneyflow_hsgt", "hgt", MoneyUnit.WAN_YUAN),
    "moneyflow_hsgt.sgt":       TushareFieldUnit("moneyflow_hsgt", "sgt", MoneyUnit.WAN_YUAN),

    # stock holders / float — 股, 万股
    "top10_holders.hold_amount":   TushareFieldUnit("top10_holders", "hold_amount", ShareUnit.SHARE),
    "top10_floatholders.hold_amount": TushareFieldUnit("top10_floatholders", "hold_amount", ShareUnit.SHARE),
    "stk_holdertrade.change_vol":  TushareFieldUnit("stk_holdertrade", "change_vol", ShareUnit.SHARE),
    "share_float.float_share":     TushareFieldUnit("share_float", "float_share", ShareUnit.SHARE),

    # block_trade — 万股, 万元
    "block_trade.vol":          TushareFieldUnit("block_trade", "vol", ShareUnit.WAN_SHARE),
    "block_trade.amount":       TushareFieldUnit("block_trade", "amount", MoneyUnit.WAN_YUAN),

    # dividend — cash_div 元/股, base_share 万股
    "dividend.base_share":      TushareFieldUnit("dividend", "base_share", ShareUnit.WAN_SHARE),
    # cash_div is per-share so already in base 元/股; not converted

    # stk_rewards — reward 元
    "stk_rewards.reward":       TushareFieldUnit("stk_rewards", "reward", MoneyUnit.YUAN),
    "stk_rewards.hold_vol":     TushareFieldUnit("stk_rewards", "hold_vol", ShareUnit.SHARE),

    # cyq_chips — percent 是 0-1 小数（特殊！）
    "cyq_chips.percent":        TushareFieldUnit("cyq_chips", "percent", RatioUnit.DECIMAL_0_1),

    # stock_company
    "stock_company.reg_capital": TushareFieldUnit("stock_company", "reg_capital", MoneyUnit.WAN_YUAN),
}


def lookup_unit(api: str, field: str) -> TushareFieldUnit:
    """Look up the authoritative unit for a Tushare field.

    Raises KeyError if not registered — fetcher MUST register before using.
    """
    key = f"{api}.{field}"
    if key not in TUSHARE_UNITS:
        raise KeyError(
            f"Unit not registered for {key}. Add it to TUSHARE_UNITS in "
            f"core/units.py and document it in docs/tushare-units-reference.md."
        )
    return TUSHARE_UNITS[key]


def normalize_tushare_value(api: str, field: str, raw_value: float | int | None) -> Decimal | None:
    """Single-call helper: look up unit + convert to base. The recommended way."""
    if raw_value is None:
        return None
    spec = lookup_unit(api, field)
    if isinstance(spec.unit, MoneyUnit):
        return to_yuan(raw_value, spec.unit)
    if isinstance(spec.unit, ShareUnit):
        return to_share(raw_value, spec.unit)
    if isinstance(spec.unit, RatioUnit):
        return to_pct_0_100(raw_value, spec.unit)
    raise TypeError(f"Unknown unit type: {type(spec.unit)}")


# ─── Renderer-side formatting ────────────────────────────────────────────────

def fmt_amt(yuan: float | int | Decimal | None, *, mode: str = "auto", precision: int = 2) -> str:
    """Format a 元-base monetary value for human display.

    mode='auto' picks the largest unit that yields a value >= 1.
    mode='yi'   forces 亿 unit.
    mode='wan'  forces 万 unit.
    mode='yuan' forces raw 元.

    Returns "—" for None.
    """
    if yuan is None:
        return "—"
    v = Decimal(str(yuan))
    if mode == "auto":
        abs_v = abs(v)
        if abs_v >= Decimal(1_0000_0000_0000):  # >= 万亿
            return f"{v / Decimal(1_0000_0000_0000):.{precision}f} 万亿"
        if abs_v >= Decimal(1_0000_0000):       # >= 亿
            return f"{v / Decimal(1_0000_0000):.{precision}f} 亿"
        if abs_v >= Decimal(10_000):            # >= 万
            return f"{v / Decimal(10_000):,.{precision}f} 万"
        return f"{v:,.{precision}f} 元"
    if mode == "yi":
        return f"{v / Decimal(1_0000_0000):.{precision}f} 亿"
    if mode == "wan":
        return f"{v / Decimal(10_000):,.{precision}f} 万"
    if mode == "yuan":
        return f"{v:,.{precision}f} 元"
    raise ValueError(f"Unknown mode: {mode}")


def fmt_pct(pct_0_100: float | int | Decimal | None, *, precision: int = 2) -> str:
    """Format a 0-100 percentage value for display.

    Input is in 0-100 range; output adds % suffix.
    """
    if pct_0_100 is None:
        return "—"
    return f"{Decimal(str(pct_0_100)):.{precision}f}%"


def fmt_share(shares: float | int | Decimal | None, *, mode: str = "auto") -> str:
    """Format a share count for display.

    mode='auto' picks the largest unit that yields a value >= 1.
    """
    if shares is None:
        return "—"
    v = Decimal(str(shares))
    if mode == "auto":
        abs_v = abs(v)
        if abs_v >= Decimal(1_0000_0000):
            return f"{v / Decimal(1_0000_0000):.2f} 亿股"
        if abs_v >= Decimal(10_000):
            return f"{v / Decimal(10_000):,.2f} 万股"
        return f"{v:,.0f} 股"
    if mode == "yi":
        return f"{v / Decimal(1_0000_0000):.2f} 亿股"
    if mode == "wan":
        return f"{v / Decimal(10_000):,.2f} 万股"
    if mode == "share":
        return f"{v:,.0f} 股"
    raise ValueError(f"Unknown mode: {mode}")


def fmt_price(yuan_per_share: float | int | Decimal | None, *, precision: int = 2) -> str:
    """Format a per-share price (元/股)."""
    if yuan_per_share is None:
        return "—"
    return f"{Decimal(str(yuan_per_share)):.{precision}f}"


def fmt_multiple(x: float | int | Decimal | None, *, precision: int = 2) -> str:
    """Format a ratio / multiple (PE, PB, PS, ...) — adds 'x' suffix."""
    if x is None:
        return "—"
    return f"{Decimal(str(x)):.{precision}f}x"
