"""宁波短线策略报告 family.

A standalone short-term trading strategy report system, fully independent
from smartmoney. Reads raw data from smartmoney.raw_* via JOIN (no copy)
but writes to its own ningbo schema.

Strategies (Phase 1):
    - 选股六步曲       (six_step): MA/量/MACD/KDJ/RSI/WR 多头筛选
    - 神枪手           (sniper):   5/24 MA 回调买入
    - 聚宝盆           (treasure_basin): 阳-阴-阳 K线组合
    - 半年翻倍         (half_year_double): 周线共振强势

Holding period: 5-15 trading days
Target: ≥ +20% cumulative return, or stop loss on close < MA24
Tracking: 15 trading days from rec_date

Scoring modes:
    - heuristic (Phase 1+):  rule-based score 0-1
    - ml        (Phase 3+):  RF/XGB/LGBM/CatBoost stacking, with calibration
"""
__version__ = "0.1.0"
