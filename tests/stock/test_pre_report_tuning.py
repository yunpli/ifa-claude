from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

import pandas as pd

from ifa.families.stock.backtest import (
    PredictionObjectiveInputs,
    continuous_overlay_bounds,
    find_latest_tuning_artifact,
    fit_global_preset,
    fit_pre_report_overlay,
    plan_global_preset_refresh,
    plan_pre_report_tuning,
    prepare_report_params,
    read_tuning_artifact,
    score_prediction_objective,
    write_tuning_artifact,
)
from ifa.families.stock.context import StockEdgeRequest
from ifa.families.stock.params import apply_param_overlay, load_params, params_hash
from ifa.families.stock.data.tushare_backfill import BackfillResult
from ifa.families.stock.backtest.tuning_artifact import TuningArtifact
from ifa.families.stock.strategies import IMPLEMENTED_STRATEGIES, by_category, future_count, implemented_count


def _bars(n: int) -> pd.DataFrame:
    start = dt.date(2024, 1, 1)
    return pd.DataFrame(
        {
            "trade_date": [start + dt.timedelta(days=i) for i in range(n)],
            "open": [10 + i * 0.01 for i in range(n)],
            "high": [10.2 + i * 0.01 for i in range(n)],
            "low": [9.8 + i * 0.01 for i in range(n)],
            "close": [10 + i * 0.01 for i in range(n)],
        }
    )


def test_strategy_catalog_counts_current_matrix():
    assert implemented_count() == 85
    assert future_count() == 0
    cats = by_category()
    assert len(cats["ta"]) == 11
    assert len(cats["statistical"]) >= 19
    assert len(cats["ml"]) >= 6
    assert len(cats["smartmoney"]) >= 10
    assert len(cats["llm"]) >= 4
    assert len(cats["execution"]) >= 5
    assert len(cats["dl"]) >= 4
    assert any(item.key == "kronos_pattern" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "analog_kronos_nearest_neighbors" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "kronos_path_cluster_transition" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "peer_research_auto_trigger" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "llm_regime_cache" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "vwap_reclaim_execution" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "quantile_return_forecaster" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "isotonic_score_calibrator" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "right_tail_meta_gbm" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "temporal_fusion_sequence_ranker" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "target_stop_survival_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "stop_loss_hazard_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "gap_risk_open_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "multi_horizon_target_classifier" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "target_ladder_probability_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "path_shape_mixture_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "mfe_mae_surface_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "regime_adaptive_weight_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "peer_financial_alpha_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "limit_up_event_path_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "position_sizing_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "forward_entry_timing_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "entry_price_surface_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "pullback_rebound_classifier" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "squeeze_breakout_classifier" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "model_stack_blender" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "event_catalyst_llm" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "fundamental_contradiction_llm" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "fundamental_price_dislocation_model" for item in IMPLEMENTED_STRATEGIES)
    assert any(item.key == "scenario_tree_llm" for item in IMPLEMENTED_STRATEGIES)


def test_pre_report_tuning_runs_when_artifact_is_stale_or_missing():
    plan = plan_pre_report_tuning(
        _bars(420),
        ts_code="300042.SZ",
        as_of_trade_date=dt.date(2025, 2, 23),
    )

    assert plan.should_tune is True
    assert plan.history_rows >= 360
    assert plan.output_namespace.endswith("300042_SZ/20250223")


def test_global_preset_refresh_policy_reuses_recent_weekend_artifact():
    recent = plan_global_preset_refresh(
        as_of_date=dt.date(2026, 5, 5),
        last_trained_at=dt.datetime(2026, 5, 2),
    )
    stale = plan_global_preset_refresh(
        as_of_date=dt.date(2026, 5, 12),
        last_trained_at=dt.datetime(2026, 5, 2),
    )

    assert recent.should_refresh is False
    assert recent.universe == "top_liquidity_500"
    assert stale.should_refresh is True


def test_pre_report_tuning_reuses_recent_artifact():
    plan = plan_pre_report_tuning(
        _bars(420),
        ts_code="300042.SZ",
        as_of_trade_date=dt.date(2025, 2, 23),
        last_tuned_at=dt.datetime(2025, 2, 18),
    )

    assert plan.should_tune is False
    assert "复用" in plan.reason


def test_pre_report_tuning_clamps_future_artifact_age_for_backdated_runs():
    plan = plan_pre_report_tuning(
        _bars(420),
        ts_code="300042.SZ",
        as_of_trade_date=dt.date(2026, 4, 30),
        last_tuned_at=dt.datetime(2026, 5, 5, 7, 6, tzinfo=dt.UTC),
        reference_datetime=dt.datetime(2026, 5, 5, 7, 1, tzinfo=dt.UTC),
    )

    assert plan.should_tune is False
    assert "最近 0 天" in plan.reason
    assert "-6" not in plan.reason


