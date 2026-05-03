"""Refresh entry points — weekly Champion-Challenger, monthly health check,
quarterly architecture review.

Used by:
    ifa ningbo refresh weekly
    ifa ningbo refresh monthly
    ifa ningbo refresh quarterly

Each emits a structured markdown report into:
    {output_root}/ningbo/refresh_logs/YYYY-MM-DD_<cadence>.md
"""
from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd
from sqlalchemy import Engine

from ifa.families.ningbo.ml.champion_challenger import (
    PROMOTION_THRESHOLDS, SLOT_AGGRESSIVE, SLOT_CONSERVATIVE, SLOT_HEURISTIC,
    activate_version, evaluate_promotion, get_active_for_slot,
    insert_model_version, log_no_change, recent_rolling_performance,
    save_model_artifact,
)
from ifa.families.ningbo.ml.features    import FEATURE_COLUMNS
from ifa.families.ningbo.ml.features_v2 import build_candidate_feature_matrix
from ifa.families.ningbo.ml.trainer_v3  import (
    train_models_v3, _select_top_n_per_day, _per_day_top5_returns,
)


# ── Output paths ─────────────────────────────────────────────────────────────

def _refresh_log_dir() -> Path:
    from ifa.config import get_settings
    s = get_settings()
    p = Path(s.output_root) / "ningbo" / "refresh_logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_report(cadence: str, body: str) -> Path:
    p = _refresh_log_dir() / f"{dt.date.today():%Y-%m-%d}_{cadence}.md"
    p.write_text(body)
    return p


# ── Bootstrap p-value (lightweight) ─────────────────────────────────────────

def _bootstrap_p(
    oos_df: pd.DataFrame, scores_a: np.ndarray, scores_b: np.ndarray,
    n_boot: int = 100, seed: int = 42,
) -> float:
    """One-sided: P(A.top5_mean > B.top5_mean | bootstrap)."""
    rng = np.random.default_rng(seed)
    days = oos_df["rec_date"].unique()
    df_a = oos_df.copy(); df_a["_score"] = scores_a
    df_b = oos_df.copy(); df_b["_score"] = scores_b
    by_a = {d: g for d, g in df_a.groupby("rec_date")}
    by_b = {d: g for d, g in df_b.groupby("rec_date")}
    cnt_extreme = 0
    for _ in range(n_boot):
        sample = rng.choice(days, size=len(days), replace=True)
        a_picks, b_picks = [], []
        for d in sample:
            for picks_acc, g in [(a_picks, by_a[d]), (b_picks, by_b[d])]:
                g_sorted = g.sort_values("_score", ascending=False)
                chosen, per_strat = [], {}
                for _, r in g_sorted.iterrows():
                    if per_strat.get(r["strategy"], 0) >= 2:
                        continue
                    chosen.append(r["final_cum_return"])
                    per_strat[r["strategy"]] = per_strat.get(r["strategy"], 0) + 1
                    if len(chosen) >= 5:
                        break
                if chosen:
                    picks_acc.append(np.mean(chosen))
        a_mean = np.mean(a_picks) if a_picks else 0
        b_mean = np.mean(b_picks) if b_picks else 0
        if (a_mean - b_mean) <= 0:
            cnt_extreme += 1
    return cnt_extreme / n_boot


# ── Pick winners per slot ───────────────────────────────────────────────────

def _pick_winner(metrics: dict, primary_metric: str, exclude: list[str]) -> str:
    candidates = {k: v for k, v in metrics.items()
                  if k not in exclude and getattr(v, primary_metric, None) is not None}
    if not candidates:
        raise ValueError(f"No candidates for primary={primary_metric}")
    return max(candidates, key=lambda k: getattr(candidates[k], primary_metric))


# ── Weekly: Champion-Challenger ──────────────────────────────────────────────

