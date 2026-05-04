"""TA-M4 setups — 18 candidate detectors organized by family.

Families:
    T1-T3 trend       · P1-P3 pullback     · R1-R3 reversal
    F1-F3 pattern     · V1-V2 volume       · S1-S3 sector     · C1-C2 chip

Each setup is a callable `(SetupContext) -> Candidate | None`. Use the
`SETUPS` registry to iterate or look up by name.
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext, SetupFn
from ifa.families.ta.setups.t1_breakout import T1_BREAKOUT
from ifa.families.ta.setups.t2_pullback_resume import T2_PULLBACK_RESUME
from ifa.families.ta.setups.t3_acceleration import T3_ACCELERATION
from ifa.families.ta.setups.p1_ma20_pullback import P1_MA20_PULLBACK
from ifa.families.ta.setups.p2_gap_fill import P2_GAP_FILL
from ifa.families.ta.setups.p3_tight_consolidation import P3_TIGHT_CONSOLIDATION
from ifa.families.ta.setups.r1_double_bottom import R1_DOUBLE_BOTTOM
from ifa.families.ta.setups.r2_hs_bottom import R2_HS_BOTTOM
from ifa.families.ta.setups.r3_hammer import R3_HAMMER
from ifa.families.ta.setups.f1_flag import F1_FLAG
from ifa.families.ta.setups.f2_triangle import F2_TRIANGLE
from ifa.families.ta.setups.f3_rectangle import F3_RECTANGLE
from ifa.families.ta.setups.v1_vol_price_up import V1_VOL_PRICE_UP
from ifa.families.ta.setups.v2_quiet_coil import V2_QUIET_COIL
from ifa.families.ta.setups.s1_sector_resonance import S1_SECTOR_RESONANCE
from ifa.families.ta.setups.s2_leader_followthrough import S2_LEADER_FOLLOWTHROUGH
from ifa.families.ta.setups.s3_laggard_catchup import S3_LAGGARD_CATCHUP
from ifa.families.ta.setups.c1_chip_concentrated import C1_CHIP_CONCENTRATED
from ifa.families.ta.setups.c2_chip_loose import C2_CHIP_LOOSE

SETUPS: dict[str, SetupFn] = {
    "T1_BREAKOUT": T1_BREAKOUT,
    "T2_PULLBACK_RESUME": T2_PULLBACK_RESUME,
    "T3_ACCELERATION": T3_ACCELERATION,
    "P1_MA20_PULLBACK": P1_MA20_PULLBACK,
    "P2_GAP_FILL": P2_GAP_FILL,
    "P3_TIGHT_CONSOLIDATION": P3_TIGHT_CONSOLIDATION,
    "R1_DOUBLE_BOTTOM": R1_DOUBLE_BOTTOM,
    "R2_HS_BOTTOM": R2_HS_BOTTOM,
    "R3_HAMMER": R3_HAMMER,
    "F1_FLAG": F1_FLAG,
    "F2_TRIANGLE": F2_TRIANGLE,
    "F3_RECTANGLE": F3_RECTANGLE,
    "V1_VOL_PRICE_UP": V1_VOL_PRICE_UP,
    "V2_QUIET_COIL": V2_QUIET_COIL,
    "S1_SECTOR_RESONANCE": S1_SECTOR_RESONANCE,
    "S2_LEADER_FOLLOWTHROUGH": S2_LEADER_FOLLOWTHROUGH,
    "S3_LAGGARD_CATCHUP": S3_LAGGARD_CATCHUP,
    "C1_CHIP_CONCENTRATED": C1_CHIP_CONCENTRATED,
    "C2_CHIP_LOOSE": C2_CHIP_LOOSE,
}

__all__ = ["Candidate", "SetupContext", "SetupFn", "SETUPS"]
