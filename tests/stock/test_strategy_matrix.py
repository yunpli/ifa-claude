from __future__ import annotations

import datetime as dt
from dataclasses import replace

import pandas as pd

from ifa.families.stock.data.availability import LoadResult
from ifa.families.stock.params import load_params
from ifa.families.stock.strategies import compute_strategy_matrix
from tests.stock.test_baseline_strategy import _snapshot


def test_strategy_matrix_uses_param_surface_and_clusters():
    snapshot = _snapshot()
    matrix = compute_strategy_matrix(snapshot)
    params = load_params()["strategy_matrix"]

    assert matrix["model_version"] == params["model_version"]
    assert 0 <= matrix["aggregate_score"] <= 1
    assert matrix["signals"]
    trend = next(signal for signal in matrix["signals"] if signal["key"] == "trend_following")
    assert trend["cluster"] == "trend_breakout"
    assert trend["cluster_label"] == "趋势/突破"
    assert trend["weight"] > 1.0
    assert any(signal["key"].startswith("ta_family_") for signal in matrix["signals"])
    keys = {signal["key"] for signal in matrix["signals"]}
    assert {
        "volume_confirmation",
        "volatility_structure",
        "liquidity_slippage",
        "gap_risk_open_model",
        "auction_imbalance_proxy",
        "orderflow_mix",
        "northbound_regime",
        "market_margin_impulse",
        "block_trade_pressure",
        "event_catalyst_llm",
        "fundamental_contradiction_llm",
        "fundamental_price_dislocation_model",
        "peer_financial_alpha_model",
        "lhb_institution_hotmoney_divergence",
        "flow_persistence_decay",
        "limit_up_microstructure",
        "limit_up_event_path_model",
        "daily_basic_style",
        "peer_leader_fundamental_spread",
        "historical_replay_edge",
        "target_stop_replay",
        "entry_fill_replay",
        "entry_fill_classifier",
        "isotonic_score_calibrator",
        "right_tail_meta_gbm",
        "temporal_fusion_sequence_ranker",
        "multi_horizon_target_classifier",
        "target_ladder_probability_model",
        "path_shape_mixture_model",
        "mfe_mae_surface_model",
        "stop_loss_hazard_model",
        "position_sizing_model",
        "regime_adaptive_weight_model",
        "forward_entry_timing_model",
        "entry_price_surface_model",
        "pullback_rebound_classifier",
        "squeeze_breakout_classifier",
        "analog_kronos_nearest_neighbors",
        "kronos_path_cluster_transition",
        "peer_research_auto_trigger",
        "quantile_return_forecaster",
        "conformal_return_band",
        "stop_first_classifier",
        "hierarchical_sector_shrinkage",
        "trend_quality_r2",
        "candle_reversal_structure",
        "volume_price_divergence",
        "sector_diffusion_breadth",
        "volume_profile_support",
        "vwap_reclaim_execution",
        "strategy_validation_decay",
        "t0_uplift",
    }.issubset(keys)


