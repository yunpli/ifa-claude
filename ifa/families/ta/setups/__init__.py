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

SETUPS: dict[str, SetupFn] = {
    "T1_BREAKOUT": T1_BREAKOUT,
}

__all__ = ["Candidate", "SetupContext", "SetupFn", "SETUPS"]
