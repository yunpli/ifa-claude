"""Ningbo model registry + promotion log for Champion-Challenger.

Two production "slots" — aggressive and conservative — plus heuristic
baseline. Each slot has at most one active model at any time. Weekly
retraining trains all candidate models and applies slot-specific
promotion rules to maybe replace the active model.

Revision ID: d4e2f6c5a83b
Revises: 8c3f7a91b245
Create Date: 2026-05-02
"""
from alembic import op


revision = "d4e2f6c5a83b"
down_revision = "8c3f7a91b245"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ningbo.model_registry (
            model_version       VARCHAR(64) NOT NULL,
            slot                VARCHAR(20) NOT NULL,
            model_name          VARCHAR(32) NOT NULL,
            objective           VARCHAR(32) NOT NULL,
            feature_set_id      VARCHAR(64) NOT NULL,
            feature_columns     JSONB,
            train_range_start   DATE,
            train_range_end     DATE,
            oos_range_start     DATE,
            oos_range_end       DATE,
            n_train             INT,
            n_oos               INT,
            metrics             JSONB,
            artifact_path       TEXT,
            is_active           BOOLEAN NOT NULL DEFAULT FALSE,
            activated_at        TIMESTAMPTZ,
            deactivated_at      TIMESTAMPTZ,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (model_version, slot),
            CONSTRAINT ck_registry_slot CHECK (slot IN ('aggressive', 'conservative', 'heuristic')),
            CONSTRAINT ck_registry_objective CHECK (
                objective IN ('classifier', 'ranker', 'ensemble', 'baseline')
            )
        );

        -- Only one active per slot at any time
        CREATE UNIQUE INDEX IF NOT EXISTS ix_one_active_per_slot
            ON ningbo.model_registry (slot)
            WHERE is_active = TRUE;

        CREATE INDEX IF NOT EXISTS ix_registry_active
            ON ningbo.model_registry (slot, is_active);
    """)

    op.execute("""
        CREATE TABLE IF NOT EXISTS ningbo.promotion_log (
            id              SERIAL PRIMARY KEY,
            promoted_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            slot            VARCHAR(20) NOT NULL,
            new_version     VARCHAR(64),
            old_version     VARCHAR(64),
            event_type      VARCHAR(20) NOT NULL,  -- 'promoted', 'rollback', 'no_change'
            reason          TEXT,
            decision_data   JSONB,
            CONSTRAINT ck_promo_slot CHECK (slot IN ('aggressive', 'conservative', 'heuristic')),
            CONSTRAINT ck_promo_event CHECK (
                event_type IN ('promoted', 'rollback', 'no_change', 'manual_override', 'emergency_rollback')
            )
        );

        CREATE INDEX IF NOT EXISTS ix_promo_log_slot_time
            ON ningbo.promotion_log (slot, promoted_at DESC);
    """)

    # Extend recommendations_daily.scoring_mode to allow new ML modes
    # (drop existing CHECK if any, add wider one)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'ck_recommendations_scoring_mode'
            ) THEN
                ALTER TABLE ningbo.recommendations_daily
                    DROP CONSTRAINT ck_recommendations_scoring_mode;
            END IF;
            ALTER TABLE ningbo.recommendations_daily
                ADD CONSTRAINT ck_recommendations_scoring_mode CHECK (
                    scoring_mode IN ('heuristic', 'ml_aggressive', 'ml_conservative', 'ml')
                );
        EXCEPTION WHEN OTHERS THEN NULL;  -- if no prior constraint, just add
        END $$;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ningbo.promotion_log;")
    op.execute("DROP TABLE IF EXISTS ningbo.model_registry;")
