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
    "你是 A 股策略助手。基于提供的近 5 日各 setup 表现归因 + 当前风险扫描"
    "（衰退 setup、筹码松动数、可能的体制风险），用简体中文写一段不超过 200 字的"
    "策略评论。严格规则：\n"
    "1. 不引入新数字、新事件或新公司。只引用提供字段。\n"
    "2. 内容覆盖：(a) 哪些 setup 近期表现好/差，(b) 是否需要降低某类型 setup 权重，"
    "(c) 风险是否升高、是否建议减仓。语气克制，避免‘必涨/必跌’类断言。\n"
    "3. 严格 JSON：{\"narrative\": str}\n"
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
                 max_tokens: int = 400, temperature: float = 0.3) -> None:
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
