"""stock: create stock schema v0 — analysis_record, sections, support_resistance, tracking, watchlist, lock

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-05-03

Tables (PostgreSQL `stock` schema):
  · analysis_record      — One row per analysis run (fast/deep/update)
  · report_sections      — Sections per run (§01–§09/§16)
  · support_resistance   — Computed S/R levels per stock per day
  · tracking_log         — T+N outcome tracking
  · user_watchlist       — User watchlist with priority
  · user_context         — Personalisation layer (V2.2.3, built now)
  · analysis_lock        — Distributed dedup lock (5-min stale cleanup)

DuckDB initialisation happens separately in ifa/families/stock/db/duckdb_client.py
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None

SCHEMA = "stock"
N = sa.Numeric


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ── analysis_record ────────────────────────────────────────────────────
    op.create_table(
        "analysis_record",
        sa.Column("record_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("analysis_type", sa.Text,
                  sa.CheckConstraint(
                      "analysis_type IN ('fast','deep','update','morning_refresh','intraday')"
                  )),
        sa.Column("base_record_id", sa.UUID),
        sa.Column("triggered_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("triggered_by_user", sa.UUID),
        sa.Column("data_cutoff", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text,
                  sa.CheckConstraint("status IN ('running','succeeded','partial','failed','cached')")),
        sa.Column("conclusion_label", sa.String(16),
                  sa.CheckConstraint(
                      "conclusion_label IN ('high_watch','normal_watch','cautious','avoid')"
                  )),
        sa.Column("conclusion_text", sa.Text),
        sa.Column("key_levels_json", sa.JSON),
        sa.Column("setup_match_json", sa.JSON),
        sa.Column("validation_json", sa.JSON),
        sa.Column("invalidation_json", sa.JSON),
        sa.Column("next_watch_json", sa.JSON),
        sa.Column("forecast_json", sa.JSON),
        sa.Column("duration_seconds", N),
        sa.Column("llm_calls", sa.Integer),
        sa.Column("llm_tokens", sa.Integer),
        sa.Column("output_html_path", sa.Text),
        sa.Column("output_pdf_path", sa.Text),
        sa.Column("error_summary", sa.Text),
        sa.PrimaryKeyConstraint("record_id"),
        sa.ForeignKeyConstraint(["base_record_id"], [f"{SCHEMA}.analysis_record.record_id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_analysis_record_ts_code_triggered",
        "analysis_record", ["ts_code", "triggered_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_analysis_record_type_status",
        "analysis_record", ["analysis_type", "status"],
        schema=SCHEMA,
    )

    # ── report_sections ────────────────────────────────────────────────────
    op.create_table(
        "report_sections",
        sa.Column("section_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("record_id", sa.UUID, nullable=False),
        sa.Column("section_key", sa.Text),
        sa.Column("section_order", sa.Integer),
        sa.Column("content_json", sa.JSON),
        sa.Column("status", sa.Text),
        sa.Column("skip_reason", sa.Text),
        sa.Column("model_used", sa.Text),
        sa.Column("prompt_version", sa.Text),
        sa.Column("latency_seconds", N),
        sa.PrimaryKeyConstraint("section_id"),
        sa.UniqueConstraint("record_id", "section_key"),
        sa.ForeignKeyConstraint(["record_id"], [f"{SCHEMA}.analysis_record.record_id"]),
        schema=SCHEMA,
    )

    # ── support_resistance ────────────────────────────────────────────────
    op.create_table(
        "support_resistance",
        sa.Column("sr_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("trade_date", sa.Date, nullable=False),
        sa.Column("price", N(12, 4), nullable=False),
        sa.Column("sr_type", sa.Text,
                  sa.CheckConstraint("sr_type IN ('support','resistance','pivot')")),
        sa.Column("sources", sa.ARRAY(sa.Text)),
        sa.Column("strength", N(8, 4)),
        sa.Column("distance_pct", N(8, 4)),
        sa.Column("confidence", sa.Text),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("sr_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_stock_support_resistance_ts_code_date",
        "support_resistance", ["ts_code", "trade_date"],
        schema=SCHEMA,
    )

    # ── tracking_log ───────────────────────────────────────────────────────
    op.create_table(
        "tracking_log",
        sa.Column("track_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("record_id", sa.UUID, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("eval_date", sa.Date, nullable=False),
        sa.Column("days_after_base", sa.Integer),
        sa.Column("price_change_pct", N(8, 4)),
        sa.Column("validation_status", sa.Text,
                  sa.CheckConstraint(
                      "validation_status IN ('confirmed','partial','invalidated','pending','expired')"
                  )),
        sa.Column("validation_evidence", sa.JSON),
        sa.PrimaryKeyConstraint("track_id"),
        sa.UniqueConstraint("record_id", "eval_date"),
        sa.ForeignKeyConstraint(["record_id"], [f"{SCHEMA}.analysis_record.record_id"]),
        schema=SCHEMA,
    )

    # ── user_watchlist ─────────────────────────────────────────────────────
    op.create_table(
        "user_watchlist",
        sa.Column("watchlist_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("added_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("priority", sa.Text,
                  sa.CheckConstraint("priority IN ('key','normal','condition_only')")),
        sa.Column("note", sa.Text),
        sa.Column("last_record_id", sa.UUID),
        sa.PrimaryKeyConstraint("watchlist_id"),
        sa.UniqueConstraint("user_id", "ts_code"),
        sa.ForeignKeyConstraint(["last_record_id"], [f"{SCHEMA}.analysis_record.record_id"]),
        schema=SCHEMA,
    )

    # ── user_context ───────────────────────────────────────────────────────
    op.create_table(
        "user_context",
        sa.Column("user_id", sa.UUID, nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("holding_status", sa.Text),
        sa.Column("cost_basis", N(12, 4)),
        sa.Column("position_size_pct", N(8, 4)),
        sa.Column("horizon", sa.Text),
        sa.Column("style", sa.Text),
        sa.Column("risk_tolerance", sa.Text),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("user_id", "ts_code"),
        schema=SCHEMA,
    )

    # ── analysis_lock ──────────────────────────────────────────────────────
    op.create_table(
        "analysis_lock",
        sa.Column("lock_key", sa.String(64), nullable=False),
        sa.Column("holder_record_id", sa.UUID),
        sa.Column("acquired_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("waiter_count", sa.Integer, server_default="0"),
        sa.PrimaryKeyConstraint("lock_key"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    for tbl in [
        "analysis_lock", "user_context", "user_watchlist",
        "tracking_log", "support_resistance", "report_sections", "analysis_record",
    ]:
        op.drop_table(tbl, schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
