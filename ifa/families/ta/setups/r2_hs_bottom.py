"""R2 inverse head-and-shoulders bottom.

Heuristic (simple): in last 40 days find three lows where:
  · middle low is the lowest (head)
  · left and right lows are roughly equal (within 5%) and >= 3% above head
  · neckline = max of the two intervening peaks
  · today's close > neckline

Triggers (all):
  · three-low structure satisfying above
  · today's close > neckline

Score:
  base 0.5
  + 0.2 if regime in {weak_rebound, cooldown, range_bound}
  + 0.2 if shoulders symmetric (within 3%)
  + 0.1 if volume_ratio >= 1.5
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def R2_HS_BOTTOM(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or len(ctx.lows) < 40 or len(ctx.highs) < 40):
        return None

    lows = list(ctx.lows[-40:-1])
    highs = list(ctx.highs[-40:-1])
    if not lows:
        return None

    head_idx = min(range(len(lows)), key=lambda i: lows[i])
    head = lows[head_idx]

    left_zone = lows[max(0, head_idx - 15):head_idx - 2] if head_idx >= 5 else []
    right_zone = lows[head_idx + 3:head_idx + 16] if head_idx <= len(lows) - 6 else []
    if not left_zone or not right_zone:
        return None

    left_idx_local = min(range(len(left_zone)), key=lambda i: left_zone[i])
    right_idx_local = min(range(len(right_zone)), key=lambda i: right_zone[i])
    left = left_zone[left_idx_local]
    right = right_zone[right_idx_local]

    # shoulders >= 3% above head, within 5% of each other
    if min(left, right) / head - 1 < 0.03:
        return None
    if abs(left - right) / max(left, 1e-9) > 0.05:
        return None

    left_idx = max(0, head_idx - 15) + left_idx_local
    right_idx = head_idx + 3 + right_idx_local
    peak_left = max(highs[left_idx:head_idx + 1])
    peak_right = max(highs[head_idx:right_idx + 1])
    neckline = max(peak_left, peak_right)
    if ctx.close_today <= neckline:
        return None

    triggers = ["inverse_hs", "neckline_break"]
    score = 0.5

    # Continuous: 肩部对称度 — 0% diff→full, 5%→0
    shoulder_diff = abs(left - right) / max(left, 1e-9)
    symmetry = max(0.0, min(1.0, (0.05 - shoulder_diff) / 0.05))
    score += 0.20 * symmetry
    if symmetry >= 0.4:
        triggers.append("symmetric_shoulders")

    # Continuous: 量能突破
    if ctx.volume_ratio is not None:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio - 1.0) / 1.5))
        score += 0.10 * vol_strength
        if vol_strength >= 0.3:
            triggers.append("volume_breakout")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="R2_HS_BOTTOM",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "head": head,
            "left_shoulder": left,
            "right_shoulder": right,
            "neckline": neckline,
        },
    )
