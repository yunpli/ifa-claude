"""LLM augmentation — adds short narrative paragraphs to each report section.

Design principles:
  · **Strictly opt-in.** Builder accepts an optional `augmenter`; when None,
    every narrative field stays empty and the report is fully deterministic.
  · **Read-only of structured inputs.** The prompt forbids the model from
    introducing any new financial number. It can only paraphrase the values
    we already computed and listed in the prompt body.
  · **Per-section scope.** One short narrative per family (5 sections) plus
    one executive summary. Splitting reduces cross-contamination and lets us
    cache + retry per family.
  · **Cache by content.** computed_cache keyed on SHA256 of the structured
    input — same factor state always returns the same paragraph; reruns cost
    nothing.
  · **Fail soft.** API failure / parse error → narrative=""; the report still
    renders. The caller never has to handle exceptions.

Public API:
  · LLMAugmenter(client=None, *, model=None, cache_engine=None)
  · augmenter.family_narrative(family, scoring, results) → str
  · augmenter.overall_narrative(snap, scoring) → str
"""
from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

from sqlalchemy.engine import Engine

from ifa.core.llm.client import LLMClient
from ifa.families.research.analyzer.factors import FactorResult
from ifa.families.research.analyzer.scoring import (
    FAMILY_LABEL_ZH,
    FamilyScore,
    ScoringResult,
)
from ifa.families.research.fetcher.cache import computed_get, computed_set

log = logging.getLogger(__name__)

_FAMILY_FOCUS = {
    "profitability": "盈利能力（毛利、净利、ROE/ROIC、扣非vs净利差距）",
    "growth": "增长（营收/净利同比、3年CAGR、业绩预告达成率）",
    "cash_quality": "现金流质量（CFO/净利、FCF、应收/存货扩张速度、CCC）",
    "balance": "资产负债结构（杠杆、流动性、商誉、质押、有息负债）",
    "governance": "治理与披露（减持、审计、管理层稳定性、互动易回复、披露及时性）",
}

_SYSTEM_PROMPT = (
    "你是一个谨慎的卖方研究分析师，正在撰写基于结构化因子的财报快评。"
    "规则严格执行：\n"
    "1. 只能引用我在 user 消息中明确给出的数值/状态/排名，不得编造或推断任何其他财务数字、市值、估值、行业平均。\n"
    "\n"
    "★ 同业排名口径（极其重要，方向不能搞反）：\n"
    "   - peer_rank=[N, M] 表示同业第 N 名（N=1 最佳，N=M 最差），共 M 只票。\n"
    "   - peer_pct 是百分位（100=同业最佳，0=同业最差）。\n"
    "   - 示例：peer_rank=[7, 89], peer_pct=93 → 同业第 7 名（前 8%），是领先；peer_rank=[80, 89], peer_pct=11 → 同业末段。\n"
    "\n"
    "2. 必须优先解释 RED/YELLOW 因子的含义与潜在成因；如有 'peer_pct' 字段，需结合"
    "'绝对状态 vs 同业排名'的反差给出洞察（如'绝对低但同业领先（peer_pct≥70）' = '行业整体承压'）。\n"
    "3. 输出 80-150 字纯中文段落，不使用 markdown，不下买卖建议，不出现'建议/买入/卖出/目标价'。\n"
    "4. 如果输入显示因子状态全为 GREEN 或 UNKNOWN，输出 50-80 字简短即可。\n"
    "5. 不要复述因子名称的英文代码，使用提供的中文名。"
)

