"""Load TA family parameters from ta_v2.3.yaml (was 2.2.0; bumped 2026-05-04 for M10 P1).

Cached process-wide; call `reload_params()` to force a fresh read (for
tests or after editing the file in a long-running process).

Schema reference (the YAML itself stays comment-free because the tuning
script `scripts/ta_param_tune.py` rewrites it via `yaml.safe_dump` which
strips comments — keep documentation here):

regime:
  vetos:                                    # if any veto fires, detector returns 0
    trend_continuation:
      udr_min                  float        # require up_count ≥ this × down_count
      n_limit_down_max         int          # too many limit-downs → not trend
      n_down_max               int          # too many losers → not trend
      defer_to_early_lu_min    int          # if lu ≥ this AND up ≥ next, defer
      defer_to_early_up_min    int          # to early_risk_on detector
    range_bound:
      n_up_max                 int          # too many gainers → defer to early_risk_on
      n_limit_up_max           int          # too many limit-ups → defer to early_risk_on
      n_down_max               int          # too many losers → defer to cooldown
      n_limit_down_max         int          # too many limit-downs → defer to cooldown
      cooldown_path_udr_max    float        # combined with cooldown_path_n_down_min
      cooldown_path_n_down_min int          # below udr × above n_down → defer to cooldown
  thresholds:                               # internal score-component breakpoints
    early_risk_on:
      absolute_lu_min          int          # absolute breadth-positive path entry
      absolute_up_min          int
      udr_strong_min           float        # extra-strong breadth bonus threshold
    cooldown:
      n_limit_down_strong      int          # large limit-down count
      n_limit_down_weak        int          # moderate
      udr_strong               float        # very weak breadth (down >> up)
      udr_med                  float
      udr_weak                 float
      n_down_min               int          # baseline "broad weakness"
    distribution_risk:
      n_limit_down_strong      int          # crash-day floor
      n_down_strong            int
      n_limit_down_with_down   int          # combined with n_down_strong
      ld_vs_lu_ratio           float        # limit-downs > limit-ups × ratio
    range_bound:
      vol_pct_max              float        # 20-day SSE volatility ceiling
      ma_diff_pct_max          float        # 5MA / 20MA tightness for "intertwined"

ranker:
  decay:
    observation_floor_pp       float        # decay < this → OBSERVATION_ONLY
    suspension_floor_pp        float        # decay < this → SUSPENDED
  winrate:
    target_pct                 float        # winrate at this gives full score
    floor_ratio                float        # never discount below this × raw
  diversity:
    top_cap_per_setup          int          # max picks of same setup_name in top
  regime_boost                 float        # +score when regime in suitable_regimes

To tune: `uv run python scripts/ta_param_tune.py [--start ... --end ...]`.
Auto-applies any change with Δ ≥ +1pp on oracle agreement; backup goes
to `tmp/ta_v2.2_before_<ts>.yaml`. To revert, `cp` that backup back.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_PARAMS_PATH = Path(__file__).parent / "ta_v2.3.yaml"


def _load_raw() -> dict[str, Any]:
    return yaml.safe_load(_PARAMS_PATH.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_params() -> dict[str, Any]:
    """Returns the parsed YAML as a nested dict. Cached. Treat as read-only."""
    return _load_raw()


def reload_params() -> dict[str, Any]:
    """Bust cache and re-read from disk. Returns the fresh params."""
    load_params.cache_clear()
    return load_params()
