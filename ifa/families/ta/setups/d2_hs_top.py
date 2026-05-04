"""D2 头肩顶反转 — bearish head-and-shoulders top.

Detection:
  · 60-bar window: identify three peaks where middle (head) is highest,
    left & right shoulders within 5% of each other and 3-12% below head.
  · Today's close has broken below the neckline (max of two intermediate troughs)
    by >=1%.
  · Prior 30d return >= 8% (top must form after a real run-up).

Score (bearish warning):
  base 0.5
  + up to 0.20 continuous = clip((break_depth - 1) / 4, 0, 1)
  + up to 0.10 if shoulders are well-symmetric (within 2%)
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def D2_HS_TOP(ctx: SetupContext) -> Candidate | None:
    closes = ctx.closes
    if len(closes) < 60 or ctx.close_today is None:
        return None
    window = closes[-60:]
    n = len(window)

    peaks = []
    for i in range(2, n - 2):
        if window[i] > window[i - 1] and window[i] > window[i - 2] \
                and window[i] > window[i + 1] and window[i] > window[i + 2]:
            peaks.append(i)
    if len(peaks) < 3:
        return None

    found = None
    for i in range(len(peaks) - 2):
        for j in range(i + 1, len(peaks) - 1):
            for k in range(j + 1, len(peaks)):
                a, b, c = peaks[i], peaks[j], peaks[k]
                if not (5 <= b - a <= 25 and 5 <= c - b <= 25):
                    continue
                la, head, ra = window[a], window[b], window[c]
                if head <= la or head <= ra:
                    continue
                if abs(la - ra) / max(la, ra) > 0.05:
                    continue
                if not (0.03 <= (head - la) / head <= 0.20):
                    continue
                if not (0.03 <= (head - ra) / head <= 0.20):
                    continue
                trough_ab = min(window[a + 1:b])
                trough_bc = min(window[b + 1:c])
                neckline = max(trough_ab, trough_bc)
                shoulder_sym = abs(la - ra) / max(la, ra) * 100
                found = (a, b, c, neckline, shoulder_sym)
    if not found:
        return None
    a, b, c, neckline, shoulder_sym = found
    if ctx.close_today >= neckline * 0.99:
        return None
    break_depth = (neckline - ctx.close_today) / neckline * 100
    if break_depth < 1.0:
        return None
    ret_30d = (window[-1] / window[-31] - 1.0) * 100 if n >= 31 else 0
    if ret_30d < 8.0:
        return None

    triggers = ["hs_top", "neckline_break", "post_runup"]
    score = 0.5
    strength = max(0.0, min(1.0, (break_depth - 1.0) / 4.0))
    score += 0.20 * strength
    if strength >= 0.5:
        triggers.append("decisive_break")
    if shoulder_sym <= 2.0:
        score += 0.10
        triggers.append("symmetric_shoulders")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="D2_HS_TOP",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "neckline": neckline,
            "head": window[b],
            "break_depth_pct": break_depth,
            "shoulder_sym_pct": shoulder_sym,
        },
    )