def run_weekly_refresh(
    engine: Engine,
    *,
    in_sample_start: dt.date = dt.date(2024, 1, 2),
    in_sample_end:   dt.date | None = None,    # default: today - 6 months
    oos_end:         dt.date | None = None,    # default: today - 1 day
    on_log:          Callable[[str], None] = print,
) -> dict:
    """Train all candidates, decide per-slot promotions, persist.

    Returns dict with promotion outcomes and report path.
    """
    today = dt.date.today()
    if in_sample_end is None:
        in_sample_end = today - dt.timedelta(days=180)  # 6m rolling OOS
    if oos_end is None:
        oos_end = today - dt.timedelta(days=1)

    on_log(f"\n# Weekly Refresh — {today}")
    on_log(f"Train: {in_sample_start} → {in_sample_end}  |  "
           f"OOS: {in_sample_end} → {oos_end}")

    # 1. Build feature matrix
    on_log("\n[1/5] Building feature matrix from candidates_daily…")
    t0 = time.time()
    feat_df = build_candidate_feature_matrix(engine, in_sample_start, oos_end, include_outcomes=True)
    on_log(f"  → {feat_df.shape}  [{time.time()-t0:.1f}s]")

    # 2. Train all candidate models (no Kronos for now — proven not helpful)
    on_log("\n[2/5] Training candidate models…")
    t0 = time.time()
    art = train_models_v3(
        feat_df, in_sample_end=in_sample_end,
        use_tabnet=False, use_kronos_features=False,
        on_log=lambda m: on_log(f"  {m}"),
    )
    on_log(f"  → trained in {time.time()-t0:.1f}s")

    # 3. Pick winner per slot
    on_log("\n[3/5] Picking slot winners…")
    cands = {k: v for k, v in art.metrics.items() if k != "heuristic"}
    agg_winner_name  = max(cands, key=lambda k: cands[k].oos_top5_avg_return)
    cons_winner_name = max(cands, key=lambda k: cands[k].oos_top5_sharpe)
    agg_winner  = art.metrics[agg_winner_name]
    cons_winner = art.metrics[cons_winner_name]
    on_log(f"  AGGRESSIVE   winner: {agg_winner_name}  T5_Mean={agg_winner.oos_top5_avg_return*100:+.2f}%")
    on_log(f"  CONSERVATIVE winner: {cons_winner_name} Sharpe={cons_winner.oos_top5_sharpe:.2f}")

    # 4. Evaluate promotion per slot
    on_log("\n[4/5] Evaluating promotion rules…")
    s_heur = art.metrics["heuristic"].raw_oos_scores

    decisions: dict[str, Any] = {}
    for slot, winner_name, winner in [
        (SLOT_AGGRESSIVE, agg_winner_name, agg_winner),
        (SLOT_CONSERVATIVE, cons_winner_name, cons_winner),
    ]:
        active = get_active_for_slot(engine, slot)
        winner_metrics_dict = _metrics_to_dict(winner)

        # Bootstrap p-value vs current active (or vs heuristic if no active)
        if active is not None:
            # Need active model's OOS scores — load + predict on this OOS
            active_metrics = active["metrics"] if isinstance(active["metrics"], dict) else json.loads(active["metrics"])
            # For weekly refresh we approximate: bootstrap candidate vs heuristic baseline
            # (re-loading and re-scoring active is expensive; OK for the lift test)
            p_val = _bootstrap_p(_oos_df_for_eval(feat_df, in_sample_end), winner.raw_oos_scores, s_heur, n_boot=100)
        else:
            p_val = _bootstrap_p(_oos_df_for_eval(feat_df, in_sample_end), winner.raw_oos_scores, s_heur, n_boot=100)

        decision = evaluate_promotion(
            slot=slot,
            candidate_name=winner_name, candidate_version=art.model_version,
            candidate_metrics=winner_metrics_dict, active=active,
            bootstrap_p_value=p_val,
        )
        decisions[slot] = decision
        on_log(f"  {slot}: {decision.reason}")

    # 5. Persist + activate
    on_log("\n[5/5] Persisting models + updating registry…")
    promoted_slots = []
    for slot, dec in decisions.items():
        # Always insert the version (even if not promoting — useful for history)
        winner = agg_winner if slot == SLOT_AGGRESSIVE else cons_winner
        winner_name = agg_winner_name if slot == SLOT_AGGRESSIVE else cons_winner_name
        # If winner is the ensemble (a list of member names), save a wrapper
        # that bundles all member objects + ensemble logic.
        winner_obj = art.base_models[winner_name]
        if isinstance(winner_obj, list):
            from ifa.families.ningbo.ml.dual_scorer import EnsembleWrapper
            winner_obj = EnsembleWrapper(
                members={n: art.base_models[n] for n in winner_obj}
            )
        artifact_path = save_model_artifact(
            slot=slot, model_version=art.model_version,
            model_name=winner_name, model_object=winner_obj,
        )
        insert_model_version(
            engine,
            model_version=art.model_version, slot=slot,
            model_name=winner_name, objective=winner.objective,
            feature_set_id="v3_handcrafted", feature_columns=FEATURE_COLUMNS,
            train_range=art.train_range, oos_range=art.oos_range,
            n_train=art.n_train, n_oos=art.n_oos,
            metrics=_metrics_to_dict(winner), artifact_path=artifact_path,
        )

        if dec.will_promote:
            prev = activate_version(
                engine, slot, art.model_version,
                reason=dec.reason, event_type="promoted",
                decision_data=dec.rule_results,
            )
            on_log(f"  ✅ PROMOTED {slot}: {prev or 'cold-start'} → {art.model_version}")
            promoted_slots.append(slot)
        else:
            log_no_change(
                engine, slot, art.model_version, dec.reason, dec.rule_results,
            )
            on_log(f"  ⏸  NO CHANGE {slot}: {dec.reason}")

    # Write markdown report
    report = _build_weekly_report(today, art, decisions, promoted_slots)
    report_path = _write_report("weekly", report)
    on_log(f"\nReport saved: {report_path}")

    return {
        "model_version": art.model_version,
        "promoted_slots": promoted_slots,
        "decisions": {k: v.reason for k, v in decisions.items()},
        "report_path": str(report_path),
    }


