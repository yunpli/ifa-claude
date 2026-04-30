"""Batched LLM extraction with strict JSON output.

Groups N candidate news articles into a single LLM call, asking the model to
return a JSON array with one entry per article. This cuts call volume by N×
and bounds the per-batch latency.

If a batch parse fails, we retry once with a stricter prompt; if that still
fails, we drop the batch and surface the error to the caller for logging.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ifa.core.llm import LLMClient, LLMResponse


@dataclass
class CandidateInput:
    """One news row to extract from."""

    candidate_index: int                 # stable position within the batch (0..N-1)
    title: str
    source: str                          # display source label (e.g. '新华网' / 'cls')
    publish_time: str                    # ISO string
    url: str
    content: str
    matched_keywords: list[str]          # which spec(s) caused this row to enter the batch


@dataclass
class BatchResult:
    parsed: list[dict[str, Any]]         # one dict per input candidate (length == len(inputs))
    raw_response: LLMResponse
    parse_status: str                    # "parsed" | "parse_failed" | "fallback_used" | "size_mismatch"
    error: str | None = None


def _format_candidate(c: CandidateInput) -> str:
    return (
        f"[{c.candidate_index}]\n"
        f"TITLE: {c.title}\n"
        f"SOURCE: {c.source}\n"
        f"PUBLISH_TIME: {c.publish_time}\n"
        f"URL: {c.url}\n"
        f"MATCHED_KEYWORDS: {', '.join(c.matched_keywords)}\n"
        f"CONTENT:\n{c.content[:4000]}\n"  # truncate per-article to keep batch prompt bounded
    )


def call_batch(
    llm: LLMClient,
    *,
    system_prompt: str,
    instructions: str,
    output_schema_hint: str,
    candidates: Iterable[CandidateInput],
    max_tokens: int = 4096,
    temperature: float = 0.1,
) -> BatchResult:
    """Run one batch through the LLM, parse JSON array of results.

    The instructions text MUST tell the model:
      - "Return a JSON object with key `results` whose value is an array of
        length N (one entry per input, in input order)."
      - The schema for each entry.
    `output_schema_hint` is the per-entry JSON schema example, embedded in
    the user message so the model has a concrete template.
    """
    cand_list = list(candidates)
    n = len(cand_list)
    candidate_block = "\n---\n".join(_format_candidate(c) for c in cand_list)

    user_msg = (
        f"{instructions}\n\n"
        f"You will receive {n} candidate articles, separated by `---`. Each starts with a "
        f"line `[k]` where k is the article index (0..{n-1}).\n\n"
        f"Return STRICT JSON only — no prose, no markdown fences. Top-level shape:\n"
        f"{{\n  \"results\": [\n    /* exactly {n} entries, one per input, in the same order */\n"
        f"    {output_schema_hint}\n  ]\n}}\n\n"
        f"=== CANDIDATES ===\n{candidate_block}\n=== END CANDIDATES ===\n"
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_msg},
    ]

    resp = llm.chat(messages=messages, max_tokens=max_tokens, temperature=temperature)
    parsed, status, err = _try_parse(resp.content, expected_n=n)
    if status == "parsed":
        return BatchResult(parsed=parsed, raw_response=resp, parse_status="parsed")

    # One stricter retry — append a reminder telling the model what failed.
    retry_msg = (
        f"Your previous response was not valid JSON or had the wrong shape: {err}.\n"
        f"Return ONLY the JSON object, no prose, no fences. The top-level key must be "
        f"`results`, and `results` must be an array of length {n} in input order."
    )
    retry_messages = messages + [
        {"role": "assistant", "content": resp.content[:1000]},
        {"role": "user", "content": retry_msg},
    ]
    resp2 = llm.chat(messages=retry_messages, max_tokens=max_tokens, temperature=0.0)
    parsed2, status2, err2 = _try_parse(resp2.content, expected_n=n)
    if status2 == "parsed":
        return BatchResult(parsed=parsed2, raw_response=resp2, parse_status="fallback_used")

    return BatchResult(
        parsed=[],
        raw_response=resp2,
        parse_status=status2,
        error=err2 or err,
    )


def _try_parse(s: str, *, expected_n: int) -> tuple[list[dict[str, Any]], str, str | None]:
    s = s.strip()
    # Strip markdown fences if the model still wraps them
    if s.startswith("```"):
        m = re.search(r"```(?:json|JSON)?\s*(.*?)```", s, re.DOTALL)
        if m:
            s = m.group(1).strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError as exc:
        return [], "parse_failed", f"JSONDecodeError: {exc}"
    if not isinstance(obj, dict):
        return [], "parse_failed", "top-level is not an object"
    results = obj.get("results")
    if not isinstance(results, list):
        return [], "parse_failed", "missing/invalid `results` array"
    if len(results) != expected_n:
        return results, "size_mismatch", f"expected {expected_n} results, got {len(results)}"
    return results, "parsed", None


def chunked(items: list[Any], n: int) -> list[list[Any]]:
    return [items[i:i + n] for i in range(0, len(items), n)]
