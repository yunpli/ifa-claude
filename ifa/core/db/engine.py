"""SQLAlchemy engine factory + ping.

ORM models will live in `ifa.core.db.models` once the schema design is approved.
"""
from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from ifa.config import Settings, get_settings

_engines: dict[str, Engine] = {}


def get_engine(settings: Settings | None = None, *, db: str | None = None) -> Engine:
    s = settings or get_settings()
    url = s.database_url(db=db)
    if url not in _engines:
        _engines[url] = create_engine(url, future=True, pool_pre_ping=True)
    return _engines[url]


def ping_database(settings: Settings | None = None, *, db: str | None = None) -> tuple[bool, str]:
    """Returns (ok, message). Used by healthcheck."""
    try:
        eng = get_engine(settings, db=db)
        with eng.connect() as conn:
            version = conn.execute(text("SELECT version()")).scalar_one()
        return True, str(version).split(",", 1)[0]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