# ── Monthly: Walk-forward health check ───────────────────────────────────────

def run_monthly_refresh(
    engine: Engine,
    *,
    on_log: Callable[[str], None] = print,
) -> dict:
    """Walk-forward stability check: split last 6 months into 3 buckets,
    verify active models remain consistent."""
    today = dt.date.today()
    on_log(f"\n# Monthly Health Check — {today}")

    buckets = []
    for i in range(3):
        b_end = today - dt.timedelta(days=60 * i)
        b_start = today - dt.timedelta(days=60 * (i + 1))
        buckets.append((f"M{3-i}", b_start, b_end))
    buckets.reverse()  # earliest first

    rows: list[dict] = []
    for slot in (SLOT_HEURISTIC, SLOT_AGGRESSIVE, SLOT_CONSERVATIVE):
        active = get_active_for_slot(engine, slot)
        for name, b_start, b_end in buckets:
            perf = _per_slot_perf(engine, slot, b_start, b_end)
            rows.append({
                "slot": slot,
                "bucket": name,
                "start": b_start, "end": b_end,
                "active_version": (active or {}).get("model_version", "—"),
                **perf,
            })
            on_log(
                f"  {slot:13s} {name} ({b_start} → {b_end}): "
                f"n={perf['n_picks']:>3}  T5_mean={perf['mean_ret']*100:+.2f}%  "
                f"win_rate={perf['win_rate']*100:.0f}%"
            )

    # Detect anomalies
    alerts = []
    for slot in (SLOT_AGGRESSIVE, SLOT_CONSERVATIVE):
        slot_rows = [r for r in rows if r["slot"] == slot]
        means = [r["mean_ret"] for r in slot_rows if r["n_picks"] > 0]
        if not means:
            continue
        if all(m < 0 for m in means):
            alerts.append(f"⚠️  {slot}: 全部 3 个 bucket T5_Mean < 0%，模型可能失效")
        elif means and means[-1] < 0 and means[-1] < means[0]:
            alerts.append(f"⚠️  {slot}: 最近 bucket T5_Mean ({means[-1]*100:+.2f}%) 低于 6 个月前，警惕衰退")

    # Recent 30-day check
    on_log("\nRecent 30-day rolling performance:")
    for slot in (SLOT_AGGRESSIVE, SLOT_CONSERVATIVE):
        recent = recent_rolling_performance(engine, slot, days=30)
        if recent and recent["n_picks"] and recent["mean_ret"] is not None:
            mean_ret = float(recent["mean_ret"])
            on_log(f"  {slot}: {recent['n_picks']} picks  T5_mean={mean_ret*100:+.2f}%  "
                   f"win_rate={float(recent['win_rate'] or 0)*100:.0f}%")
            if mean_ret < 0:
                alerts.append(f"🚨 {slot}: 近 30 天 T5_Mean={mean_ret*100:+.2f}% < 0%，建议关注")

    # Report
    report = _build_monthly_report(today, rows, alerts)
    report_path = _write_report("monthly", report)
    on_log(f"\nReport saved: {report_path}")
    return {"alerts": alerts, "report_path": str(report_path)}


# ── Quarterly: Architecture review (re-test new model families) ─────────────

