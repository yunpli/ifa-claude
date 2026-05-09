from __future__ import annotations

import datetime as dt
import warnings
import json

from ifa.families.stock.backtest.panel_evaluator import (
    compute_signal_ic_priors,
    evaluate_overlay_on_panel,
    panel_matrix_from_rows,
)
from ifa.families.stock.backtest.replay_panel import (
    ALL_SIGNAL_KEYS,
    PanelRow,
    _chunk_checkpoint_dir,
    _chunk_key,
    _load_chunk_checkpoint,
    _load_manifest,
    _membership_hash,
    _panel_cache_path,
    _panel_chunks,
    _save_chunk_checkpoint,
)
from ifa.families.stock.backtest.outcome_proxy import compare_proxy_candidate_families, score_proxy_candidate_families, summarize_outcome_proxy
from scripts.stock_edge_panel_tune import _cheap_proxy_rows, _strata_counts


def test_signal_ic_priors_do_not_warn_on_all_inactive_columns():
    rows = []
    active_key = ALL_SIGNAL_KEYS[0]
    for i in range(30):
        rows.append(
            PanelRow(
                ts_code=f"000{i:03d}.SZ",
                as_of_date=dt.date(2026, 1, 1),
                entry_close=10.0,
                signals={active_key: {"score": i / 30.0, "status": "active", "cluster": "test"}},
                forward_5d_return=float(i),
                forward_10d_return=float(i),
                forward_20d_return=float(i),
                forward_5d_target_first=False,
                forward_10d_target_first=False,
                forward_20d_target_first=False,
                forward_5d_stop_first=False,
                forward_10d_stop_first=False,
                forward_20d_stop_first=False,
                forward_5d_max_drawdown=-1.0,
                forward_10d_max_drawdown=-1.0,
                forward_20d_max_drawdown=-1.0,
                forward_5d_mfe=1.0,
                forward_10d_mfe=1.0,
                forward_20d_mfe=1.0,
                forward_available_days=20,
            )
        )
    panel = panel_matrix_from_rows(rows)

    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        priors = compute_signal_ic_priors(panel, min_samples=30)

    assert active_key in priors["5d"]


def test_pit_local_panel_chunks_use_date_specific_universe():
    d1 = dt.date(2026, 1, 2)
    d2 = dt.date(2026, 1, 5)

    chunks = _panel_chunks(
        ts_codes=["latest_a", "latest_b"],
        as_of_dates=[d1, d2],
        ts_codes_by_date={d1: ["pit_a", "pit_b"], d2: ["pit_c"]},
    )

    assert chunks == [(d1, ["pit_a", "pit_b"]), (d2, ["pit_c"])]
    assert _membership_hash(chunks) == _membership_hash(chunks)
    assert _membership_hash(chunks) != _membership_hash([(d1, ["pit_b", "pit_a"]), (d2, ["pit_c"])])


def test_panel_chunks_can_split_large_date_cohorts_for_progress():
    d1 = dt.date(2026, 1, 2)
    chunks = _panel_chunks(
        ts_codes=["a", "b", "c", "d", "e"],
        as_of_dates=[d1],
        ts_codes_by_date=None,
        max_codes_per_chunk=2,
    )

    assert chunks == [(d1, ["a", "b"]), (d1, ["c", "d"]), (d1, ["e"])]


def test_chunk_checkpoint_roundtrip_preserves_rows_and_failures(tmp_path):
    day = dt.date(2026, 1, 2)
    cache_path = tmp_path / "panel.parquet"
    chunk_dir = _chunk_checkpoint_dir(cache_path)
    key = _chunk_key(day, ["000001.SZ", "000002.SZ"])
    row = PanelRow(
        ts_code="000001.SZ",
        as_of_date=day,
        entry_close=10.0,
        signals={"entry_fill_replay": {"score": 0.7, "status": "active", "cluster": "execution"}},
        forward_5d_return=1.0,
        forward_10d_return=2.0,
        forward_20d_return=3.0,
        forward_5d_target_first=False,
        forward_10d_target_first=False,
        forward_20d_target_first=False,
        forward_5d_stop_first=False,
        forward_10d_stop_first=False,
        forward_20d_stop_first=False,
        forward_5d_max_drawdown=-1.0,
        forward_10d_max_drawdown=-1.0,
        forward_20d_max_drawdown=-1.0,
        forward_5d_mfe=2.0,
        forward_10d_mfe=3.0,
        forward_20d_mfe=4.0,
        forward_available_days=20,
        regime="range",
    )
    failures = [{"ts_code": "000002.SZ", "as_of_date": day.isoformat(), "reason": "unit"}]

    _save_chunk_checkpoint(chunk_dir, key, day, ["000001.SZ", "000002.SZ"], [row], failures)
    loaded = _load_chunk_checkpoint(chunk_dir, key)

    assert loaded is not None
    rows, loaded_failures, requested = loaded
    assert requested == 2
    assert len(rows) == 1
    assert rows[0].ts_code == "000001.SZ"
    assert rows[0].forward_20d_return == 3.0
    assert loaded_failures == failures


