"""ningbo: create schema + 4 tables for short-term strategy reports

Revision ID: 3dd12ef8cb0f
Revises: c712b540766e
Create Date: 2026-05-02

Purpose
-------
Create the `ningbo` schema and 4 tables for the 宁波短线策略 family:

    ningbo.strategy_params         — versioned param sets per strategy
    ningbo.recommendations_daily   — daily picks (heuristic OR ML mode)
    ningbo.recommendation_tracking — per-rec daily tracking (T+1 .. T+15)
    ningbo.recommendation_outcomes — terminal state of each recommendation

Schema is independent from smartmoney; ningbo READS smartmoney.raw_*
tables via JOIN (no duplication of raw data).

scoring_mode column is part of PK from day 1 — Phase 1 only writes
'heuristic'; Phase 3 will add 'ml' alongside (both modes coexist).
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "3dd12ef8cb0f"
down_revision = "c712b540766e"
branch_labels = None
depends_on = None

SCHEMA = "ningbo"


def upgrade() -> None:
    # ── 0. Schema ──────────────────────────────────────────────────────
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ── 1. strategy_params ─────────────────────────────────────────────
    # Versioned param sets. Phase 1 seeds 'heuristic_v1.0' rows for each
    # strategy. Phase 3 adds 'ml_v2026.05' etc. for ML model versions.
    op.create_table(
        "strategy_params",
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("version_tag", sa.String(32), nullable=False),
        sa.Column("scoring_mode", sa.String(16), nullable=False),  # 'heuristic' | 'ml'
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("source", sa.String(16), nullable=False),  # 'manual' | 'llm-suggested' | 'backtest-promoted'
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("strategy", "version_tag"),
        schema=SCHEMA,
    )

    # ── 2. recommendations_daily ───────────────────────────────────────
    # Daily picks. Phase 1: 5 heuristic picks/day max. Phase 3: + 5 ml picks/day.
    op.create_table(
        "recommendations_daily",
        sa.Column("rec_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("scoring_mode", sa.String(16), nullable=False),
        sa.Column("param_version", sa.String(32), nullable=False),
        sa.Column("rec_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("confidence_score", sa.Numeric(8, 6), nullable=False),
        sa.Column("rec_signal_meta", sa.JSON(), nullable=True),
        sa.Column("llm_narrative", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("rec_date", "ts_code", "strategy", "scoring_mode"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ningbo_recs_rec_date",
        "recommendations_daily",
        ["rec_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ningbo_recs_ts_code",
        "recommendations_daily",
        ["ts_code"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ningbo_recs_mode_date",
        "recommendations_daily",
        ["scoring_mode", "rec_date"],
        schema=SCHEMA,
    )

    # ── 3. recommendation_tracking ─────────────────────────────────────
    # Per-recommendation daily tracking. Up to 15 rows per recommendation.
    # Written by daily tracking batch.
    op.create_table(
        "recommendation_tracking",
        sa.Column("rec_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("scoring_mode", sa.String(16), nullable=False),
        sa.Column("track_day", sa.Integer(), nullable=False),  # 1 .. 15
        sa.Column("track_date", sa.Date(), nullable=False),
        sa.Column("close_price", sa.Numeric(14, 4), nullable=False),
        sa.Column("cum_return", sa.Numeric(10, 6), nullable=False),  # (close - rec_price) / rec_price
        sa.Column("ma24", sa.Numeric(14, 4), nullable=True),
        sa.Column("below_ma24", sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint("rec_date", "ts_code", "strategy", "scoring_mode", "track_day"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ningbo_track_track_date",
        "recommendation_tracking",
        ["track_date"],
        schema=SCHEMA,
    )

    # ── 4. recommendation_outcomes ─────────────────────────────────────
    # Terminal state per recommendation. Updated daily until terminal state reached.
    op.create_table(
        "recommendation_outcomes",
        sa.Column("rec_date", sa.Date(), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("strategy", sa.String(32), nullable=False),
        sa.Column("scoring_mode", sa.String(16), nullable=False),
        sa.Column("outcome_status", sa.String(16), nullable=False),  # 'in_progress' | 'stop_loss' | 'take_profit' | 'expired'
        sa.Column("outcome_track_day", sa.Integer(), nullable=True),
        sa.Column("outcome_date", sa.Date(), nullable=True),
        sa.Column("final_cum_return", sa.Numeric(10, 6), nullable=True),
        sa.Column("peak_cum_return", sa.Numeric(10, 6), nullable=True),
        sa.Column("trough_cum_return", sa.Numeric(10, 6), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("rec_date", "ts_code", "strategy", "scoring_mode"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ningbo_outcomes_status",
        "recommendation_outcomes",
        ["outcome_status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_ningbo_outcomes_status", table_name="recommendation_outcomes", schema=SCHEMA)
    op.drop_table("recommendation_outcomes", schema=SCHEMA)
    op.drop_index("ix_ningbo_track_track_date", table_name="recommendation_tracking", schema=SCHEMA)
    op.drop_table("recommendation_tracking", schema=SCHEMA)
    op.drop_index("ix_ningbo_recs_mode_date", table_name="recommendations_daily", schema=SCHEMA)
    op.drop_index("ix_ningbo_recs_ts_code", table_name="recommendations_daily", schema=SCHEMA)
    op.drop_index("ix_ningbo_recs_rec_date", table_name="recommendations_daily", schema=SCHEMA)
    op.drop_table("recommendations_daily", schema=SCHEMA)
    op.drop_table("strategy_params", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA} CASCADE")
