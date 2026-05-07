"""Logical source resolver for SME.

MVP-1 runs in co-located mode and reads existing smartmoney raw tables. The
resolver keeps business code away from physical table literals so standalone
`sme_raw_*` can be added later without rewiring feature logic.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text


@dataclass(frozen=True)
class PhysicalSource:
    logical_name: str
    schema: str
    table: str

    @property
    def fqtn(self) -> str:
        return f"{self.schema}.{self.table}"


SMARTMONEY_SOURCES: dict[str, PhysicalSource] = {
    "moneyflow": PhysicalSource("moneyflow", "smartmoney", "raw_moneyflow"),
    "daily": PhysicalSource("daily", "smartmoney", "raw_daily"),
    "daily_basic": PhysicalSource("daily_basic", "smartmoney", "raw_daily_basic"),
    "sw_daily": PhysicalSource("sw_daily", "smartmoney", "raw_sw_daily"),
    "sw_member": PhysicalSource("sw_member", "smartmoney", "raw_sw_member"),
    "sw_member_monthly": PhysicalSource("sw_member_monthly", "smartmoney", "sw_member_monthly"),
}


def resolve_source(logical_name: str, *, source_mode: str = "prefer_smartmoney") -> PhysicalSource:
    if source_mode != "prefer_smartmoney":
        raise ValueError(f"MVP-1 supports source_mode=prefer_smartmoney only, got {source_mode!r}")
    try:
        return SMARTMONEY_SOURCES[logical_name]
    except KeyError as exc:
        raise KeyError(f"Unknown SME logical source {logical_name!r}") from exc


def table_exists(engine, source: PhysicalSource) -> bool:
    sql = text("""
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = :schema AND table_name = :table
        )
    """)
    with engine.connect() as conn:
        return bool(conn.execute(sql, {"schema": source.schema, "table": source.table}).scalar())


def validate_sources(engine, *, source_mode: str = "prefer_smartmoney") -> list[str]:
    errors: list[str] = []
    for name in ["moneyflow", "daily", "daily_basic", "sw_daily", "sw_member"]:
        src = resolve_source(name, source_mode=source_mode)
        if not table_exists(engine, src):
            errors.append(f"missing source {src.fqtn}")
    return errors