def test_event_strategy_signals_use_lhb_and_limit_context():
    snapshot = replace(
        _snapshot(),
        event_context=LoadResult(
            "event_context",
            {
                "top_list": [{"trade_date": dt.date(2026, 4, 29), "net_amount": 3200.0, "l_buy": 9000.0, "l_sell": 5800.0}],
                "top_inst": [{"trade_date": dt.date(2026, 4, 29), "net_buy": 1800.0, "buy": 2600.0, "sell": 800.0}],
                "kpl": [{"trade_date": dt.date(2026, 4, 30), "pct_chg": 10.0, "status": "涨停", "bid_amount": 1800.0, "amount": 9000.0}],
                "limit_list": [{"trade_date": dt.date(2026, 4, 30), "pct_chg": 10.0, "open_times": 0, "fc_ratio": 8.0, "amount": 9000.0}],
                "block_trade": [{"trade_date": dt.date(2026, 4, 29), "price": 16.2, "amount": 6000.0}],
                "market_margin": [
                    {"trade_date": dt.date(2026, 4, 24), "rzye": 1000000.0, "rzmre": 8000.0, "rzche": 7000.0},
                    {"trade_date": dt.date(2026, 4, 25), "rzye": 1005000.0, "rzmre": 8500.0, "rzche": 7000.0},
                    {"trade_date": dt.date(2026, 4, 26), "rzye": 1010000.0, "rzmre": 8500.0, "rzche": 6900.0},
                    {"trade_date": dt.date(2026, 4, 27), "rzye": 1013000.0, "rzmre": 8600.0, "rzche": 6800.0},
                    {"trade_date": dt.date(2026, 4, 28), "rzye": 1015000.0, "rzmre": 8700.0, "rzche": 6700.0},
                    {"trade_date": dt.date(2026, 4, 29), "rzye": 1018000.0, "rzmre": 8800.0, "rzche": 6600.0},
                ],
                "northbound": [
                    {"trade_date": dt.date(2026, 4, 24), "north_money": 30000.0},
                    {"trade_date": dt.date(2026, 4, 25), "north_money": 42000.0},
                    {"trade_date": dt.date(2026, 4, 26), "north_money": 50000.0},
                    {"trade_date": dt.date(2026, 4, 27), "north_money": 55000.0},
                    {"trade_date": dt.date(2026, 4, 28), "north_money": 58000.0},
                    {"trade_date": dt.date(2026, 4, 29), "north_money": 65000.0},
                ],
                "company_events": [
                    {"capture_date": dt.date(2026, 4, 29), "title": "重大订单落地", "summary": "订单催化", "polarity": "positive", "importance": "high", "extraction_model": "gpt-5.4"},
                ],
                "catalyst_events": [],
            },
            "postgres",
            "ok",
            rows=4,
            required=False,
        ),
    )
    matrix = compute_strategy_matrix(snapshot)
    lhb = next(signal for signal in matrix["signals"] if signal["key"] == "lhb_institution_hotmoney_divergence")
    limit_up = next(signal for signal in matrix["signals"] if signal["key"] == "limit_up_microstructure")
    limit_path = next(signal for signal in matrix["signals"] if signal["key"] == "limit_up_event_path_model")

    assert lhb["status"] == "active"
    assert lhb["direction"] == "positive"
    assert lhb["cluster"] == "order_flow_smart_money"
    assert limit_up["status"] == "active"
    assert limit_up["direction"] == "positive"
    assert limit_up["cluster"] == "trend_breakout"
    assert limit_path["status"] == "active"
    assert limit_path["cluster"] == "trend_breakout"
    assert limit_path["extra"]["continuation_probability"] > 0
    north = next(signal for signal in matrix["signals"] if signal["key"] == "northbound_regime")
    margin = next(signal for signal in matrix["signals"] if signal["key"] == "market_margin_impulse")
    block = next(signal for signal in matrix["signals"] if signal["key"] == "block_trade_pressure")
    catalyst = next(signal for signal in matrix["signals"] if signal["key"] == "event_catalyst_llm")
    assert north["status"] == "active"
    assert north["cluster"] == "order_flow_smart_money"
    assert margin["status"] == "active"
    assert margin["cluster"] == "risk_warning"
    assert block["status"] == "active"
    assert block["cluster"] == "order_flow_smart_money"
    assert catalyst["status"] == "active"
    assert catalyst["cluster"] == "order_flow_smart_money"
    assert catalyst["direction"] == "positive"


def test_path_forecast_signals_activate_with_enough_history():
    snapshot = _snapshot()
    ctx = snapshot.ctx
    daily = []
    for i in range(220):
        close = 10 + i * 0.035 + (0.35 if i % 45 > 22 else 0.0)
        daily.append(
            {
                "trade_date": dt.date(2025, 1, 1) + dt.timedelta(days=i),
                "open": close - 0.04,
                "high": close + 0.35,
                "low": close - 0.18,
                "close": close,
                "amount": 100000.0 + i * 100,
            }
        )
    rich = replace(
        snapshot,
        daily_bars=LoadResult("daily_bars", pd.DataFrame(daily), "postgres", "ok", rows=len(daily), as_of=ctx.as_of.as_of_trade_date, required=True),
    )
    matrix = compute_strategy_matrix(rich)
    quantile = next(signal for signal in matrix["signals"] if signal["key"] == "quantile_return_forecaster")
    band = next(signal for signal in matrix["signals"] if signal["key"] == "conformal_return_band")
    stop = next(signal for signal in matrix["signals"] if signal["key"] == "stop_first_classifier")
    calibrator = next(signal for signal in matrix["signals"] if signal["key"] == "isotonic_score_calibrator")

    assert quantile["status"] == "active"
    assert quantile["cluster"] == "model_ensemble"
    assert quantile["extra"]["rows"]
    assert band["status"] == "active"
    assert band["cluster"] == "risk_warning"
    assert stop["status"] == "active"
    assert stop["cluster"] == "risk_warning"
    assert calibrator["status"] in {"active", "degraded"}
    assert calibrator["cluster"] == "model_ensemble"


