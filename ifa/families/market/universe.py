"""A-share main report universe — index codes, sector taxonomy, sentiment caps.

V2.1: Migrated from fixed THS thematic boards to dynamic top SW L2 sectors.
Main-line themes derived from real-time SW data.

The main report uses these as the *fixed display universe*. We never let the
LLM choose indices or sectors; data is pre-fetched against this list and the
LLM only writes commentary.
"""
from __future__ import annotations

# ── Index family (展示顺序 = 表格行顺序) ─────────────────────────────────
MARKET_INDICES: list[tuple[str, str, str]] = [
    # (ts_code, display_name, role)
    ("000001.SH", "上证指数",   "权重 / 大盘风险偏好"),
    ("399001.SZ", "深证成指",   "成长风格"),
    ("399006.SZ", "创业板指",   "科技成长 / 高 beta"),
    ("000688.SH", "科创50",     "硬科技 / 成长锐度"),
    ("899050.BJ", "北证50",     "小市值活跃度"),
    ("000300.SH", "沪深300",    "核心资产 / 外资偏好"),
]

# ── 申万一级行业（用于板块轮动复盘） ────────────────────────────────────
SW_LEVEL1: list[tuple[str, str]] = [
    ("801010.SI", "农林牧渔"),
    ("801030.SI", "基础化工"),
    ("801040.SI", "钢铁"),
    ("801050.SI", "有色金属"),
    ("801080.SI", "电子"),
    ("801110.SI", "家用电器"),
    ("801120.SI", "食品饮料"),
    ("801130.SI", "纺织服饰"),
    ("801140.SI", "轻工制造"),
    ("801150.SI", "医药生物"),
    ("801160.SI", "公用事业"),
    ("801170.SI", "交通运输"),
    ("801180.SI", "房地产"),
    ("801200.SI", "商贸零售"),
    ("801210.SI", "社会服务"),
    ("801230.SI", "综合"),
    ("801710.SI", "建筑材料"),
    ("801720.SI", "建筑装饰"),
    ("801730.SI", "电力设备"),
    ("801740.SI", "国防军工"),
    ("801750.SI", "计算机"),
    ("801760.SI", "传媒"),
    ("801770.SI", "通信"),
    ("801780.SI", "银行"),
    ("801790.SI", "非银金融"),
    ("801880.SI", "汽车"),
    ("801890.SI", "机械设备"),
    ("801950.SI", "煤炭"),
    ("801960.SI", "石油石化"),
    ("801970.SI", "环保"),
    ("801980.SI", "美容护理"),
]


# ── 主线候选（动态：今日 SW L2 资金流 + 涨幅 top-N） ─────────────────────
# V2.1: 取代固定 THS 概念列表。实际选择逻辑见
# `data.fetch_main_lines()` —— 优先按 sector_moneyflow_sw_daily.net_amount
# 排序，fallback 到 raw_sw_daily.pct_change。
MAIN_LINE_TOP_N: int = 10

# ── 短线情绪指标阈值 ─────────────────────────────────────────────────────
# Used by `detect_market_temperature` heuristic before LLM commentary.
SENTIMENT_THRESHOLDS = {
    "limit_up_strong": 80,        # 涨停家数 >= 80 视为强情绪
    "limit_up_weak":   30,         # < 30 视为弱情绪
    "limit_down_caution": 20,      # 跌停 >= 20 视为风险释放显著
    "broke_limit_pct_high": 0.4,  # 炸板率 > 40% 分歧风险
    "high_streak_strong": 4,       # 连板高度 >= 4 视为情绪高位
}