def test_pit_local_cache_key_isolated_from_latest_mode():
    dates = [dt.date(2026, 1, 2), dt.date(2026, 1, 5)]

    latest_path = _panel_cache_path("top_liquidity_top5", dates, "abc123", True)
    pit_path = _panel_cache_path(
        "top_liquidity_top5_pitlocal",
        dates,
        "abc123",
        True,
        cache_key_extra="membership-a",
    )
    pit_path_changed = _panel_cache_path(
        "top_liquidity_top5_pitlocal",
        dates,
        "abc123",
        True,
        cache_key_extra="membership-b",
    )

    assert latest_path != pit_path
    assert pit_path != pit_path_changed
    assert "pitlocal" in pit_path.name


def test_manifest_load_defaults_legacy_universe_mode(tmp_path):
    manifest_path = tmp_path / "panel.manifest.json"
    manifest_path.write_text(
        json.dumps({
            "universe_id": "top_liquidity_top5",
            "universe_size": 5,
            "as_of_dates": ["2026-01-02", "2026-01-05"],
            "base_param_hash": "abc123",
            "skip_llm": True,
            "n_rows": 10,
            "built_at": "2026-01-06T00:00:00+00:00",
            "panel_path": "/tmp/panel.parquet",
            "manifest_path": str(manifest_path),
        }),
        encoding="utf-8",
    )

    manifest = _load_manifest(manifest_path)

    assert manifest.universe_mode == "latest"
    assert manifest.universe_selection == {}


def test_manifest_load_reads_checkpoint_fields(tmp_path):
    manifest_path = tmp_path / "panel.manifest.json"
    manifest_path.write_text(
        json.dumps({
            "universe_id": "top_liquidity_top5",
            "universe_size": 5,
            "as_of_dates": ["2026-01-02", "2026-01-05"],
            "base_param_hash": "abc123",
            "skip_llm": True,
            "n_rows": 9,
            "built_at": "2026-01-06T00:00:00+00:00",
            "panel_path": "/tmp/panel.parquet",
            "manifest_path": str(manifest_path),
            "total_pairs": 10,
            "failed_rows": 1,
            "chunk_size": 2,
            "completed_pairs": 10,
            "completed_chunks": 5,
            "requested_chunks": 5,
            "runtime_sec": 12.5,
        }),
        encoding="utf-8",
    )

    manifest = _load_manifest(manifest_path)

    assert manifest.total_pairs == 10
    assert manifest.chunk_size == 2
    assert manifest.completed_chunks == 5
    assert manifest.runtime_sec == 12.5


def test_stratified_metadata_counts_dimensions():
    rows = [
        {"ts_code": "000001.SZ", "l1_code": "801010", "liquidity_bucket": 1, "size_bucket": 1, "volatility_bucket": 2},
        {"ts_code": "000002.SZ", "l1_code": "801010", "liquidity_bucket": 2, "size_bucket": 1, "volatility_bucket": 2},
        {"ts_code": "600001.SH", "l1_code": "801020", "liquidity_bucket": 1, "size_bucket": 3, "volatility_bucket": 1},
    ]

    counts = _strata_counts(rows)

    assert counts["l1_code"] == {"801010": 2, "801020": 1}
    assert counts["liquidity_bucket"] == {"1": 2, "2": 1}
    assert counts["size_bucket"] == {"1": 2, "3": 1}
    assert counts["volatility_bucket"] == {"2": 2, "1": 1}


def test_cheap_proxy_rows_balances_regime_and_date():
    rows = []
    for regime in ("trend", "range"):
        for day in (dt.date(2026, 1, 2), dt.date(2026, 1, 5)):
            for i in range(5):
                rows.append(
                    PanelRow(
                        ts_code=f"{regime[:1]}{day.day:02d}{i:03d}.SZ",
                        as_of_date=day,
                        entry_close=10.0,
                        signals={},
                        forward_5d_return=float(i),
                        forward_10d_return=float(i),
                        forward_20d_return=float(i),
                        forward_5d_target_first=False,
                        forward_10d_target_first=False,
                        forward_20d_target_first=False,
                        forward_5d_stop_first=False,
                        forward_10d_stop_first=False,
                        forward_20d_stop_first=False,
                        forward_5d_max_drawdown=-1.0,
                        forward_10d_max_drawdown=-1.0,
                        forward_20d_max_drawdown=-1.0,
                        forward_5d_mfe=1.0,
                        forward_10d_mfe=1.0,
                        forward_20d_mfe=1.0,
                        forward_available_days=20,
                        regime=regime,
                    )
                )

    proxy = _cheap_proxy_rows(rows, max_rows=8, seed="unit")
    cohorts = {(row.regime, row.as_of_date) for row in proxy}

    assert len(proxy) == 8
    assert cohorts == {
        ("trend", dt.date(2026, 1, 2)),
        ("trend", dt.date(2026, 1, 5)),
        ("range", dt.date(2026, 1, 2)),
        ("range", dt.date(2026, 1, 5)),
    }