def test_single_stock_meta_models_activate_with_enough_label_history():
    snapshot = _snapshot()
    ctx = snapshot.ctx
    params = load_params()
    params["risk"] = {**params["risk"], "right_tail_target_pct": 8.0}
    params["strategy_matrix"]["right_tail_meta_gbm"] = {**params["strategy_matrix"]["right_tail_meta_gbm"], "target_pct": 8.0}
    params["strategy_matrix"]["temporal_fusion_sequence_ranker"] = {**params["strategy_matrix"]["temporal_fusion_sequence_ranker"], "target_pct": 8.0}
    params["strategy_matrix"]["target_stop_survival_model"] = {**params["strategy_matrix"]["target_stop_survival_model"], "target_pct": 5.0, "stop_pct": 4.0}
    params["strategy_matrix"]["stop_loss_hazard_model"] = {**params["strategy_matrix"]["stop_loss_hazard_model"], "horizon_bars": 20, "target_pct": 5.0, "stop_pct": 4.0}
    params["strategy_matrix"]["gap_risk_open_model"] = {**params["strategy_matrix"]["gap_risk_open_model"], "adverse_gap_threshold_pct": 1.5}
    params["strategy_matrix"]["multi_horizon_target_classifier"] = {
        **params["strategy_matrix"]["multi_horizon_target_classifier"],
        "scenarios": [
            {"key": "quick_12d_5", "label": "12日/+5%", "horizon_bars": 12, "target_pct": 5.0},
            {"key": "swing_20d_8", "label": "20日/+8%", "horizon_bars": 20, "target_pct": 8.0},
            {"key": "right_tail_30d_12", "label": "30日/+12%", "horizon_bars": 30, "target_pct": 12.0},
        ],
    }
    params["strategy_matrix"]["target_ladder_probability_model"] = {
        **params["strategy_matrix"]["target_ladder_probability_model"],
        "stop_pct": 4.0,
        "scenarios": [
            {"key": "quick_12d_5", "label": "12日/+5%", "horizon_bars": 12, "target_pct": 5.0},
            {"key": "swing_20d_8", "label": "20日/+8%", "horizon_bars": 20, "target_pct": 8.0},
            {"key": "right_tail_30d_12", "label": "30日/+12%", "horizon_bars": 30, "target_pct": 12.0},
        ],
    }
    params["strategy_matrix"]["path_shape_mixture_model"] = {**params["strategy_matrix"]["path_shape_mixture_model"], "horizon_bars": 20, "target_pct": 5.0, "stop_pct": 4.0}
    params["strategy_matrix"]["mfe_mae_surface_model"] = {**params["strategy_matrix"]["mfe_mae_surface_model"], "horizon_bars": 20, "target_pct": 5.0, "stop_pct": 4.0}
    params["strategy_matrix"]["forward_entry_timing_model"] = {
        **params["strategy_matrix"]["forward_entry_timing_model"],
        "horizon_bars": 20,
        "target_pct": 5.0,
        "stop_pct": 4.0,
        "pullback_pct": 2.0,
    }
    params["strategy_matrix"]["entry_price_surface_model"] = {
        **params["strategy_matrix"]["entry_price_surface_model"],
        "horizon_bars": 20,
        "target_pct": 5.0,
        "stop_pct": 4.0,
        "pullback_pct": 2.0,
        "breakout_buffer_pct": 0.8,
        "fill_miss_penalty": 0.10,
        "delay_penalty_per_bar": 0.006,
    }
    params["strategy_matrix"]["pullback_rebound_classifier"] = {
        **params["strategy_matrix"]["pullback_rebound_classifier"],
        "horizon_bars": 20,
        "target_pct": 5.0,
        "stop_pct": 4.0,
    }
    params["strategy_matrix"]["squeeze_breakout_classifier"] = {
        **params["strategy_matrix"]["squeeze_breakout_classifier"],
        "horizon_bars": 20,
        "target_pct": 5.0,
    }
    params["strategy_matrix"]["model_stack_blender"] = {**params["strategy_matrix"]["model_stack_blender"], "min_sources": 2}
    ctx = replace(ctx, params=params)
    daily = []
    for i in range(360):
        cycle = (i % 55) / 55
        close = 10 + i * 0.018 + (0.75 if 0.35 < cycle < 0.68 else -0.15)
        stop_regime = i % 47 in {2, 3, 4, 5}
        daily.append(
            {
                "trade_date": dt.date(2024, 1, 1) + dt.timedelta(days=i),
                "open": close - 0.05,
                "high": close * (1.065 if not stop_regime else 1.012),
                "low": close * (0.935 if stop_regime else 0.985),
                "close": close,
                "amount": 100000.0 + (i % 35) * 2500,
            }
        )
    rich = replace(
        snapshot,
        ctx=ctx,
        daily_bars=LoadResult("daily_bars", pd.DataFrame(daily), "postgres", "ok", rows=len(daily), as_of=ctx.as_of.as_of_trade_date, required=True),
    )
    matrix = compute_strategy_matrix(rich)
    gbm = next(signal for signal in matrix["signals"] if signal["key"] == "right_tail_meta_gbm")
    seq = next(signal for signal in matrix["signals"] if signal["key"] == "temporal_fusion_sequence_ranker")
    survival = next(signal for signal in matrix["signals"] if signal["key"] == "target_stop_survival_model")
    stop_hazard = next(signal for signal in matrix["signals"] if signal["key"] == "stop_loss_hazard_model")
    gap_open = next(signal for signal in matrix["signals"] if signal["key"] == "gap_risk_open_model")
    multi_horizon = next(signal for signal in matrix["signals"] if signal["key"] == "multi_horizon_target_classifier")
    target_ladder = next(signal for signal in matrix["signals"] if signal["key"] == "target_ladder_probability_model")
    path_shape = next(signal for signal in matrix["signals"] if signal["key"] == "path_shape_mixture_model")
    mfe_mae = next(signal for signal in matrix["signals"] if signal["key"] == "mfe_mae_surface_model")
    entry_timing = next(signal for signal in matrix["signals"] if signal["key"] == "forward_entry_timing_model")
    entry_surface = next(signal for signal in matrix["signals"] if signal["key"] == "entry_price_surface_model")
    rebound = next(signal for signal in matrix["signals"] if signal["key"] == "pullback_rebound_classifier")
    squeeze = next(signal for signal in matrix["signals"] if signal["key"] == "squeeze_breakout_classifier")
    stack = next(signal for signal in matrix["signals"] if signal["key"] == "model_stack_blender")
    sizing = next(signal for signal in matrix["signals"] if signal["key"] == "position_sizing_model")

    assert gbm["status"] == "active"
    assert gbm["cluster"] == "model_ensemble"
    assert 0 <= gbm["extra"]["probability"] <= 1
    assert seq["status"] == "active"
    assert seq["cluster"] == "model_ensemble"
    assert 0 <= seq["extra"]["probability"] <= 1
    assert survival["status"] == "active"
    assert survival["cluster"] == "model_ensemble"
    assert 0 <= survival["extra"]["probability"] <= 1
    assert stop_hazard["status"] == "active"
    assert stop_hazard["cluster"] == "risk_warning"
    assert 0 <= stop_hazard["extra"]["probability"] <= 1
    assert gap_open["status"] == "active"
    assert gap_open["cluster"] == "risk_warning"
    assert 0 <= gap_open["extra"]["probability"] <= 1
    assert multi_horizon["status"] == "active"
    assert multi_horizon["cluster"] == "model_ensemble"
    assert multi_horizon["extra"]["extra"]["rows"]
    assert target_ladder["status"] == "active"
    assert target_ladder["cluster"] == "model_ensemble"
    assert target_ladder["extra"]["extra"]["rows"]
    assert path_shape["status"] == "active"
    assert path_shape["cluster"] == "model_ensemble"
    assert path_shape["extra"]["extra"]["clusters"]
    assert mfe_mae["status"] == "active"
    assert mfe_mae["cluster"] == "model_ensemble"
    assert mfe_mae["extra"]["extra"]["expected_reward_risk"] >= 0
    assert entry_timing["status"] == "active"
    assert entry_timing["cluster"] == "intraday_t0_execution"
    assert 0 <= entry_timing["extra"]["probability"] <= 1
    assert entry_surface["status"] == "active"
    assert entry_surface["cluster"] == "intraday_t0_execution"
    assert entry_surface["extra"]["extra"]["best_route"] in {"buy_now", "wait_pullback", "breakout_confirm", "avoid"}
    assert 0 <= entry_surface["extra"]["extra"]["tradable_probability"] <= 1
    assert rebound["status"] == "active"
    assert rebound["cluster"] == "pullback_continuation"
    assert 0 <= rebound["extra"]["probability"] <= 1
    assert squeeze["status"] == "active"
    assert squeeze["cluster"] == "trend_breakout"
    assert 0 <= squeeze["extra"]["probability"] <= 1
    assert stack["status"] == "active"
    assert stack["cluster"] == "model_ensemble"
    assert stack["extra"]["sources"]
    assert sizing["status"] == "active"
    assert sizing["cluster"] == "model_ensemble"
    assert 0 <= sizing["extra"]["recommended_fraction"] <= 0.35


