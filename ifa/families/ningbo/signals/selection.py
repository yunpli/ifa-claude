"""Top-N selection across strategies with multi-strategy resonance.

Per-day flow:
    1. Each strategy emits its own candidate DataFrame with `confidence_score`
       and `signal_meta` columns.
    2. Apply ConfidenceScorer to get final per-candidate score.
    3. Group by ts_code; if a stock appears in N≥2 strategies, mark as 'multi'
       and boost score by RESONANCE_BOOST × (N - 1).
    4. Sort all unique candidates by final_score desc.
    5. Take top-N (default 5).

Output columns:
    ts_code, strategy ('sniper'|'treasure_basin'|'half_year_double'|'multi'),
    strategies_hit (list of all triggering strategies),
    confidence_score (post-boost),
    rec_signal_meta (combined dict)
"""
from __future__ import annotations

import pandas as pd

from ifa.families.ningbo.signals.confidence import ConfidenceScorer

DEFAULT_TOP_N = 5
DEFAULT_PER_STRATEGY_CAP = 2     # max picks from a single strategy in final top-N
                                 # (multi-strategy resonance hits are exempt)
RESONANCE_BOOST = 0.15           # boost confidence by this per extra strategy match
MAX_RESONANCE_BONUS = 0.30       # cap total bonus


def _apply_scorer(
    candidates_df: pd.DataFrame,
    strategy: str,
    scorer: ConfidenceScorer,
    context: dict | None = None,
) -> pd.DataFrame:
    """Run scorer on every candidate row, return df with 'final_score' column."""
    if candidates_df.empty:
        return pd.DataFrame()

    df = candidates_df.copy()
    df["strategy"] = strategy
    final_scores = []
    for _, row in df.iterrows():
        cand = {
            "ts_code": row["ts_code"],
            "strategy": strategy,
            "confidence_score": row.get("confidence_score", 0.0),
            "signal_meta": row.get("signal_meta", {}),
            "components": row.get("components", {}),
        }
        final_scores.append(scorer.score(cand, context or {}))
    df["final_score"] = final_scores
    return df


def select_top_n(
    candidates_by_strategy: dict[str, pd.DataFrame],
    scorer: ConfidenceScorer,
    *,
    top_n: int = DEFAULT_TOP_N,
    per_strategy_cap: int = DEFAULT_PER_STRATEGY_CAP,
    context: dict | None = None,
) -> pd.DataFrame:
    """Merge multi-strategy candidates and pick top-N by final score.

    Diversity rule: a single strategy contributes at most `per_strategy_cap`
    picks to the final top-N (multi-strategy resonance hits are EXEMPT —
    they count as their own bucket).

    Args:
        candidates_by_strategy: {'sniper': df, 'treasure_basin': df, 'half_year_double': df}
            Each df has at minimum: ts_code, confidence_score, signal_meta, components
        scorer: ConfidenceScorer instance (Heuristic or ML)
        top_n: max picks (default 5)
        per_strategy_cap: max picks from any single strategy (default 2);
            set to top_n to disable diversity constraint
        context: optional market context dict passed to scorer

    Returns:
        DataFrame ordered by confidence_score desc, columns:
            ts_code, strategy, strategies_hit (list),
            confidence_score (final, post-boost),
            scoring_mode (from scorer.mode),
            param_version (from scorer.version),
            rec_signal_meta (dict combining all strategies' meta + components)
    """
    # ── 1. Score each strategy's candidates ──────────────────────────────
    scored_dfs: list[pd.DataFrame] = []
    for strategy, df in candidates_by_strategy.items():
        if df is None or df.empty:
            continue
        scored = _apply_scorer(df, strategy, scorer, context)
        if not scored.empty:
            scored_dfs.append(scored)

    if not scored_dfs:
        return pd.DataFrame()

    all_scored = pd.concat(scored_dfs, ignore_index=True)

    # ── 2. Group by ts_code; merge multi-strategy hits ───────────────────
    merged_rows: list[dict] = []
    for ts_code, group in all_scored.groupby("ts_code"):
        strategies_hit = sorted(group["strategy"].unique().tolist())
        n_hits = len(strategies_hit)

        # Take max score across strategies as base; boost for resonance
        base_score = float(group["final_score"].max())
        boost = min(MAX_RESONANCE_BONUS, RESONANCE_BOOST * (n_hits - 1))
        final = float(min(1.0, base_score + boost))

        # Combined signal_meta: dict of {strategy: meta} + components per strategy
        combined_meta = {
            "by_strategy": {},
            "strategies_hit": strategies_hit,
            "n_hits": n_hits,
            "resonance_boost": boost,
            "best_individual_score": base_score,
        }
        for _, row in group.iterrows():
            s = row["strategy"]
            combined_meta["by_strategy"][s] = {
                "raw_score": float(row.get("confidence_score", 0.0)),
                "final_score": float(row.get("final_score", 0.0)),
                "signal_meta": row.get("signal_meta", {}),
                "components": row.get("components", {}),
            }

        merged_rows.append({
            "ts_code": ts_code,
            "strategy": "multi" if n_hits > 1 else strategies_hit[0],
            "strategies_hit": strategies_hit,
            "confidence_score": final,
            "scoring_mode": scorer.mode,
            "param_version": f"{scorer.mode}_{scorer.version}",
            "rec_signal_meta": combined_meta,
        })

    merged = pd.DataFrame(merged_rows)
    merged = merged.sort_values("confidence_score", ascending=False).reset_index(drop=True)

    # ── Diversity-constrained top-N ─────────────────────────────────────
    # Multi-strategy hits are exempt; otherwise cap picks per single strategy.
    picked: list[dict] = []
    per_strategy_count: dict[str, int] = {}
    for _, row in merged.iterrows():
        if len(picked) >= top_n:
            break
        s = row["strategy"]
        if s == "multi":
            picked.append(row.to_dict())
            continue
        cur = per_strategy_count.get(s, 0)
        if cur >= per_strategy_cap:
            continue
        per_strategy_count[s] = cur + 1
        picked.append(row.to_dict())

    # If diversity cap left us with fewer than top_n, fill remaining slots
    # by next-best regardless of strategy
    if len(picked) < top_n:
        already_picked = {r["ts_code"] for r in picked}
        for _, row in merged.iterrows():
            if len(picked) >= top_n:
                break
            if row["ts_code"] in already_picked:
                continue
            picked.append(row.to_dict())

    return pd.DataFrame(picked).reset_index(drop=True)