def test_outcome_proxy_summary_reports_feature_rank_ic():
    import pandas as pd

    df = pd.DataFrame({
        "ts_code": [f"000{i:03d}.SZ" for i in range(40)],
        "as_of_date": [dt.date(2026, 1, 2)] * 40,
        "ret_5d_pct": list(range(40)),
        "ret_20d_pct": list(range(40)),
        "volatility_20d_pct": list(reversed(range(40))),
        "avg_amount_20d": [1000 + i for i in range(40)],
        "moneyflow_net_5d_pct_amount": [i / 100.0 for i in range(40)],
        "total_mv": [10000 + i for i in range(40)],
        "forward_5d_return": list(range(40)),
        "forward_10d_return": list(range(40)),
        "forward_20d_return": list(range(40)),
    })

    summary = summarize_outcome_proxy(df)

    assert summary["rows"] == 40
    assert summary["horizons"]["5d"]["n"] == 40
    assert summary["feature_rank_ic"]["5d"]["ret_5d_pct"] > 0.99
    assert summary["cheap_composite_rank_ic"]["10d"] > 0


def test_proxy_candidate_family_comparison_reports_month_stability():
    import pandas as pd

    rows = []
    industries = ["有色金属", "房地产", "家用电器", "商贸零售"]
    regimes = ["trend_continuation", "range_bound"]
    for month, date in [("2026-02", dt.date(2026, 2, 10)), ("2026-03", dt.date(2026, 3, 10))]:
        for i in range(60):
            good = i % 4 in {0, 2}
            rows.append({
                "ts_code": f"000{i:03d}.SZ",
                "as_of_date": date,
                "l1_name": industries[i % len(industries)],
                "regime": regimes[i % len(regimes)],
                "ret_5d_pct": float(i % 7 - 3),
                "ret_20d_pct": float(20 - i % 30),
                "volatility_20d_pct": float(8 if good else 25),
                "avg_amount_20d": float(1000 + (i % 20) * 100),
                "moneyflow_net_5d_pct_amount": float(0.05 if good else -0.04),
                "total_mv": float(5000 + (i if good else -i) * 10),
                "turnover_rate": float(2 if good else 8),
                "forward_5d_return": float(3 if good else -2),
                "forward_10d_return": float(5 if good else -4),
                "forward_20d_return": float(8 if good else -6),
            })

    comparison = compare_proxy_candidate_families(pd.DataFrame(rows))

    assert comparison["rows"] == 120
    assert "mid_liquidity_large_cap_quality_flow" in comparison["families"]
    assert "2026-03" in comparison["families"]["weak_industry_avoid_quality_flow"]["month_stability"]
    assert comparison["families"]["weak_industry_avoid_quality_flow"]["horizons"]["10d"]["rank_ic"] > 0
    assert comparison["ranking"][0]["family"] != "baseline_cheap_composite_v1"


def test_proxy_candidate_family_scores_are_reusable_for_gate_selection():
    import pandas as pd

    df = pd.DataFrame({
        "ts_code": [f"000{i:03d}.SZ" for i in range(40)],
        "as_of_date": [dt.date(2026, 3, 10)] * 40,
        "l1_name": ["有色金属" if i % 2 == 0 else "房地产" for i in range(40)],
        "regime": ["range_bound"] * 40,
        "ret_5d_pct": [float(i % 8) for i in range(40)],
        "ret_20d_pct": [float(40 - i) for i in range(40)],
        "volatility_20d_pct": [float(10 + i % 4) for i in range(40)],
        "avg_amount_20d": [float(1000 + i * 10) for i in range(40)],
        "moneyflow_net_5d_pct_amount": [float(i / 1000.0) for i in range(40)],
        "total_mv": [float(5000 + i * 20) for i in range(40)],
        "turnover_rate": [float(2 + i % 3) for i in range(40)],
    })

    scores = score_proxy_candidate_families(df)

    assert "weak_industry_avoid_quality_flow" in scores
    assert "industry_relative_momentum_flow" in scores
    assert len(scores["weak_industry_avoid_quality_flow"]) == len(df)
    assert scores["weak_industry_avoid_quality_flow"].notna().all()