def test_execution_and_hierarchical_prior_signals_activate():
    matrix = compute_strategy_matrix(_snapshot())
    auction = next(signal for signal in matrix["signals"] if signal["key"] == "auction_imbalance_proxy")
    shrinkage = next(signal for signal in matrix["signals"] if signal["key"] == "hierarchical_sector_shrinkage")

    assert auction["status"] in {"active", "degraded"}
    assert auction["cluster"] == "intraday_t0_execution"
    assert shrinkage["status"] in {"active", "missing"}
    assert shrinkage["cluster"] == "sw_l2_sector_leadership"


def test_historical_replay_signal_activates_with_enough_history():
    snapshot = _snapshot()
    ctx = snapshot.ctx
    daily = []
    for i in range(180):
        close = 10 + i * 0.03 + (0.25 if i % 30 > 15 else 0.0)
        daily.append(
            {
                "trade_date": dt.date(2025, 1, 1) + dt.timedelta(days=i),
                "open": close - 0.04,
                "high": close + 0.25,
                "low": close - 0.20,
                "close": close,
                "amount": 100000.0 + i * 100,
            }
        )
    rich = replace(
        snapshot,
        daily_bars=LoadResult("daily_bars", pd.DataFrame(daily), "postgres", "ok", rows=len(daily), as_of=ctx.as_of.as_of_trade_date, required=True),
    )
    matrix = compute_strategy_matrix(rich)
    replay = next(signal for signal in matrix["signals"] if signal["key"] == "historical_replay_edge")

    assert replay["status"] == "active"
    assert replay["cluster"] == "model_ensemble"
    assert replay["extra"]["target_stats"]
    assert replay["extra"]["best_key"] in {"tactical_15d_20", "swing_25d_30", "right_tail_40d_50"}


