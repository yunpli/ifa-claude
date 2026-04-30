"""Macro policy event memory job — entry point."""
from __future__ import annotations

from collections.abc import Callable

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.core.llm import LLMClient
from ifa.core.tushare import TuShareClient
from ifa.jobs.common.llm_batch import BatchResult, CandidateInput
from ifa.jobs.common.news_source import SOURCES_FOR_POLICY_MEMORY
from ifa.jobs.common.runner import JobReport, run_scan_and_extract
from ifa.jobs.common.text_filter import POLICY_DIMENSION_SPECS

from . import prompts
from .repo import upsert_policy_event

JOB_NAME = "macro_policy_event_memory"


def run_macro_policy_memory(
    *,
    lookback_days: int = 90,
    batch_size: int = 5,
    max_extra_lookback_days: int = 90,
    on_log: Callable[[str], None] | None = None,
) -> JobReport:
    settings = get_settings()
    engine = get_engine(settings)
    tushare = TuShareClient(settings)
    llm = LLMClient(settings)

    def _process(batch: list[CandidateInput], result: BatchResult) -> int:
        n = 0
        for cand in batch:
            entry = next(
                (r for r in result.parsed if r.get("candidate_index") == cand.candidate_index),
                None,
            )
            if entry is None and cand.candidate_index < len(result.parsed):
                entry = result.parsed[cand.candidate_index]
            if entry is None:
                continue
            n += upsert_policy_event(
                engine,
                candidate_meta={
                    "title": cand.title,
                    "source": cand.source,
                    "publish_time": cand.publish_time,
                    "url": cand.url,
                    "source_label": _label_from_src(cand),
                },
                parsed_entry=entry,
                extraction_model=result.raw_response.model,
                extraction_endpoint=result.raw_response.endpoint,
            )
        return n

    return run_scan_and_extract(
        job_name=JOB_NAME,
        sources=SOURCES_FOR_POLICY_MEMORY,
        keyword_specs=POLICY_DIMENSION_SPECS,
        system_prompt=prompts.SYSTEM_PROMPT,
        instructions=prompts.INSTRUCTIONS,
        output_schema_hint=prompts.OUTPUT_SCHEMA_HINT,
        process_batch_results=_process,
        engine=engine,
        tushare=tushare,
        llm=llm,
        run_mode=settings.run_mode,
        lookback_days=lookback_days,
        batch_size=batch_size,
        max_extra_lookback_days=max_extra_lookback_days,
        on_log=on_log,
    )


def _label_from_src(cand: CandidateInput) -> str:
    if cand.source.startswith("国务院") or cand.source in {"国务院", "国务院办公厅"}:
        return "npr"
    if cand.source in {"sina", "wallstreetcn", "10jqka", "eastmoney", "cls",
                        "yicai", "jinrongjie", "fenghuang", "yuncaijing"}:
        return f"news.{cand.source}"
    return f"major_news.{cand.source}"
