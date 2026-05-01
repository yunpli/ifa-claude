"""LLM 新闻催化打标 — news_catalyst scoring via OpenAI-compatible relay.

Given a list of today's news headlines (from raw_ths_hot, raw_dc_hot, or the
main news scan tables), ask the LLM to score which sectors receive a positive
"catalyst" signal from the news flow.

Output: dict[sector_name → catalyst_score (0.0–1.0)]

Prompt design:
  - Input: top-N headlines + list of active sector names (from sector_state_daily)
  - Output: JSON array of {"sector": str, "score": float, "reason": str}
  - Score semantics:
      0.9–1.0 = strong direct catalyst (e.g. policy explicitly mentions sector)
      0.6–0.8 = moderate catalyst (related / adjacent sector benefits)
      0.3–0.5 = weak / speculative (sector might benefit)
      0.0–0.2 = no catalyst signal

Integration:
  - Called in daily report orchestrator (Phase 5) to enhance role.py's
    catalyst detection.  ML models can also use the scores as extra features.
  - Uses ifa.core.llm.client (gpt-5.4 → fallback gpt-5.5).
  - If LLM call fails, returns empty dict (safe degradation).

Cost control:
  - Headlines are deduplicated and truncated to max_headlines (default 30).
  - Sector list is limited to active sectors only (not all 1000+ DC sectors).
  - Temperature=0.1 for deterministic scoring.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from ifa.core.llm.client import LLMClient

log = logging.getLogger(__name__)
SCHEMA = "smartmoney"

_SYSTEM_PROMPT = """\
你是一位专注于中国A股市场的量化分析师。
你的任务是：根据给定的新闻标题，判断哪些板块会受到正面催化，并给出0到1之间的催化强度评分。

评分标准：
- 0.9~1.0：强直接催化（政策/事件明确指向该板块）
- 0.6~0.8：中等催化（相关或受益板块）
- 0.3~0.5：弱/推测性催化
- 0.0~0.2：无明显催化

仅对有信号的板块评分，忽略无关板块。
"""

_USER_TEMPLATE = """\
今日新闻摘要（{n_headlines} 条）：
{headlines}

需要评估的板块列表：
{sectors}

请以如下 JSON 格式回复（仅输出 JSON，不要添加其他文字）：
[
  {{"sector": "板块名", "score": 0.85, "reason": "简要原因"}},
  ...
]
"""


def _load_hot_headlines(engine: Engine, trade_date: Any, max_n: int) -> list[str]:
    """Load top headlines from THS/DC hot lists."""
    import datetime as dt
    sql = f"""
        SELECT ts_name, rank_reason
        FROM {SCHEMA}.raw_ths_hot
        WHERE trade_date = :d AND data_type = '个股'
        ORDER BY rank ASC
        LIMIT :n
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date, "n": max_n}).fetchall()
    headlines = []
    for r in rows:
        parts = [r[0]] if r[0] else []
        if r[1]:
            parts.append(r[1])
        if parts:
            headlines.append("、".join(parts))
    return headlines


def _load_active_sector_names(engine: Engine, trade_date: Any) -> list[str]:
    """Load sector names for active (non-cold, non-retreat) sectors."""
    sql = f"""
        SELECT DISTINCT sector_name
        FROM {SCHEMA}.sector_state_daily
        WHERE trade_date = :d
          AND role IN ('主线','中军','轮动','催化')
          AND sector_name IS NOT NULL
        LIMIT 50
    """
    with engine.connect() as conn:
        rows = conn.execute(text(sql), {"d": trade_date}).fetchall()
    return [r[0] for r in rows if r[0]]


def score_news_catalysts(
    engine: Engine,
    llm: LLMClient,
    trade_date: Any,
    *,
    params: dict[str, Any] | None = None,
    extra_headlines: list[str] | None = None,
) -> dict[str, dict[str, Any]]:
    """Score news catalysts for active sectors using the LLM.

    Args:
        engine:           SQLAlchemy engine.
        llm:              Initialized LLMClient.
        trade_date:       The trade date to score.
        params:           Full params dict (reads params.ml.news_catalyst.*).
        extra_headlines:  Additional headlines not in the DB (e.g. from main
                          news scan tables).

    Returns:
        dict[sector_name → {"score": float, "reason": str}]
        Empty dict on LLM failure (safe degradation).
    """
    np = (params or {}).get("ml", {}).get("news_catalyst", {})
    max_headlines = int(np.get("max_headlines", 30))
    model = np.get("model", None)  # None = use LLMClient default
    temperature = float(np.get("temperature", 0.1))

    # Load headlines
    db_headlines = _load_hot_headlines(engine, trade_date, max_headlines)
    all_headlines = list(dict.fromkeys(
        (extra_headlines or []) + db_headlines
    ))[:max_headlines]

    if not all_headlines:
        log.info("[news_catalyst] no headlines for %s; skipping", trade_date)
        return {}

    # Load active sectors
    sector_names = _load_active_sector_names(engine, trade_date)
    if not sector_names:
        log.info("[news_catalyst] no active sectors for %s; skipping", trade_date)
        return {}

    headlines_str = "\n".join(f"{i+1}. {h}" for i, h in enumerate(all_headlines))
    sectors_str = "、".join(sector_names)

    user_msg = _USER_TEMPLATE.format(
        n_headlines=len(all_headlines),
        headlines=headlines_str,
        sectors=sectors_str,
    )

    try:
        raw = llm.chat(
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            model=model,
            temperature=temperature,
            max_tokens=800,
        )
        # Parse JSON response
        text_content = raw.strip()
        # Strip markdown code fences if present
        if text_content.startswith("```"):
            lines = text_content.split("\n")
            text_content = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

        items = json.loads(text_content)
        result: dict[str, dict[str, Any]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            name = item.get("sector", "")
            score = float(item.get("score", 0.0))
            reason = item.get("reason", "")
            if name and 0.0 <= score <= 1.0:
                result[name] = {"score": round(score, 3), "reason": reason}

        log.info("[news_catalyst] %s: scored %d sectors (from %d headlines)",
                 trade_date, len(result), len(all_headlines))
        return result

    except Exception as exc:  # noqa: BLE001
        log.warning("[news_catalyst] LLM call failed: %s; returning empty", exc)
        return {}
