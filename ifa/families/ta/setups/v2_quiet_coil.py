"""V2 quiet coil — declining volume in tight range, often precedes a move.

Triggers (all):
  · last 5 days: volume_ratio is None at the time, but today's volume_ratio < 0.7
  · last 5 days range <= 4%                       — tight range
  · MA20 > MA60                                    — uptrend (we only fire upside-bias)
  · close[-1] >= min(closes[-5:])                  — not a breakdown

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, range_bound}
  + 0.2 if volume_ratio < 0.5                       — exceptionally quiet
  + 0.1 if rsi_qfq_6 is not None and 40 <= rsi_qfq_6 <= 60
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def V2_QUIET_COIL(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or ctx.volume_ratio is None or len(ctx.closes) < 6
            or len(ctx.highs) < 5 or len(ctx.lows) < 5):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    vol_ratio_max = setup_param("V2_QUIET_COIL", "vol_ratio_max", 0.7)
    box_range_max = setup_param("V2_QUIET_COIL", "box_range_max", 0.04)

    if ctx.volume_ratio >= vol_ratio_max:
        return None

    box_high = max(ctx.highs[-5:])
    box_low = min(ctx.lows[-5:])
    if box_high <= 0:
        return None
    box_range_pct = (box_high - box_low) / box_high
    if box_range_pct > box_range_max:
        return None

    if ctx.close_today < min(ctx.closes[-5:]):
        return None

    triggers = ["uptrend_stack", "vol_ratio<0.7", "tight_5d_range"]
    score = 0.5

    # Cross-sectional 缩量 — rank 越低（市场底端）越是真缩量
    if ctx.volume_ratio_rank is not None:
        # rank ≤ 0.3 (bottom 30% of market) → full
        quiet_strength = max(0.0, min(1.0, (0.3 - ctx.volume_ratio_rank) / 0.3))
    else:
        quiet_strength = max(0.0, min(1.0, (0.7 - ctx.volume_ratio) / 0.4))
    score += 0.20 * quiet_strength
    if quiet_strength >= 0.5:
        triggers.append("very_quiet")

    # Continuous: RSI 中性度 (50 = peak, 偏离 ±15 → 0)
    if ctx.rsi_qfq_6 is not None:
        rsi_neutral = max(0.0, min(1.0, 1 - abs(ctx.rsi_qfq_6 - 50) / 15))
        score += 0.10 * rsi_neutral
        if rsi_neutral >= 0.5:
            triggers.append("rsi_neutral")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="V2_QUIET_COIL",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "volume_ratio": ctx.volume_ratio,
            "box_range_pct": box_range_pct * 100,
        },
    )
