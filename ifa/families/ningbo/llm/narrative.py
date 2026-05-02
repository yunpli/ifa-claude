"""LLM narrative augmentation for new ningbo recommendations.

Generates 80-120 字 Chinese explanation per recommendation.  Pure read-only
augmentation — does NOT modify rules or scores.

Design philosophy
-----------------
The narrative must do three things in three sentences:
    1. State what triggered (with concrete numbers from signal_meta)
    2. Explain why this signal is worth attention (industry / price position / volume)
    3. Give a SPECIFIC risk hint with a price level or indicator threshold

Template套话 ("出现X信号，建议关注，注意风险") is the failure mode we
guard against by:
  - Strict word/phrase blacklist in system prompt
  - Per-strategy few-shot examples that show variety
  - temperature=0.5 (encourages variation without going off-topic)

Output is stored in `ningbo.recommendations_daily.llm_narrative`.

If LLM call fails, falls back to a deterministic template-based narrative
so the report still renders (degraded but functional).
"""
from __future__ import annotations

import logging
from typing import Any

from ifa.core.llm import LLMClient

logger = logging.getLogger(__name__)

NARRATIVE_MIN_CHARS = 70
NARRATIVE_MAX_CHARS = 140
LLM_TEMPERATURE = 0.5
LLM_MAX_TOKENS = 220   # generous: 120 chars CN ≈ 200 tokens
DEFAULT_TIMEOUT_SECONDS = 60.0


# ─── System prompt — strict and detailed ───────────────────────────────────────

SYSTEM_PROMPT = """你是一位有十年实战经验的中国 A 股短线交易员，专注 5-15 天持仓的宁波派打法。

任务：根据给出的策略信号数据，为该股写一段 80-120 字的客观解读。

【硬性要求】
1. 字数严格 80-120 字（含标点符号），少于 70 或多于 140 视为不合格
2. 三句话结构：
   - 第一句：客观陈述触发的形态/信号，必须包含至少一个具体数字（价格、涨跌幅、量比、均线值等）
   - 第二句：分析为什么该信号在当前位置值得关注（行业地位、近期表现、资金面、技术位置等其中一项）
   - 第三句：给一个明确的止损或观察价位，使用"若…则…"句式
3. 禁用以下词汇：建议、推荐、投资、买入、必涨、稳赚、强烈推荐、绝对、肯定、目标价、看涨、看好
4. 用第三人称客观叙述，不要出现"我"、"个人认为"、"笔者"
5. 不要把你看到的所有数字都罗列出来，挑最关键的 1-2 个具体数字使用
6. 不要使用反问句、感叹号
7. 每段必须有自己的措辞特点，避免句式重复

【信号术语解读】
- 神枪手 strike_1: 5 日均线刚上穿 24 日均线后，价格首次回调测试 24 日均线（"生命线"）获得支撑
- 神枪手 strike_2: 第二次回调测试 24 日均线，宁波派认为这是最高置信度的入场点
- 神枪手 strike_3p: 第三次或更多次回调，支撑可靠性下降
- 聚宝盆: T-2/T-1/T0 三日 K 线呈"阳-小K-阳"形态，T-1 缩量、T0 放量突破 T-2 收盘价
- 半年翻倍: 日线 5/24 均线金叉 + 5/60 量线放大 + 周线 MACD 上穿 0 轴的多周期共振
- 多策略共振: 同一只股票同时被两个或更多策略识别为候选

【输出】
直接输出解读文字，不要前缀"解读："、不要 markdown、不要引号包裹。"""


# ─── Few-shot examples per strategy ────────────────────────────────────────────
# Use real-style numbers; show variety in sentence structure / vocabulary.

