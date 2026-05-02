"""Sector cycle phase transition matrix with Bayesian per-sector adjustment.

Computes empirical transition probabilities between the 7 cycle phases:
  冷, 点火, 确认, 扩散, 高潮, 分歧, 退潮

Architecture:
  · Global empirical matrix:  count(from → to) across all sectors over a
    lookback window.  Acts as the prior.
  · Per-sector counts:        count(from → to) for one specific sector.
  · Bayesian smoothing:       Dirichlet posterior with concentration α₀
    (default 5.0 pseudo-observations).  Sectors with little history lean
    on the global prior; data-rich sectors approach their own empirical.
  · State-machine guard:      illegal transitions (per cycle.py
    ALLOWED_TRANSITIONS) get probability 0 even if data leaks.
  · Optional LLM nudge:       a ``llm_adjuster`` callback can perturb each
    phase probability by up to ±10% multiplicatively, then renormalize.

Usage::

    model = TransitionMatrixModel.fit(engine, lookback_days=180)
    dist = model.predict(
        sector_code="801080.SI",
        sector_source="sw_l2",
        current_phase="确认",
    )
    # dist == {'确认': 0.42, '扩散': 0.31, '分歧': 0.18, '退潮': 0.09, ...}

The matrix is *sector-source-agnostic* by default — transitions are pooled
across all sources to maximize the global prior's stability.  Per-sector
counts are keyed by ``(sector_code, sector_source)`` so a sector tracked in
both 'sw_l2' and 'dc' gets two independent profiles.
"""
from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from .factors.cycle import ALLOWED_TRANSITIONS

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"

# 7 productive phases.  '未识别' is bootstrap-only and excluded from the matrix.
PHASES: list[str] = ["冷", "点火", "确认", "扩散", "高潮", "分歧", "退潮"]
PHASE_SET: set[str] = set(PHASES)

# Tunables ────────────────────────────────────────────────────────────────────
DEFAULT_ALPHA0 = 5.0      # global-prior concentration in pseudo-observations
DEFAULT_LOOKBACK_DAYS = 180
LLM_NUDGE_MAX = 0.10      # ±10% multiplicative cap per phase


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class TransitionPrediction:
    """Output of TransitionMatrixModel.predict()."""
    sector_code: str
    sector_source: str
    current_phase: str
    distribution: dict[str, float]    # phase → probability (sums to ≈1)
    method: str                       # 'empirical' | 'bayes' | 'bayes+llm' | 'fallback'
    n_observations_global: int        # # transitions FROM current_phase in global
    n_observations_sector: int        # # transitions FROM current_phase for this sector
    next_phase_argmax: str
    evidence: dict[str, Any] = field(default_factory=dict)


# ── Counting helpers ──────────────────────────────────────────────────────────

def _empty_row() -> dict[str, int]:
    return {p: 0 for p in PHASES}


def _empty_matrix() -> dict[str, dict[str, int]]:
    return {p: _empty_row() for p in PHASES}


def _load_phase_pairs(
    engine: Engine,
    *,
    lookback_days: int,
    end_date: dt.date | None = None,
) -> list[tuple[str, str, dt.date, str]]:
    """Pull (sector_code, sector_source, trade_date, cycle_phase) ordered by
    (sector_code, sector_source, trade_date).

    The caller turns consecutive same-sector rows into (from, to) transition
    pairs.  We pool across sources by default — the per-sector key is
    ``(sector_code, sector_source)`` so each profile stays distinct.
    """
    end = end_date or dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    sql = text(f"""
        SELECT sector_code, sector_source, trade_date, cycle_phase
        FROM {SCHEMA}.sector_state_daily
        WHERE trade_date >= :start AND trade_date <= :end
          AND cycle_phase IS NOT NULL
          AND cycle_phase <> '未识别'
        ORDER BY sector_code, sector_source, trade_date
    """)
    with engine.connect() as conn:
        return [
            (r[0], r[1], r[2], r[3])
            for r in conn.execute(sql, {"start": start, "end": end}).all()
        ]


def _build_counts(
    rows: list[tuple[str, str, dt.date, str]],
) -> tuple[dict[str, dict[str, int]], dict[tuple[str, str], dict[str, dict[str, int]]]]:
    """Walk sorted rows; emit one transition each time the same sector appears
    on consecutive trade dates.  Returns (global_counts, sector_counts)."""
    global_counts = _empty_matrix()
    sector_counts: dict[tuple[str, str], dict[str, dict[str, int]]] = {}

    prev_key: tuple[str, str] | None = None
    prev_phase: str | None = None
    for code, src, _td, phase in rows:
        key = (code, src)
        if (
            prev_key == key
            and prev_phase in PHASE_SET
            and phase in PHASE_SET
        ):
            # Respect state machine: silently drop illegal transitions even if
            # they snuck into the data (shouldn't happen post cycle.py refactor,
            # but defensive).
            allowed = ALLOWED_TRANSITIONS.get(prev_phase, set())
            if phase in allowed:
                global_counts[prev_phase][phase] += 1
                sector_counts.setdefault(key, _empty_matrix())[prev_phase][phase] += 1
        prev_key = key
        prev_phase = phase
    return global_counts, sector_counts


