"""Glossary of common financial terms for iFA reports.

Terms are plain-language explanations aimed at non-specialist investors.
Used by templates via the `ifa_term(text)` filter which wraps known terms
in a hover-tooltip span.
"""
from __future__ import annotations

import re

GLOSSARY: dict[str, str] = {
    # ── Market mechanics ──────────────────────────────────────────────────
    "北向资金": "通过沪深港通从香港流入A股的外资净买入额，常作为外资态度的风向标",
    "南向资金": "通过港股通从内地流入港股的资金净额",
    "量价背离": "价格上涨但成交量反而萎缩，提示上涨可能缺乏持续性",
    "换手率": "当日成交量÷流通股数，越高代表交易越活跃，主力换手意愿越强",
    "融资余额": "投资者借券商的钱买股票的未还金额总和，余额高说明市场杠杆偏高",
    "封单比例": "涨停板委托买入金额÷当日成交额，比例越高封板越稳固",
    "主力资金": "超大单（≥100万）+大单（≥20万）的净买入，代表机构或大资金的方向",
    "板块轮动": "资金从已大涨板块流出、流入相对滞涨板块的周期性切换",
    "做多情绪": "市场整体倾向于买入看涨的氛围，常用涨停比、北向流入等指标衡量",
    "缩量": "成交量明显萎缩，可能是资金观望或趋势即将反转的信号",
    "放量": "成交量明显放大，通常伴随价格突破或主力建仓/出货",
    "强势整理": "股价小幅回落但未破关键支撑位，准备再次上攻的横盘蓄势",
    "均线压制": "股价在均线下方运行，均线对上涨形成阻力",
    "均线支撑": "股价在均线上方运行，均线对下跌形成支撑",
    # ── Industry / chain terms ────────────────────────────────────────────
    "成本传导": "上游原材料涨价→中游制造商成本上升→下游消费品价格提高的连锁过程",
    "产业链": "从原材料→生产制造→分销→最终消费者的完整价值创造链条",
    "算力链": "从AI芯片→服务器→数据中心→云服务的完整AI算力产业链",
    "利润传导": "价格变化如何在产业链上下游之间传导和再分配利润",
    # ── Tech / AI terms ───────────────────────────────────────────────────
    "ASIC": "专用集成电路，专门为特定任务（AI推理/挖矿）设计的芯片，比通用芯片效率更高",
    "算力": "处理AI计算任务的能力，单位常用FLOPS（每秒浮点运算次数）表示",
    "大模型": "参数量数十亿到数千亿的AI语言/多模态模型，如GPT、Claude、DeepSeek",
    "算力瓶颈": "AI训练和推理受到芯片或数据中心供给限制，导致需求远超供给",
    "推理端": "AI模型训练完成后，向用户提供服务时的运算环节，比训练更频繁",
    "训练端": "AI模型从海量数据学习参数的阶段，算力需求最大",
    # ── Sector/policy labels ──────────────────────────────────────────────
    "稳增长": "政府托底经济增速的政策方向，通常涉及基建、地产松绑、消费补贴",
    "新质生产力": "以科技创新为驱动的高质量增长方式，包括AI、绿能、生物制造等",
    "地产信用": "房地产行业的融资条件与企业债务偿还能力",
    "顺周期": "随经济周期同向波动的板块，如有色、钢铁、化工",
    "逆周期": "与经济周期反向或独立运行的板块，如公用事业、消费必需品",
    "龙头类型": "板块内价格发现能力最强的代表股，其涨跌对板块有带动效应",
    # ── Risk / quant terms ────────────────────────────────────────────────
    "拥挤度": "同一方向押注资金占比，越拥挤反转风险越高",
    "回撤": "从最高点下跌的幅度，衡量持仓风险的常用指标",
    "超买": "价格短期涨幅过大，技术指标显示可能需要回调修正",
    "超卖": "价格短期跌幅过大，技术指标显示可能出现反弹",
    "止损位": "预设的最大亏损价位，触及后应卖出以控制风险",
    "支撑位": "股价下跌时可能获得买盘支撑、止跌企稳的价格区域",
    "压力位": "股价上涨时可能遭遇抛压、难以突破的价格区域",
}

# Pre-compile a single regex that matches any glossary term (longest first)
_SORTED_TERMS = sorted(GLOSSARY.keys(), key=len, reverse=True)
_PATTERN: re.Pattern | None = None


def _get_pattern() -> re.Pattern:
    global _PATTERN
    if _PATTERN is None:
        escaped = [re.escape(t) for t in _SORTED_TERMS]
        _PATTERN = re.compile("|".join(escaped))
    return _PATTERN


def annotate(text: str, *, max_annotations: int = 3) -> str:
    """Wrap up to `max_annotations` glossary terms in <span class="ifa-term"> tooltips.

    Replaces only the first occurrence of each term to avoid cluttering output.
    Safe to call with None or empty string.
    """
    if not text:
        return text or ""
    pat = _get_pattern()
    seen: set[str] = set()
    count = 0

    def _replace(m: re.Match) -> str:
        nonlocal count
        term = m.group(0)
        if term in seen or count >= max_annotations:
            return term
        seen.add(term)
        count += 1
        defn = GLOSSARY[term].replace('"', "&quot;")
        return f'<span class="ifa-term" data-def="{defn}">{term}</span>'

    return pat.sub(_replace, text)