def test_pre_report_tuning_skips_when_history_is_short():
    plan = plan_pre_report_tuning(
        _bars(80),
        ts_code="300042.SZ",
        as_of_trade_date=dt.date(2024, 3, 20),
    )

    assert plan.should_tune is False
    assert "低于" in plan.reason


def test_prediction_objective_rewards_prediction_quality_not_only_return():
    strong = PredictionObjectiveInputs(
        hit_target_40d_quality=0.70,
        expected_return_40d=0.55,
        entry_fill_quality=0.80,
        reward_risk=0.65,
        calibration_quality=0.75,
        expected_drawdown=0.20,
        stop_first_rate=0.15,
        turnover_liquidity_penalty=0.20,
    )
    fragile = PredictionObjectiveInputs(
        hit_target_40d_quality=0.55,
        expected_return_40d=0.90,
        entry_fill_quality=0.35,
        reward_risk=0.35,
        calibration_quality=0.20,
        expected_drawdown=0.80,
        stop_first_rate=0.70,
        turnover_liquidity_penalty=0.60,
    )

    assert score_prediction_objective(strong) > score_prediction_objective(fragile)


def test_continuous_overlay_bounds_cover_core_price_and_weight_params():
    bounds = continuous_overlay_bounds()

    assert bounds["aggregate.buy_threshold"][0] < bounds["aggregate.buy_threshold"][1]
    assert "cluster_weights.model_ensemble" in bounds
    assert "cluster_weights.risk_warning" in bounds
    assert "signal_weights.trend_quality_r2" in bounds
    assert "signal_weights.vwap_reclaim_execution" in bounds
    assert "signal_weights.quantile_return_forecaster" in bounds
    assert "signal_weights.entry_fill_classifier" in bounds
    assert "signal_weights.isotonic_score_calibrator" in bounds
    assert "signal_weights.right_tail_meta_gbm" in bounds
    assert "signal_weights.temporal_fusion_sequence_ranker" in bounds
    assert "signal_weights.stop_loss_hazard_model" in bounds
    assert "signal_weights.gap_risk_open_model" in bounds
    assert "signal_weights.target_ladder_probability_model" in bounds
    assert "signal_weights.path_shape_mixture_model" in bounds
    assert "signal_weights.mfe_mae_surface_model" in bounds
    assert "signal_weights.regime_adaptive_weight_model" in bounds
    assert "signal_weights.peer_financial_alpha_model" in bounds
    assert "signal_weights.limit_up_event_path_model" in bounds
    assert "signal_weights.position_sizing_model" in bounds
    assert "signal_weights.entry_price_surface_model" in bounds
    assert "signal_weights.analog_kronos_nearest_neighbors" in bounds
    assert "signal_weights.kronos_path_cluster_transition" in bounds
    assert "signal_weights.peer_research_auto_trigger" in bounds
    assert "signal_weights.stop_first_classifier" in bounds
    assert "signal_weights.hierarchical_sector_shrinkage" in bounds
    assert "signal_weights.auction_imbalance_proxy" in bounds
    assert "signal_weights.northbound_regime" in bounds
    assert "signal_weights.market_margin_impulse" in bounds
    assert "signal_weights.block_trade_pressure" in bounds
    assert "signal_weights.event_catalyst_llm" in bounds
    assert "signal_weights.fundamental_contradiction_llm" in bounds
    assert "risk.right_tail_target_pct" in bounds
    assert "risk.max_entry_distance_from_support_pct" in bounds
    assert "risk.max_stop_distance_pct" in bounds
    assert all(low < high for low, high in bounds.values())


def test_fit_pre_report_overlay_writes_reusable_artifact(tmp_path):
    artifact = fit_pre_report_overlay(
        _bars(460),
        ts_code="300042.SZ",
        as_of_trade_date=dt.date(2025, 4, 4),
        base_params=load_params(),
        max_candidates=8,
    )
    path = write_tuning_artifact(artifact, root=tmp_path)
    loaded = read_tuning_artifact(path)
    latest = find_latest_tuning_artifact(ts_code="300042.SZ", root=tmp_path)

    assert loaded.kind == "pre_report_overlay"
    assert loaded.candidate_count == 8
    assert "fill_rate_5d" in loaded.metrics
    assert latest is not None
    assert latest.objective_score == loaded.objective_score