def test_entry_fill_replay_signal_activates_with_enough_history():
    snapshot = _snapshot()
    ctx = snapshot.ctx
    daily = []
    for i in range(180):
        close = 10 + (i % 40) * 0.04 + i * 0.01
        daily.append(
            {
                "trade_date": dt.date(2025, 1, 1) + dt.timedelta(days=i),
                "open": close - 0.03,
                "high": close + 0.18,
                "low": close - 0.18,
                "close": close,
                "amount": 100000.0 + i * 100,
            }
        )
    rich = replace(
        snapshot,
        daily_bars=LoadResult("daily_bars", pd.DataFrame(daily), "postgres", "ok", rows=len(daily), as_of=ctx.as_of.as_of_trade_date, required=True),
    )
    matrix = compute_strategy_matrix(rich)
    fill = next(signal for signal in matrix["signals"] if signal["key"] == "entry_fill_replay")
    classifier = next(signal for signal in matrix["signals"] if signal["key"] == "entry_fill_classifier")

    assert fill["status"] == "active"
    assert fill["cluster"] == "intraday_t0_execution"
    assert fill["extra"]["sample_count"] >= 20
    assert classifier["status"] == "active"
    assert classifier["cluster"] == "intraday_t0_execution"
    assert 0 <= classifier["extra"]["predicted_fill_probability"] <= 1


def test_target_stop_replay_signal_activates_with_enough_history():
    snapshot = _snapshot()
    ctx = snapshot.ctx
    daily = []
    for i in range(190):
        close = 10 + i * 0.035 + (0.35 if i % 36 > 17 else 0.0)
        daily.append(
            {
                "trade_date": dt.date(2025, 1, 1) + dt.timedelta(days=i),
                "open": close - 0.03,
                "high": close + 0.35,
                "low": close - 0.16,
                "close": close,
                "amount": 100000.0 + i * 100,
            }
        )
    rich = replace(
        snapshot,
        daily_bars=LoadResult("daily_bars", pd.DataFrame(daily), "postgres", "ok", rows=len(daily), as_of=ctx.as_of.as_of_trade_date, required=True),
    )
    matrix = compute_strategy_matrix(rich)
    path = next(signal for signal in matrix["signals"] if signal["key"] == "target_stop_replay")

    assert path["status"] == "active"
    assert path["cluster"] == "model_ensemble"
    assert path["extra"]["target_stats"]
    assert path["extra"]["best_key"] in {"tactical_15d_20", "swing_25d_30", "right_tail_40d_50"}


def test_intraday_profile_uses_vwap_volume_structure_when_available():
    snapshot = _snapshot()
    rows = []
    for i in range(80):
        close = 10 + (i % 20) * 0.02
        rows.append(
            {
                "trade_time": dt.datetime(2026, 5, 5, 9, 35) + dt.timedelta(minutes=5 * i),
                "open": close - 0.01,
                "high": close + 0.05,
                "low": close - 0.04,
                "close": close,
                "vol": 1000 + (i % 10) * 100,
                "amount": 100000 + (i % 10) * 1000,
            }
        )
    rich = replace(
        snapshot,
        intraday_5min=LoadResult("intraday_5min", pd.DataFrame(rows), "duckdb", "ok", rows=len(rows), required=False),
    )
    matrix = compute_strategy_matrix(rich)
    intraday = next(signal for signal in matrix["signals"] if signal["key"] == "intraday_profile")

    assert intraday["status"] == "active"
    assert intraday["extra"]["vwap"] is not None
    assert intraday["extra"]["concentration"] is not None


def test_t0_uplift_uses_intraday_when_available():
    snapshot = _snapshot()
    rows = []
    for day in range(4):
        base = dt.datetime(2026, 5, 1 + day, 9, 35)
        for i in range(30):
            close = 10 + day * 0.1 + (i % 12) * 0.04
            rows.append(
                {
                    "trade_time": base + dt.timedelta(minutes=5 * i),
                    "open": close - 0.02,
                    "high": close + 0.08,
                    "low": close - 0.08,
                    "close": close,
                    "vol": 1000 + i * 10,
                }
            )
    rich = replace(
        snapshot,
        intraday_5min=LoadResult("intraday_5min", pd.DataFrame(rows), "duckdb", "ok", rows=len(rows), required=False),
    )
    matrix = compute_strategy_matrix(rich)
    t0 = next(signal for signal in matrix["signals"] if signal["key"] == "t0_uplift")

    assert t0["status"] == "active"
    assert t0["cluster"] == "intraday_t0_execution"
    assert t0["extra"]["source"] == "duckdb.intraday_5min"