_WATCHPOINTS_SYSTEM_PROMPT = (
    "你是一个审慎的卖方分析师，需要把分散的 RED/YELLOW 因子信号合并为 3-5 个具体可观察的关注点。"
    "严格规则：\n"
    "1. 只能引用我在 user 消息中明确给出的数据（因子名/值/状态/同业排名/趋势/notes），不得编造任何其他数字。\n"
    "\n"
    "★ 同业排名口径（极其重要，方向不能搞反）：\n"
    "   - peer_rank=[N, M] 表示同业第 N 名（N=1 最佳，N=M 最差），共 M 只票。\n"
    "   - peer_pct 是百分位（100=同业最佳，0=同业最差）。\n"
    "   - 示例：peer_rank=[7, 89], peer_pct=93 → '同业第 7 / 89 名（前 8%）'，**这是领先**，不是末段。\n"
    "   - 示例：peer_rank=[80, 89], peer_pct=11 → '同业第 80 / 89 名（后 11%）'，这才是末段。\n"
    "   - 引用同业排名时务必用 'peer_rank' 而不是仅看 peer_pct，避免误读。\n"
    "   - '绝对差但同业领先（peer_pct≥70）' = 行业整体承压，公司相对优秀，应作为正面对比因素提及，不要描述为'末段'。\n"
    "\n"
    "2. 输出严格的 JSON 对象，结构：{\"watchpoints\": [{\"severity\": \"high|medium|low\", "
    "\"category\": \"盈利|增长|现金|结构|治理\", \"title\": \"≤14字简短标题\", "
    "\"description\": \"50-90字描述含义与机理\", \"what_to_watch\": \"下一个最有价值的观察数据点或事件触发\"}, ...]}.\n"
    "3. severity 分配规则：因子 RED + peer_pct < 30 → high；单独 RED 或 YELLOW 多项叠加 → medium；YELLOW 单项 → low。"
    "若因子 RED 但 peer_pct ≥ 70（绝对差但同业领先），severity 通常降为 medium，描述中必须明确'行业普遍承压'语气。\n"
    "4. category 必须是上述 5 个之一。\n"
    "5. 数量 3-5 个，按 severity 从高到低排序；不输出 GREEN-only 公司的 watchpoints（返回空数组）。\n"
    "6. description 必须解释'为什么这是问题'，不只是复述数字。如能利用 peer_pct 的反差必须指出。\n"
    "7. what_to_watch 必须是具体可观察的：下一份季报某指标、某事件触发、某阈值突破，不是空泛的'继续观察'。\n"
    "8. 不下买卖建议、不出现'建议/买入/卖出/目标价'。\n"
    "9. 严禁输出非 JSON 内容（无 markdown 代码块、无前后说明文字）。"
)


