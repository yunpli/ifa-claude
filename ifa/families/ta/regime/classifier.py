"""TA-M3 — 9-regime market state classifier (rule-based, explainable).

The 9 regimes (per docs/ta-strategy-deep-dive.md §3.2):
  1. trend_continuation  — 趋势延续
  2. early_risk_on       — 风险偏好回升初期
  3. weak_rebound        — 弱反弹
  4. range_bound         — 震荡区间
  5. sector_rotation     — 板块轮动
  6. emotional_climax    — 情绪极度高潮
  7. distribution_risk   — 顶部派发风险
  8. cooldown            — 退潮冷却
  9. high_difficulty     — 高难度（无序）

Each detector returns a score 0-1 (confidence). The main classifier picks the
highest-scoring regime, records `evidence_json` with all scores, and returns
a `RegimeResult` ready for persistence.

Inputs come bundled in `RegimeContext` (loader.py builds one from DB rows for
a given trade_date). Keeping detectors pure (no DB) makes them trivially
testable.

Design choices:
  · Rules-only — every regime can be hand-checked; no ML.
  · Multiple detectors can fire; tie-breaks favor the more "concrete"
    regime (climax/distribution > continuation > rotation > range > rebound).
  · Confidence is the winner's score; <0.4 → fall back to high_difficulty.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Literal

# Canonical regime names (also used as DB column values)
REGIMES: tuple[str, ...] = (
    "trend_continuation",
    "early_risk_on",
    "weak_rebound",
    "range_bound",
    "sector_rotation",
    "emotional_climax",
    "distribution_risk",
    "cooldown",
    "high_difficulty",
)

Regime = Literal[
    "trend_continuation",
    "early_risk_on",
    "weak_rebound",
    "range_bound",
    "sector_rotation",
    "emotional_climax",
    "distribution_risk",
    "cooldown",
    "high_difficulty",
]


@dataclass
class RegimeContext:
    """All inputs the 9 regime detectors need for one trade_date.

    Anything missing → set to None and detectors that need it will skip.
    Loader.py is responsible for filling these from `smartmoney.*` tables.
    """
    trade_date: date

    # ── Index structure (上证 / 创业 / 科创) ────────────────────────────
    sse_close: float | None = None              # SSE composite close
    sse_ma5: float | None = None
    sse_ma20: float | None = None
    sse_ma20_prev: float | None = None          # for derivative
    sse_volatility_20d_pct: float | None = None  # rolling 20d std/mean × 100

    # ── Breadth ────────────────────────────────────────────────────────
    n_up: int | None = None                     # stocks closing up today
    n_down: int | None = None                   # stocks closing down today
    n_limit_up: int | None = None               # 涨停数
    n_limit_up_prev: int | None = None          # 昨日涨停数
    n_limit_down: int | None = None             # 跌停数
    consecutive_lb_high: int | None = None      # 最高连板数

    # ── Liquidity / volume ─────────────────────────────────────────────
    market_amount_yuan: float | None = None         # market total turnover
    market_amount_yuan_ma20: float | None = None    # 20d MA

    # ── Northbound (HK Connect) ────────────────────────────────────────
    hsgt_net_amount_yuan: float | None = None       # today's net inflow (元)
    hsgt_net_pct_60d: float | None = None           # percentile in last 60d, 0-100

    # ── Sector dispersion ──────────────────────────────────────────────
    sector_pct_change_std: float | None = None      # std dev of L1 sector returns

    # ── Sentiment / hot ────────────────────────────────────────────────
    hot_pct_change_std: float | None = None         # heat-rank turnover proxy

    # Free-form metadata for evidence JSON
    extras: dict = field(default_factory=dict)


@dataclass
class RegimeResult:
    trade_date: date
    regime: Regime
    confidence: float       # 0-1
    evidence: dict          # all scores + key inputs

    def to_db_row(self) -> dict:
        return {
            "trade_date": self.trade_date,
            "regime": self.regime,
            "confidence": round(self.confidence, 4),
            "evidence_json": self.evidence,
        }


# ─── Public API ───────────────────────────────────────────────────────────────

def classify_regime(ctx: RegimeContext) -> RegimeResult:
    """Run all 9 detectors and pick the highest-scoring one.

    Falls back to 'high_difficulty' when no detector scores ≥ 0.4
    (means we don't have a clean read on the market state).
    """
    scores: dict[Regime, float] = {
        "trend_continuation": _score_trend_continuation(ctx),
        "early_risk_on":      _score_early_risk_on(ctx),
        "weak_rebound":       _score_weak_rebound(ctx),
        "range_bound":        _score_range_bound(ctx),
        "sector_rotation":    _score_sector_rotation(ctx),
        "emotional_climax":   _score_emotional_climax(ctx),
        "distribution_risk":  _score_distribution_risk(ctx),
        "cooldown":           _score_cooldown(ctx),
        # high_difficulty is the catch-all when nothing else fires
    }

    # Tie-breaking: when two detectors are within 0.05 of each other, prefer
    # the more "concrete" regime (climax/distribution beat continuation, etc).
    priority = {
        "emotional_climax": 8,
        "distribution_risk": 7,
        "cooldown": 6,
        "trend_continuation": 5,
        "sector_rotation": 4,
        "early_risk_on": 3,
        "range_bound": 2,
        "weak_rebound": 1,
    }
    sorted_pairs = sorted(
        scores.items(),
        key=lambda kv: (-kv[1], -priority.get(kv[0], 0)),
    )
    winner, top_score = sorted_pairs[0]

    if top_score < 0.4:
        winner = "high_difficulty"
        confidence = 0.5  # we know it's messy, but only by exclusion
    else:
        confidence = top_score

    return RegimeResult(
        trade_date=ctx.trade_date,
        regime=winner,  # type: ignore[arg-type]
        confidence=confidence,
        evidence={
            "scores": {k: round(v, 4) for k, v in scores.items()},
            "inputs": {
                "n_up": ctx.n_up,
                "n_down": ctx.n_down,
                "n_limit_up": ctx.n_limit_up,
                "n_limit_up_prev": ctx.n_limit_up_prev,
                "consecutive_lb_high": ctx.consecutive_lb_high,
                "sse_ma5_above_ma20": _sse_ma5_above_ma20(ctx),
                "sse_ma20_rising": _sse_ma20_rising(ctx),
                "sse_volatility_20d_pct": ctx.sse_volatility_20d_pct,
                "sector_pct_change_std": ctx.sector_pct_change_std,
                "amount_vs_ma20": _amount_vs_ma20(ctx),
                "hsgt_net_pct_60d": ctx.hsgt_net_pct_60d,
            },
        },
    )


# ─── Detectors ────────────────────────────────────────────────────────────────

def _sse_ma5_above_ma20(ctx: RegimeContext) -> bool | None:
    if ctx.sse_ma5 is None or ctx.sse_ma20 is None:
        return None
    return ctx.sse_ma5 > ctx.sse_ma20


def _sse_ma20_rising(ctx: RegimeContext) -> bool | None:
    if ctx.sse_ma20 is None or ctx.sse_ma20_prev is None:
        return None
    return ctx.sse_ma20 > ctx.sse_ma20_prev


def _amount_vs_ma20(ctx: RegimeContext) -> float | None:
    """Today's total amount / 20d MA. >1.2 = unusually high; <0.8 = quiet."""
    if not ctx.market_amount_yuan or not ctx.market_amount_yuan_ma20:
        return None
    return ctx.market_amount_yuan / ctx.market_amount_yuan_ma20


def _up_down_ratio(ctx: RegimeContext) -> float | None:
    if ctx.n_up is None or ctx.n_down is None or ctx.n_down == 0:
        return None
    return ctx.n_up / ctx.n_down


def _limit_up_yoy(ctx: RegimeContext) -> float | None:
    """Today's limit-up count vs prev day, as ratio (e.g. 1.5 = +50%)."""
    if (ctx.n_limit_up is None or ctx.n_limit_up_prev is None
            or ctx.n_limit_up_prev <= 0):
        return None
    return ctx.n_limit_up / ctx.n_limit_up_prev


def _score_trend_continuation(ctx: RegimeContext) -> float:
    """趋势延续: 上证 20MA 上行 + 5MA>20MA + 涨家数/跌家数>1.5 + 量稳."""
    score = 0.0
    rising = _sse_ma20_rising(ctx)
    if rising is True:
        score += 0.3
    elif rising is False:
        return 0.0  # 20MA not rising → not continuation
    if _sse_ma5_above_ma20(ctx) is True:
        score += 0.25
    udr = _up_down_ratio(ctx)
    if udr is not None and udr > 1.5:
        score += 0.25
    elif udr is not None and udr > 1.2:
        score += 0.1
    amt_ratio = _amount_vs_ma20(ctx)
    if amt_ratio is not None and 0.85 < amt_ratio < 1.25:  # stable volume
        score += 0.2
    return min(score, 1.0)


def _score_early_risk_on(ctx: RegimeContext) -> float:
    """风险偏好回升初期: 20MA 拐点向上 + 涨停 +50% + 龙头出现."""
    score = 0.0
    if _sse_ma20_rising(ctx) is True:
        score += 0.25
    lu_ratio = _limit_up_yoy(ctx)
    if lu_ratio is not None and lu_ratio >= 1.5:
        score += 0.35
    elif lu_ratio is not None and lu_ratio >= 1.2:
        score += 0.15
    if ctx.consecutive_lb_high is not None and ctx.consecutive_lb_high >= 3:
        score += 0.25
    if (ctx.hsgt_net_pct_60d is not None and ctx.hsgt_net_pct_60d >= 60):
        score += 0.15
    return min(score, 1.0)


def _score_weak_rebound(ctx: RegimeContext) -> float:
    """弱反弹: 5MA 反弹但未破 20MA + 量能不足 + 涨家数 50-55%."""
    score = 0.0
    above = _sse_ma5_above_ma20(ctx)
    if above is False:  # 5MA below 20MA — necessary
        score += 0.3
    elif above is True:
        return 0.0
    udr = _up_down_ratio(ctx)
    if udr is not None and 1.0 <= udr <= 1.25:  # marginally up
        score += 0.3
    amt_ratio = _amount_vs_ma20(ctx)
    if amt_ratio is not None and amt_ratio < 0.85:  # weak volume
        score += 0.25
    lu = ctx.n_limit_up
    if lu is not None and lu < 30:
        score += 0.15
    return min(score, 1.0)


def _score_range_bound(ctx: RegimeContext) -> float:
    """震荡区间: 20 日波动率 <8% + 5MA 与 20MA 缠绕 + 量能平淡."""
    score = 0.0
    if ctx.sse_volatility_20d_pct is not None and ctx.sse_volatility_20d_pct < 8:
        score += 0.4
    if (ctx.sse_ma5 is not None and ctx.sse_ma20 is not None
            and abs(ctx.sse_ma5 - ctx.sse_ma20) / ctx.sse_ma20 < 0.01):
        score += 0.3
    amt_ratio = _amount_vs_ma20(ctx)
    if amt_ratio is not None and 0.85 <= amt_ratio <= 1.1:
        score += 0.3
    return min(score, 1.0)


def _score_sector_rotation(ctx: RegimeContext) -> float:
    """板块轮动: 大盘震荡 + SW L1 涨跌幅离散度高 + 资金高速换手."""
    score = 0.0
    if (ctx.sector_pct_change_std is not None
            and ctx.sector_pct_change_std > 1.5):
        score += 0.5
    elif (ctx.sector_pct_change_std is not None
          and ctx.sector_pct_change_std > 1.0):
        score += 0.25
    # market itself shouldn't be strongly trending
    rising = _sse_ma20_rising(ctx)
    if rising is False or rising is None:
        score += 0.2
    amt_ratio = _amount_vs_ma20(ctx)
    if amt_ratio is not None and amt_ratio > 1.1:  # high turnover
        score += 0.3
    return min(score, 1.0)


def _score_emotional_climax(ctx: RegimeContext) -> float:
    """情绪高潮: 涨停 >120 + 连板高度 >7 + 北向超大流入 + 量能破前高."""
    score = 0.0
    if ctx.n_limit_up is not None and ctx.n_limit_up > 120:
        score += 0.35
    elif ctx.n_limit_up is not None and ctx.n_limit_up > 90:
        score += 0.2
    if ctx.consecutive_lb_high is not None and ctx.consecutive_lb_high >= 7:
        score += 0.3
    if ctx.hsgt_net_pct_60d is not None and ctx.hsgt_net_pct_60d >= 90:
        score += 0.15
    amt_ratio = _amount_vs_ma20(ctx)
    if amt_ratio is not None and amt_ratio > 1.4:
        score += 0.2
    return min(score, 1.0)


def _score_distribution_risk(ctx: RegimeContext) -> float:
    """顶部派发: 高位放量滞涨 + 龙头分歧 + 量价背离 + 主力净流出.

    Without intraday flow data we approximate via:
    high index level + high volume + few new limit-ups + breadth deterioration.
    """
    score = 0.0
    rising = _sse_ma20_rising(ctx)
    above = _sse_ma5_above_ma20(ctx)
    # Index is at high but ma5 below ma20 = topping pattern
    if rising is True and above is False:
        score += 0.35
    amt_ratio = _amount_vs_ma20(ctx)
    if amt_ratio is not None and amt_ratio > 1.2:
        score += 0.25
    udr = _up_down_ratio(ctx)
    if udr is not None and udr < 0.9:  # breadth deteriorating
        score += 0.25
    if ctx.hsgt_net_pct_60d is not None and ctx.hsgt_net_pct_60d <= 25:
        score += 0.15
    return min(score, 1.0)


def _score_cooldown(ctx: RegimeContext) -> float:
    """退潮冷却: 涨停数环比-50% + 跌家数>涨家数 + 5MA 跌破 20MA."""
    score = 0.0
    lu_ratio = _limit_up_yoy(ctx)
    if lu_ratio is not None and lu_ratio <= 0.5:
        score += 0.4
    elif lu_ratio is not None and lu_ratio <= 0.7:
        score += 0.2
    udr = _up_down_ratio(ctx)
    if udr is not None and udr < 0.8:
        score += 0.3
    if _sse_ma5_above_ma20(ctx) is False:
        score += 0.2
    if ctx.n_limit_down is not None and ctx.n_limit_down > 20:
        score += 0.1
    return min(score, 1.0)