FEW_SHOT_EXAMPLES = {
    "sniper": [
        {
            "signal": "神枪手 strike_2，5MA 11.42 站上 24MA 11.18，今日最低 11.20 精准触及生命线后收 11.55，量能较 5 日均萎缩 28%",
            "narrative": (
                "今日最低 11.20 精准触及 24 日均线 11.18 后收红 11.55，"
                "为该股自 4 月 8 日 5/24 金叉以来第二次回踩生命线，"
                "宁波派最看重的二次确认形态成立，量能萎缩 28% 显示无主动抛压。"
                "若次日跌破 11.10 则支撑失效，需立即离场。"
            ),
        },
        {
            "signal": "神枪手 strike_1，cross 仅 3 个交易日，5MA 8.45 vs 24MA 8.42，今日 low 8.40 缩量回踩",
            "narrative": (
                "5/24 金叉后第三个交易日首次回调到 24 日均线 8.42 附近，"
                "今日最低 8.40 略破后随即被买回，收 8.66 成交量低于 5 日均量 22%。"
                "首次确认信号置信度尚未充分验证，"
                "若明日不能站稳 8.40 上方则该轮上行节奏被打破。"
            ),
        },
    ],
    "treasure_basin": [
        {
            "signal": "聚宝盆 完美形态，T0 突破 T-2 收盘 5.41%，T-1 缩量至 T-2 的 75%，T0 量能 T-1 的 1.46 倍，处于 MA24 附近",
            "narrative": (
                "T-2 阳线收 8.88 后 T-1 缩量小阳收 8.89，T0 放量 1.46 倍上攻收 9.36，"
                "完成两阳夹一阴的标准盆底形态，突破前高 5.4 个百分点。"
                "形态触发位置接近 24 日均线，与神枪手共振概率较高。"
                "若回踩 8.85 仍止跌则支撑确认，跌破 T-1 收盘价 8.89 则形态破坏。"
            ),
        },
    ],
    "half_year_double": [
        {
            "signal": "半年翻倍，5/24 金叉 31 天，5/60 量线 2.14 倍且陡峭，周线 MACD 本周上穿 0 轴 + 周 MA 5/10 金叉，60 日累涨 9.5%",
            "narrative": (
                "日线 5/24 均线已稳固金叉 31 个交易日，"
                "5 日均量较 60 日放大 2.1 倍且斜率持续上行；"
                "周线 MACD 本周完成上穿 0 轴并伴随周 MA 5/10 金叉，多周期信号同步。"
                "60 日累计涨幅仅 9.5% 显示仍处启动初段，若周线收盘失守上周低点则趋势证伪。"
            ),
        },
    ],
    "multi": [
        {
            "signal": "多策略共振：神枪手 strike_2 + 聚宝盆，前者置信 0.74，后者 0.69，融合后 0.89",
            "narrative": (
                "神枪手二次回踩 24 日均线信号与聚宝盆三日盆底形态在同一交易日触发，"
                "两个独立策略指向同一标的属于较高质量信号。"
                "神枪手 0.74、聚宝盆 0.69 的双独立确认融合后置信度抬至 0.89。"
                "若次日开盘跳空跌破今日 low 则共振失效，按神枪手单独止损线处理。"
            ),
        },
        {
            "signal": "多策略共振：聚宝盆 + 半年翻倍，basin 0.78 + hyd 0.82 → 多策略 0.95",
            "narrative": (
                "聚宝盆短线形态与半年翻倍中线信号叠加，"
                "短中两个时间维度同时给出入场点属较少见的高质量配置。"
                "聚宝盆完成日内三日组合，半年翻倍指向周线 MACD 刚突破 0 轴。"
                "若周线收盘价跌破上周收盘 21.30 则中线逻辑被打破，应优先撤出。"
            ),
        },
    ],
}


# ─── Prompt builders ───────────────────────────────────────────────────────────

def _format_signal_blob(rec: dict) -> str:
    """Compact, LLM-friendly summary of the signal data.

    Strategy-specific extraction so the LLM gets the most relevant numbers
    for the narrative, not a JSON dump.
    """
    strategy = rec.get("strategy", "")
    meta = rec.get("rec_signal_meta") or {}
    by_strategy = meta.get("by_strategy", {})
    confidence = rec.get("confidence_score", 0)
    rec_price = rec.get("rec_price", 0)
    ts_code = rec.get("ts_code", "")
    name = rec.get("name", ts_code)

    lines = [
        f"标的：{name}（{ts_code}）",
        f"今日收盘价：{rec_price:.2f}",
        f"综合置信度：{confidence:.2f}",
    ]

    if strategy == "multi":
        hits = meta.get("strategies_hit", [])
        boost = meta.get("resonance_boost", 0)
        base = meta.get("best_individual_score", 0)
        lines.append(f"触发策略：{', '.join(hits)}")
        lines.append(f"最优单策略分数：{base:.2f}，共振加成：+{boost:.2f}")
        for s in hits:
            sub = by_strategy.get(s, {})
            sub_meta = sub.get("signal_meta", {})
            sm = _strategy_specific_summary(s, sub_meta)
            lines.append(f"[{s}] {sm}")
    else:
        sub = by_strategy.get(strategy, {})
        sub_meta = sub.get("signal_meta", {})
        sm = _strategy_specific_summary(strategy, sub_meta)
        lines.append(f"[{strategy}] {sm}")

    return "\n".join(lines)