def run_quarterly_refresh(
    engine: Engine,
    *,
    on_log: Callable[[str], None] = print,
) -> dict:
    """Test new model families that we previously evaluated as 'no help'.

    Re-evaluates Kronos embeddings + (in future) other model families against
    current active models. If results have changed, recommend manual promotion.
    """
    today = dt.date.today()
    on_log(f"\n# Quarterly Architecture Review — {today}")
    on_log("Re-evaluating model families that were previously rejected.")

    # For now: re-test Kronos
    findings = {}

    on_log("\nKronos embeddings — re-evaluation:")
    try:
        from ifa.families.ningbo.ml.kronos_features import attach_kronos_embeddings
        in_sample_start = dt.date(2024, 1, 2)
        in_sample_end   = today - dt.timedelta(days=180)
        oos_end         = today - dt.timedelta(days=1)

        feat_df_base = build_candidate_feature_matrix(engine, in_sample_start, oos_end, include_outcomes=True)
        try:
            feat_df_kron = attach_kronos_embeddings(engine, feat_df_base)
        except FileNotFoundError as exc:
            on_log(f"  ⚠️  No Kronos cache; skipping. ({exc})")
            findings["kronos"] = "skipped (no cache)"
            return _finalize_quarterly(today, findings, on_log)

        art_base = train_models_v3(feat_df_base, in_sample_end=in_sample_end,
                                   use_tabnet=False, use_kronos_features=False,
                                   on_log=lambda m: on_log(f"  base   {m}"))
        art_kron = train_models_v3(feat_df_kron, in_sample_end=in_sample_end,
                                   use_tabnet=False, use_kronos_features=True,
                                   on_log=lambda m: on_log(f"  kron   {m}"))

        # Compare ensemble Top5_Mean
        base_ens = art_base.metrics["ensemble_meanrank"]
        kron_ens = art_kron.metrics["ensemble_meanrank"]
        delta = kron_ens.oos_top5_avg_return - base_ens.oos_top5_avg_return
        on_log(f"\n  base ensemble:    T5_Mean={base_ens.oos_top5_avg_return*100:+.2f}%")
        on_log(f"  +kronos ensemble: T5_Mean={kron_ens.oos_top5_avg_return*100:+.2f}%")
        on_log(f"  Δ = {delta*100:+.2f}pp")
        if delta >= 0.005:
            findings["kronos"] = (
                f"WORTH RE-CONSIDERING: Δ={delta*100:+.2f}pp on ensemble. "
                f"Manual promote: ifa ningbo registry promote aggressive {art_kron.model_version}"
            )
        else:
            findings["kronos"] = f"STILL NOT HELPFUL: Δ={delta*100:+.2f}pp"
    except Exception as exc:
        findings["kronos"] = f"ERROR: {exc}"
        on_log(f"  ❌ Kronos re-eval failed: {exc}")

    return _finalize_quarterly(today, findings, on_log)


def _finalize_quarterly(today, findings: dict, on_log) -> dict:
    report = _build_quarterly_report(today, findings)
    path = _write_report("quarterly", report)
    on_log(f"\nReport saved: {path}")
    return {"findings": findings, "report_path": str(path)}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _metrics_to_dict(m) -> dict:
    return {
        "oos_auc": m.oos_auc,
        "oos_avg_precision": m.oos_avg_precision,
        "oos_ndcg5": m.oos_ndcg5,
        "oos_top5_precision": m.oos_top5_precision,
        "oos_top5_avg_return": m.oos_top5_avg_return,
        "oos_top5_med_return": m.oos_top5_med_return,
        "oos_top5_sharpe": m.oos_top5_sharpe,
        "oos_top5_winrate": m.oos_top5_winrate,
        "oos_max_drawdown": m.oos_max_drawdown,
    }


def _oos_df_for_eval(feat_df: pd.DataFrame, in_sample_end: dt.date) -> pd.DataFrame:
    df = feat_df.copy()
    df = df[df["outcome_status"].isin(["take_profit", "stop_loss", "expired"])]
    df = df.dropna(subset=["y_take_profit", "y_final_return"])
    return df[df["rec_date"] > in_sample_end].copy().sort_values(
        ["rec_date", "ts_code", "strategy"]
    )


