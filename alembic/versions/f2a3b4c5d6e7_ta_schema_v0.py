"""ta: create ta schema v0 — factor_pro_daily, cyq, hot_rank, suspend, catalyst, regime, candidates

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-05-03

Tables created (ta schema):
  · factor_pro_daily     — stk_factor_pro 80-field subset per (trade_date, ts_code)
  · cyq_chips_daily      — JSONB chip distribution per (trade_date, ts_code)
  · cyq_perf_daily       — Cost quantiles + winner_rate per (trade_date, ts_code)
  · hot_rank_daily       — THS / DC / KPL hot rank per (trade_date, src, type, ts_code)
  · suspend_daily        — Suspension records
  · stk_limit_daily      — Limit-up / limit-down records
  · catalyst_event_memory— LLM-extracted cross-family market events (shared with Stock Intel)
  · regime_daily         — Daily market regime classification
  · setup_metrics_daily  — Rolling 60d/250d setup edge metrics
  · candidates_daily     — Daily setup candidates with scoring
  · candidate_tracking   — T+N actual outcome tracking
  · report_judgments     — Falsifiable next-day hypotheses
  · user_watchlist       — User stock watchlist (V2.2.3)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

SCHEMA = "ta"
N = sa.Numeric  # shorthand


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ── factor_pro_daily ─────────────────────────────────────────────────
    # 80 selected fields from stk_factor_pro
    # Prices: NUMERIC(12,4); ratios/indicators: NUMERIC(12,4); mv in yuan: NUMERIC(20,2)
    op.create_table(
        "factor_pro_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        # OHLCV qfq
        sa.Column("close_qfq", N(12, 4)),
        sa.Column("open_qfq", N(12, 4)),
        sa.Column("high_qfq", N(12, 4)),
        sa.Column("low_qfq", N(12, 4)),
        sa.Column("vol", N(20, 0)),           # 手 (stored as-is from Tushare)
        sa.Column("amount_yuan", N(20, 2)),   # 元 (converted from 千元)
        sa.Column("turnover_rate_pct", N(8, 4)),
        sa.Column("turnover_rate_f_pct", N(8, 4)),
        sa.Column("volume_ratio", N(8, 4)),
        # Valuation
        sa.Column("pe_ttm", N(12, 4)),
        sa.Column("pb", N(12, 4)),
        sa.Column("ps_ttm", N(12, 4)),
        sa.Column("total_mv_yuan", N(20, 2)),   # 元 (converted from 万元)
        sa.Column("circ_mv_yuan", N(20, 2)),    # 元 (converted from 万元)
        # Moving averages qfq
        sa.Column("ma_qfq_5", N(12, 4)),
        sa.Column("ma_qfq_10", N(12, 4)),
        sa.Column("ma_qfq_20", N(12, 4)),
        sa.Column("ma_qfq_30", N(12, 4)),
        sa.Column("ma_qfq_60", N(12, 4)),
        sa.Column("ma_qfq_90", N(12, 4)),
        sa.Column("ma_qfq_250", N(12, 4)),
        sa.Column("ema_qfq_5", N(12, 4)),
        sa.Column("ema_qfq_10", N(12, 4)),
        sa.Column("ema_qfq_20", N(12, 4)),
        sa.Column("ema_qfq_30", N(12, 4)),
        sa.Column("ema_qfq_60", N(12, 4)),
        sa.Column("ema_qfq_90", N(12, 4)),
        sa.Column("ema_qfq_250", N(12, 4)),
        # MACD qfq
        sa.Column("macd_qfq", N(12, 4)),
        sa.Column("macd_dea_qfq", N(12, 4)),
        sa.Column("macd_dif_qfq", N(12, 4)),
        # KDJ qfq
        sa.Column("kdj_qfq", N(12, 4)),
        sa.Column("kdj_d_qfq", N(12, 4)),
        sa.Column("kdj_k_qfq", N(12, 4)),
        # BOLL qfq
        sa.Column("boll_upper_qfq", N(12, 4)),
        sa.Column("boll_mid_qfq", N(12, 4)),
        sa.Column("boll_lower_qfq", N(12, 4)),
        # RSI qfq
        sa.Column("rsi_qfq_6", N(8, 4)),
        sa.Column("rsi_qfq_12", N(8, 4)),
        sa.Column("rsi_qfq_24", N(8, 4)),
        # BIAS qfq
        sa.Column("bias1_qfq", N(8, 4)),
        sa.Column("bias2_qfq", N(8, 4)),
        sa.Column("bias3_qfq", N(8, 4)),
        # Oscillators qfq
        sa.Column("cci_qfq", N(12, 4)),
        sa.Column("wr_qfq", N(8, 4)),
        sa.Column("mfi_qfq", N(8, 4)),
        sa.Column("obv_qfq", N(20, 4)),
        sa.Column("atr_qfq", N(12, 4)),
        sa.Column("psy_qfq", N(8, 4)),
        sa.Column("mtm_qfq", N(12, 4)),
        sa.Column("roc_qfq", N(8, 4)),
        sa.Column("trix_qfq", N(12, 4)),
        # DMI qfq
        sa.Column("dmi_adx_qfq", N(8, 4)),
        sa.Column("dmi_pdi_qfq", N(8, 4)),
        sa.Column("dmi_mdi_qfq", N(8, 4)),
        # Trend days
        sa.Column("updays", sa.Integer),
        sa.Column("downdays", sa.Integer),
        sa.Column("topdays", sa.Integer),
        sa.Column("lowdays", sa.Integer),
        # Channels qfq
        sa.Column("bbi_qfq", N(12, 4)),
        sa.Column("ktn_upper_qfq", N(12, 4)),
        sa.Column("ktn_mid_qfq", N(12, 4)),
        sa.Column("ktn_down_qfq", N(12, 4)),
        sa.Column("expma_12_qfq", N(12, 4)),
        sa.Column("expma_50_qfq", N(12, 4)),
        sa.Column("taq_up_qfq", N(12, 4)),
        sa.Column("taq_mid_qfq", N(12, 4)),
        sa.Column("taq_down_qfq", N(12, 4)),
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_factor_pro_ts_code_date",
        "factor_pro_daily", ["ts_code", "trade_date"],
        schema=SCHEMA,
    )

    # ── cyq_chips_daily ───────────────────────────────────────────────────
    op.create_table(
        "cyq_chips_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("chips_json", sa.JSON),   # [{"price": 67.2, "percent_pct": 1.2}, ...]
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
        schema=SCHEMA,
    )

    # ── cyq_perf_daily ────────────────────────────────────────────────────
    op.create_table(
        "cyq_perf_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("his_low", N(12, 4)),
        sa.Column("his_high", N(12, 4)),
        sa.Column("cost_5pct", N(12, 4)),
        sa.Column("cost_15pct", N(12, 4)),
        sa.Column("cost_50pct", N(12, 4)),
        sa.Column("cost_85pct", N(12, 4)),
        sa.Column("cost_95pct", N(12, 4)),
        sa.Column("weight_avg", N(12, 4)),
        sa.Column("winner_rate_pct", N(8, 4)),   # 0-100 (converted from 0-1)
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
        schema=SCHEMA,
    )

    # ── hot_rank_daily ────────────────────────────────────────────────────
    op.create_table(
        "hot_rank_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("src", sa.String(8), nullable=False),      # 'ths' | 'dc'
        sa.Column("data_type", sa.String(16), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("rank", sa.Integer),
        sa.Column("hot", N(20, 4)),
        sa.Column("pct_change_pct", N(8, 4)),
        sa.PrimaryKeyConstraint("trade_date", "src", "data_type", "ts_code"),
        schema=SCHEMA,
    )

    # ── suspend_daily ─────────────────────────────────────────────────────
    op.create_table(
        "suspend_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("suspend_type", sa.Text),   # 'S' start | 'R' resume
        sa.Column("suspend_timing", sa.Text),  # '盘前' / '盘中' / etc.
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
        schema=SCHEMA,
    )

    # ── stk_limit_daily ───────────────────────────────────────────────────
    op.create_table(
        "stk_limit_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("trade_date_str", sa.String(8)),
        sa.Column("name", sa.Text),
        sa.Column("close", N(12, 4)),
        sa.Column("pct_chg_pct", N(8, 4)),
        sa.Column("amp", N(8, 4)),
        sa.Column("fc_ratio", N(8, 4)),
        sa.Column("fl_ratio", N(8, 4)),
        sa.Column("fd_amount_yuan", N(20, 2)),   # 元 (from 元)
        sa.Column("first_time", sa.Text),
        sa.Column("last_time", sa.Text),
        sa.Column("open_times", sa.Integer),
        sa.Column("strth", N(8, 4)),
        sa.Column("limit", sa.Text),   # 'U' up | 'D' down
        sa.PrimaryKeyConstraint("trade_date", "ts_code"),
        schema=SCHEMA,
    )

    # ── catalyst_event_memory ─────────────────────────────────────────────
    op.create_table(
        "catalyst_event_memory",
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("capture_date", sa.Date, nullable=False),
        sa.Column("event_type", sa.String(32)),
        sa.Column("target_ts_codes", sa.ARRAY(sa.Text)),
        sa.Column("target_sectors", sa.ARRAY(sa.Text)),
        sa.Column("title", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("polarity", sa.Text,
                  sa.CheckConstraint("polarity IN ('positive','neutral','negative')")),
        sa.Column("importance", sa.Text,
                  sa.CheckConstraint("importance IN ('high','medium','low')")),
        sa.Column("source_url", sa.Text),
        sa.Column("publish_time", sa.TIMESTAMP(timezone=True)),
        sa.Column("extraction_model", sa.Text),
        sa.Column("extraction_prompt_version", sa.Text),
        sa.Column("valid_until", sa.Date),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("event_id"),
        schema=SCHEMA,
    )
    op.create_index("ix_ta_catalyst_capture_date", "catalyst_event_memory", ["capture_date"], schema=SCHEMA)
    op.execute(
        "CREATE INDEX ix_ta_catalyst_ts_codes ON ta.catalyst_event_memory USING GIN (target_ts_codes)"
    )
    op.execute(
        "CREATE INDEX ix_ta_catalyst_sectors ON ta.catalyst_event_memory USING GIN (target_sectors)"
    )

    # ── regime_daily ──────────────────────────────────────────────────────
    op.create_table(
        "regime_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("regime", sa.String(32), nullable=False),
        sa.Column("confidence", N(5, 4)),
        sa.Column("evidence_json", sa.JSON),
        sa.Column("transitions_json", sa.JSON),
        sa.PrimaryKeyConstraint("trade_date"),
        schema=SCHEMA,
    )

    # ── setup_metrics_daily ───────────────────────────────────────────────
    op.create_table(
        "setup_metrics_daily",
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("setup_name", sa.String(32), nullable=False),
        sa.Column("triggers_count", sa.Integer),
        sa.Column("winrate_60d", N(8, 4)),
        sa.Column("avg_return_60d", N(8, 4)),
        sa.Column("pl_ratio_60d", N(8, 4)),
        sa.Column("winrate_250d", N(8, 4)),
        sa.Column("decay_score", N(8, 4)),
        sa.Column("suitable_regimes", sa.ARRAY(sa.Text)),
        sa.PrimaryKeyConstraint("trade_date", "setup_name"),
        schema=SCHEMA,
    )

    # ── candidates_daily ──────────────────────────────────────────────────
    op.create_table(
        "candidates_daily",
        sa.Column("candidate_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("setup_name", sa.String(32), nullable=False),
        sa.Column("rank", sa.Integer),
        sa.Column("final_score", N(8, 4)),
        sa.Column("star_rating", sa.Integer),
        sa.Column("regime_at_gen", sa.String(32)),
        sa.Column("evidence_json", sa.JSON),
        sa.Column("validation_json", sa.JSON),
        sa.Column("invalidation_json", sa.JSON),
        sa.Column("in_top_watchlist", sa.Boolean, server_default="false"),
        sa.PrimaryKeyConstraint("candidate_id"),
        sa.UniqueConstraint("trade_date", "ts_code", "setup_name"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ta_candidates_date_watchlist",
        "candidates_daily", ["trade_date", "in_top_watchlist"],
        schema=SCHEMA,
    )

    # ── candidate_tracking ────────────────────────────────────────────────
    op.create_table(
        "candidate_tracking",
        sa.Column("track_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("candidate_id", sa.UUID, nullable=False),
        sa.Column("horizon_days", sa.Integer, nullable=False),
        sa.Column("eval_date", sa.Date, nullable=False),
        sa.Column("return_pct", N(8, 4)),
        sa.Column("max_return_pct", N(8, 4)),
        sa.Column("max_drawdown_pct", N(8, 4)),
        sa.Column("validation_status", sa.Text,
                  sa.CheckConstraint(
                      "validation_status IN ('confirmed','partial','invalidated','timeout','pending')"
                  )),
        sa.Column("confirmation_evidence", sa.JSON),
        sa.PrimaryKeyConstraint("track_id"),
        sa.UniqueConstraint("candidate_id", "horizon_days"),
        sa.ForeignKeyConstraint(["candidate_id"], [f"{SCHEMA}.candidates_daily.candidate_id"]),
        schema=SCHEMA,
    )

    # ── report_judgments ──────────────────────────────────────────────────
    op.create_table(
        "report_judgments",
        sa.Column("judgment_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("report_run_id", sa.UUID),
        sa.Column("judgment_type", sa.Text),
        sa.Column("statement", sa.Text),
        sa.Column("target", sa.Text),
        sa.Column("horizon_days", sa.Integer),
        sa.Column("validation_rule_json", sa.JSON),
        sa.Column("review_status", sa.Text),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("review_evidence", sa.JSON),
        sa.PrimaryKeyConstraint("judgment_id"),
        schema=SCHEMA,
    )

    # ── user_watchlist ────────────────────────────────────────────────────
    op.create_table(
        "user_watchlist",
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("note", sa.Text),
        sa.PrimaryKeyConstraint("user_id", "ts_code"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for tbl in [
        "user_watchlist", "report_judgments", "candidate_tracking", "candidates_daily",
        "setup_metrics_daily", "regime_daily", "catalyst_event_memory",
        "stk_limit_daily", "suspend_daily", "hot_rank_daily",
        "cyq_perf_daily", "cyq_chips_daily", "factor_pro_daily",
    ]:
        op.drop_table(tbl, schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
