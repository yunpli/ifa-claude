"""Settings for iFA, loaded from the local secrets file (never the repo).

Resolution order:
  1. Real environment variables (highest precedence — useful for CI / overrides)
  2. The secrets file at IFA_SECRETS_FILE (default: ~/claude/ifaenv/secrets/.env)
  3. The repo-level .env (non-secret defaults only)
"""
from __future__ import annotations

import os
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunMode(str, Enum):
    test = "test"
    manual = "manual"
    production = "production"


_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_SECRETS = Path("/Users/neoclaw/claude/ifaenv/secrets/.env")


def _load_dotenv_chain() -> None:
    """Load secrets first (lower precedence), then real env wins on conflict."""
    secrets_path = Path(os.environ.get("IFA_SECRETS_FILE", str(_DEFAULT_SECRETS)))
    if secrets_path.exists():
        load_dotenv(secrets_path, override=False)
    repo_env = _REPO_ROOT / ".env"
    if repo_env.exists():
        load_dotenv(repo_env, override=False)


_load_dotenv_chain()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # ── Runtime ────────────────────────────────────────────────────────────
    run_mode: RunMode = Field(default=RunMode.manual, alias="IFA_RUN_MODE")
    output_root: Path = Field(
        default=Path("/Users/neoclaw/claude/ifaenv/out"), alias="IFA_OUTPUT_ROOT"
    )
    log_root: Path = Field(
        default=Path("/Users/neoclaw/claude/ifaenv/logs"), alias="IFA_LOG_ROOT"
    )
    timezone: str = Field(default="Asia/Shanghai", alias="IFA_TIMEZONE")

    # ── TuShare ────────────────────────────────────────────────────────────
    tushare_token: SecretStr = Field(alias="TUSHARE_TOKEN")

    # ── LLM primary ────────────────────────────────────────────────────────
    llm_primary_base_url: str = Field(alias="LLM_PRIMARY_BASE_URL")
    llm_primary_api_key: SecretStr = Field(alias="LLM_PRIMARY_API_KEY")
    llm_primary_model: str = Field(alias="LLM_PRIMARY_MODEL")
    llm_primary_alias: str = Field(default="primary", alias="LLM_PRIMARY_ALIAS")

    # ── LLM fallback ───────────────────────────────────────────────────────
    llm_fallback_base_url: str = Field(alias="LLM_FALLBACK_BASE_URL")
    llm_fallback_api_key: SecretStr = Field(alias="LLM_FALLBACK_API_KEY")
    llm_fallback_model: str = Field(alias="LLM_FALLBACK_MODEL")

    # ── PostgreSQL ─────────────────────────────────────────────────────────
    pg_host: str = Field(default="127.0.0.1", alias="PG_HOST")
    pg_port: int = Field(default=55432, alias="PG_PORT")
    pg_user: str = Field(default="ifa", alias="PG_USER")
    pg_password: SecretStr = Field(alias="PG_PASSWORD")
    pg_db_production: str = Field(default="ifavr", alias="PG_DB_PRODUCTION")
    pg_db_test: str = Field(default="ifavr_test", alias="PG_DB_TEST")

    @property
    def active_database(self) -> str:
        """Pick the DB name based on the run mode (test → ifavr_test)."""
        return self.pg_db_test if self.run_mode == RunMode.test else self.pg_db_production

    def database_url(self, *, db: str | None = None, driver: str = "psycopg") -> str:
        name = db or self.active_database
        pwd = self.pg_password.get_secret_value()
        return f"postgresql+{driver}://{self.pg_user}:{pwd}@{self.pg_host}:{self.pg_port}/{name}"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