def test_vwap_execution_signals_use_intraday_when_available():
    snapshot = _snapshot()
    rows = []
    for day in range(4):
        base = dt.datetime(2026, 5, 1 + day, 9, 35)
        for i in range(30):
            close = 10 + day * 0.08 + (i - 12) * 0.015
            rows.append(
                {
                    "trade_time": base + dt.timedelta(minutes=5 * i),
                    "open": close - 0.01,
                    "high": close + 0.05,
                    "low": close - 0.05,
                    "close": close,
                    "vol": 1000 + i * 20,
                }
            )
    rich = replace(
        snapshot,
        intraday_5min=LoadResult("intraday_5min", pd.DataFrame(rows), "duckdb", "ok", rows=len(rows), required=False),
    )
    matrix = compute_strategy_matrix(rich)
    support = next(signal for signal in matrix["signals"] if signal["key"] == "volume_profile_support")
    reclaim = next(signal for signal in matrix["signals"] if signal["key"] == "vwap_reclaim_execution")

    assert support["status"] == "active"
    assert reclaim["status"] == "active"
    assert support["cluster"] == "intraday_t0_execution"
    assert reclaim["extra"]["sample_count"] >= 3


def test_price_action_signals_are_continuous_and_active():
    snapshot = _snapshot()
    matrix = compute_strategy_matrix(snapshot)
    trend = next(signal for signal in matrix["signals"] if signal["key"] == "trend_quality_r2")
    candle = next(signal for signal in matrix["signals"] if signal["key"] == "candle_reversal_structure")
    divergence = next(signal for signal in matrix["signals"] if signal["key"] == "volume_price_divergence")

    assert trend["status"] == "active"
    assert candle["status"] == "active"
    assert divergence["status"] == "active"
    assert trend["cluster"] == "trend_breakout"
    assert -1.0 <= trend["score"] <= 1.0


def test_strategy_validation_decay_enters_model_cluster():
    snapshot = _snapshot()
    rich = replace(
        snapshot,
        ta_context=LoadResult(
            "ta_context",
            {
                "candidates": [{"setup_name": "T1"}],
                "warnings": [],
                "regime": {},
                "setup_metrics": [
                    {"setup_name": "T1", "winrate_60d": 42.0, "combined_score_60d": 0.32, "decay_score": 2.0},
                    {"setup_name": "V1", "winrate_60d": 37.0, "combined_score_60d": 0.21, "decay_score": -1.0},
                ],
            },
            "postgres",
            "ok",
            rows=2,
        ),
    )
    matrix = compute_strategy_matrix(rich)
    decay = next(signal for signal in matrix["signals"] if signal["key"] == "strategy_validation_decay")

    assert decay["status"] == "active"
    assert decay["cluster"] == "model_ensemble"
    assert decay["extra"]["avg_winrate_60d"] > 35.0


def test_kronos_analog_and_research_prefetch_signals_activate():
    snapshot = replace(
        _snapshot(),
        model_context=LoadResult(
            "model_context",
            {
                "kronos_analog": {
                    "available": True,
                    "analog_count": 24,
                    "avg_similarity": 0.84,
                    "expected_return_40d": 0.18,
                    "avg_drawdown_40d": -0.06,
                    "hit_30pct_40d": 0.33,
                "hit_50pct_40d": 0.12,
                "stop_12pct_first_rate": 0.08,
                "path_cluster_distribution": {
                    "right_tail": 0.12,
                    "swing_up": 0.33,
                    "grind_up": 0.21,
                    "pop_and_fade": 0.08,
                    "range_chop": 0.18,
                    "stop_first": 0.08,
                },
                "dominant_path_cluster": "swing_up",
                "path_cluster_edge": 0.18,
                },
                "llm_counterfactual": {
                    "available": True,
                    "robustness_verdict": "robust",
                    "counterfactual_narrative": "基本面韧性仍在。",
                    "risk_factors": [],
                },
            },
            "postgres",
            "ok",
            rows=1,
        ),
        research_prefetch=LoadResult(
            "research_prefetch",
            {
                "items": [
                    {"ts_code": "300042.SZ", "status": "reused", "record_id": "r1"},
                    {"ts_code": "000001.SZ", "status": "generated", "record_id": "r2"},
                ],
                "failures": [],
            },
            "postgres",
            "ok",
            rows=2,
        ),
        research_lineup=LoadResult(
            "research_lineup",
            {"annual_factors": [{}] * 7, "quarterly_factors": [{}] * 7},
            "postgres",
            "ok",
            rows=14,
        ),
    )
    matrix = compute_strategy_matrix(snapshot)
    analog = next(signal for signal in matrix["signals"] if signal["key"] == "analog_kronos_nearest_neighbors")
    path_cluster = next(signal for signal in matrix["signals"] if signal["key"] == "kronos_path_cluster_transition")
    research = next(signal for signal in matrix["signals"] if signal["key"] == "peer_research_auto_trigger")
    contradiction = next(signal for signal in matrix["signals"] if signal["key"] == "fundamental_contradiction_llm")

    assert analog["status"] == "active"
    assert analog["cluster"] == "model_ensemble"
    assert analog["direction"] == "positive"
    assert path_cluster["status"] == "active"
    assert path_cluster["cluster"] == "model_ensemble"
    assert path_cluster["extra"]["dominant"] == "swing_up"
    assert research["status"] == "active"
    assert research["cluster"] == "fundamentals_quality"
    assert research["extra"]["prefetch_success_count"] == 2
    assert contradiction["status"] == "active"
    assert contradiction["cluster"] == "fundamentals_quality"


