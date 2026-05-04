"""S2 leader follow-through — stock outperforms L2 peers in a strong sector.

Triggers (all):
  · sw_l2_pct_change >= 2%                                           — sector strong
  · today's stock return >= sw_l2_pct_change + 2pp                   — outperforms L2 by ≥2pp
  · sector_peers_pct_change is not empty
  · stock return is in top 30% of L2 peers (excluding self)
  · MA20 > MA60

Score:
  base 0.5
  + 0.2 if regime in {trend_continuation, early_risk_on, sector_rotation}
  + 0.2 if stock is in top 10% of peers
  + 0.1 if volume_ratio >= 1.5
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext
from ifa.families.ta.setups._params import setup_param


def S2_LEADER_FOLLOWTHROUGH(ctx: SetupContext) -> Candidate | None:
    if (ctx.close_today is None or ctx.ma_qfq_20 is None or ctx.ma_qfq_60 is None
            or ctx.sw_l2_pct_change is None
            or not ctx.sector_peers_pct_change
            or len(ctx.closes) < 2):
        return None
    if ctx.ma_qfq_20 <= ctx.ma_qfq_60:
        return None

    l2_min = setup_param("S2_LEADER_FOLLOWTHROUGH", "l2_pct_min", 2.0)
    outperform_pp = setup_param("S2_LEADER_FOLLOWTHROUGH", "outperform_l2_min_pp", 2.0)

    if ctx.sw_l2_pct_change < l2_min:
        return None

    stock_ret = (ctx.close_today / ctx.closes[-2] - 1.0) * 100
    if stock_ret < ctx.sw_l2_pct_change + outperform_pp:
        return None

    peers = sorted(ctx.sector_peers_pct_change.values(), reverse=True)
    if not peers:
        return None
    rank_threshold_top30 = peers[max(0, int(len(peers) * 0.3) - 1)]
    if stock_ret < rank_threshold_top30:
        return None

    triggers = ["uptrend_stack", "L2>=2%", "outperforms_L2", "top_30pct_in_L2"]
    score = 0.5

    # Continuous: 板块内排名分位 — 计算 stock_ret 在 peers 中的百分位
    # peers sorted desc; find index 0..len-1; pos 0 = top → strength 1.0
    n_peers = len(peers)
    n_below = sum(1 for p in peers if p < stock_ret)
    rank_pct = n_below / max(n_peers, 1)   # 0 = bottom, 1 = top
    # bonus 的 [0,0.20]: 30% percentile (top 70%) → 0, 90%+ percentile → full
    rank_strength = max(0.0, min(1.0, (rank_pct - 0.7) / 0.2))
    score += 0.20 * rank_strength
    if rank_strength >= 0.5:
        triggers.append("top_10pct_in_L2")

    # Continuous: 量能
    if ctx.volume_ratio is not None:
        vol_strength = max(0.0, min(1.0, (ctx.volume_ratio - 1.0) / 1.5))
        score += 0.10 * vol_strength
        if vol_strength >= 0.3:
            triggers.append("volume_confirmation")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="S2_LEADER_FOLLOWTHROUGH",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "stock_ret_pct": stock_ret,
            "sw_l2_pct": ctx.sw_l2_pct_change,
            "peers_n": len(peers),
            "top_30pct_threshold": rank_threshold_top30,
        },
    )
