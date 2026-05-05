"""Chinese labels for regimes / setups / triggers — surface to user, never expose codes."""
from __future__ import annotations

REGIME_ZH: dict[str, str] = {
    "trend_continuation": "趋势延续",
    "early_risk_on": "风险偏好回升初期",
    "weak_rebound": "弱反弹",
    "range_bound": "区间震荡",
    "sector_rotation": "板块轮动",
    "emotional_climax": "情绪高潮",
    "distribution_risk": "顶部派发",
    "cooldown": "退潮冷却",
    "high_difficulty": "无序难辨",
}

REGIME_TONE: dict[str, str] = {
    "trend_continuation": "积极",
    "early_risk_on": "积极",
    "weak_rebound": "中性",
    "range_bound": "中性",
    "sector_rotation": "中性",
    "emotional_climax": "谨慎",
    "distribution_risk": "谨慎",
    "cooldown": "谨慎",
    "high_difficulty": "中性",
}

REGIME_DESCRIPTION: dict[str, str] = {
    "trend_continuation": "上证 20MA 上行 + 涨家数明显占优 + 量能稳健，趋势型机会延续。",
    "early_risk_on": "涨停个股显著放量、广度强势翻多，风险偏好处于回升初期，做多窗口打开。",
    "weak_rebound": "20MA 仍下行但短期反弹，量能偏弱，本质为下跌中继的反弹，不宜重仓追高。",
    "range_bound": "波动率偏低、涨跌家数均衡，缺乏明确方向，适合波段而非趋势策略。",
    "sector_rotation": "大盘相对平静但行业涨跌幅离散度高，资金在板块间快速换手。",
    "emotional_climax": "涨停超过 120 + 高位连板 + 北向极强流入，情绪过热，进入分歧倒计时。",
    "distribution_risk": "高位放量但涨家数收窄、跌停增加，主力派发迹象明显，需警惕回调。",
    "cooldown": "涨停数环比骤减、跌家数大于涨家数，短线退潮，应降低仓位、回避追高。",
    "high_difficulty": "各项信号互相矛盾，难以归类，建议观望。",
}

SETUP_ZH: dict[str, dict[str, str]] = {
    "T1_BREAKOUT":            {"name": "突破启动", "family": "趋势"},
    "T2_PULLBACK_RESUME":     {"name": "回踩续涨", "family": "趋势"},
    "T3_ACCELERATION":        {"name": "加速冲刺", "family": "趋势"},
    "P1_MA20_PULLBACK":       {"name": "20 日线回踩", "family": "回踩"},
    "P2_GAP_FILL":            {"name": "缺口回补", "family": "回踩"},
    "P3_TIGHT_CONSOLIDATION": {"name": "紧密整理", "family": "回踩"},
    "R1_DOUBLE_BOTTOM":       {"name": "双底反转", "family": "反转"},
    "R2_HS_BOTTOM":           {"name": "头肩底", "family": "反转"},
    "R3_HAMMER":              {"name": "锤子线", "family": "反转"},
    "F1_FLAG":                {"name": "旗形整理", "family": "形态"},
    "F2_TRIANGLE":            {"name": "三角收敛", "family": "形态"},
    "F3_RECTANGLE":           {"name": "矩形整理", "family": "形态"},
    "V1_VOL_PRICE_UP":        {"name": "量价齐升", "family": "量价"},
    "V2_QUIET_COIL":          {"name": "缩量蓄势", "family": "量价"},
    "S1_SECTOR_RESONANCE":    {"name": "板块共振", "family": "板块"},
    "S2_LEADER_FOLLOWTHROUGH": {"name": "龙头跟风", "family": "板块"},
    "S3_LAGGARD_CATCHUP":     {"name": "落后补涨", "family": "板块"},
    "C1_CHIP_CONCENTRATED":   {"name": "筹码集中", "family": "筹码"},
    "C2_CHIP_LOOSE":          {"name": "筹码松动（警示）", "family": "筹码"},
    "O1_INST_PERSISTENT_BUY": {"name": "机构连续抢筹", "family": "主力资金"},
    "O2_LHB_INST_BUY":        {"name": "龙虎榜机构净买入", "family": "主力资金"},
    "O3_LIMIT_SEAL_STRENGTH": {"name": "涨停封单强度", "family": "主力资金"},
    "D1_DOUBLE_TOP":          {"name": "双顶反转（警示）", "family": "顶部反转"},
    "D2_HS_TOP":              {"name": "头肩顶（警示）", "family": "顶部反转"},
    "D3_SHOOTING_STAR":       {"name": "流星线（警示）", "family": "顶部反转"},
    "Z1_ZSCORE_EXTREME":      {"name": "极端 Z-score", "family": "统计"},
    "Z2_OVERSOLD_REBOUND":    {"name": "超卖反弹", "family": "统计"},
    "Z3_RANGE_FADE":          {"name": "横盘 fade-rally", "family": "统计"},
    "R4_SUPPORT_BOUNCE":      {"name": "MA60 支撑反弹", "family": "反转"},
    "E1_EVENT_CATALYST":      {"name": "事件催化", "family": "事件"},
}