@dataclass
class LLMAugmenter:
    client: LLMClient | None = None
    model: str | None = None              # let LLMClient pick if None
    cache_engine: Engine | None = None    # if set, write-through computed_cache
    max_tokens: int = 320
    temperature: float = 0.2

    def __post_init__(self) -> None:
        if self.client is None:
            self.client = LLMClient()

    # ─── Public API ──────────────────────────────────────────────────────────

    def family_narrative(
        self,
        family: str,
        family_score: FamilyScore,
        results: list[FactorResult],
        *,
        ts_code: str | None = None,
    ) -> str:
        focus = _FAMILY_FOCUS.get(family, family)
        payload = {
            "family": FAMILY_LABEL_ZH.get(family, family),
            "focus": focus,
            "family_score": _round(family_score.score),
            "family_status": family_score.status.value,
            "factors": [self._serialize_factor(r) for r in results],
        }
        return self._cached_chat(
            cache_key=f"family_{family}",
            ts_code=ts_code,
            payload=payload,
            user_intro=f"为 {payload['family']} 维度撰写一段评述：",
        )

    def narratives_for_report(
        self,
        ts_code: str,
        scoring: ScoringResult,
        results_by_family: dict[str, list[FactorResult]],
    ) -> dict[str, Any]:
        """Compute all narratives + watchpoints concurrently.

        Returns a dict with:
          · 'overall'      → str (executive summary paragraph)
          · '<family>'     → str (5 family narratives)
          · 'watchpoints'  → list[dict] (3-5 structured observations, possibly [])

        Cache hits return immediately; only true API calls pay the latency.
        Thread pool sized to total distinct calls — each worker holds its own
        LLMClient HTTP session via OpenAI SDK.
        """
        n = 2 + len(results_by_family)   # overall + watchpoints + N families
        out: dict[str, Any] = {}
        with ThreadPoolExecutor(max_workers=n) as ex:
            futures: dict[str, Any] = {
                "overall": ex.submit(self.overall_narrative, ts_code, scoring),
                "watchpoints": ex.submit(
                    self.watchpoints, ts_code, scoring, results_by_family,
                ),
            }
            for fam, results in results_by_family.items():
                fs = scoring.families.get(fam)
                if fs is None:
                    continue
                futures[fam] = ex.submit(
                    self.family_narrative, fam, fs, results,
                    ts_code=ts_code,
                )
            for key, fut in futures.items():
                try:
                    out[key] = fut.result()
                except Exception as e:
                    log.warning("narrative %s failed: %s", key, e)
                    out[key] = [] if key == "watchpoints" else ""
        return out

    def watchpoints(
        self,
        ts_code: str,
        scoring: ScoringResult,
        results_by_family: dict[str, list[FactorResult]],
    ) -> list[dict[str, Any]]:
        """Synthesize RED/YELLOW factors into 3-5 actionable observations.

        Returns list of dicts with keys: severity / category / title /
        description / what_to_watch. On any failure (LLM error, JSON parse
        error, schema mismatch) returns []. Caller renders empty list as
        "no watchpoints".
        """
        # Pre-filter: if everything is GREEN/UNKNOWN, skip the LLM call entirely
        # to save tokens — return [] directly.
        all_results = [r for results in results_by_family.values() for r in results]
        concerning = [r for r in all_results
                      if r.status.value in ("red", "yellow")]
        if not concerning:
            return []

        payload = {
            "overall_score": _round(scoring.overall_score),
            "overall_status": scoring.overall_status.value,
            "concerning_factors": [
                {
                    "family": r.spec.family,
                    "name_zh": r.spec.display_name_zh,
                    "value": _round(r.value),
                    "unit": r.spec.unit,
                    "status": r.status.value,
                    "peer_pct": _round(r.peer_percentile)
                                if r.peer_percentile is not None else None,
                    "peer_rank": list(r.peer_rank) if r.peer_rank else None,
                    "notes": list(r.notes),
                }
                for r in concerning
            ],
        }
        cache_key = "watchpoints"
        inputs_hash = _hash(payload)

        if self.cache_engine is not None:
            cached = computed_get(self.cache_engine, ts_code,
                                  cache_key, inputs_hash)
            if cached and isinstance(cached, dict) and "watchpoints" in cached:
                return list(cached["watchpoints"])

        try:
            resp = self.client.chat(
                messages=[
                    {"role": "system", "content": _WATCHPOINTS_SYSTEM_PROMPT},
                    {"role": "user",
                     "content": ("根据下列 concerning 因子合成 3-5 个 watchpoint，"
                                 "严格输出 JSON：\n\n```json\n"
                                 + json.dumps(payload, ensure_ascii=False, indent=2)
                                 + "\n```")},
                ],
                max_tokens=900,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            parsed = resp.parse_json()
            watchpoints = parsed.get("watchpoints", [])
            if not isinstance(watchpoints, list):
                return []
            # Light schema validation — drop any items missing required keys.
            valid = [
                wp for wp in watchpoints
                if isinstance(wp, dict)
                and {"severity", "category", "title",
                     "description", "what_to_watch"} <= wp.keys()
                and wp.get("severity") in ("high", "medium", "low")
            ]
        except Exception as e:
            log.warning("watchpoints LLM/parse failed for %s: %s", ts_code, e)
            return []

        # Sort high → low severity for stable rendering
        sev_rank = {"high": 0, "medium": 1, "low": 2}
        valid.sort(key=lambda w: sev_rank.get(w["severity"], 3))

        if self.cache_engine is not None and valid:
            try:
                computed_set(self.cache_engine, ts_code, cache_key,
                             inputs_hash, {"watchpoints": valid})
            except Exception as e:
                log.debug("cache_set watchpoints failed: %s", e)
        return valid

    def overall_narrative(self, ts_code: str, scoring: ScoringResult) -> str:
        payload = {
            "overall_score": _round(scoring.overall_score),
            "overall_status": scoring.overall_status.value,
            "families": [
                {
                    "family": fs.label_zh,
                    "score": _round(fs.score),
                    "status": fs.status.value,
                }
                for fs in scoring.families.values()
            ],
        }
        return self._cached_chat(
            cache_key="overall",
            ts_code=ts_code,
            payload=payload,
            user_intro="基于以下 5 维评分，撰写一段总体投资观察（不下结论，不给建议）：",
        )

    # ─── Internals ───────────────────────────────────────────────────────────

    def _serialize_factor(self, r: FactorResult) -> dict[str, Any]:
        return {
            "name_zh": r.spec.display_name_zh,
            "value": _round(r.value),
            "unit": r.spec.unit,
            "status": r.status.value,
            "peer_pct": _round(r.peer_percentile) if r.peer_percentile is not None else None,
            "peer_rank": list(r.peer_rank) if r.peer_rank else None,
            "notes": list(r.notes),
        }

    def _cached_chat(
        self,
        *,
        cache_key: str,
        ts_code: str | None,
        payload: dict,
        user_intro: str,
    ) -> str:
        inputs_hash = _hash(payload)

        # Cache hit (only when we have an engine + a stable ts_code key).
        if self.cache_engine is not None and ts_code:
            cached = computed_get(self.cache_engine, ts_code, cache_key, inputs_hash)
            if cached:
                return str(cached.get("narrative", "")) if isinstance(cached, dict) else str(cached)

        try:
            resp = self.client.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",
                     "content": f"{user_intro}\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"},
                ],
                max_tokens=self.max_tokens,
                temperature=self.temperature,
            )
            narrative = (resp.content or "").strip()
        except Exception as e:
            log.warning("llm narrative failed for %s/%s: %s", ts_code, cache_key, e)
            return ""

        if self.cache_engine is not None and ts_code and narrative:
            try:
                computed_set(self.cache_engine, ts_code, cache_key, inputs_hash,
                             {"narrative": narrative})
            except Exception as e:  # cache failure shouldn't block return
                log.debug("cache_set failed: %s", e)

        return narrative


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash(payload: dict) -> str:
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _round(v: object, ndigits: int = 2) -> float | None:
    if v is None:
        return None
    try:
        return round(float(v), ndigits)
    except (TypeError, ValueError):
        return None
