"""SmartMoney ML models — Phase 4.

P1 ML layer sits on top of P0 rule-based factors:
  features      — feature matrix from factor_daily + sector_state_daily
  dataset       — train/val/predict dataset assembly + next-day labels
  logistic      — LogisticRegression wrapper (fastest, most interpretable)
  random_forest — RandomForestClassifier wrapper
  xgboost_model — XGBoost wrapper (M1-safe: max_depth ≤ 6, n_est ≤ 200)
  news_catalyst — LLM news catalyst scoring (OpenAI-compatible relay)
  persistence   — model pickle/load/versioning to ~/claude/ifaenv/models/
"""
from __future__ import annotations