def test_sector_diffusion_scores_flow_and_crowding():
    snapshot = _snapshot()
    rich = replace(
        snapshot,
        sector_membership=LoadResult(
            "sector_membership",
            {
                "l2_name": "测试行业",
                "sector_flow_7d": [
                    {"net_amount": -1000.0},
                    {"net_amount": 2000.0},
                    {"net_amount": 3000.0},
                    {"net_amount": 5000.0},
                    {"net_amount": 7000.0},
                ],
                "sector_factor": {"persistence_score": 0.72, "crowding_score": 0.40},
                "sector_leaders": {
                    "size": [{"ts_code": "300042.SZ"}],
                    "momentum": [{"ts_code": "300042.SZ"}],
                },
            },
            "postgres",
            "ok",
            rows=1,
        ),
    )
    matrix = compute_strategy_matrix(rich)
    diffusion = next(signal for signal in matrix["signals"] if signal["key"] == "sector_diffusion_breadth")

    assert diffusion["status"] == "active"
    assert diffusion["cluster"] == "sw_l2_sector_leadership"
    assert diffusion["extra"]["leader_overlap"] == 1


def test_liquidity_slippage_signal_estimates_capacity():
    matrix = compute_strategy_matrix(_snapshot(amount=900000.0))
    signal = next(signal for signal in matrix["signals"] if signal["key"] == "liquidity_slippage")

    assert signal["status"] == "active"
    assert signal["cluster"] == "risk_warning"
    assert signal["extra"]["estimated_slippage_bps"] is not None


def test_flow_persistence_signal_scores_moneyflow_decay():
    snapshot = _snapshot()
    ctx = snapshot.ctx
    moneyflow = pd.DataFrame(
        {
            "trade_date": [dt.date(2026, 4, 1) + dt.timedelta(days=i) for i in range(10)],
            "net_mf_amount": [1200, 1500, 1800, 1600, 1400, 1100, 900, 600, 300, 100],
            "buy_elg_amount": [80.0] * 10,
            "sell_elg_amount": [30.0] * 10,
            "buy_lg_amount": [60.0] * 10,
            "sell_lg_amount": [20.0] * 10,
        }
    )
    rich = replace(
        snapshot,
        moneyflow=LoadResult("moneyflow", moneyflow, "postgres", "ok", rows=len(moneyflow), as_of=ctx.as_of.as_of_trade_date),
    )
    matrix = compute_strategy_matrix(rich)
    flow = next(signal for signal in matrix["signals"] if signal["key"] == "flow_persistence_decay")

    assert flow["status"] == "active"
    assert flow["cluster"] == "order_flow_smart_money"
    assert flow["extra"]["positive_day_share"] == 1.0


def test_peer_fundamental_spread_scores_target_vs_sector_leaders():
    snapshot = _snapshot()
    leaders = {
        "size": [
            {"ts_code": "300042.SZ", "name": "朗科科技", "total_mv": 120.0, "pe_ttm": 28.0, "pb": 3.2, "return_5d_pct": 8.0, "return_10d_pct": 12.0, "return_15d_pct": 18.0, "is_target": True},
            {"ts_code": "000001.SZ", "name": "同行A", "total_mv": 200.0, "pe_ttm": 45.0, "pb": 5.2, "return_5d_pct": 3.0, "return_10d_pct": 5.0, "return_15d_pct": 6.0},
            {"ts_code": "000002.SZ", "name": "同行B", "total_mv": 80.0, "pe_ttm": 35.0, "pb": 4.4, "return_5d_pct": -2.0, "return_10d_pct": 1.0, "return_15d_pct": 2.0},
        ],
        "momentum": [],
        "moneyflow": [],
        "ta": [],
    }
    peer_factors = []
    for code, roe, growth, cash, debt in [
        ("300042.SZ", 14.0, 32.0, 1.25, 34.0),
        ("000001.SZ", 9.0, 12.0, 0.72, 48.0),
        ("000002.SZ", 6.0, 8.0, 0.55, 58.0),
    ]:
        for period_type, period in [("annual", "20251231"), ("quarterly", "20260331")]:
            for name, value in {"ROE": roe, "营收同比增速": growth, "CFO/NI": cash, "资产负债率": debt}.items():
                peer_factors.append({"ts_code": code, "period_type": period_type, "period": period, "factor_name": name, "value": value})
    rich = replace(
        snapshot,
        sector_membership=LoadResult("sector_membership", {"sector_leaders": leaders, "peer_fundamentals": peer_factors, "l2_name": "测试行业"}, "postgres", "ok", rows=1),
        research_lineup=LoadResult(
            "research_lineup",
            {"annual_factors": [{}] * 8, "quarterly_factors": [{}] * 8, "recent_research_reports": [{}]},
            "postgres",
            "ok",
            rows=17,
        ),
    )
    matrix = compute_strategy_matrix(rich)
    peer = next(signal for signal in matrix["signals"] if signal["key"] == "peer_leader_fundamental_spread")
    alpha = next(signal for signal in matrix["signals"] if signal["key"] == "peer_financial_alpha_model")

    assert peer["status"] == "active"
    assert peer["cluster"] == "fundamentals_quality"
    assert peer["extra"]["target_in_leader_set"] is True
    assert peer["extra"]["research_coverage_score"] > 0.8
    assert alpha["status"] == "active"
    assert alpha["cluster"] == "fundamentals_quality"
    assert alpha["extra"]["expected_alpha_pct"] > 0


