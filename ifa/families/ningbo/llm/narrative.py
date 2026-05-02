"""LLM narrative augmentation for new recommendations.

Generates 80-120 字 explanation per recommendation. Pure read-only
augmentation — does NOT modify rules or scores.

Prompt design (Phase 1.12, Opus):
    - Sentence 1: state the triggered pattern
    - Sentence 2: explain why this is worth chasing (sector / fund flow)
    - Sentence 3: specific risk hint (e.g. "若明日开盘跌破 X 元立即止损")

Constraints:
    - No "建议" / "投资" / "推荐买入" sensitive words
    - No return promises
    - <= 120 字
    - For ML-mode recs: include SHAP top-3 contributing features (Phase 3.8)

Phase 1.12 — to be implemented (Opus).
"""
from __future__ import annotations

from sqlalchemy import Engine


def generate_narrative(
    engine: Engine,
    rec: dict,
    market_context: dict,
) -> str:
    """Generate one narrative paragraph for a single recommendation.

    Args:
        rec: recommendation dict (ts_code, strategy, scoring_mode, signal_meta, ...)
        market_context: today's market regime / sector flow snapshot

    Returns:
        80-120 char narrative string
    """
    raise NotImplementedError("Phase 1.12 — Opus")