# ── Distribution math ─────────────────────────────────────────────────────────

def _row_to_prob(row: dict[str, int], legal: set[str]) -> dict[str, float]:
    """Normalise a count row to a probability distribution.  Mass restricted
    to the ``legal`` next-phase set (state-machine guard).  Returns uniform-
    over-legal if total count is zero."""
    legal_total = sum(row[p] for p in legal) if legal else 0
    if legal_total == 0:
        # Uniform over legal moves — no information yet
        if not legal:
            return {p: 0.0 for p in PHASES}
        u = 1.0 / len(legal)
        return {p: (u if p in legal else 0.0) for p in PHASES}
    return {p: ((row[p] / legal_total) if p in legal else 0.0) for p in PHASES}


def _bayesian_blend(
    *,
    global_row: dict[str, int],
    sector_row: dict[str, int] | None,
    legal: set[str],
    alpha0: float,
) -> dict[str, float]:
    """Dirichlet posterior over the legal next-phase set.

    Prior:    α₀ * P_global(•|legal)        — α₀ pseudo-observations of global pattern
    Likelihood: counts in sector_row over legal moves
    Posterior: α_post(p) = α₀ * P_global(p|legal) + sector_count(p)
    Returns:  α_post / sum α_post
    """
    if not legal:
        return {p: 0.0 for p in PHASES}

    global_dist = _row_to_prob(global_row, legal)
    alpha_post: dict[str, float] = {}
    for p in PHASES:
        if p not in legal:
            alpha_post[p] = 0.0
            continue
        prior_mass = alpha0 * global_dist[p]
        observed = sector_row[p] if sector_row else 0
        alpha_post[p] = prior_mass + observed

    total = sum(alpha_post.values())
    if total <= 0:
        # Defensive: shouldn't trigger because legal is non-empty and α₀ > 0
        u = 1.0 / len(legal)
        return {p: (u if p in legal else 0.0) for p in PHASES}
    return {p: (alpha_post[p] / total) for p in PHASES}


def _apply_llm_nudge(
    distribution: dict[str, float],
    deltas: dict[str, float],
    *,
    cap: float = LLM_NUDGE_MAX,
) -> dict[str, float]:
    """Multiplicatively nudge each prob by (1 + clip(delta, -cap, +cap)) and
    renormalize so probabilities sum to 1.  Phases not in ``deltas`` keep
    their original mass (delta=0).  Phases with prob=0 stay at 0 (hard
    state-machine guard wins over LLM)."""
    if not deltas:
        return dict(distribution)
    nudged: dict[str, float] = {}
    for p, prob in distribution.items():
        if prob == 0.0:
            nudged[p] = 0.0
            continue
        d = float(deltas.get(p, 0.0))
        d = max(-cap, min(cap, d))
        nudged[p] = max(prob * (1.0 + d), 0.0)

    total = sum(nudged.values())
    if total <= 0:
        return dict(distribution)
    return {p: v / total for p, v in nudged.items()}


# ── Public model ──────────────────────────────────────────────────────────────

