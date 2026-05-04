"""research: create research schema v0 — company_identity, caches, runs, sections, judgments, events, users

Revision ID: e1f2a3b4c5d6
Revises: d4e2f6c5a83b
Create Date: 2026-05-03

Purpose
-------
Bootstrap the `research` schema for the Research Family (V2.2 Part A).

Tables created:
  · company_identity        — Master stock dimension (SW L1/L2 enriched)
  · api_cache               — Tushare API response cache with TTL
  · computed_cache          — Derived factor / score cache
  · report_runs             — One row per report run (quick/standard/deep)
  · report_sections         — One row per section per run (§01-§18)
  · report_judgments        — Risk signals / watchpoints extracted per run
  · company_event_memory    — LLM-extracted structured events (announcement, IRM QA, etc.)
  · users                   — User records with daily quota
  · usage_log               — Per-request audit trail

Naming conventions enforced:
  · Monetary fields suffix _yuan; share count _share; percentage _pct
  · No FK across schema boundaries (soft reference only)
  · All timestamps TIMESTAMPTZ UTC; business dates DATE (BJT)
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d4e2f6c5a83b"
branch_labels = None
depends_on = None

SCHEMA = "research"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")

    # ── company_identity ──────────────────────────────────────────────────
    op.create_table(
        "company_identity",
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("exchange", sa.String(8)),
        sa.Column("market", sa.String(16)),
        sa.Column("list_date", sa.Date),
        sa.Column("list_status", sa.CHAR(1)),
        sa.Column("sw_l1_code", sa.String(12)),
        sa.Column("sw_l1_name", sa.String(32)),
        sa.Column("sw_l2_code", sa.String(12)),
        sa.Column("sw_l2_name", sa.String(32)),
        sa.Column("last_refreshed", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("ts_code"),
        sa.UniqueConstraint("name"),
        schema=SCHEMA,
    )

    # ── api_cache ─────────────────────────────────────────────────────────
    op.create_table(
        "api_cache",
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("api_name", sa.String(64), nullable=False),
        sa.Column("params_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.JSON, nullable=False),
        sa.Column("fetched_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("ts_code", "api_name", "params_hash"),
        schema=SCHEMA,
    )
    op.create_index("ix_research_api_cache_expires", "api_cache", ["expires_at"], schema=SCHEMA)

    # ── computed_cache ────────────────────────────────────────────────────
    op.create_table(
        "computed_cache",
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("compute_key", sa.String(128), nullable=False),
        sa.Column("inputs_hash", sa.String(64), nullable=False),
        sa.Column("result_json", sa.JSON, nullable=False),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("ts_code", "compute_key"),
        schema=SCHEMA,
    )

    # ── report_runs ───────────────────────────────────────────────────────
    op.create_table(
        "report_runs",
        sa.Column("run_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("company_name", sa.String(64)),
        sa.Column("report_type", sa.Text,
                  sa.CheckConstraint("report_type IN ('quick','standard','deep')")),
        sa.Column("scope_json", sa.JSON),
        sa.Column("status", sa.Text,
                  sa.CheckConstraint("status IN ('running','succeeded','partial','failed','cached')")),
        sa.Column("triggered_by", sa.Text),
        sa.Column("user_id", sa.UUID),
        sa.Column("template_version", sa.String(32)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("run_mode", sa.Text,
                  sa.CheckConstraint("run_mode IN ('test','manual','production')")),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("duration_seconds", sa.Numeric),
        sa.Column("output_html_path", sa.Text),
        sa.Column("output_pdf_path", sa.Text),
        sa.Column("output_json_path", sa.Text),
        sa.Column("llm_calls", sa.Integer, server_default="0"),
        sa.Column("llm_tokens", sa.Integer, server_default="0"),
        sa.Column("fallback_used", sa.Boolean, server_default="false"),
        sa.Column("error_summary", sa.Text),
        sa.PrimaryKeyConstraint("run_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_report_runs_ts_code_started",
        "report_runs", ["ts_code", "started_at"],
        schema=SCHEMA,
    )

    # ── report_sections ───────────────────────────────────────────────────
    op.create_table(
        "report_sections",
        sa.Column("section_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID, nullable=False),
        sa.Column("section_key", sa.Text, nullable=False),
        sa.Column("section_order", sa.Integer),
        sa.Column("content_json", sa.JSON),
        sa.Column("status", sa.Text,
                  sa.CheckConstraint("status IN ('ok','degraded','skipped')")),
        sa.Column("skip_reason", sa.Text),
        sa.Column("model_used", sa.Text),
        sa.Column("prompt_name", sa.Text),
        sa.Column("prompt_version", sa.Text),
        sa.Column("latency_seconds", sa.Numeric),
        sa.PrimaryKeyConstraint("section_id"),
        sa.UniqueConstraint("run_id", "section_key"),
        sa.ForeignKeyConstraint(["run_id"], [f"{SCHEMA}.report_runs.run_id"]),
        schema=SCHEMA,
    )

    # ── report_judgments ──────────────────────────────────────────────────
    op.create_table(
        "report_judgments",
        sa.Column("judgment_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.UUID, nullable=False),
        sa.Column("judgment_type", sa.Text),
        sa.Column("severity", sa.Text,
                  sa.CheckConstraint("severity IN ('high','medium','low','info')")),
        sa.Column("text", sa.Text),
        sa.Column("data_basis", sa.JSON),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("judgment_id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{SCHEMA}.report_runs.run_id"]),
        schema=SCHEMA,
    )

    # ── company_event_memory ──────────────────────────────────────────────
    op.create_table(
        "company_event_memory",
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("ts_code", sa.String(12), nullable=False),
        sa.Column("capture_date", sa.Date, nullable=False),
        sa.Column("event_type", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("polarity", sa.Text,
                  sa.CheckConstraint("polarity IN ('positive','neutral','negative')")),
        sa.Column("importance", sa.Text,
                  sa.CheckConstraint("importance IN ('high','medium','low')")),
        sa.Column("source_type", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("publish_time", sa.TIMESTAMP(timezone=True)),
        sa.Column("extraction_model", sa.Text),
        sa.Column("extraction_prompt_version", sa.Text),
        sa.Column("valid_until", sa.Date),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("event_id"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_event_memory_ts_code_date",
        "company_event_memory", ["ts_code", "capture_date"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_event_memory_type_importance",
        "company_event_memory", ["event_type", "importance"],
        schema=SCHEMA,
    )

    # ── users ─────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("user_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("external_id", sa.Text, nullable=False),
        sa.Column("external_type", sa.Text, server_default="'telegram'"),
        sa.Column("display_name", sa.Text),
        sa.Column("tier", sa.Text, server_default="'free'"),
        sa.Column("daily_quota", sa.Integer, server_default="5"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("external_id", "external_type"),
        schema=SCHEMA,
    )

    # ── usage_log ─────────────────────────────────────────────────────────
    op.create_table(
        "usage_log",
        sa.Column("log_id", sa.UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID),
        sa.Column("run_id", sa.UUID),
        sa.Column("report_type", sa.Text),
        sa.Column("ts_code", sa.Text),
        sa.Column("cache_hit", sa.Boolean),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("log_id"),
        sa.ForeignKeyConstraint(["user_id"], [f"{SCHEMA}.users.user_id"]),
        sa.ForeignKeyConstraint(["run_id"], [f"{SCHEMA}.report_runs.run_id"]),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_usage_log_user_created",
        "usage_log", ["user_id", "created_at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("usage_log", schema=SCHEMA)
    op.drop_table("users", schema=SCHEMA)
    op.drop_table("company_event_memory", schema=SCHEMA)
    op.drop_table("report_judgments", schema=SCHEMA)
    op.drop_table("report_sections", schema=SCHEMA)
    op.drop_table("report_runs", schema=SCHEMA)
    op.drop_table("computed_cache", schema=SCHEMA)
    op.drop_table("api_cache", schema=SCHEMA)
    op.drop_table("company_identity", schema=SCHEMA)
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
