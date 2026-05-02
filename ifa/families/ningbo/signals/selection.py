"""Top-N selection across strategies.

Daily flow:
    1. Each strategy emits candidate list with confidence_score
    2. Merge candidates across strategies (multi-strategy resonance:
       same ts_code from multiple strategies → consolidate, boost score)
    3. Sort by confidence_score
    4. Return top 5 (or N as configured)

Phase 1.8 — to be implemented (Sonnet).
"""
from __future__ import annotations

import pandas as pd

DEFAULT_TOP_N = 5
RESONANCE_BOOST = 0.15  # add to confidence per extra strategy hit


def select_top_n(
    candidates_by_strategy: dict[str, pd.DataFrame],
    top_n: int = DEFAULT_TOP_N,
) -> pd.DataFrame:
    """Merge multi-strategy candidates and pick top-N by confidence.

    Args:
        candidates_by_strategy: {'sniper': df, 'treasure_basin': df, ...}
            Each df has columns: ts_code, confidence_score, signal_meta
        top_n: max picks per scoring_mode (default 5)

    Returns:
        DataFrame with: ts_code, strategy ('multi' if resonance), strategies (list),
        confidence_score, signal_meta_combined
    """
    raise NotImplementedError("Phase 1.8")
