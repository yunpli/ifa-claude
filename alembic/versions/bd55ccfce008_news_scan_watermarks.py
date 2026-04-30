"""news_scan_watermarks

Revision ID: bd55ccfce008
Revises: 8e59a150b363
Create Date: 2026-04-29 23:21:44.225063

Adds the watermark table that lets each scan job (macro text capture, macro
policy memory, …) advance incrementally instead of re-scanning the full
source every run.

Key design:
  - Composite PK (job_name, source_label) — one row per (job, news source).
  - `last_publish_time_scanned` is the high-water mark of *source publish_time*
    we've successfully processed; the next run starts strictly after it.
  - `last_run_mode` records whether the watermark came from a test or real run.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "bd55ccfce008"
down_revision: str | Sequence[str] | None = "8e59a150b363"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "news_scan_watermarks",
        sa.Column("job_name", sa.Text, nullable=False),
        sa.Column("source_label", sa.Text, nullable=False),
        sa.Column("last_publish_time_scanned", postgresql.TIMESTAMP(timezone=True)),
        sa.Column("last_run_at", postgresql.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.text("now()")),
        sa.Column("last_run_mode", sa.Text, nullable=False),
        sa.Column("rows_scanned_total", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("candidates_filtered_total", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.Column("candidates_extracted_total", sa.BigInteger, nullable=False, server_default=sa.text("0")),
        sa.PrimaryKeyConstraint("job_name", "source_label", name="pk_news_scan_watermarks"),
        sa.CheckConstraint("last_run_mode IN ('test', 'manual', 'production')",
                           name="ck_nsw_run_mode"),
    )


def downgrade() -> None:
    op.drop_table("news_scan_watermarks")