@dataclass
class TransitionMatrixModel:
    """Empirical + Bayesian transition matrix over the 7 cycle phases.

    Construct via :py:meth:`fit`; reuse for many predictions in one report
    run to amortise the SQL load.
    """
    global_counts: dict[str, dict[str, int]]
    sector_counts: dict[tuple[str, str], dict[str, dict[str, int]]]
    alpha0: float = DEFAULT_ALPHA0
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    fitted_at: dt.datetime = field(default_factory=dt.datetime.utcnow)

    # ── Construction ──────────────────────────────────────────────────────
    @classmethod
    def fit(
        cls,
        engine: Engine,
        *,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        end_date: dt.date | None = None,
        alpha0: float = DEFAULT_ALPHA0,
    ) -> "TransitionMatrixModel":
        rows = _load_phase_pairs(engine, lookback_days=lookback_days, end_date=end_date)
        gc, sc = _build_counts(rows)
        log.info(
            "[transition] fit: %d phase rows, %d sector profiles, α₀=%.2f, lookback=%dd",
            len(rows), len(sc), alpha0, lookback_days,
        )
        return cls(
            global_counts=gc,
            sector_counts=sc,
            alpha0=alpha0,
            lookback_days=lookback_days,
        )

    # ── Inference ─────────────────────────────────────────────────────────
    def predict(
        self,
        *,
        sector_code: str,
        sector_source: str,
        current_phase: str,
        llm_adjuster: Callable[[str, str, dict[str, float]], dict[str, float]] | None = None,
    ) -> TransitionPrediction:
        """Posterior distribution P(next_phase | current_phase, sector).

        ``llm_adjuster``, if provided, is called with
        ``(sector_code, current_phase, bayes_distribution)`` and must return a
        dict of phase → delta.  Each delta is clipped to ±10% (multiplicative).
        Probabilities pinned to 0 by the state machine remain 0.
        """
        # Bootstrap: '未识别' or unknown → uniform over legal first moves of '冷'
        if current_phase not in PHASE_SET:
            legal = ALLOWED_TRANSITIONS.get("冷", set()) & PHASE_SET
            dist = {p: (1.0 / len(legal)) if p in legal else 0.0 for p in PHASES}
            return TransitionPrediction(
                sector_code=sector_code,
                sector_source=sector_source,
                current_phase=current_phase,
                distribution=dist,
                method="fallback",
                n_observations_global=0,
                n_observations_sector=0,
                next_phase_argmax=max(dist, key=dist.get),
                evidence={"reason": f"current_phase={current_phase!r} not in PHASES"},
            )

        legal = ALLOWED_TRANSITIONS.get(current_phase, set()) & PHASE_SET
        global_row = self.global_counts[current_phase]
        sec_row = self.sector_counts.get((sector_code, sector_source), {}).get(current_phase)
        n_global = sum(global_row[p] for p in legal)
        n_sector = sum(sec_row[p] for p in legal) if sec_row else 0

        bayes_dist = _bayesian_blend(
            global_row=global_row,
            sector_row=sec_row,
            legal=legal,
            alpha0=self.alpha0,
        )

        method = "empirical" if n_global > 0 and n_sector == 0 else "bayes"
        final_dist = bayes_dist
        evidence: dict[str, Any] = {
            "alpha0": self.alpha0,
            "legal_transitions": sorted(legal),
            "global_counts_from_current": dict(global_row),
        }
        if sec_row:
            evidence["sector_counts_from_current"] = dict(sec_row)

        if llm_adjuster is not None:
            try:
                deltas = llm_adjuster(sector_code, current_phase, dict(bayes_dist)) or {}
                final_dist = _apply_llm_nudge(bayes_dist, deltas, cap=LLM_NUDGE_MAX)
                method = "bayes+llm"
                evidence["llm_deltas"] = {k: round(float(v), 4) for k, v in deltas.items()}
            except Exception as exc:  # noqa: BLE001
                log.warning("[transition] llm_adjuster failed: %s", exc)
                evidence["llm_error"] = f"{type(exc).__name__}: {exc}"

        return TransitionPrediction(
            sector_code=sector_code,
            sector_source=sector_source,
            current_phase=current_phase,
            distribution=final_dist,
            method=method,
            n_observations_global=n_global,
            n_observations_sector=n_sector,
            next_phase_argmax=max(final_dist, key=final_dist.get),
            evidence=evidence,
        )

    # ── Inspection ────────────────────────────────────────────────────────
    def empirical_matrix(self) -> dict[str, dict[str, float]]:
        """Return the global empirical 7×7 transition matrix as nested dict
        of probabilities (rows sum to 1 over legal moves; illegal cells = 0).
        """
        out: dict[str, dict[str, float]] = {}
        for from_p in PHASES:
            legal = ALLOWED_TRANSITIONS.get(from_p, set()) & PHASE_SET
            out[from_p] = _row_to_prob(self.global_counts[from_p], legal)
        return out


# ── Convenience wrapper ───────────────────────────────────────────────────────

def predict_next_phase(
    engine: Engine,
    *,
    sector_code: str,
    sector_source: str,
    current_phase: str,
    trade_date: dt.date,
    model: TransitionMatrixModel | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    alpha0: float = DEFAULT_ALPHA0,
    llm_adjuster: Callable[[str, str, dict[str, float]], dict[str, float]] | None = None,
) -> dict[str, float]:
    """One-shot prediction.  Fits a fresh model if ``model`` is not provided
    (slow — prefer reusing a single model across many predictions in a run).

    The ``trade_date`` argument scopes the lookback for fresh fits and also
    serves as the asof for the returned distribution.
    """
    if model is None:
        model = TransitionMatrixModel.fit(
            engine,
            lookback_days=lookback_days,
            end_date=trade_date,
            alpha0=alpha0,
        )
    pred = model.predict(
        sector_code=sector_code,
        sector_source=sector_source,
        current_phase=current_phase,
        llm_adjuster=llm_adjuster,
    )
    return pred.distribution
