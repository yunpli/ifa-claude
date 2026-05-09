"""stock: diagnostic run persistence

Revision ID: p0q1r2s3t4u5
Revises: o3p4q5r6s7t8
Create Date: 2026-05-08

Adds an audit-only persistence surface for Stock Edge single-stock diagnostic
runs.  These tables record rendered diagnostic evidence and synthesis output;
they do not drive production YAML, auto-promotion, or report delivery crons.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "p0q1r2s3t4u5"
down_revision = "o3p4q5r6s7t8"
branch_labels = None
depends_on = None

SCHEMA = "stock"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    op.create_table(
        "diagnostic_runs",
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ts_code", sa.Text, nullable=False),
        sa.Column("name", sa.Text),
        sa.Column("requested_at", sa.DateTime(timezone=True)),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of_trade_date", sa.Date, nullable=False),
        sa.Column("run_mode", sa.Text, nullable=False, server_default="manual"),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("conclusion", sa.Text, nullable=False),
        sa.Column("confidence", sa.Text, nullable=False),
        sa.Column("logic_version", sa.Text, nullable=False),
        sa.Column("output_paths_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("perspective_status_json", postgresql.JSONB, nullable=False),
        sa.Column("evidence_freshness_json", postgresql.JSONB, nullable=False),
        sa.Column("synthesis_json", postgresql.JSONB, nullable=False),
        sa.Column("manifest_json", postgresql.JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.PrimaryKeyConstraint("run_id", name="pk_stock_diagnostic_runs"),
        sa.CheckConstraint("status IN ('succeeded', 'partial', 'failed')", name="ck_stock_diagnostic_runs_status"),
        schema=SCHEMA,
    )
    op.create_index("idx_stock_diagnostic_runs_ts_date", "diagnostic_runs", ["ts_code", "as_of_trade_date"], schema=SCHEMA)
    op.create_index("idx_stock_diagnostic_runs_created", "diagnostic_runs", ["created_at"], schema=SCHEMA)

    op.create_table(
        "diagnostic_perspective_evidence",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("perspective_key", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("view", sa.Text, nullable=False),
        sa.Column("freshness_status", sa.Text, nullable=False),
        sa.Column("latency_ms", sa.Float),
        sa.Column("source_tables_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_evidence_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("missing_required_json", postgresql.JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("source_as_of", sa.Date),
        sa.Column("summary", sa.Text, nullable=False),
        sa.Column("evidence_json", postgresql.JSONB, nullable=False),
        sa.Column("raw_json", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["run_id"], [f"{SCHEMA}.diagnostic_runs.run_id"], ondelete="CASCADE"),
        schema=SCHEMA,
    )
    op.create_index(
        "idx_stock_diagnostic_evidence_run_key",
        "diagnostic_perspective_evidence",
        ["run_id", "perspective_key"],
        schema=SCHEMA,
    )
    op.create_index(
        "idx_stock_diagnostic_evidence_key_status",
        "diagnostic_perspective_evidence",
        ["perspective_key", "status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("idx_stock_diagnostic_evidence_key_status", table_name="diagnostic_perspective_evidence", schema=SCHEMA)
    op.drop_index("idx_stock_diagnostic_evidence_run_key", table_name="diagnostic_perspective_evidence", schema=SCHEMA)
    op.drop_table("diagnostic_perspective_evidence", schema=SCHEMA)
    op.drop_index("idx_stock_diagnostic_runs_created", table_name="diagnostic_runs", schema=SCHEMA)
    op.drop_index("idx_stock_diagnostic_runs_ts_date", table_name="diagnostic_runs", schema=SCHEMA)
    op.drop_table("diagnostic_runs", schema=SCHEMA)
