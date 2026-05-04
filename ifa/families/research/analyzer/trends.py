"""M2.3a · Trend classifier — 5 levels: rapid_up / steady_up / flat / steady_down / rapid_down.

Algorithm (L1 stats, no ML):
  1. Take last N periods from a TimeSeries (default last 8).
  2. Compute OLS slope (least squares) on (i, value) pairs.
  3. Normalize slope to **% per period** by dividing by mean(|values|) (so the
     thresholds are scale-free across factors with different units).
  4. Classify by absolute slope_pct against bands from research_v2.2.yaml:
        |slope_pct| < flat_band_pct                  → flat
        flat_band  ≤ |slope_pct| < rapid_threshold   → steady_up / steady_down
        |slope_pct| ≥ rapid_threshold                → rapid_up / rapid_down

Why slope-on-values (not slope-on-YoY):
  YoY already removes seasonality; doing slope on YoY would dampen real momentum.
  We classify the *level* trajectory and let YoY tell a separate story.

Edge cases (return TrendLevel.UNKNOWN):
  · series too short (< min_periods)
  · all values None
  · mean(|values|) == 0 (can't normalize)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class TrendLevel(str, Enum):
    RAPID_UP = "rapid_up"
    STEADY_UP = "steady_up"
    FLAT = "flat"
    STEADY_DOWN = "steady_down"
    RAPID_DOWN = "rapid_down"
    UNKNOWN = "unknown"


# Display labels for reports
TREND_LABEL_ZH: dict[TrendLevel, str] = {
    TrendLevel.RAPID_UP: "急升",
    TrendLevel.STEADY_UP: "稳升",
    TrendLevel.FLAT: "持平",
    TrendLevel.STEADY_DOWN: "稳降",
    TrendLevel.RAPID_DOWN: "急降",
    TrendLevel.UNKNOWN: "数据不足",
}

TREND_ARROW: dict[TrendLevel, str] = {
    TrendLevel.RAPID_UP: "↑↑",
    TrendLevel.STEADY_UP: "↑",
    TrendLevel.FLAT: "→",
    TrendLevel.STEADY_DOWN: "↓",
    TrendLevel.RAPID_DOWN: "↓↓",
    TrendLevel.UNKNOWN: "?",
}


@dataclass
class TrendResult:
    level: TrendLevel
    slope_pct_per_period: float | None   # normalized slope (% of mean per period)
    n_periods: int                        # actual periods used (after dropping None)
    mean_abs_value: float | None          # used for normalization (audit)
    label_zh: str
    arrow: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level.value,
            "slope_pct_per_period": self.slope_pct_per_period,
            "n_periods": self.n_periods,
            "label_zh": self.label_zh,
            "arrow": self.arrow,
        }


def classify_trend(
    values: list[float | None],
    *,
    flat_band_pct: float = 5.0,
    rapid_threshold_pct: float = 15.0,
    min_periods: int = 4,
    last_n: int = 8,
) -> TrendResult:
    """Classify a numeric series into 5 trend levels.

    Args:
        values: oldest→newest values (Nones allowed and skipped).
        flat_band_pct: |slope_pct| below this → flat.
        rapid_threshold_pct: |slope_pct| above this → rapid_up/rapid_down.
        min_periods: minimum non-None points required.
        last_n: window size (use last N points only).

    Returns:
        TrendResult with the 5-level classification.
    """
    # Take last_n window first (so very old points don't dominate slope)
    window = values[-last_n:] if last_n > 0 else values
    pairs = [(i, v) for i, v in enumerate(window) if v is not None]

    if len(pairs) < min_periods:
        return _unknown(len(pairs))

    xs = [float(i) for i, _ in pairs]
    ys = [float(v) for _, v in pairs]

    mean_abs = sum(abs(y) for y in ys) / len(ys)
    if mean_abs == 0:
        return _unknown(len(pairs), mean_abs=0.0)

    slope = _ols_slope(xs, ys)
    if slope is None:
        return _unknown(len(pairs), mean_abs=mean_abs)

    slope_pct = (slope / mean_abs) * 100  # % of mean per 1 period step

    abs_pct = abs(slope_pct)
    sign = 1 if slope_pct > 0 else -1

    if abs_pct < flat_band_pct:
        level = TrendLevel.FLAT
    elif abs_pct >= rapid_threshold_pct:
        level = TrendLevel.RAPID_UP if sign > 0 else TrendLevel.RAPID_DOWN
    else:
        level = TrendLevel.STEADY_UP if sign > 0 else TrendLevel.STEADY_DOWN

    return TrendResult(
        level=level,
        slope_pct_per_period=slope_pct,
        n_periods=len(pairs),
        mean_abs_value=mean_abs,
        label_zh=TREND_LABEL_ZH[level],
        arrow=TREND_ARROW[level],
    )


def classify_trend_from_params(values: list[float | None], params: dict) -> TrendResult:
    """Convenience wrapper that reads thresholds from research_v2.2.yaml `trends:` block."""
    cfg = params.get("trends", {})
    return classify_trend(
        values,
        flat_band_pct=float(cfg.get("flat_band_pct", 5.0)),
        rapid_threshold_pct=float(cfg.get("rapid_threshold_pct", 15.0)),
        min_periods=int(cfg.get("min_periods", 4)),
    )


# ─── Internals ────────────────────────────────────────────────────────────────

def _unknown(n: int, mean_abs: float | None = None) -> TrendResult:
    return TrendResult(
        level=TrendLevel.UNKNOWN,
        slope_pct_per_period=None,
        n_periods=n,
        mean_abs_value=mean_abs,
        label_zh=TREND_LABEL_ZH[TrendLevel.UNKNOWN],
        arrow=TREND_ARROW[TrendLevel.UNKNOWN],
    )


def _ols_slope(xs: list[float], ys: list[float]) -> float | None:
    """Least-squares slope. Returns None if denominator is zero."""
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    if den == 0:
        return None
    return num / den
