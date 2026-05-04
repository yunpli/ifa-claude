"""R1 double bottom — two lows ~equal price, neckline reclaim.

Heuristic (simple): in last 30 days find two distinct local-low days with
similar price (within 3%), separated by a peak >= 5% above them. Today
closes above that peak (neckline).

Triggers (all):
  · low1, low2 found in last 30 days, |low1 - low2| / low1 <= 3%
  · peak between them, peak / min(low1, low2) - 1 >= 5%
  · today's close > peak                   — neckline reclaim

Score:
  base 0.5
  + 0.2 if regime in {weak_rebound, range_bound, cooldown}   (after weakness)
  + 0.2 if MACD divergence: macd_dif_qfq > 0 (turning positive)
  + 0.1 if volume_ratio >= 1.5 on neckline break
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def R1_DOUBLE_BOTTOM(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or len(ctx.lows) < 30 or len(ctx.highs) < 30):
        return None

    window_lows = list(ctx.lows[-30:-1])
    if not window_lows:
        return None

    # Find the lowest low and the second-lowest at least 5 days away
    sorted_idx = sorted(range(len(window_lows)), key=lambda i: window_lows[i])
    low1_idx = sorted_idx[0]
    low2_idx = None
    for j in sorted_idx[1:]:
        if abs(j - low1_idx) >= 5:
            low2_idx = j
            break
    if low2_idx is None:
        return None

    low1 = window_lows[low1_idx]
    low2 = window_lows[low2_idx]
    if abs(low1 - low2) / max(low1, 1e-9) > 0.03:
        return None

    peak_start = min(low1_idx, low2_idx)
    peak_end = max(low1_idx, low2_idx)
    if peak_end <= peak_start:
        return None
    peak = max(ctx.highs[-30:-1][peak_start:peak_end + 1])
    if peak / min(low1, low2) - 1 < 0.05:
        return None

    if ctx.close_today <= peak:
        return None

    triggers = ["double_bottom_pattern", "neckline_reclaim"]
    score = 0.5

    if ctx.regime in ("weak_rebound", "range_bound", "cooldown"):
        score += 0.2
        triggers.append("post_weakness_regime")
    if ctx.macd_dif_qfq is not None and ctx.macd_dif_qfq > 0:
        score += 0.2
        triggers.append("macd_dif_positive")
    if ctx.volume_ratio is not None and ctx.volume_ratio >= 1.5:
        score += 0.1
        triggers.append("volume_breakout")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="R1_DOUBLE_BOTTOM",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "low1": low1, "low2": low2,
            "neckline_peak": peak,
            "low_diff_pct": abs(low1 - low2) / low1 * 100,
        },
    )
