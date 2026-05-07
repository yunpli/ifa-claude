"""SME unit registry and conversion helpers."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


@dataclass(frozen=True)
class UnitSpec:
    source_name: str
    source_field: str
    source_unit: str
    target_field: str
    target_unit: str
    conversion_factor: float
    rounding_policy: str = "round"


UNIT_REGISTRY: tuple[UnitSpec, ...] = (
    UnitSpec("moneyflow", "buy_sm_amount", "万元", "buy_sm_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "sell_sm_amount", "万元", "sell_sm_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "buy_md_amount", "万元", "buy_md_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "sell_md_amount", "万元", "sell_md_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "buy_lg_amount", "万元", "buy_lg_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "sell_lg_amount", "万元", "sell_lg_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "buy_elg_amount", "万元", "buy_elg_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "sell_elg_amount", "万元", "sell_elg_amount_yuan", "元", 10_000),
    UnitSpec("moneyflow", "net_mf_amount", "万元", "net_mf_amount_yuan", "元", 10_000),
    UnitSpec("daily", "amount", "千元", "amount_yuan", "元", 1_000),
    UnitSpec("daily_basic", "total_mv", "万元", "total_mv_yuan", "元", 10_000),
    UnitSpec("daily_basic", "circ_mv", "万元", "circ_mv_yuan", "元", 10_000),
)


def registry_by_target() -> dict[str, UnitSpec]:
    return {spec.target_field: spec for spec in UNIT_REGISTRY}


def to_yuan(value: Any, factor: float) -> int | None:
    """Convert a numeric source value to integer yuan.

    Missing values stay missing. Decimal rounding is deliberate: raw TuShare
    amounts are decimal financial quantities, and Python float rounding can
    create off-by-one noise in reconciliation checks.
    """
    if value is None:
        return None
    dec = Decimal(str(value)) * Decimal(str(factor))
    return int(dec.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def seed_unit_registry(engine) -> int:
    from sqlalchemy import text

    sql = text("""
        INSERT INTO sme.sme_unit_registry (
            source_name, source_field, source_unit, target_field, target_unit,
            conversion_factor, rounding_policy, last_verified_at
        ) VALUES (
            :source_name, :source_field, :source_unit, :target_field, :target_unit,
            :conversion_factor, :rounding_policy, now()
        )
        ON CONFLICT (source_name, source_field) DO UPDATE SET
            source_unit = EXCLUDED.source_unit,
            target_field = EXCLUDED.target_field,
            target_unit = EXCLUDED.target_unit,
            conversion_factor = EXCLUDED.conversion_factor,
            rounding_policy = EXCLUDED.rounding_policy,
            last_verified_at = now()
    """)
    rows = [spec.__dict__ for spec in UNIT_REGISTRY]
    with engine.begin() as conn:
        conn.execute(sql, rows)
    return len(rows)


def validate_unit_registry(engine) -> list[str]:
    from sqlalchemy import text

    expected = {(s.source_name, s.source_field): s for s in UNIT_REGISTRY}
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT source_name, source_field, target_field, conversion_factor
            FROM sme.sme_unit_registry
        """)).fetchall()
    found = {(r[0], r[1]): r for r in rows}
    errors: list[str] = []
    for key, spec in expected.items():
        row = found.get(key)
        if row is None:
            errors.append(f"missing unit mapping {key[0]}.{key[1]}")
            continue
        if row[2] != spec.target_field:
            errors.append(f"bad target for {key}: {row[2]} != {spec.target_field}")
        if abs(float(row[3]) - spec.conversion_factor) > 1e-9:
            errors.append(f"bad factor for {key}: {row[3]} != {spec.conversion_factor}")
    return errors
