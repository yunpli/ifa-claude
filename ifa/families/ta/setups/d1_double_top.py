"""D1 双顶反转 — bearish double-top warning.

Detection:
  · Look at last 40 closes; find two local maxima within 2% of each other,
    separated by 5-20 trading days, with a trough in between >= 5% below.
  · Today's close has broken below the trough (neckline) by >=1%.
  · 20d prior return >= 10% (must be after a real run-up, else not a top).

Score (bearish — ranker handles separately or LLM warns):
  base 0.5
  + up to 0.20 continuous = clip((break_depth - 1) / 4, 0, 1)
  + up to 0.10 if regime in {distribution_risk, emotional_climax, cooldown}
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def D1_DOUBLE_TOP(ctx: SetupContext) -> Candidate | None:
    closes = ctx.closes
    if len(closes) < 40 or ctx.close_today is None:
        return None
    window = closes[-40:]
    n = len(window)

    peak_diff_max = setup_param("D1_DOUBLE_TOP", "peak_diff_max", 0.02)
    trough_drop_min = setup_param("D1_DOUBLE_TOP", "trough_drop_min", 0.05)
    break_depth_min_pct = setup_param("D1_DOUBLE_TOP", "break_depth_min_pct", 1.0)
    ret_20d_min_pct = setup_param("D1_DOUBLE_TOP", "ret_20d_min_pct", 10.0)

    peak_idxs: list[int] = []
    for i in range(2, n - 2):
        if window[i] > window[i - 1] and window[i] > window[i - 2] \
                and window[i] > window[i + 1] and window[i] > window[i + 2]:
            peak_idxs.append(i)
    if len(peak_idxs) < 2:
        return None

    found = None
    for i in range(len(peak_idxs)):
        for j in range(i + 1, len(peak_idxs)):
            a, b = peak_idxs[i], peak_idxs[j]
            if not (5 <= b - a <= 20):
                continue
            pa, pb = window[a], window[b]
            if abs(pa - pb) / max(pa, pb) > peak_diff_max:
                continue
            trough = min(window[a + 1:b])
            avg_peak = (pa + pb) / 2
            if (avg_peak - trough) / avg_peak < trough_drop_min:
                continue
            found = (a, b, avg_peak, trough)
    if not found:
        return None
    _, _, avg_peak, trough = found
    if ctx.close_today >= trough * 0.99:
        return None
    break_depth = (trough - ctx.close_today) / trough * 100
    if break_depth < break_depth_min_pct:
        return None
    ret_20d = (window[-1] / window[-21] - 1.0) * 100 if n >= 21 else 0
    if ret_20d < ret_20d_min_pct:
        return None

    triggers = ["double_top", "neckline_break", "post_runup"]
    score = 0.5
    strength = max(0.0, min(1.0, (break_depth - 1.0) / 4.0))
    score += 0.20 * strength
    if strength >= 0.5:
        triggers.append("decisive_break")
    if ctx.regime in {"distribution_risk", "emotional_climax", "cooldown"}:
        score += 0.10
        triggers.append("regime_warning")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="D1_DOUBLE_TOP",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "neckline": trough,
            "avg_peak": avg_peak,
            "break_depth_pct": break_depth,
        },
    )
