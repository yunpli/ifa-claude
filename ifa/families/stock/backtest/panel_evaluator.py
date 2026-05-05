"""Vectorized panel evaluator — compute objective metrics for any candidate overlay.

Panel is built ONCE (replay_panel.build_replay_panel). Then for each candidate overlay,
we re-aggregate signal scores using the overlay's weight vectors. Per-overlay cost is
O(N × K) numpy matmul, ~1ms for 3000 rows × 80 signals × 3 horizons.

This is the core of the speedup that makes coarse-to-fine optimization tractable.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from ifa.families.stock.decision_layer import DEFAULT_KEYS

from .objectives import build_composite_objective
from .replay_panel import ALL_SIGNAL_KEYS, HORIZONS, PanelRow

KEY_INDEX: dict[str, int] = {k: i for i, k in enumerate(ALL_SIGNAL_KEYS)}


@dataclass(frozen=True)
class PanelMatrix:
    """Vectorized panel — builds once from list[PanelRow]."""
    ts_codes: np.ndarray              # [N]  string
    as_of_dates: np.ndarray           # [N]  date
    score: np.ndarray                 # [N, K] float
    active: np.ndarray                # [N, K] bool
    forward_5d_return: np.ndarray     # [N]  float (NaN if missing)
    forward_10d_return: np.ndarray
    forward_20d_return: np.ndarray
    forward_5d_target_first: np.ndarray   # [N]  bool (NaN→False, but mask via forward_5d_valid)
    forward_10d_target_first: np.ndarray
    forward_20d_target_first: np.ndarray
    forward_5d_stop_first: np.ndarray
    forward_10d_stop_first: np.ndarray
    forward_20d_stop_first: np.ndarray
    forward_5d_max_drawdown: np.ndarray
    forward_10d_max_drawdown: np.ndarray
    forward_20d_max_drawdown: np.ndarray
    forward_5d_mfe: np.ndarray
    forward_10d_mfe: np.ndarray
    forward_20d_mfe: np.ndarray
    forward_5d_valid: np.ndarray      # [N]  bool — has 5 forward bars
    forward_10d_valid: np.ndarray
    forward_20d_valid: np.ndarray
    regime: np.ndarray                # [N]  string

    @property
    def n_rows(self) -> int:
        return len(self.ts_codes)


def panel_matrix_from_rows(rows: Sequence[PanelRow]) -> PanelMatrix:
    n = len(rows)
    k = len(ALL_SIGNAL_KEYS)
    score = np.zeros((n, k), dtype=np.float32)
    active = np.zeros((n, k), dtype=np.bool_)
    f5_ret = np.full(n, np.nan, dtype=np.float64)
    f10_ret = np.full(n, np.nan, dtype=np.float64)
    f20_ret = np.full(n, np.nan, dtype=np.float64)
    f5_tgt = np.zeros(n, dtype=np.bool_)
    f10_tgt = np.zeros(n, dtype=np.bool_)
    f20_tgt = np.zeros(n, dtype=np.bool_)
    f5_stp = np.zeros(n, dtype=np.bool_)
    f10_stp = np.zeros(n, dtype=np.bool_)
    f20_stp = np.zeros(n, dtype=np.bool_)
    f5_dd = np.full(n, np.nan, dtype=np.float64)
    f10_dd = np.full(n, np.nan, dtype=np.float64)
    f20_dd = np.full(n, np.nan, dtype=np.float64)
    f5_mfe = np.full(n, np.nan, dtype=np.float64)
    f10_mfe = np.full(n, np.nan, dtype=np.float64)
    f20_mfe = np.full(n, np.nan, dtype=np.float64)
    f5_valid = np.zeros(n, dtype=np.bool_)
    f10_valid = np.zeros(n, dtype=np.bool_)
    f20_valid = np.zeros(n, dtype=np.bool_)
    ts_codes = np.empty(n, dtype=object)
    as_ofs = np.empty(n, dtype=object)
    regimes = np.empty(n, dtype=object)
    for i, r in enumerate(rows):
        ts_codes[i] = r.ts_code
        as_ofs[i] = r.as_of_date
        regimes[i] = r.regime or "unknown"
        for key, sig in r.signals.items():
            j = KEY_INDEX.get(key)
            if j is None:
                continue
            score[i, j] = float(sig.get("score") or 0.0)
            active[i, j] = sig.get("status") != "missing"
        if r.forward_5d_return is not None:
            f5_ret[i] = r.forward_5d_return; f5_valid[i] = True
            f5_tgt[i] = bool(r.forward_5d_target_first); f5_stp[i] = bool(r.forward_5d_stop_first)
            f5_dd[i] = r.forward_5d_max_drawdown if r.forward_5d_max_drawdown is not None else np.nan
            f5_mfe[i] = r.forward_5d_mfe if r.forward_5d_mfe is not None else np.nan
        if r.forward_10d_return is not None:
            f10_ret[i] = r.forward_10d_return; f10_valid[i] = True
            f10_tgt[i] = bool(r.forward_10d_target_first); f10_stp[i] = bool(r.forward_10d_stop_first)
            f10_dd[i] = r.forward_10d_max_drawdown if r.forward_10d_max_drawdown is not None else np.nan
            f10_mfe[i] = r.forward_10d_mfe if r.forward_10d_mfe is not None else np.nan
        if r.forward_20d_return is not None:
            f20_ret[i] = r.forward_20d_return; f20_valid[i] = True
            f20_tgt[i] = bool(r.forward_20d_target_first); f20_stp[i] = bool(r.forward_20d_stop_first)
            f20_dd[i] = r.forward_20d_max_drawdown if r.forward_20d_max_drawdown is not None else np.nan
            f20_mfe[i] = r.forward_20d_mfe if r.forward_20d_mfe is not None else np.nan
    return PanelMatrix(
        ts_codes=ts_codes, as_of_dates=as_ofs,
        score=score, active=active,
        forward_5d_return=f5_ret, forward_10d_return=f10_ret, forward_20d_return=f20_ret,
        forward_5d_target_first=f5_tgt, forward_10d_target_first=f10_tgt, forward_20d_target_first=f20_tgt,
        forward_5d_stop_first=f5_stp, forward_10d_stop_first=f10_stp, forward_20d_stop_first=f20_stp,
        forward_5d_max_drawdown=f5_dd, forward_10d_max_drawdown=f10_dd, forward_20d_max_drawdown=f20_dd,
        forward_5d_mfe=f5_mfe, forward_10d_mfe=f10_mfe, forward_20d_mfe=f20_mfe,
        forward_5d_valid=f5_valid, forward_10d_valid=f10_valid, forward_20d_valid=f20_valid,
        regime=regimes,
    )


# ──────────────────────────────────────────────────────────────────────────
# Decision score computation — vectorized
# ──────────────────────────────────────────────────────────────────────────


def compute_horizon_scores(
    panel: PanelMatrix,
    horizon: str,
    weights_override: Mapping[str, float],
    base_score: float,
    raw_edge_scale: float,
) -> np.ndarray:
    """Compute decision_<horizon>.score for every panel row, vectorized.

    Replicates `decision_layer._horizon_score` exactly:
        edge = sum_k(score[k] * w[k]) / sum_k(|w[k]|)  over active+valid keys
        score = clip(base + edge * scale, 0, 1)
    """
    keys = DEFAULT_KEYS.get(horizon, {})
    positive_keys = list(keys.get("positive", []))
    risk_keys = list(keys.get("risk", []))
    horizon_keys = positive_keys + risk_keys

    w_vec = np.zeros(len(ALL_SIGNAL_KEYS), dtype=np.float32)
    use_mask = np.zeros(len(ALL_SIGNAL_KEYS), dtype=np.bool_)
    risk_default = float(weights_override.get("risk_penalty_weight", 1.0))
    for key in horizon_keys:
        idx = KEY_INDEX.get(key)
        if idx is None:
            continue
        if key in positive_keys:
            w = float(weights_override.get(key, 1.0))
        else:
            w = float(weights_override.get(key, risk_default))
        w_vec[idx] = w
        use_mask[idx] = True

    eff = panel.active & use_mask                           # [N, K]
    weighted = (panel.score * w_vec) * eff                  # [N, K]
    raw = weighted.sum(axis=1)                              # [N]
    denom = (np.abs(w_vec) * eff).sum(axis=1)               # [N]
    edge = np.divide(raw, denom, out=np.zeros_like(raw, dtype=np.float64), where=denom > 0)
    score = base_score + edge * raw_edge_scale
    return np.clip(score, 0.0, 1.0)


# ──────────────────────────────────────────────────────────────────────────
# Per-horizon metrics
# ──────────────────────────────────────────────────────────────────────────


def _horizon_metrics(
    score_per_row: np.ndarray,
    valid: np.ndarray,
    forward_return: np.ndarray,        # in %
    target_first: np.ndarray,
    stop_first: np.ndarray,
    max_drawdown: np.ndarray,           # in %, negative
    mfe: np.ndarray,                    # in %, positive
    horizon: int,
    buy_threshold: float = 0.55,
) -> dict[str, Any]:
    """Compute per-horizon objective fields that score_prediction_objective consumes."""
    n = int(valid.sum())
    if n == 0:
        return {
            "sample_count": 0,
            "ic": 0.0, "rank_ic": 0.0,
            "positive_return_quality": 0.0, "target_first_quality": 0.0,
            "entry_fill_quality": 0.0, "reward_risk": 0.0, "risk_adjusted_return": 0.0,
            "drawdown_penalty": 1.0, "stop_first_penalty": 1.0,
            "liquidity_penalty": 0.0, "chase_failure_penalty": 0.0,
            "overheat_penalty": 0.0, "decay_penalty": 0.0, "auxiliary_penalty": 0.0,
            "avg_return": 0.0, "median_return": 0.0,
            "positive_return_rate": 0.0, "target_first_rate": 0.0, "stop_first_rate": 0.0,
            "buy_threshold_used": buy_threshold,
            "buy_signals": 0, "buy_hit_rate": 0.0,
        }
    s = score_per_row[valid]
    r = forward_return[valid]
    tf = target_first[valid]
    sf = stop_first[valid]
    dd = max_drawdown[valid]
    mfe_v = mfe[valid]

    ic = float(np.corrcoef(s, r)[0, 1]) if np.std(s) > 1e-9 and np.std(r) > 1e-9 else 0.0
    s_rank = np.argsort(np.argsort(s))
    r_rank = np.argsort(np.argsort(r))
    rank_ic = float(np.corrcoef(s_rank, r_rank)[0, 1]) if n > 1 else 0.0

    pos = (r > 0).astype(np.float64)
    positive_rate = float(pos.mean())
    target_first_rate = float(tf.mean())
    stop_first_rate = float(sf.mean())
    avg_ret = float(r.mean())
    median_ret = float(np.median(r))
    avg_dd = float(np.nanmean(np.abs(dd))) / 100.0 if not np.all(np.isnan(dd)) else 0.10
    avg_mfe = float(np.nanmean(mfe_v)) / 100.0 if not np.all(np.isnan(mfe_v)) else 0.05

    # Quality scaling — high score should correlate with positive outcomes
    sample_factor = min(1.0, np.sqrt(n / 60.0))
    return_scale = {5: 0.12, 10: 0.18, 20: 0.28}[horizon]
    drawdown_scale = {5: 0.10, 10: 0.14, 20: 0.20}[horizon]

    # Subset above buy threshold — these are the "buys" the model would suggest
    buy_mask = s >= buy_threshold
    buy_n = int(buy_mask.sum())
    buy_hit_rate = float(tf[buy_mask].mean()) if buy_n > 0 else 0.0
    buy_avg_return = float(r[buy_mask].mean()) if buy_n > 0 else 0.0

    # rank IC quality: maps rank IC of [-0.10, +0.20] → [0, 1]; below -0.10 = 0; above +0.20 = 1
    rank_ic_quality = float(np.clip((rank_ic + 0.10) / 0.30, 0.0, 1.0))
    positive_return_quality = float(np.clip(positive_rate * sample_factor, 0.0, 1.0))
    target_first_quality = float(np.clip(target_first_rate * sample_factor, 0.0, 1.0))
    entry_fill_quality = float(np.clip(min(1.0, buy_n / max(20, n / 5)) * sample_factor, 0.0, 1.0))
    reward_risk = float(np.clip(avg_mfe / max(0.01, avg_dd) / 3.0, 0.0, 1.0))
    risk_adjusted_return = float(np.clip((avg_ret / 100.0 + return_scale / 2.0) / (return_scale * 1.5), 0.0, 1.0))
    drawdown_penalty = float(np.clip(avg_dd / drawdown_scale, 0.0, 1.0))
    stop_first_penalty = float(np.clip(stop_first_rate, 0.0, 1.0))

    return {
        "sample_count": n,
        "ic": round(ic, 6),
        "rank_ic": round(rank_ic, 6),
        "positive_return_rate": round(positive_rate, 6),
        "target_first_rate": round(target_first_rate, 6),
        "stop_first_rate": round(stop_first_rate, 6),
        "avg_return": round(avg_ret / 100.0, 6),
        "median_return": round(median_ret / 100.0, 6),
        "avg_drawdown": round(avg_dd, 6),
        "avg_mfe": round(avg_mfe, 6),
        "mfe_mae_ratio": round(avg_mfe / max(0.01, avg_dd), 6),
        "rank_ic_quality": rank_ic_quality,
        "positive_return_quality": positive_return_quality,
        "target_first_quality": target_first_quality,
        "entry_fill_quality": entry_fill_quality,
        "reward_risk": reward_risk,
        "risk_adjusted_return": risk_adjusted_return,
        "drawdown_penalty": drawdown_penalty,
        "stop_first_penalty": stop_first_penalty,
        "liquidity_penalty": 0.0,
        "chase_failure_penalty": 0.0,
        "overheat_penalty": 0.0,
        "decay_penalty": 0.0,
        "auxiliary_penalty": 0.0,
        "buy_threshold_used": buy_threshold,
        "buy_signals": buy_n,
        "buy_hit_rate": round(buy_hit_rate, 6),
        "buy_avg_return_pct": round(buy_avg_return, 6),
    }


# ──────────────────────────────────────────────────────────────────────────
# Main entry — evaluate one overlay on the panel
# ──────────────────────────────────────────────────────────────────────────


def evaluate_overlay_on_panel(
    panel: PanelMatrix,
    overlay: Mapping[str, Any],
    base_params: Mapping[str, Any],
) -> dict[str, Any]:
    """Apply an overlay (parameter delta) to base_params, score every panel row, return objective metrics.

    `overlay` keys are dotted: `decision_layer.horizons.5d.weights.entry_fill_replay`,
    `decision_layer.horizons.5d.base_score`, `decision_layer.horizons.5d.raw_edge_scale`,
    `decision_layer.horizons.5d.thresholds.buy`.
    """
    eff_params = _apply_overlay(base_params, overlay)
    decision_cfg = (eff_params.get("decision_layer") or {}).get("horizons") or {}

    horizon_results = {}
    for h in HORIZONS:
        hkey = f"{h}d"
        cfg = decision_cfg.get(hkey, {}) or {}
        weights = cfg.get("weights") or {}
        base = float(cfg.get("base_score", 0.50))
        scale = float(cfg.get("raw_edge_scale", 0.50))
        thresholds = cfg.get("thresholds") or {}
        buy_thresh = float(thresholds.get("buy", 0.55 if h == 5 else (0.54 if h == 10 else 0.53)))

        score_per_row = compute_horizon_scores(panel, hkey, weights, base, scale)
        valid = getattr(panel, f"forward_{h}d_valid")
        metrics = _horizon_metrics(
            score_per_row=score_per_row,
            valid=valid,
            forward_return=getattr(panel, f"forward_{h}d_return"),
            target_first=getattr(panel, f"forward_{h}d_target_first"),
            stop_first=getattr(panel, f"forward_{h}d_stop_first"),
            max_drawdown=getattr(panel, f"forward_{h}d_max_drawdown"),
            mfe=getattr(panel, f"forward_{h}d_mfe"),
            horizon=h,
            buy_threshold=buy_thresh,
        )
        horizon_results[hkey] = metrics

    # Composite objective using existing scorer (uses horizon quality fields)
    cw = (eff_params.get("tuning", {}).get("objective", {}).get("composite_weights") or {})
    payload = build_composite_objective(
        objective_5d=horizon_results["5d"],
        objective_10d=horizon_results["10d"],
        objective_20d=horizon_results["20d"],
        calibration_quality=_calibration_from_ic(horizon_results),
        turnover_liquidity_penalty=0.0,
        strategy_decay_penalty=0.0,
        weights=cw,
    )
    payload["panel_rows_used"] = int(
        panel.forward_5d_valid.sum() + panel.forward_10d_valid.sum() + panel.forward_20d_valid.sum()
    ) // 3
    return payload


def _calibration_from_ic(horizon_results: dict[str, dict[str, Any]]) -> float:
    """Use mean rank-IC across horizons as a proxy for score-to-truth calibration."""
    ics = [
        max(0.0, float(horizon_results.get(f"{h}d", {}).get("rank_ic", 0.0)))
        for h in HORIZONS
    ]
    if not ics:
        return 0.0
    return float(np.clip(np.mean(ics) * 5.0, 0.0, 1.0))   # scale rank-IC ~0.20 → 1.0


# ──────────────────────────────────────────────────────────────────────────
# Per-signal IC analysis & warm-start priors (Phase 3-A)
# ──────────────────────────────────────────────────────────────────────────


def compute_signal_ic_priors(panel: PanelMatrix, *, min_samples: int = 30) -> dict[str, dict[str, float]]:
    """Per-(horizon, signal_key) rank IC vs forward returns — vectorized.

    Algorithm:
        For each horizon, rank-transform every signal column AND the forward return
        column simultaneously, then compute Pearson correlation column-by-column via
        a single covariance matrix product. ~50× faster than per-signal np.corrcoef.

    Active-mask handling: we set inactive cells to a sentinel rank that does not
    contaminate the correlation by re-ranking only the active subset per signal.
    Cost is still O(N·K·log N) (rank sort) but it's one numpy call.
    """
    out: dict[str, dict[str, float]] = {"5d": {}, "10d": {}, "20d": {}}
    for h in (5, 10, 20):
        valid = getattr(panel, f"forward_{h}d_valid")
        if int(valid.sum()) < min_samples:
            continue
        fwd = getattr(panel, f"forward_{h}d_return")[valid].astype(np.float64)
        scores = panel.score[valid]                          # [N, K]
        active = panel.active[valid]                         # [N, K]
        # Mask inactive cells with NaN so they don't bias the rank
        scores_masked = np.where(active, scores, np.nan)
        # Vectorized rank per column treating NaN consistently (NaN → max rank but ignored downstream)
        # nanrank: convert to ordinal rank only over non-NaN positions per column
        # Trick: subtract column min from active cells, leave NaN as-is; argsort handles NaN as +inf
        n = scores_masked.shape[0]
        order = np.argsort(scores_masked, axis=0)            # [N, K] indices in ascending order, NaN last
        ranks = np.empty_like(order, dtype=np.float64)
        # Build ranks using vectorized scatter
        cols_idx = np.broadcast_to(np.arange(scores_masked.shape[1]), order.shape)
        ranks[order, cols_idx] = np.arange(n)[:, None].astype(np.float64)
        # Wherever original was NaN, ranks are still 0..(n-1) but we mark them inactive
        ranks_masked = np.where(active, ranks, np.nan)

        fwd_rank = _ranks_with_average_ties(fwd)
        # Pearson on rank vectors equals Spearman; we compute per-column corr to fwd_rank
        f = fwd_rank - fwd_rank.mean()
        f_var = float(np.dot(f, f))
        if f_var <= 0:
            continue
        # For each signal column j: corr_j = sum((r_j - mean(r_j)) * f) / sqrt(var(r_j) * var(f))
        # using only rows where active[j] is True
        # Vectorized: replace NaN with column mean (centering then yields 0 contribution)
        with np.errstate(invalid="ignore"):
            col_means = np.nanmean(ranks_masked, axis=0)
        ranks_centered = np.where(active, ranks_masked - col_means, 0.0)  # NaN → 0 contribution
        cov = ranks_centered.T @ f                                          # [K]
        col_var = (ranks_centered ** 2).sum(axis=0)                          # [K]
        denom = np.sqrt(col_var * f_var)
        ic_vec = np.where(denom > 0, cov / np.where(denom > 0, denom, 1.0), 0.0)

        # Also enforce min_samples per signal (active count threshold)
        active_counts = active.sum(axis=0)
        for j, key in enumerate(ALL_SIGNAL_KEYS):
            if active_counts[j] >= min_samples and not math.isnan(ic_vec[j]):
                out[f"{h}d"][key] = float(ic_vec[j])
    return out


def _ranks_with_average_ties(values: np.ndarray) -> np.ndarray:
    """Plain rank (0..n-1) for a 1-D array; ties broken by argsort stability.

    Sufficient for Spearman approximation when ties are rare (forward returns are
    continuous floats so collisions are edge cases).
    """
    order = np.argsort(values)
    ranks = np.empty_like(order, dtype=np.float64)
    ranks[order] = np.arange(len(values), dtype=np.float64)
    return ranks


def ic_to_weight(ic: float, *, allow_negative: bool = True) -> float:
    """Map a per-signal rank IC to a recommended overlay weight.

    Logic (drawn from the hand-craft validation that lifted 10d -0.244 → +0.313):
      - IC < -0.20 (strongly inverted): negative weight (model learns to invert signal)
      - IC in [-0.20, -0.05]: zero-out (signal is noise or mildly inverted)
      - IC in [-0.05, +0.05]: 1.0 (default)
      - IC > +0.05: linear boost, capped at 1.80
    """
    if ic < -0.20 and allow_negative:
        return max(-1.50, -1.0 + 5.0 * (ic + 0.20))   # IC = -0.30 → -1.50
    if ic < -0.05:
        return 0.0
    if ic < 0.05:
        return 1.0
    return min(1.80, 1.0 + 4.0 * (ic - 0.05))


def build_ic_warmstart_overlay(
    ic_priors: Mapping[str, Mapping[str, float]],
    *,
    allow_negative: bool = True,
) -> dict[str, float]:
    """Convert per-(horizon, signal) IC into a search-center overlay."""
    from ifa.families.stock.decision_layer import DEFAULT_KEYS

    overlay: dict[str, float] = {}
    for h_label, ics in ic_priors.items():
        keys_for_horizon = (
            list(DEFAULT_KEYS.get(h_label, {}).get("positive", []))
            + list(DEFAULT_KEYS.get(h_label, {}).get("risk", []))
        )
        for key in keys_for_horizon:
            ic = ics.get(key)
            if ic is None:
                continue
            overlay[f"decision_layer.horizons.{h_label}.weights.{key}"] = round(
                ic_to_weight(ic, allow_negative=allow_negative), 4
            )
    return overlay


def negative_weight_bounds_for_panel(
    base_bounds: Mapping[str, tuple[float, float]],
    ic_priors: Mapping[str, Mapping[str, float]],
    *,
    threshold_invert: float = -0.20,
) -> dict[str, tuple[float, float]]:
    """Per-key bounds that open up a negative range only for strongly-inverted signals."""
    out = dict(base_bounds)
    for h_label, ics in ic_priors.items():
        for key, ic in ics.items():
            bound_key = f"decision_layer.horizons.{h_label}.weights.{key}"
            if bound_key in out and ic < threshold_invert:
                low, high = out[bound_key]
                out[bound_key] = (-1.50, high)
    return out


# ──────────────────────────────────────────────────────────────────────────


def _apply_overlay(base_params: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    """Apply dotted-key overlay to a deep-copied params dict."""
    import copy
    eff = copy.deepcopy(dict(base_params))
    for dotted, value in overlay.items():
        parts = dotted.split(".")
        cur = eff
        for part in parts[:-1]:
            if not isinstance(cur, dict):
                break
            cur = cur.setdefault(part, {})
        if isinstance(cur, dict):
            cur[parts[-1]] = value
    return eff
