"""research period factor and PDF extract memory

Revision ID: j8k9l0m1n2o3
Revises: i7j8k9l0m1n2
Create Date: 2026-05-04

"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "j8k9l0m1n2o3"
down_revision = "i7j8k9l0m1n2"
branch_labels = None
depends_on = None

SCHEMA = "research"


def upgrade() -> None:
    op.create_table(
        "period_factor_decomposition",
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("factor_family", sa.String(length=48), nullable=False),
        sa.Column("factor_name", sa.String(length=64), nullable=False),
        sa.Column("period", sa.String(length=8), nullable=False),
        sa.Column("period_type", sa.String(length=16), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=True),
        sa.Column("unit", sa.String(length=24), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_hash", sa.String(length=64), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("ts_code", "factor_family", "factor_name", "period"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_period_factor_ts_family",
        "period_factor_decomposition",
        ["ts_code", "factor_family", "period"],
        schema=SCHEMA,
    )

    op.create_table(
        "pdf_extract_cache",
        sa.Column("url_hash", sa.String(length=64), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source_date", sa.String(length=10), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("extractable", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("text_hash", sa.String(length=64), nullable=True),
        sa.Column("extract_json", postgresql.JSONB(), nullable=False),
        sa.Column("extracted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("url_hash"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_research_pdf_extract_ts_date",
        "pdf_extract_cache",
        ["ts_code", "source_date"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_research_pdf_extract_ts_date", table_name="pdf_extract_cache", schema=SCHEMA)
    op.drop_table("pdf_extract_cache", schema=SCHEMA)
    op.drop_index("ix_research_period_factor_ts_family", table_name="period_factor_decomposition", schema=SCHEMA)
    op.drop_table("period_factor_decomposition", schema=SCHEMA)
