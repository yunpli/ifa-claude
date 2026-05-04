"""Setup framework — Protocol + dataclasses.

A setup is a pure function `(SetupContext) -> Candidate | None`. Returning
None means "not triggered today". Returning a Candidate writes one row to
ta.candidates_daily later in the pipeline.

Design choices:
  · Setups are stateless — all data passed via SetupContext.
  · Multiple setups can fire for the same (ts_code, trade_date) — the
    candidate ranker downstream merges & ranks.
  · Evidence is a free-form dict per setup — schema lives in each setup
    module's docstring, not enforced here. Persisted as JSONB.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Sequence

from ifa.families.ta.regime.classifier import Regime


@dataclass(frozen=True)
class SetupContext:
    """Per-stock per-day inputs to all setups.

    Setups read what they need; missing fields stay None. A setup that
    requires a missing field should return None (not raise).
    """
    ts_code: str
    trade_date: date

    # Price series — close prices, ascending by date, length up to 60.
    closes: Sequence[float] = field(default_factory=tuple)
    highs: Sequence[float] = field(default_factory=tuple)
    lows: Sequence[float] = field(default_factory=tuple)
    volumes: Sequence[float] = field(default_factory=tuple)

    # Today's factor_pro_daily row (subset that setups actually read)
    close_today: float | None = None
    ma_qfq_5: float | None = None
    ma_qfq_10: float | None = None
    ma_qfq_20: float | None = None
    ma_qfq_60: float | None = None
    macd_qfq: float | None = None
    macd_dea_qfq: float | None = None
    macd_dif_qfq: float | None = None
    rsi_qfq_6: float | None = None
    turnover_rate_pct: float | None = None
    volume_ratio: float | None = None

    # Volatility (ATR proxy = 20d std-dev of pct_chg / 100, in % units)
    atr_pct_20d: float | None = None

    # Cross-sectional ranks (0-1) computed by context_loader at scan time:
    # rank within today's full tradeable universe, where 1.0 = highest in market.
    # These let setups use "relatively strong" instead of "absolutely above N",
    # making strength definitions adaptive to today's market activity.
    volume_ratio_rank: float | None = None
    today_pct_chg_rank: float | None = None       # today's stock return percentile

    # Market context
    regime: Regime | None = None

    # Sector context — SW L1/L2 codes + same-day sector pct_change
    sw_l1_code: str | None = None
    sw_l2_code: str | None = None
    sw_l1_pct_change: float | None = None
    sw_l2_pct_change: float | None = None
    # Same-day pct_change for L2 peers, indexed by ts_code (excludes self).
    sector_peers_pct_change: dict | None = None

    # M9.7 — SmartMoney sector flow integration
    # sector_quality ∈ [0, 1] computed from L2 net_amount rank + cycle_phase
    # (data-derived score) + SmartMoney confidence. Set None when SmartMoney
    # data missing — ranker falls back to no adjustment.
    sector_quality: float | None = None
    sector_role: str | None = None         # 主线/中军/轮动/防守/催化/退潮/未识别
    sector_cycle_phase: str | None = None  # 冷/点火/确认/扩散/高潮/分歧/退潮

    # Chip distribution (from ta.cyq_perf_daily); set when available, else None.
    chip_concentration_pct: float | None = None     # smaller = more concentrated
    chip_winner_rate_pct: float | None = None       # 0-100, % of float in profit


@dataclass(frozen=True)
class Candidate:
    ts_code: str
    trade_date: date
    setup_name: str
    score: float                     # 0.0-1.0; ranker rescales later
    triggers: tuple[str, ...]        # short reason codes, e.g. ("close>ma20", "ma20>ma60")
    evidence: dict                   # free-form numeric/textual evidence; persisted as JSONB


SetupFn = Callable[[SetupContext], Candidate | None]
