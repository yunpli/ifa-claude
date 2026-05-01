"""SmartMoney factor computation modules.

Phase 3.A (P0 rule-based):
  common   — math utilities shared across all factor modules
  liquidity — market water-level computation → market_state_daily
  flow      — 4 explainable sector factors → factor_daily

Phase 3.B (domain judgment, Opus):
  role      — sector role identification (主线/中军/轮动/防守/催化/退潮)
  cycle     — 7-stage sentiment cycle state machine
  leader    — intra-sector leader stock identification
  candidate — candidate stock pool for tomorrow's watchlist
"""
from __future__ import annotations
