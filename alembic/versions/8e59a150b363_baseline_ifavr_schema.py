"""baseline ifavr schema

Revision ID: 8e59a150b363
Revises:
Create Date: 2026-04-29 22:45:20.175198

Implements docs/database-schema.md v0.1.

Tables created:
  - report_runs, report_inputs, report_facts, report_signals,
    report_judgments, report_sections, report_references,
    report_model_outputs, report_reviews
  - macro_text_derived_indicators, macro_policy_event_memory

Conventions:
  - UUID PKs via gen_random_uuid() (pgcrypto extension)
  - JSONB for flexible payloads
  - TIMESTAMPTZ everywhere
  - Soft enums via TEXT + CHECK constraints
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "8e59a150b363"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ck(name: str, column: str, values: list[str]) -> sa.CheckConstraint:
    rendered = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({rendered})", name=name)


def upgrade() -> None:
    # Required PG extensions
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ── report_runs ─────────────────────────────────────────────────────────
    op.create_table(
        "report_runs",
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("market", sa.Text, nullable=False),
        sa.Column("report_family", sa.Text, nullable=False),
        sa.Column("report_type", sa.Text, nullable=False),
        sa.Column("report_date", sa.Date, nullable=False),
        sa.Column("slot", sa.Text, nullable=False),
        sa.Column("timezone", sa.Text, nullable=False, server_default=sa.text("'Asia/Shanghai'")),
        sa.Column("data_cutoff_at", postgresql.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'running'")),
        sa.Column("run_mode", sa.Text, nullable=False),
        sa.Column("triggered_by", sa.Text),
        sa.Column("template_version", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("output_html_path", sa.Text),
        sa.Column("output_json_path", sa.Text),
        sa.Column("output_md_path", sa.Text),
        sa.Column("started_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("completed_at", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("duration_seconds", sa.Numeric(10, 3)),
        sa.Column("fallback_used", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("error_summary", sa.Text),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_report_runs_market", "market",
            ["china_a", "hk", "us", "cross_asset"]),
        _ck("ck_report_runs_family", "report_family",
            ["macro", "asset", "tech", "main", "weekend", "briefing", "adhoc", "smartmoney"]),
        _ck("ck_report_runs_status", "status",
            ["running", "succeeded", "failed", "partial", "superseded"]),
        _ck("ck_report_runs_run_mode", "run_mode",
            ["test", "manual", "production"]),
    )
    op.create_index("ix_report_runs_identity", "report_runs",
                    ["market", "report_family", "report_type", "report_date", "slot"])
    op.create_index("ix_report_runs_ops", "report_runs",
                    ["run_mode", "status", "created_at"])
    op.create_index("ix_report_runs_date_slot", "report_runs",
                    ["report_date", "slot"])

    # ── report_inputs ───────────────────────────────────────────────────────
    op.create_table(
        "report_inputs",
        sa.Column("input_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("input_type", sa.Text, nullable=False),
        sa.Column("source_name", sa.Text, nullable=False),
        sa.Column("source_table_or_api", sa.Text),
        sa.Column("data_window_start", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("data_window_end", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("data_timing_label", sa.Text, nullable=False),
        sa.Column("row_count", sa.Integer),
        sa.Column("freshness_status", sa.Text, nullable=False),
        sa.Column("raw_snapshot_path", sa.Text),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_report_inputs_timing", "data_timing_label",
            ["latest_available", "previous_trading_day_confirmed",
             "overnight_to_cutoff", "text_derived_capture",
             "to_be_validated_today", "historical_memory"]),
        _ck("ck_report_inputs_freshness", "freshness_status",
            ["fresh", "stale", "missing", "partial"]),
    )
    op.create_index("ix_report_inputs_run", "report_inputs", ["report_run_id"])

    # ── report_facts ────────────────────────────────────────────────────────
    op.create_table(
        "report_facts",
        sa.Column("fact_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("section_key", sa.Text, nullable=False),
        sa.Column("fact_type", sa.Text, nullable=False),
        sa.Column("subject", sa.Text),
        sa.Column("fact_text", sa.Text),
        sa.Column("value_json", postgresql.JSONB),
        sa.Column("data_timing_label", sa.Text, nullable=False),
        sa.Column("source_reference_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("confidence", sa.Text, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_report_facts_timing", "data_timing_label",
            ["latest_available", "previous_trading_day_confirmed",
             "overnight_to_cutoff", "text_derived_capture",
             "to_be_validated_today", "historical_memory"]),
        _ck("ck_report_facts_confidence", "confidence",
            ["high", "medium", "low"]),
    )
    op.create_index("ix_report_facts_run_section", "report_facts",
                    ["report_run_id", "section_key"])

    # ── report_signals ──────────────────────────────────────────────────────
    op.create_table(
        "report_signals",
        sa.Column("signal_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("section_key", sa.Text, nullable=False),
        sa.Column("signal_type", sa.Text, nullable=False),
        sa.Column("signal_text", sa.Text),
        sa.Column("based_on_fact_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("direction", sa.Text, nullable=False),
        sa.Column("strength", sa.Text, nullable=False),
        sa.Column("confidence", sa.Text, nullable=False),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_report_signals_direction", "direction",
            ["up", "down", "flat", "mixed", "unknown"]),
        _ck("ck_report_signals_strength", "strength",
            ["strong", "medium", "weak"]),
        _ck("ck_report_signals_confidence", "confidence",
            ["high", "medium", "low"]),
    )
    op.create_index("ix_report_signals_run_section", "report_signals",
                    ["report_run_id", "section_key"])

    # ── report_judgments ────────────────────────────────────────────────────
    op.create_table(
        "report_judgments",
        sa.Column("judgment_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("section_key", sa.Text, nullable=False),
        sa.Column("judgment_type", sa.Text, nullable=False),
        sa.Column("judgment_text", sa.Text, nullable=False),
        sa.Column("target", sa.Text),
        sa.Column("horizon", sa.Text, nullable=False),
        sa.Column("confidence", sa.Text, nullable=False),
        sa.Column("validation_method", sa.Text),
        sa.Column("review_status", sa.Text, nullable=False, server_default=sa.text("'pending'")),
        sa.Column("superseded_by", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_judgments.judgment_id",
                                onupdate="CASCADE", ondelete="SET NULL")),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_report_judgments_confidence", "confidence",
            ["high", "medium", "low"]),
        _ck("ck_report_judgments_review", "review_status",
            ["pending", "validated", "partial", "failed", "not_applicable"]),
    )
    op.create_index("ix_report_judgments_run_section", "report_judgments",
                    ["report_run_id", "section_key"])
    op.create_index("ix_report_judgments_review", "report_judgments",
                    ["review_status", "created_at"])

    # ── report_sections ─────────────────────────────────────────────────────
    op.create_table(
        "report_sections",
        sa.Column("section_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("section_key", sa.Text, nullable=False),
        sa.Column("section_title", sa.Text, nullable=False),
        sa.Column("section_order", sa.Integer, nullable=False),
        sa.Column("content_markdown", sa.Text),
        sa.Column("content_json", postgresql.JSONB),
        sa.Column("input_fact_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("input_signal_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("input_judgment_ids", postgresql.ARRAY(postgresql.UUID(as_uuid=True))),
        sa.Column("prompt_name", sa.Text),
        sa.Column("prompt_version", sa.Text),
        sa.Column("model_output_id", postgresql.UUID(as_uuid=True)),  # FK added below
        sa.Column("fallback_used", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("report_run_id", "section_key", name="uq_report_sections_run_section"),
    )

    # ── report_references ───────────────────────────────────────────────────
    op.create_table(
        "report_references",
        sa.Column("reference_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_name", sa.Text),
        sa.Column("source_table_or_api", sa.Text),
        sa.Column("title", sa.Text),
        sa.Column("url", sa.Text),
        sa.Column("url_hash", sa.LargeBinary),
        sa.Column("publish_time", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("data_time", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("evidence_sentence", sa.Text),
        sa.Column("used_in_section_key", sa.Text),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_report_references_run_type", "report_references",
                    ["report_run_id", "source_type"])
    op.create_index("ix_report_references_url_hash", "report_references", ["url_hash"])

    # ── report_model_outputs ────────────────────────────────────────────────
    op.create_table(
        "report_model_outputs",
        sa.Column("model_output_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("section_key", sa.Text, nullable=False),
        sa.Column("prompt_name", sa.Text, nullable=False),
        sa.Column("prompt_version", sa.Text, nullable=False),
        sa.Column("model_name", sa.Text, nullable=False),
        sa.Column("endpoint", sa.Text, nullable=False),
        sa.Column("input_json_path", sa.Text),
        sa.Column("output_json_path", sa.Text),
        sa.Column("parsed_json", postgresql.JSONB),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("latency_seconds", sa.Numeric(10, 3)),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_report_model_outputs_status", "status",
            ["parsed", "parse_failed", "fallback_used", "error"]),
        _ck("ck_report_model_outputs_endpoint", "endpoint",
            ["primary", "fallback"]),
    )

    # back-link FK from report_sections.model_output_id
    op.create_foreign_key(
        "fk_report_sections_model_output",
        "report_sections", "report_model_outputs",
        ["model_output_id"], ["model_output_id"],
        onupdate="CASCADE", ondelete="SET NULL",
    )

    # ── report_reviews ──────────────────────────────────────────────────────
    op.create_table(
        "report_reviews",
        sa.Column("review_id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("judgment_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_judgments.judgment_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("review_report_run_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("report_runs.report_run_id",
                                onupdate="CASCADE", ondelete="RESTRICT"),
                  nullable=False),
        sa.Column("review_result", sa.Text, nullable=False),
        sa.Column("evidence_text", sa.Text),
        sa.Column("evidence_json", postgresql.JSONB),
        sa.Column("lesson", sa.Text),
        sa.Column("should_update_rule", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_report_reviews_result", "review_result",
            ["validated", "partial", "failed", "not_applicable"]),
    )
    op.create_index("ix_report_reviews_judgment", "report_reviews", ["judgment_id"])

    # ── macro_text_derived_indicators ───────────────────────────────────────
    op.create_table(
        "macro_text_derived_indicators",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("indicator_name", sa.Text, nullable=False),
        sa.Column("reported_period", sa.Text),
        sa.Column("value", sa.Numeric),
        sa.Column("unit", sa.Text),
        sa.Column("yoy", sa.Numeric),
        sa.Column("mom", sa.Numeric),
        sa.Column("release_type", sa.Text, nullable=False),
        sa.Column("publisher_or_origin", sa.Text),
        sa.Column("source_table", sa.Text),
        sa.Column("source_name", sa.Text),
        sa.Column("source_title", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("source_publish_time", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("evidence_sentence", sa.Text),
        sa.Column("extraction_model", sa.Text),
        sa.Column("extraction_prompt_version", sa.Text),
        sa.Column("confidence", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'extracted'")),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_mtdi_release_type", "release_type",
            ["official_release", "media_report_citing_official_data",
             "forecast_or_expectation", "market_commentary",
             "unrelated_or_false_positive", "unknown"]),
        _ck("ck_mtdi_confidence", "confidence", ["high", "medium", "low"]),
        _ck("ck_mtdi_status", "status",
            ["extracted", "confirmed", "revised", "rejected"]),
        sa.UniqueConstraint("source_url", "indicator_name", "reported_period",
                            name="uq_mtdi_source_indicator_period"),
    )
    op.create_index("ix_mtdi_indicator_period_status", "macro_text_derived_indicators",
                    ["indicator_name", "reported_period", "status"])
    op.create_index("ix_mtdi_publish_desc", "macro_text_derived_indicators",
                    [sa.text("source_publish_time DESC")])

    # ── macro_policy_event_memory ───────────────────────────────────────────
    op.create_table(
        "macro_policy_event_memory",
        sa.Column("id", postgresql.UUID(as_uuid=True),
                  primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", sa.Text, nullable=False, unique=True),
        sa.Column("event_date", sa.Date),
        sa.Column("event_window_start", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("event_window_end", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("policy_dimension", sa.Text, nullable=False),
        sa.Column("event_title", sa.Text),
        sa.Column("source_name", sa.Text),
        sa.Column("source_table", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("publish_time", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("summary", sa.Text),
        sa.Column("policy_signal", sa.Text, nullable=False),
        sa.Column("affected_areas", postgresql.JSONB),
        sa.Column("market_implication", sa.Text),
        sa.Column("carry_forward_until", sa.Date),
        sa.Column("confidence", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        _ck("ck_mpem_policy_signal", "policy_signal",
            ["升温", "平稳", "降温", "延续既有框架", "无新增信号"]),
        _ck("ck_mpem_confidence", "confidence", ["high", "medium", "low"]),
        _ck("ck_mpem_status", "status", ["active", "expired", "superseded"]),
    )
    op.create_index("ix_mpem_status_carry", "macro_policy_event_memory",
                    ["status", "carry_forward_until"])
    op.create_index("ix_mpem_dim_status", "macro_policy_event_memory",
                    ["policy_dimension", "status"])


def downgrade() -> None:
    # Drop in reverse FK order
    op.drop_table("macro_policy_event_memory")
    op.drop_table("macro_text_derived_indicators")
    op.drop_table("report_reviews")
    op.drop_constraint("fk_report_sections_model_output", "report_sections", type_="foreignkey")
    op.drop_table("report_model_outputs")
    op.drop_table("report_references")
    op.drop_table("report_sections")
    op.drop_table("report_judgments")
    op.drop_table("report_signals")
    op.drop_table("report_facts")
    op.drop_table("report_inputs")
    op.drop_table("report_runs")
    # leave pgcrypto extension in place
