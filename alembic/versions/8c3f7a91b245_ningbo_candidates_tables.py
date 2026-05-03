"""Ningbo full candidate pool tables for Phase 3.B ML training.

Adds two tables for the unselected candidate universe:

  ningbo.candidates_daily       — ALL strategy hits (not just top-5).
                                  Each (rec_date, ts_code, strategy) row.
                                  ~310 rows/day × 562 days ≈ 174k total.

  ningbo.candidate_outcomes     — 15-day forward labels for every candidate.
                                  Used as ML training labels (sample bias-free).

Why we need this:
  The existing recommendations_daily only stores top-5 picks AFTER the
  heuristic select_top_n filter, creating sample selection bias when training
  ML on it.  To learn a true ranking model, we need the full candidate pool.

Revision ID: 8c3f7a91b245
Revises: 213b3798f97d
Create Date: 2026-05-02
"""
from alembic import op

revision = "8c3f7a91b245"
down_revision = "213b3798f97d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ningbo.candidates_daily (
            rec_date         DATE         NOT NULL,
            ts_code          VARCHAR(20)  NOT NULL,
            strategy         VARCHAR(32)  NOT NULL,
            confidence_score NUMERIC      NOT NULL,
            rec_price        NUMERIC      NOT NULL,
            signal_meta      JSONB,
            inserted_at      TIMESTAMPTZ  DEFAULT NOW(),
            PRIMARY KEY (rec_date, ts_code, strategy),
            CONSTRAINT ck_candidates_strategy CHECK (
                strategy IN ('sniper', 'treasure_basin', 'half_year_double')
            )
        );
        CREATE INDEX IF NOT EXISTS ix_candidates_daily_date
            ON ningbo.candidates_daily (rec_date);
        CREATE INDEX IF NOT EXISTS ix_candidates_daily_ts
            ON ningbo.candidates_daily (ts_code, rec_date);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ningbo.candidate_outcomes (
            rec_date          DATE         NOT NULL,
            ts_code           VARCHAR(20)  NOT NULL,
            strategy          VARCHAR(32)  NOT NULL,
            outcome_status    VARCHAR(20),
            outcome_track_day INT,
            outcome_date      DATE,
            final_cum_return  NUMERIC,
            peak_cum_return   NUMERIC,
            trough_cum_return NUMERIC,
            n_tracking_days   INT,
            updated_at        TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (rec_date, ts_code, strategy),
            CONSTRAINT ck_cand_outcome_status CHECK (
                outcome_status IN ('take_profit', 'stop_loss', 'expired', 'in_progress')
            )
        );
        CREATE INDEX IF NOT EXISTS ix_cand_outcomes_date
            ON ningbo.candidate_outcomes (rec_date);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ningbo.candidate_outcomes;")
    op.execute("DROP TABLE IF EXISTS ningbo.candidates_daily;")