def test_panel_metrics_include_top_bucket_payoff_and_spread():
    key = "entry_fill_replay"
    rows = []
    for i in range(30):
        rows.append(
            PanelRow(
                ts_code=f"000{i:03d}.SZ",
                as_of_date=dt.date(2026, 1, 1),
                entry_close=10.0,
                signals={key: {"score": i / 29.0, "status": "active", "cluster": "test"}},
                forward_5d_return=float(i - 10),
                forward_10d_return=float(i - 10),
                forward_20d_return=float(i - 10),
                forward_5d_target_first=i >= 20,
                forward_10d_target_first=i >= 20,
                forward_20d_target_first=i >= 20,
                forward_5d_stop_first=False,
                forward_10d_stop_first=False,
                forward_20d_stop_first=False,
                forward_5d_max_drawdown=-float(max(1, 30 - i)),
                forward_10d_max_drawdown=-float(max(1, 30 - i)),
                forward_20d_max_drawdown=-float(max(1, 30 - i)),
                forward_5d_mfe=float(i + 1),
                forward_10d_mfe=float(i + 1),
                forward_20d_mfe=float(i + 1),
                forward_available_days=20,
            )
        )
    base_params = {
        "decision_layer": {
            "horizons": {
                "5d": {"weights": {key: 1.0}, "base_score": 0.5, "raw_edge_scale": 0.5, "thresholds": {"buy": 0.7}},
                "10d": {"weights": {key: 1.0}, "base_score": 0.5, "raw_edge_scale": 0.5, "thresholds": {"buy": 0.7}},
                "20d": {"weights": {key: 1.0}, "base_score": 0.5, "raw_edge_scale": 0.5, "thresholds": {"buy": 0.7}},
            }
        }
    }

    metrics = evaluate_overlay_on_panel(panel_matrix_from_rows(rows), {}, base_params)
    h5 = metrics["objective_5d"]

    assert h5["rank_ic"] > 0.95
    assert h5["top_bucket_avg_return"] > h5["bottom_bucket_avg_return"]
    assert h5["top_bottom_spread"] > 0
    assert h5["bucket_monotonicity"] > 0.95
    assert h5["top_bucket_win_rate"] == 1.0
    assert h5["top_bucket_left_tail"] > 0
    assert h5["top_bucket_return_quality"] > 0
    assert h5["top_bottom_spread_quality"] > 0
    assert h5["bucket_monotonicity_quality"] > 0.95


def test_panel_objective_uses_yaml_horizon_weights_for_outcome_first_terms():
    key = "entry_fill_replay"
    rows = []
    for i in range(30):
        rows.append(
            PanelRow(
                ts_code=f"000{i:03d}.SZ",
                as_of_date=dt.date(2026, 1, 1),
                entry_close=10.0,
                signals={key: {"score": i / 29.0, "status": "active", "cluster": "test"}},
                forward_5d_return=float(i - 15),
                forward_10d_return=float(i - 15),
                forward_20d_return=float(i - 15),
                forward_5d_target_first=False,
                forward_10d_target_first=False,
                forward_20d_target_first=False,
                forward_5d_stop_first=False,
                forward_10d_stop_first=False,
                forward_20d_stop_first=False,
                forward_5d_max_drawdown=-2.0,
                forward_10d_max_drawdown=-2.0,
                forward_20d_max_drawdown=-2.0,
                forward_5d_mfe=2.0,
                forward_10d_mfe=2.0,
                forward_20d_mfe=2.0,
                forward_available_days=20,
            )
        )
    params = {
        "decision_layer": {
            "horizons": {
                h: {"weights": {key: 1.0}, "base_score": 0.5, "raw_edge_scale": 0.5, "thresholds": {"buy": 0.7}}
                for h in ("5d", "10d", "20d")
            }
        },
        "tuning": {
            "objective": {
                "horizon_weights": {
                    "rank_ic_quality": 1.0,
                    "positive_return_quality": 0.0,
                    "target_first_quality": 0.0,
                    "entry_fill_quality": 0.0,
                    "reward_risk": 0.0,
                    "risk_adjusted_return": 0.0,
                    "drawdown_penalty": 0.0,
                    "stop_first_penalty": 0.0,
                    "liquidity_penalty": 0.0,
                    "top_bucket_return_quality": 0.0,
                    "top_bottom_spread_quality": 0.0,
                    "bucket_monotonicity_quality": 0.0,
                    "top_bucket_win_quality": 0.0,
                    "top_bucket_left_tail_penalty": 0.0,
                }
            }
        },
    }

    metrics = evaluate_overlay_on_panel(panel_matrix_from_rows(rows), {}, params)

    assert metrics["composite_objective"]["horizon_scores"]["5d"] == metrics["objective_5d"]["rank_ic_quality"]
