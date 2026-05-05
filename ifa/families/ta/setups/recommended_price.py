"""ATR-based three-tier recommended pricing for TA setups.

Wall-Street standard formulation:
  entry  = entry_close × (1 + entry_offset_atr × ATR_pct%)
  stop   = entry        × (1 − K_stop   × ATR_pct%)   # K_stop = 2.0
  target = entry        × (1 + K_target × ATR_pct%)   # K_target = 4.0  → R:R = 2:1

`entry_offset_atr` depends on setup category:
  · breakout / momentum (T1/T3/V1/O3/O2)         →  0.0   (mark at close)
  · pullback / continuation (T2/P1/P2/P3/F1-F3)  → −0.5   (lay below close)
  · reversal / oversold (R1/R2/R3/Z1-long/Z2)    → −0.8   (deep limit)
  · sector / chip / event (S1-S3/C1/Z1-short/E1) →  0.0   (mark at close)

Multi-setup merging on the same stock — take the conservative side:
  · entry  = max(entries)   (be picky to enter)
  · stop   = max(stops)     (tighter risk control)
  · target = min(targets)   (sooner to take profit)
"""
from __future__ import annotations

from dataclasses import dataclass

from ifa.families.ta.params import load_params

# Default ATR coefficients (used when yaml unavailable).
K_STOP_DEFAULT = 2.0
K_TARGET_DEFAULT = 4.0


def _yaml_k_stop() -> float:
    p = load_params().get("recommended_price", {}) or {}
    return float(p.get("k_stop", K_STOP_DEFAULT))


def _yaml_k_target() -> float:
    p = load_params().get("recommended_price", {}) or {}
    return float(p.get("k_target", K_TARGET_DEFAULT))

_BREAKOUT_SETUPS = {
    "T1_BREAKOUT", "T3_ACCELERATION", "V1_VOL_PRICE_UP",
    "O2_LHB_INST_BUY", "O3_LIMIT_SEAL_STRENGTH",
}
_PULLBACK_SETUPS = {
    "T2_PULLBACK_RESUME",
    "P1_MA20_PULLBACK", "P2_GAP_FILL", "P3_TIGHT_CONSOLIDATION",
    "F1_FLAG", "F2_TRIANGLE", "F3_RECTANGLE",
    "V2_QUIET_COIL",
}
_REVERSAL_SETUPS = {
    "R1_DOUBLE_BOTTOM", "R2_HS_BOTTOM", "R3_HAMMER",
    "R4_SUPPORT_BOUNCE",   # M10 P2 Q2 — MA60 bounce (mean-reversion long)
    "Z1_ZSCORE_EXTREME",   # long-direction case; for short z, treat at close (handled below)
    "Z2_OVERSOLD_REBOUND",
    "Z3_RANGE_FADE",       # M10 P2 Q2 — fade-rally in range (deep limit, wait retrace)
}


def _entry_offset_atr(setup_name: str, evidence: dict | None = None) -> float:
    """Map setup_name → ATR units of entry offset (negative = lay below close).

    For Z1 specifically: if direction='short' (post-runup exhaustion warning),
    treat as breakout-style (mark at close). For 'long' it's reversal-style.
    """
    if setup_name == "Z1_ZSCORE_EXTREME":
        if evidence and evidence.get("direction") == "short":
            return 0.0
        return -0.8
    if setup_name in _BREAKOUT_SETUPS:
        return 0.0
    if setup_name in _PULLBACK_SETUPS:
        return -0.5
    if setup_name in _REVERSAL_SETUPS:
        return -0.8
    # default: at-close
    return 0.0


@dataclass(frozen=True)
class RecommendedPrice:
    entry: float
    stop: float
    target: float
    rr: float                       # reward:risk ratio (target_gain / stop_loss)
    entry_offset_atr: float
    k_stop: float
    k_target: float


def compute_recommended_price(
    setup_name: str,
    entry_close: float,
    atr_pct_20d: float | None,
    *,
    evidence: dict | None = None,
    k_stop: float | None = None,
    k_target: float | None = None,
) -> RecommendedPrice | None:
    """Return entry/stop/target prices in 元 for a single setup hit.

    Returns None when ATR is missing or non-positive (cannot scale risk).
    """
    if entry_close is None or entry_close <= 0:
        return None
    if atr_pct_20d is None or atr_pct_20d <= 0:
        return None
    # Read from yaml when not explicitly overridden.
    if k_stop is None:
        k_stop = _yaml_k_stop()
    if k_target is None:
        k_target = _yaml_k_target()
    offset = _entry_offset_atr(setup_name, evidence)
    entry = entry_close * (1.0 + offset * atr_pct_20d / 100.0)
    stop = entry * (1.0 - k_stop * atr_pct_20d / 100.0)
    target = entry * (1.0 + k_target * atr_pct_20d / 100.0)
    if entry <= stop:
        return None
    risk = entry - stop
    reward = target - entry
    rr = reward / risk if risk > 0 else 0.0
    return RecommendedPrice(
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        rr=round(rr, 2),
        entry_offset_atr=offset,
        k_stop=k_stop,
        k_target=k_target,
    )


def merge_recommendations(prices: list[RecommendedPrice]) -> RecommendedPrice | None:
    """Combine multi-setup recommendations on the same stock — conservative side.

    entry  = max(entries)    — be picky on entry
    stop   = max(stops)      — tighter stop
    target = min(targets)    — sooner profit-take
    """
    prices = [p for p in prices if p is not None]
    if not prices:
        return None
    entry = max(p.entry for p in prices)
    stop = max(p.stop for p in prices)
    target = min(p.target for p in prices)
    if entry <= stop or entry >= target:
        # Inconsistent — fall back to most-conservative single setup
        return max(prices, key=lambda p: p.k_stop)
    risk = entry - stop
    rr = (target - entry) / risk if risk > 0 else 0.0
    return RecommendedPrice(
        entry=round(entry, 2),
        stop=round(stop, 2),
        target=round(target, 2),
        rr=round(rr, 2),
        entry_offset_atr=prices[0].entry_offset_atr,
        k_stop=prices[0].k_stop,
        k_target=prices[0].k_target,
    )