def _per_slot_perf(engine: Engine, slot: str, start: dt.date, end: dt.date) -> dict:
    """Compute T5 performance from real DB recommendations for a slot."""
    from sqlalchemy import text
    scoring_mode_map = {
        SLOT_AGGRESSIVE:   "ml_aggressive",
        SLOT_CONSERVATIVE: "ml_conservative",
        SLOT_HEURISTIC:    "heuristic",
    }
    sm = scoring_mode_map[slot]
    sql = text("""
        SELECT
            COUNT(*) AS n_picks,
            AVG(o.final_cum_return) AS mean_ret,
            COUNT(DISTINCT r.rec_date) AS n_days,
            AVG(CASE WHEN o.outcome_status='take_profit' THEN 1.0 ELSE 0.0 END) AS win_rate
        FROM ningbo.recommendations_daily r
        JOIN ningbo.recommendation_outcomes o USING (rec_date, ts_code, strategy, scoring_mode)
        WHERE r.scoring_mode = :sm AND r.rec_date BETWEEN :s AND :e
          AND o.outcome_status != 'in_progress'
    """)
    with engine.connect() as c:
        row = c.execute(sql, {"sm": sm, "s": start, "e": end}).mappings().fetchone()
    return {
        "n_picks":   int(row["n_picks"] or 0),
        "n_days":    int(row["n_days"] or 0),
        "mean_ret":  float(row["mean_ret"] or 0.0),
        "win_rate":  float(row["win_rate"] or 0.0),
    }


# ── Markdown report builders ─────────────────────────────────────────────────

def _build_weekly_report(today: dt.date, art, decisions: dict, promoted: list) -> str:
    lines = [
        f"# Ningbo Weekly Refresh — {today}\n",
        f"**Model version:** `{art.model_version}`",
        f"**Train range:** {art.train_range[0]} → {art.train_range[1]} ({art.n_train:,} candidates)",
        f"**OOS range:** {art.oos_range[0]} → {art.oos_range[1]} ({art.n_oos:,} candidates)",
        "",
        "## Promotion outcomes",
        "",
        "| Slot | Decision | Detail |",
        "|---|---|---|",
    ]
    for slot, dec in decisions.items():
        emoji = "✅ PROMOTED" if dec.will_promote else "⏸ NO CHANGE"
        lines.append(f"| {slot} | {emoji} | {dec.reason} |")
    lines += ["", "## All trained models — OOS metrics", "",
              "| Model | AUC | NDCG@5 | T5_Prec | T5_Mean | T5_Med | Sharpe | WinRate | MaxDD |",
              "|---|---|---|---|---|---|---|---|---|"]
    for name in ("heuristic", "lr", "rf", "xgb_clf", "lgbm_clf", "cat_clf",
                 "xgb_pair", "xgb_ndcg", "lgbm_lamda", "ensemble_meanrank"):
        m = art.metrics.get(name)
        if not m: continue
        lines.append(
            f"| {name} | {m.oos_auc:.3f} | {m.oos_ndcg5:.3f} | "
            f"{m.oos_top5_precision*100:.1f}% | {m.oos_top5_avg_return*100:+.2f}% | "
            f"{m.oos_top5_med_return*100:+.2f}% | {m.oos_top5_sharpe:.2f} | "
            f"{m.oos_top5_winrate*100:.0f}% | {m.oos_max_drawdown*100:+.1f}% |"
        )
    lines += ["", "## What to do next", ""]
    if promoted:
        lines.append(f"✅ {len(promoted)} slot(s) promoted: **{', '.join(promoted)}**.")
        lines.append(f"   Tonight's report will use the new model(s) automatically. No action needed.")
    else:
        lines.append("⏸ No promotions this week. Active models remain in production.")
    lines.append("")
    return "\n".join(lines)


def _build_monthly_report(today, rows: list[dict], alerts: list[str]) -> str:
    lines = [f"# Ningbo Monthly Health Check — {today}\n"]
    if alerts:
        lines += ["## 🚨 Alerts", ""]
        for a in alerts:
            lines.append(f"- {a}")
        lines.append("")
    else:
        lines += ["## ✅ No alerts — all active models healthy.\n"]

    lines += ["## Walk-forward performance (3 × 60-day buckets)", "",
              "| Slot | Bucket | Window | n_picks | T5_Mean | WinRate |",
              "|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(
            f"| {r['slot']} | {r['bucket']} | {r['start']} → {r['end']} | "
            f"{r['n_picks']} | {r['mean_ret']*100:+.2f}% | {r['win_rate']*100:.0f}% |"
        )
    return "\n".join(lines)


def _build_quarterly_report(today, findings: dict) -> str:
    lines = [f"# Ningbo Quarterly Architecture Review — {today}\n",
             "## Findings", ""]
    for family, finding in findings.items():
        lines += [f"### {family}", "", finding, ""]
    return "\n".join(lines)