TRIGGER_ZH: dict[str, str] = {
    # T1 / breakout
    "close>ma20": "收于 20 日线上方",
    "ma20>ma60": "20 日线高于 60 日线（多头排列）",
    "20d_breakout": "突破近 20 日新高",
    "decisive_above_ma20": "明显站上 20 日线（≥1.02 倍）",
    "regime_tailwind": "体制利好",
    "volume_confirmation": "量能放大确认",
    "volume_breakout": "突破伴量放大",
    # T2
    "uptrend_stack": "趋势多头排列",
    "touched_ma20": "回踩 20 日线",
    "back_above_ma5": "重返 5 日线上方",
    "actual_ma20_touch": "实际触及 20 日线",
    "rsi_balanced": "RSI 处于均衡区",
    # T3
    "full_ma_stack": "5/10/20/60 日线完美多头排列",
    "macd_golden": "MACD 金叉区",
    "macd_positive": "MACD 红柱",
    "5d_ret>=5%": "近 5 日涨幅 ≥5%",
    "strong_acceleration": "近 5 日涨幅 ≥10%",
    # P1/P2/P3
    "defended_close": "回踩后收盘守住",
    "net_pullback_5d": "近 5 日净回调",
    "above_ma5": "收于 5 日线上方",
    "rsi_oversold_room": "RSI 仍有空间",
    "gap_filled": "缺口已回补",
    "above_gap_bottom": "守住缺口下沿",
    "full_reclaim": "完全回收缺口",
    "upper_half_close": "收盘位于当日上半区",
    "prior_20d_gain>=10%": "前 20 日累计 ≥10%",
    "tight_5d_box<=5%": "近 5 日箱体 ≤5%",
    "very_tight_box": "极窄箱体（≤3%）",
    "volume_drying": "量能萎缩",
    # R1/R2/R3
    "double_bottom_pattern": "双底形态",
    "neckline_reclaim": "突破颈线",
    "post_weakness_regime": "处于走弱后修复体制",
    "macd_dif_positive": "MACD DIF 转正",
    "inverse_hs": "倒头肩底形态",
    "neckline_break": "突破颈线",
    "symmetric_shoulders": "肩部对称",
    "downtrend_20d<=-8%": "近 20 日跌幅 ≥8%",
    "small_body": "实体偏小",
    "long_lower_shadow": "长下影线",
    "bullish_close": "阳线收盘",
    "deep_drop": "近 20 日深跌（≥15%）",
    # F1/F2/F3
    "pole>=8%": "前期上涨旗杆 ≥8%",
    "pole>=10%": "前期上涨旗杆 ≥10%",
    "tight_flag<=7%": "旗面窄幅整理",
    "tight_flag<=9%": "旗面窄幅整理",
    "downward_drift": "旗面缓慢下倾",
    "near_breakout": "接近突破",
    "near_top_of_flag": "靠近旗面顶部",
    "strong_pole": "旗杆强劲（≥15%）",
    "range_contracting": "区间持续收敛",
    "upside_breakout": "向上突破",
    "strong_contraction": "极强收敛",
    "rectangle_box": "矩形整理",
    # V1/V2
    "vol_ratio>=1.5": "量比 ≥1.5",
    "strong_5d_return": "近 5 日涨幅 ≥10%",
    "volume_exceptional": "异常放量（量比 ≥2）",
    "vol_ratio<0.7": "量比 <0.7（缩量）",
    "tight_5d_range": "5 日窄幅",
    "very_quiet": "极致缩量（量比 <0.5）",
    "rsi_neutral": "RSI 处于中性区",
    # S1/S2/S3
    "L1>=1%": "一级板块 ≥1%",
    "L2>=1.5%": "二级板块 ≥1.5%",
    "L2>=2%": "二级板块 ≥2%",
    "stock_ret>=2%": "个股涨幅 ≥2%",
    "L2_leading": "二级板块领涨（≥3%）",
    "stock_strong": "个股强势（≥5%）",
    "outperforms_L2": "强于所属二级板块",
    "top_30pct_in_L2": "板块内 Top 30%",
    "top_10pct_in_L2": "板块内 Top 10%",
    "stock_was_laggard": "个股相对滞涨",
    "catchup_today": "今日跟随补涨",
    "true_laggard": "真实滞涨（20 日 ≤0%）",
    # C1/C2
    "chip_concentrated<=15%": "筹码集中（成本带 ≤15%）",
    "very_concentrated": "筹码高度集中（≤10%）",
    "balanced_winners": "盈利盘比例均衡",
    "chip_loose>=25%": "筹码松散（成本带 ≥25%）",
    "winner_rate>=80%": "盈利盘比例 ≥80%",
    "20d_ret>=15%": "近 20 日累计 ≥15%",
    "regime_warning": "体制警示",
    "extreme_winner_rate": "盈利盘比例极高（≥90%）",
    "extreme_run": "近期累计涨幅 ≥30%",
    # O1/O2/O3 — order flow
    "inst_5d_inflow>=1%": "5 日机构净流入 ≥1% 流通市值",
    "strong_inst_inflow": "机构净流入显著",
    "lhb_inst_buy_recent": "近 5 日龙虎榜机构买入",
    "lhb_net_buy>=0.5%float": "龙虎榜净买入 ≥0.5% 流通市值",
    "lhb_inst_seat_today": "今日机构席位净买",
    "lhb_strong_net_buy": "龙虎榜净买入显著",
    "lhb_inst_persistent": "近 5 日机构持续买入",
    "limit_sealed": "涨停封死（未炸板）",
    "seal_ratio>=1%": "封单 ≥1% 流通市值",
    "strong_seal": "封单结构强",
    "inst_inflow_aligned": "机构资金同向流入",
    # D1/D2/D3 — top reversal
    "double_top": "双顶形态",
    "post_runup": "前期已大幅上涨",
    "decisive_break": "破位明显",
    "hs_top": "头肩顶形态",
    "shooting_star": "流星线",
    "post_strong_runup": "前 20 日累计 ≥15%",
    "no_continuation": "今日无延续",
    "long_upper_shadow": "长上影线",
    "climax_volume": "高位放量",
    # Z1/Z2 — statistical
    "z_extreme_long": "极端负 Z（统计性反弹候选）",
    "z_extreme_short": "极端正 Z（统计性衰竭候选）",
    "very_extreme": "极端程度高",
    "rsi_oversold": "RSI 超卖",
    "rsi_overbought": "RSI 超买",
    "5d_drawdown": "近 5 日回撤 ≥5%",
    "stabilizing": "今日企稳",
    "deeply_oversold": "深度超卖",
    "turn_volume": "转折成交放大",
    # E1 — event-driven
    "event:forecast": "业绩预告日",
    "event:express": "业绩快报日",
    "event:disclosure_pre": "披露窗口临近",
    "positive_polarity": "正面催化",
    "negative_polarity": "负面预警",
    "imminent_disclosure": "披露 ≤5 个交易日",
}


def regime_label(code: str | None) -> str:
    if not code:
        return "（未分类）"
    return REGIME_ZH.get(code, code)


def regime_tone(code: str | None) -> str:
    if not code:
        return "中性"
    return REGIME_TONE.get(code, "中性")


def regime_description(code: str | None) -> str:
    if not code:
        return ""
    return REGIME_DESCRIPTION.get(code, "")


def setup_label(code: str) -> str:
    return SETUP_ZH.get(code, {"name": code})["name"]


def setup_family(code: str) -> str:
    return SETUP_ZH.get(code, {"family": "其他"}).get("family", "其他")


def trigger_label(code: str) -> str:
    return TRIGGER_ZH.get(code, code)
