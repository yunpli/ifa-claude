"""SmartMoney backtest engine — factor IC / RankIC / TopN / group returns.

Pipeline:
  1. engine.py   — loads factor + return data, runs per-date evaluations
  2. metrics.py  — pure-math IC / RankIC / TopN / quintile functions
  3. runner.py   — DB persistence + CLI orchestration entry point

Typical usage (via CLI):
  ifa smartmoney backtest --start 2025-11-01 --end 2026-04-30
  ifa smartmoney params freeze --name v2026_05 --from-backtest <run_id>
"""
