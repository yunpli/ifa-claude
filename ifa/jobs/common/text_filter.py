"""Keyword filter — stage 1 of every text-derived job.

Why a separate module:
  - Keep filter rules + negative filters in one place
  - Make the filter cheap and pure (no LLM, no DB) so we can run it on tens of
    thousands of news rows without API cost.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordSpec:
    """One keyword family — the union of regex patterns that count as a hit."""

    name: str                       # e.g. "新增贷款"
    patterns: list[str]             # regex patterns, each `re.IGNORECASE`
    requires_no_negative: bool = True  # True for M-numeric kinds; for Chinese phrases negatives don't apply


# Negative filters: false-positive guards (PM2.5, HBM2, M2.1 model, SQM company …).
NEGATIVE_PATTERNS = [
    r"PM2\.5",
    r"PM\s*2\.5",
    r"HBM2",
    r"M2\.1",
    r"\bSQM\b",
    r"M2\s*Pro",
    r"M2\s*Max",
    r"M2\s*Ultra",
    r"M2\s*芯片",
]


def has_negative(text: str) -> bool:
    return any(re.search(p, text, re.IGNORECASE) for p in NEGATIVE_PATTERNS)


def text_matches(text: str, spec: KeywordSpec) -> bool:
    if not text:
        return False
    if spec.requires_no_negative and has_negative(text):
        return False
    return any(re.search(p, text, re.IGNORECASE) for p in spec.patterns)


def first_matching_keywords(text: str, specs: list[KeywordSpec]) -> list[str]:
    """Return the names of all keyword specs that match this text."""
    return [s.name for s in specs if text_matches(text, s)]


# ─── Macro indicator keyword specs (job 1) ──────────────────────────────────
# Scope reflects the audit: M0/M1/M2/社融/CPI/PPI/PMI/GDP all already structured
# in TuShare (cn_m, sf_month, cn_cpi, cn_ppi, cn_pmi, cn_gdp). The ONLY
# indicators not covered structurally are 新增人民币贷款 and 人民币贷款余额,
# so those are the only two we text-extract. No catchall — anything else
# will be added explicitly when we identify a real gap.
MACRO_INDICATOR_SPECS: list[KeywordSpec] = [
    KeywordSpec(
        name="新增人民币贷款",
        patterns=[
            r"新增人民币贷款",
            r"人民币贷款.{0,4}新增",
            r"新增贷款",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="人民币贷款余额",
        patterns=[
            r"人民币贷款余额",
            r"贷款余额",
        ],
        requires_no_negative=False,
    ),
]


# ─── Policy event keyword specs (job 2) ─────────────────────────────────────
# Maps to PRD §2.2 policy_dimension vocabulary.
POLICY_DIMENSION_SPECS: list[KeywordSpec] = [
    KeywordSpec(
        name="稳增长",
        patterns=[
            r"稳增长", r"扩大内需", r"扩内需", r"促消费",
            r"刺激.{0,4}经济", r"经济.{0,4}刺激",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="新质生产力/科技自立",
        patterns=[
            r"新质生产力", r"科技自立", r"科技自强",
            r"人工智能\+", r"AI\+", r"国产替代",
            r"半导体.{0,4}政策", r"芯片.{0,4}政策",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="消费与内需",
        patterns=[
            r"消费.{0,4}政策", r"以旧换新", r"促消费",
            r"消费券", r"内需.{0,4}政策",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="地产与信用",
        patterns=[
            r"房地产.{0,4}政策", r"楼市.{0,4}政策",
            r"房贷.{0,4}(利率|政策)", r"限购",
            r"白名单.{0,4}房地产", r"保交楼",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="资本市场",
        patterns=[
            r"证监会", r"资本市场", r"注册制", r"退市",
            r"上市公司.{0,4}质量", r"投资者.{0,4}保护",
            r"中长期资金", r"国九条",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="金融监管/行业监管",
        patterns=[
            r"银保监", r"金融监管总局", r"反垄断",
            r"行业监管", r"穿透.{0,4}监管", r"中央金融工作会议",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="货币/财政",
        patterns=[
            r"货币政策", r"降准", r"降息", r"逆回购", r"MLF",
            r"财政政策", r"专项债", r"国债", r"减税降费", r"赤字率",
            r"中国人民银行", r"^央行\b", r"财政部",
        ],
        requires_no_negative=False,
    ),
    KeywordSpec(
        name="外部冲击",
        patterns=[
            r"美联储", r"加息.{0,4}周期", r"地缘.{0,4}冲突",
            r"贸易.{0,4}(摩擦|争端|战)", r"出口管制",
        ],
        requires_no_negative=False,
    ),
]