def _strategy_specific_summary(strategy: str, sm: dict) -> str:
    """Pluck the most narrative-relevant fields from per-strategy signal_meta."""
    if strategy == "sniper":
        return (
            f"trigger_type={sm.get('touch_count')}次回踩, "
            f"5MA={sm.get('ma5')}, 24MA={sm.get('ma24')}, "
            f"今日 low={sm.get('today_low')}, close={sm.get('today_close')}, "
            f"vol={sm.get('today_vol'):.0f}, vol_ma5={sm.get('vol_ma5'):.0f}, "
            f"金叉日={sm.get('cross_date')}（{sm.get('days_since_cross')}天前）"
        )
    if strategy == "treasure_basin":
        t2 = sm.get("t2", {})
        t1 = sm.get("t1", {})
        t0 = sm.get("t0", {})
        return (
            f"T-2({t2.get('date')}) 阳线收 {t2.get('close')}; "
            f"T-1({t1.get('date')}) 收 {t1.get('close')} 缩量比 {sm.get('vol_t1_t2_ratio'):.2f}; "
            f"T0 收 {t0.get('close')} 放量比 {sm.get('vol_t0_t1_ratio'):.2f}, "
            f"突破 T-2 收盘 {sm.get('t0_breakout_pct', 0)*100:.1f}%; "
            f"距 MA24 {'近' if sm.get('near_ma24') else '远'}"
        )
    if strategy == "half_year_double":
        return (
            f"5/24 金叉 {sm.get('days_since_ma_cross')} 天, "
            f"日线 MA5={sm.get('ma5')}, MA24={sm.get('ma24')}; "
            f"5/60 量比={sm.get('vol_ma5')/sm.get('vol_ma60'):.2f}; "
            f"周线 DIF={sm.get('weekly_dif'):.4f}, DEA={sm.get('weekly_dea'):.4f}; "
            f"周 MA5={sm.get('wma5'):.2f}, MA10={sm.get('wma10'):.2f}; "
            f"60 日累计涨幅 {sm.get('cum_return_60d_pct'):.1f}%"
        )
    return str(sm)[:300]


def _select_few_shot(strategy: str) -> list[dict]:
    """Pick few-shot examples relevant to the strategy."""
    if strategy == "multi":
        return FEW_SHOT_EXAMPLES["multi"]
    return FEW_SHOT_EXAMPLES.get(strategy, []) or FEW_SHOT_EXAMPLES["sniper"]


def _build_messages(rec: dict, market_context: dict | None = None) -> list[dict]:
    """Assemble OpenAI-format chat messages."""
    strategy = rec.get("strategy", "")
    examples = _select_few_shot(strategy)

    market_blob = ""
    if market_context:
        ctx_bits = []
        if "index_pct_chg" in market_context:
            ctx_bits.append(f"今日上证指数 {market_context['index_pct_chg']:+.2f}%")
        if "sector_flow_summary" in market_context:
            ctx_bits.append(market_context["sector_flow_summary"])
        if ctx_bits:
            market_blob = "市场环境：" + "；".join(ctx_bits) + "\n"

    signal_blob = _format_signal_blob(rec)

    user_msg_parts = [market_blob, signal_blob, "", "请按要求生成 80-120 字解读。"]
    user_msg = "\n".join(p for p in user_msg_parts if p)

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Inject few-shot examples as user/assistant pairs
    for ex in examples:
        messages.append({"role": "user", "content": f"信号摘要：{ex['signal']}\n\n请按要求生成 80-120 字解读。"})
        messages.append({"role": "assistant", "content": ex["narrative"]})
    messages.append({"role": "user", "content": user_msg})
    return messages