def test_fit_global_preset_aggregates_universe():
    params = load_params()
    artifact = fit_global_preset(
        {"300042.SZ": _bars(460), "002888.SZ": _bars(470)},
        as_of_date=dt.date(2025, 4, 14),
        base_params=params,
        max_candidates=6,
    )

    assert artifact.kind == "global_preset"
    assert artifact.metrics["stock_count"] == 2
    assert artifact.candidate_count == 6


def test_param_overlay_updates_nested_strategy_and_risk_values():
    params = load_params()
    overlaid = apply_param_overlay(
        params,
        {
            "aggregate.buy_threshold": 0.61,
            "cluster_weights.model_ensemble": 1.25,
            "risk.right_tail_target_pct": 35.0,
        },
    )

    assert overlaid["strategy_matrix"]["aggregate"]["buy_threshold"] == 0.61
    assert overlaid["strategy_matrix"]["cluster_weights"]["model_ensemble"] == 1.25
    assert overlaid["risk"]["right_tail_target_pct"] == 35.0
    assert params_hash(overlaid) != params_hash(params)


def test_prepare_report_params_is_noop_when_tuning_disabled():
    params = load_params()
    params = {**params, "tuning": {**params["tuning"], "enabled": False}}
    result = prepare_report_params(
        StockEdgeRequest(ts_code="300042.SZ"),
        engine=None,  # type: ignore[arg-type]
        base_params=params,
    )

    assert result.status == "disabled"
    assert params_hash(result.params) == params_hash(params)


def test_prepare_report_params_backfills_short_history_before_overlay(monkeypatch):
    from ifa.families.stock.backtest import report_runtime

    params = load_params()
    params = {
        **params,
        "tuning": {
            **params["tuning"],
            "pre_report_overlay": {
                **params["tuning"]["pre_report_overlay"],
                "min_history_rows": 360,
                "max_history_rows": 420,
                "max_candidates": 4,
            },
        },
    }
    calls = {"load": 0, "backfill": 0}

    def fake_build_context(request, **_kwargs):
        return SimpleNamespace(as_of=SimpleNamespace(as_of_trade_date=dt.date(2026, 4, 30)))

    def fake_load_daily_bars(_engine, *, ts_code, as_of_date, lookback_rows):
        calls["load"] += 1
        return _bars(80 if calls["load"] == 1 else 420)

    def fake_backfill(_engine, ts_code, as_of, **_kwargs):
        calls["backfill"] += 1
        return BackfillResult(
            requested_dates=[dt.date(2026, 4, 29), dt.date(2026, 4, 30)],
            fetched_counts={"raw_daily": 2, "raw_daily_basic": 2, "raw_moneyflow": 2},
            errors=[],
        )

    def fake_fit_overlay(bars, *, ts_code, as_of_trade_date, base_params, max_candidates):
        return TuningArtifact(
            ts_code=ts_code,
            as_of_trade_date=as_of_trade_date,
            kind="pre_report_overlay",
            base_param_hash=params_hash(base_params),
            overlay={"aggregate.buy_threshold": 0.62},
            objective_score=0.42,
            metrics={"history_rows": len(bars)},
            candidate_count=max_candidates,
            history_start=dt.date(2024, 1, 1),
            history_end=as_of_trade_date,
            history_rows=len(bars),
            created_at=dt.datetime(2026, 5, 5, tzinfo=dt.UTC),
            namespace="stock_edge/tuning/300042_SZ/20260430",
        )

    monkeypatch.setattr(report_runtime, "build_context", fake_build_context)
    monkeypatch.setattr(report_runtime, "find_latest_tuning_artifact", lambda **_kwargs: None)
    monkeypatch.setattr(report_runtime, "load_daily_bars_for_tuning", fake_load_daily_bars)
    monkeypatch.setattr(report_runtime, "backfill_core_stock_window", fake_backfill)
    monkeypatch.setattr(report_runtime, "fit_pre_report_overlay", fake_fit_overlay)
    monkeypatch.setattr(report_runtime, "write_tuning_artifact", lambda artifact: "/tmp/stock_overlay.json")

    result = report_runtime.prepare_report_params(
        StockEdgeRequest(ts_code="300042.SZ", requested_at=dt.datetime(2026, 5, 5, tzinfo=dt.UTC)),
        engine=object(),  # type: ignore[arg-type]
        base_params=params,
    )

    assert result.status == "generated"
    assert calls == {"load": 2, "backfill": 1}
    assert "TuShare backfill 已补 2 个交易日" in result.reason
    assert result.params["strategy_matrix"]["aggregate"]["buy_threshold"] == 0.62
