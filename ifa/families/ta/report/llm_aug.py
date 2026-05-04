"""LLM augmentation for the TA evening report.

Three narrative sections (M7.5):
  · regime_explainer    plain-language explanation of today's regime + transition odds
  · candidate_narrator  narrative for the §03 5★ + §04 4★ top picks
  · strategy_review     comments on §11 attribution + §13 risk scan, suggesting tilt

Design (mirrors research/report/llm_aug.py):
  · Strictly opt-in. None augmenter → empty narrative; report stays deterministic.
  · No new numbers; LLM only paraphrases the structured input.
  · One short paragraph per section (≤ 200 chinese chars).
  · Fail-soft: timeout / parse error → empty string.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from ifa.core.llm.client import LLMClient

log = logging.getLogger(__name__)

_REGIME_PROMPT = (
    "你是 A 股技术面助手。基于提供的市场体制 (regime)、置信度、转移概率分布，"
    "用简体中文写一段不超过 180 字的解读。严格规则：\n"
    "1. 不要引入任何新数字或事件；只能引用提供的字段。\n"
    "2. 解释 (a) 当前体制的市场含义，(b) 概率最高的下一体制有何提示价值。\n"
    "3. 严格 JSON：{\"narrative\": str}\n"
)

_CANDIDATES_PROMPT = (
    "你是 A 股技术面助手。给定 5 个 5★候选 + 5 个 4★候选（已含 ts_code、setup、"
    "score、触发条件），用简体中文写一段不超过 220 字的综合解读。严格规则：\n"
    "1. 不引入新数字或新公司信息；只引用提供字段。\n"
    "2. 描述今日候选池的共同特征（如多家 P2 缺口回补 → 短期反弹），点出 1-2 个值得"
    "关注的板块或 setup 类型。\n"
    "3. 严格 JSON：{\"narrative\": str}\n"
)

_STRATEGY_PROMPT = (
    "你是顶级 A 股量化策略助手 — 服务对象为基金经理与机构交易员。"
    "基于提供的：近 5 日各 setup 表现归因（含样本数、胜率、均收益）、"
    "当前风险扫描（筹码松动数、衰退 setup、体制警示），"
    "用简体中文写**3 段、共 350-450 字**的专业策略评论。\n\n"
    "**严格规则**：\n"
    "1. 只引用提供字段，不编造新数字、新事件、新公司名。\n"
    "2. 用专业术语：胜率、归因、信号衰减、再平衡、回撤、流动性、动量、均值回归。\n"
    "3. 语气克制中立，避免‘必涨/必跌/翻倍’类断言。可使用‘倾向于...’、"
    "'相对而言...'、'统计上有利于...'、'需要警惕...'。\n\n"
    "**结构化要求**（三段论）：\n"
    "**第 1 段：表现归因**（120-150 字）— 列出近期表现最好/最差的 2-3 个 setup，"
    "用胜率与均收益数字佐证；分析为何（可能与体制契合、与行业风格关联、与流动性环境关联）。\n"
    "**第 2 段：策略再平衡建议**（100-150 字）— 基于归因结果，建议哪些 setup "
    "在次日权重提升 / 下调 / 暂停；如 setup 出现衰减信号，量化说明降权幅度（如’降至基线 70%‘）。\n"
    "**第 3 段：风险与配置**（80-120 字）— 综合筹码松动数、体制警示、衰退 setup 的"
    "组合含义；对**仓位**给出方向性建议（建议提升 / 维持 / 降低 / 谨慎），"
    "并说明触发何种条件（如‘若明日跌停 >30 即建议进一步降仓 5pp’）。\n\n"
    "**输出 JSON**：`{\"narrative\": \"...\"}`。narrative 内可用 \\n 分段，但不要"
    "Markdown 标题。"
)


def _safe_json(content: str) -> dict[str, Any] | None:
    if not content:
        return None
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return None


class TALLMAugmenter:
    """Thin wrapper that emits short narratives for TA evening report sections."""

    def __init__(self, client: LLMClient | None = None, *,
                 max_tokens: int = 800, temperature: float = 0.3) -> None:
        self.client = client or LLMClient()
        self.max_tokens = max_tokens
        self.temperature = temperature

    def regime_explainer(self, *, regime: str | None,
                         confidence: float | None,
                         transitions: dict | None) -> str:
        if not regime:
            return ""
        payload = {
            "regime": regime,
            "confidence": confidence,
            "transitions": transitions or {},
        }
        return self._chat(_REGIME_PROMPT, payload)

    def candidate_narrator(self, top5: list[dict], top4: list[dict]) -> str:
        if not top5 and not top4:
            return ""
        payload = {"top_5_star": top5[:5], "top_4_star": top4[:5]}
        return self._chat(_CANDIDATES_PROMPT, payload)

    def strategy_review(self, *, attribution_rows: list[dict],
                        decaying: list[dict], chip_loose_count: int,
                        climax_warning: str | None) -> str:
        if not attribution_rows and not decaying and not climax_warning:
            return ""
        payload = {
            "attribution": attribution_rows[:10],
            "decaying_setups": decaying,
            "chip_loose_count": chip_loose_count,
            "climax_warning": climax_warning,
        }
        return self._chat(_STRATEGY_PROMPT, payload)

    def _chat(self, system_prompt: str, payload: dict) -> str:
        try:
            resp = self.client.chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                response_format={"type": "json_object"},
            )
        except Exception as e:
            log.warning("TA LLM call failed: %s", e)
            return ""
        data = _safe_json(resp.content)
        if not data:
            return ""
        return data.get("narrative", "") or ""
