"""Alembic env: pulls connection URL from ifa.config (run-mode aware).

Database selection:
  - default: ifavr (production database name)
  - if IFA_RUN_MODE=test  → ifavr_test
  - or pass `-x db=ifavr_test` to alembic for a one-off override
"""
from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from ifa.config import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # baseline migration is hand-written, no autogenerate

# Resolve DB URL from settings, allowing -x db=... override
settings = get_settings()
x_args = context.get_x_argument(as_dictionary=True)
db_override = x_args.get("db")
db_url = settings.database_url(db=db_override) if db_override else settings.database_url()
config.set_main_option("sqlalchemy.url", db_url)


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
