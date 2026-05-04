"""E1 事件催化命中 — earnings forecast / express / disclosure-window catalyst.

Triggers (any):
  · event_type_today is one of {forecast, express, disclosure_pre}
  · Polarity is positive when known (None tolerated as neutral)
  · Optional: days_to_disclosure <= 5 marks an imminent earnings window

Source: ta.catalyst_event_memory (populated by event_etl from Tushare's
forecast / express / disclosure_date interfaces).

Score:
  base 0.5
  + 0.20 if event_polarity == 'positive'
  + 0.10 if days_to_disclosure is not None and days_to_disclosure <= 5
"""
from __future__ import annotations

from ifa.families.ta.setups.base import Candidate, SetupContext


def E1_EVENT_CATALYST(ctx: SetupContext) -> Candidate | None:
    if not ctx.event_type_today:
        return None

    triggers = [f"event:{ctx.event_type_today}"]
    score = 0.5

    if ctx.event_polarity == "positive":
        score += 0.20
        triggers.append("positive_polarity")
    elif ctx.event_polarity == "negative":
        # Bearish event — still emit candidate so report can warn,
        # but no positive boost.
        triggers.append("negative_polarity")

    if ctx.days_to_disclosure is not None and ctx.days_to_disclosure <= 5:
        score += 0.10
        triggers.append("imminent_disclosure")

    return Candidate(
        ts_code=ctx.ts_code,
        trade_date=ctx.trade_date,
        setup_name="E1_EVENT_CATALYST",
        score=min(score, 1.0),
        triggers=tuple(triggers),
        evidence={
            "close": ctx.close_today,
            "event_type": ctx.event_type_today,
            "event_polarity": ctx.event_polarity,
            "days_to_disclosure": ctx.days_to_disclosure,
        },
    )