# ─── Fallback template (when LLM fails) ────────────────────────────────────────

def _fallback_template(rec: dict) -> str:
    """Deterministic template used when LLM is unavailable."""
    strategy = rec.get("strategy", "")
    confidence = rec.get("confidence_score", 0)
    ts_code = rec.get("ts_code", "")
    rec_price = rec.get("rec_price", 0)
    meta = rec.get("rec_signal_meta") or {}

    if strategy == "multi":
        hits = meta.get("strategies_hit", [])
        return (
            f"该股同时触发 {'、'.join(hits)} 共 {len(hits)} 个独立策略信号，"
            f"综合置信度 {confidence:.2f}，融合后属较高质量配置。"
            f"今日参考价 {rec_price:.2f}，"
            f"若次日跌破今日最低则形态破坏，按预设止损纪律处理。"
        )
    strategy_cn = {
        "sniper": "神枪手回调",
        "treasure_basin": "聚宝盆三日组合",
        "half_year_double": "半年翻倍多周期共振",
    }.get(strategy, strategy)
    return (
        f"今日触发{strategy_cn}信号，综合置信度 {confidence:.2f}。"
        f"参考价 {rec_price:.2f}，"
        f"若次日跌破今日最低或失守 24 日均线则信号失效，按宁波派纪律立即离场。"
    )


# ─── Public API ────────────────────────────────────────────────────────────────

def generate_narrative(
    rec: dict,
    *,
    llm_client: LLMClient | None = None,
    market_context: dict | None = None,
    on_error: str = "fallback",  # 'fallback' | 'raise'
) -> str:
    """Generate narrative for a single recommendation.

    Args:
        rec: dict with keys ts_code, strategy, confidence_score, rec_price,
             rec_signal_meta. Optional 'name' for nicer LLM output.
        llm_client: shared LLMClient instance (creates new if None)
        market_context: optional dict (e.g., {'index_pct_chg': 0.5})
        on_error: 'fallback' returns template if LLM fails;
                  'raise' propagates the exception.

    Returns:
        80-120 char Chinese narrative string.
    """
    client = llm_client or LLMClient()
    messages = _build_messages(rec, market_context)

    try:
        resp = client.chat(
            messages=messages,
            max_tokens=LLM_MAX_TOKENS,
            temperature=LLM_TEMPERATURE,
        )
        text = resp.content.strip()
        # Strip wrapping quotes if model added them
        if text.startswith(("\"", "“", "「")) and text.endswith(("\"", "”", "」")):
            text = text[1:-1].strip()
        # If too short or too long, retry once with stricter instruction
        if len(text) < NARRATIVE_MIN_CHARS or len(text) > NARRATIVE_MAX_CHARS:
            logger.warning(
                "narrative length out of range (%d chars) for %s, retrying",
                len(text), rec.get("ts_code")
            )
            messages.append({"role": "assistant", "content": text})
            messages.append({"role": "user", "content": (
                f"上一段输出 {len(text)} 字，超出 80-120 字范围。请重写一段，"
                f"严格控制在 80-120 字（含标点）。"
            )})
            resp2 = client.chat(messages=messages, max_tokens=LLM_MAX_TOKENS, temperature=LLM_TEMPERATURE)
            text = resp2.content.strip()
        return text
    except Exception as exc:
        logger.warning("LLM narrative failed for %s: %s", rec.get("ts_code"), exc)
        if on_error == "raise":
            raise
        return _fallback_template(rec)


def generate_narratives_batch(
    recs: list[dict],
    *,
    llm_client: LLMClient | None = None,
    market_context: dict | None = None,
    on_log=lambda m: None,
) -> list[str]:
    """Sequentially generate narratives for a list of recommendations.

    Sequential (not parallel) to be friendly to single-tenant LLM endpoints.
    Each rec gets its own LLM call.
    """
    client = llm_client or LLMClient()
    results: list[str] = []
    for i, rec in enumerate(recs):
        on_log(f"  generating narrative {i+1}/{len(recs)} for {rec.get('ts_code')}")
        narrative = generate_narrative(
            rec,
            llm_client=client,
            market_context=market_context,
            on_error="fallback",
        )
        results.append(narrative)
    return results