def test_fundamental_price_dislocation_model_uses_research_factors():
    snapshot = _snapshot()
    factors = []
    for period_type, period, values in [
        ("annual", "20251231", {"ROE": 13.0, "营收同比增速": 22.0, "CFO/NI": 1.2, "资产负债率": 36.0}),
        ("quarterly", "20260331", {"ROE": 7.0, "营收同比增速": 58.0, "CFO/NI": 1.4, "资产负债率": 30.0}),
    ]:
        for name, value in values.items():
            factors.append({"period_type": period_type, "period": period, "factor_name": name, "value": value})
    rich = replace(
        snapshot,
        research_lineup=LoadResult(
            "research_lineup",
            {"annual_factors": [row for row in factors if row["period_type"] == "annual"], "quarterly_factors": [row for row in factors if row["period_type"] == "quarterly"]},
            "postgres",
            "ok",
            rows=len(factors),
        ),
        sector_membership=LoadResult(
            "sector_membership",
            {
                "sector_peers": [
                    {"ts_code": "300042.SZ", "is_target": True, "return_15d_pct": -2.0},
                    {"ts_code": "000001.SZ", "return_15d_pct": 8.0},
                    {"ts_code": "000002.SZ", "return_15d_pct": 6.0},
                    {"ts_code": "000003.SZ", "return_15d_pct": 4.0},
                ]
            },
            "postgres",
            "ok",
            rows=1,
        ),
    )
    matrix = compute_strategy_matrix(rich)
    signal = next(item for item in matrix["signals"] if item["key"] == "fundamental_price_dislocation_model")

    assert signal["status"] == "active"
    assert signal["cluster"] == "fundamentals_quality"
    assert signal["extra"]["fundamental_strength"] is not None
    assert signal["extra"]["latest_quarterly_period"] == "20260331"


def test_strategy_matrix_applies_market_and_sector_context_gates():
    base = _snapshot()
    gated = replace(
        base,
        ta_context=LoadResult("ta_context", {"candidates": [{"setup_name": "T1"}], "warnings": [], "regime": {"regime": "risk_off"}}, "postgres", "ok", rows=1),
        sector_membership=LoadResult(
            "sector_membership",
            {"sector_state": {"cycle_phase": "retreat"}, "sector_flow_7d": [], "sector_factor": {}, "l2_name": "测试行业"},
            "postgres",
            "ok",
            rows=1,
        ),
    )
    base_matrix = compute_strategy_matrix(base)
    gated_matrix = compute_strategy_matrix(gated)
    base_trend = next(signal for signal in base_matrix["signals"] if signal["key"] == "trend_following")
    gated_trend = next(signal for signal in gated_matrix["signals"] if signal["key"] == "trend_following")
    regime = next(signal for signal in gated_matrix["signals"] if signal["key"] == "regime_adaptive_weight_model")

    assert gated_trend["weight"] < base_trend["weight"]
    assert regime["status"] == "active"
    assert regime["cluster"] == "model_ensemble"
    assert regime["extra"]["market_regime"] == "risk_off"


def test_strategy_matrix_param_surface_has_tuning_contract():
    params = load_params()

    assert params["strategy_matrix"]["aggregate"]["buy_threshold"] > params["strategy_matrix"]["aggregate"]["watch_threshold"]
    assert "thresholds" not in params["strategy_matrix"]
    assert "smooth_scoring" in params["strategy_matrix"]
    assert "cluster_weights" in params["strategy_matrix"]
    assert "ta_family_weights" in params["strategy_matrix"]
    assert params["tuning"]["global_preset"]["universe"] == "top_liquidity_500"
    assert params["tuning"]["global_preset"]["max_candidates"] > params["tuning"]["pre_report_overlay"]["max_candidates"]
    assert params["tuning"]["pre_report_overlay"]["search_space"] == "continuous"
    assert params["tuning"]["offline_validation"]["embargo_days"] > 0
    assert params["tuning"]["objective"]["weights"]["hit_target_40d_quality"] > 0
    assert params["tuning"]["search_bounds"]["aggregate.buy_threshold"][0] < params["tuning"]["search_bounds"]["aggregate.buy_threshold"][1]
    assert params["tuning"]["objectives"]["hit_50pct_40d"] > 0
