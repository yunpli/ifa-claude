"""Deterministic validation for market noon morning-hypothesis reviews.

The noon report is a live decision-support artifact, so review verdicts must
come from structured market inputs before any narrative layer.  This module
keeps the PIT/noon scope explicit: it consumes the already-fetched intraday
index, breadth, SW L1/L2 rotation, and focus-stock snapshots from MarketCtx and
returns reader-facing review rows.  It does not fetch data or persist state.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


VALIDATED = "validated"
FALSIFIED = "falsified"
PARTIAL = "partially_validated"
UNABLE = "unable_to_judge"

DISPLAY = {
    VALIDATED: "验证",
    FALSIFIED: "证伪",
    PARTIAL: "部分验证",
    UNABLE: "暂无法判断",
}


@dataclass(frozen=True)
class _Component:
    status: str
    evidence: str
    missing_inputs: tuple[str, ...] = ()
    target: str = ""


@dataclass(frozen=True)
class _ChainSpec:
    key: str
    label: str
    keywords: tuple[str, ...]
    sector_keywords: tuple[str, ...]
    missing_label: str


CHAIN_SPECS: tuple[_ChainSpec, ...] = (
    _ChainSpec(
        key="defensive",
        label="防守链",
        keywords=("防守", "红利", "高股息", "避险", "低波", "银行", "煤炭", "公用", "医药", "白酒"),
        sector_keywords=("银行", "煤炭", "公用事业", "医药", "食品饮料", "白酒", "保险", "交通运输", "家用电器"),
        missing_label="防守链申万 L1/L2 实时涨幅、成交额或上涨占比",
    ),
    _ChainSpec(
        key="tech",
        label="科技链",
        keywords=(
            "科技", "AI", "人工智能", "算力", "芯片", "半导体", "光模块", "通信",
            "计算机", "传媒", "机器人", "消费电子", "电子", "科创", "创业板",
        ),
        sector_keywords=(
            "电子", "半导体", "元件", "通信", "通信设备", "通信服务", "计算机",
            "软件", "IT服务", "传媒", "游戏", "数字媒体", "消费电子", "光学光电子",
            "电力设备", "电网", "机器人", "自动化设备",
        ),
        missing_label="科技链申万 L1/L2 实时涨幅、成交额或上涨占比",
    ),
    _ChainSpec(
        key="military",
        label="军工链",
        keywords=("军工", "国防", "航天", "航空", "卫星", "低空", "商业航天", "船舶", "大飞机", "兵装"),
        sector_keywords=("国防军工", "航天", "航空", "船舶", "航海装备", "军工电子", "地面兵装"),
        missing_label="国防军工申万 L1/L2 实时涨幅、成交额或上涨占比",
    ),
)

RISK_KEYWORDS = (
    "风险偏好", "情绪", "涨停", "跌停", "炸板", "触板", "开板", "连板",
    "赚钱效应", "接力", "高标", "短线",
)
OPEN_BOARD_KEYWORDS = ("炸板", "触板", "开板", "封板")
ROTATION_KEYWORDS = ("板块", "主线", "轮动", "扩散", "行业", "L1", "L2", "链")
INDEX_ALIASES = {
    "上证": ("上证指数", "000001.SH"),
    "深证": ("深证成指", "399001.SZ"),
    "创业板": ("创业板指", "399006.SZ"),
    "科创": ("科创50", "000688.SH"),
    "北证": ("北证50", "899050.BJ"),
    "沪深300": ("沪深300", "000300.SH"),
}


def build_noon_hypothesis_reviews(ctx: Any, hyps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return deterministic reader-facing review rows for market noon S3."""
    return [_review_one(ctx, h) for h in hyps]


def _review_one(ctx: Any, hyp: dict[str, Any]) -> dict[str, Any]:
    text = _hyp_text(hyp)
    components: list[_Component] = []

    if _contains_any(text, RISK_KEYWORDS):
        components.append(_eval_risk_appetite(ctx, text))

    components.extend(_eval_indices(ctx, text))
    components.extend(_eval_focus_stocks(ctx, text))

    chain_specs = [spec for spec in CHAIN_SPECS if _contains_any(text, spec.keywords)]
    for spec in chain_specs:
        components.append(_eval_chain(ctx, text, spec))

    direct_sector_components = _eval_direct_sector_mentions(ctx, text, exclude_chain_specs=chain_specs)
    components.extend(direct_sector_components)

    if (
        not chain_specs
        and not direct_sector_components
        and _contains_any(text, ROTATION_KEYWORDS)
    ):
        components.append(_eval_overall_rotation(ctx))

    if not components:
        components.append(_Component(
            status=UNABLE,
            evidence="早报假设没有足够明确的可量化对象。",
            missing_inputs=("可量化验证规则或关联板块/标的",),
            target="验证规则",
        ))

    row = _combine_components(hyp, components)
    row["review_rule_used"] = "market_noon_structured_rules_v1"
    return row


def _hyp_text(hyp: dict[str, Any]) -> str:
    parts: list[str] = []
    for key in ("hypothesis", "review_rule", "validation_method", "related", "target"):
        v = hyp.get(key)
        if isinstance(v, (list, tuple, set)):
            parts.extend(str(x) for x in v if x is not None)
        elif v is not None:
            parts.append(str(v))
    return " ".join(parts)


def _contains_any(text: str, keywords: tuple[str, ...] | list[str]) -> bool:
    lower = text.lower()
    return any(k and k.lower() in lower for k in keywords)


def _fmt_pct(v: Any) -> str:
    f = _to_float(v)
    return "—" if f is None else f"{f:+.2f}%"


def _fmt_amount(amount_yuan: Any) -> str:
    v = _to_float(amount_yuan)
    if v is None:
        return ""
    if v >= 1e12:
        return f"{v / 1e12:.2f}万亿"
    if v >= 1e8:
        return f"{v / 1e8:.0f}亿"
    if v >= 1e4:
        return f"{v / 1e4:.0f}万"
    return f"{v:.0f}元"


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _source_note(obj: Any) -> str:
    parts: list[str] = []
    label = getattr(obj, "source_label", None)
    if label:
        parts.append(str(label))
    conf = {"high": "高置信", "medium": "中置信", "low": "低置信"}.get(
        getattr(obj, "source_confidence", None) or "",
        getattr(obj, "source_confidence", None) or "",
    )
    if conf:
        parts.append(str(conf))
    covered = getattr(obj, "covered_count", None)
    members = getattr(obj, "member_count", None)
    if covered is not None and members:
        parts.append(f"覆盖{covered}/{members}")
    return "，".join(parts)


def _has_sector_data(obj: Any) -> bool:
    return any(
        getattr(obj, attr, None) is not None
        for attr in ("pct_change", "amount_yuan", "up_ratio")
    )


def _sector_rows(ctx: Any) -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    for item in getattr(ctx, "sw_rotation", None) or []:
        rows.append(("L1", item))
    for item in getattr(ctx, "main_lines", None) or []:
        rows.append(("L2", item))
    return rows


def _eval_risk_appetite(ctx: Any, text: str) -> _Component:
    b = getattr(ctx, "breadth", None)
    missing: list[str] = []
    if b is None:
        return _Component(UNABLE, "缺少全市场广度与涨跌停结构。", ("全市场广度与涨跌停结构",), "短线情绪")

    explicit_open_board = _contains_any(text, OPEN_BOARD_KEYWORDS)
    if b.up_count is None or b.down_count is None:
        missing.append("全A涨跌家数")
    if b.limit_up_count is None or b.limit_down_count is None:
        missing.append("涨停/跌停实时代理")
    if explicit_open_board and b.broke_limit_pct is None:
        missing.append("触板/封板/炸板率实时代理")

    if explicit_open_board and b.broke_limit_pct is None:
        return _Component(
            UNABLE,
            "假设直接依赖炸板率，但午间缺少触板、封板与开板代理。",
            tuple(missing),
            "炸板率/风险偏好",
        )
    if len(missing) >= 2:
        return _Component(
            UNABLE,
            "缺少涨跌家数与涨跌停结构，无法判断风险偏好。",
            tuple(missing),
            "风险偏好",
        )

    score = 0
    up_share: float | None = None
    if b.up_count is not None and b.down_count is not None and (b.up_count + b.down_count) > 0:
        up_share = b.up_count / (b.up_count + b.down_count)
        if up_share >= 0.55:
            score += 1
        elif up_share <= 0.45:
            score -= 1

    if b.limit_up_count is not None:
        if b.limit_up_count >= 60:
            score += 1
        elif b.limit_up_count < 25:
            score -= 1
    if b.limit_down_count is not None:
        if b.limit_down_count >= 20:
            score -= 1
        elif b.limit_down_count <= 8:
            score += 1
    if b.broke_limit_pct is not None:
        if b.broke_limit_pct <= 0.30:
            score += 1
        elif b.broke_limit_pct >= 0.45:
            score -= 2
        elif b.broke_limit_pct >= 0.35:
            score -= 1

    expects_weak = _risk_hypothesis_expects_weak(text)
    if expects_weak:
        status = VALIDATED if score <= -1 else (PARTIAL if score <= 1 else FALSIFIED)
    else:
        status = VALIDATED if score >= 2 else (PARTIAL if score >= 0 else FALSIFIED)

    evidence_bits: list[str] = []
    if b.limit_up_count is not None or b.limit_down_count is not None:
        evidence_bits.append(f"涨停/跌停 {b.limit_up_count or 0}/{b.limit_down_count or 0} 家")
    if b.touched_limit_up_count is not None or b.broke_limit_count is not None:
        evidence_bits.append(f"触板/炸板 {b.touched_limit_up_count or 0}/{b.broke_limit_count or 0} 家")
    if b.broke_limit_pct is not None:
        evidence_bits.append(f"炸板率 {b.broke_limit_pct * 100:.0f}%")
    if up_share is not None:
        evidence_bits.append(f"上涨占比 {up_share * 100:.0f}%")
    source = getattr(b, "limit_source_label", None)
    conf = {"high": "高置信", "medium": "中置信", "low": "低置信"}.get(
        getattr(b, "limit_source_confidence", None) or "",
        getattr(b, "limit_source_confidence", None) or "",
    )
    if source:
        evidence_bits.append(f"{source}{f'({conf})' if conf else ''}")
    return _Component(status, "短线情绪：" + "，".join(evidence_bits) + "。", tuple(missing), "风险偏好")


def _risk_hypothesis_expects_weak(text: str) -> bool:
    lower = text.lower()
    healthy_patterns = (
        "风险偏好修复", "风险偏好改善", "风险偏好回升", "情绪修复", "情绪改善",
        "赚钱效应修复", "炸板率不能", "炸板率不应", "炸板率低", "炸板率回落",
        "炸板率下降", "不能明显抬升",
    )
    if any(p.lower() in lower for p in healthy_patterns):
        return False
    weak_patterns = (
        "风险偏好下降", "风险偏好走弱", "情绪降温", "情绪退潮", "分歧加大",
        "炸板率升高", "炸板率抬升", "开板潮", "退潮",
    )
    return any(p.lower() in lower for p in weak_patterns)


def _eval_indices(ctx: Any, text: str) -> list[_Component]:
    out: list[_Component] = []
    indices = getattr(ctx, "indices", None) or []
    for alias, (display, code) in INDEX_ALIASES.items():
        if alias not in text and display not in text and code not in text:
            continue
        snap = next((s for s in indices if s.name == display or s.ts_code == code), None)
        if snap is None or snap.pct_change is None:
            out.append(_Component(UNABLE, f"缺少{display}午间涨跌幅。", (f"{display}午间涨跌幅",), display))
            continue
        pct = _to_float(snap.pct_change) or 0.0
        status = VALIDATED if pct >= 0.5 else (PARTIAL if pct > -0.3 else FALSIFIED)
        out.append(_Component(status, f"{display}午间涨跌 {_fmt_pct(pct)}。", (), display))
    return out


def _eval_focus_stocks(ctx: Any, text: str) -> list[_Component]:
    out: list[_Component] = []
    all_specs = list(getattr(ctx, "important_focus", None) or []) + list(getattr(ctx, "regular_focus", None) or [])
    seen: set[str] = set()
    quote_by_code: dict[str, dict[str, Any]] = {}
    quote_by_code.update(getattr(ctx, "important_focus_data", None) or {})
    quote_by_code.update(getattr(ctx, "regular_focus_data", None) or {})

    for spec in all_specs:
        if spec.ts_code in seen:
            continue
        seen.add(spec.ts_code)
        if spec.display_name not in text and spec.ts_code not in text:
            continue
        quote = quote_by_code.get(spec.ts_code) or {}
        close = _to_float(quote.get("close"))
        pct = _to_float(quote.get("pct_change"))
        amount = quote.get("amount")
        missing = []
        if close is None or pct is None:
            missing.append(f"{spec.display_name}上午分时 close/pct")
        if amount is None:
            missing.append(f"{spec.display_name}上午成交额")
        if close is None or pct is None:
            out.append(_Component(
                UNABLE,
                f"缺少{spec.display_name}的上午分时价格与涨跌幅。",
                tuple(missing),
                spec.display_name,
            ))
            continue
        pct_f = pct or 0.0
        status = VALIDATED if pct_f >= 2.0 else (PARTIAL if pct_f > -0.3 else FALSIFIED)
        amount_text = f"，成交额 {_fmt_amount(amount)}" if amount is not None else ""
        source = quote.get("quote_source") or "分时"
        out.append(_Component(
            status,
            f"{spec.display_name}午间收盘 {close:.2f}，涨跌 {_fmt_pct(pct_f)}{amount_text}，来源 {source}。",
            tuple(missing),
            spec.display_name,
        ))
    return out


def _eval_chain(ctx: Any, text: str, spec: _ChainSpec) -> _Component:
    candidates = [
        (level, row)
        for level, row in _sector_rows(ctx)
        if _sector_matches(row, spec.sector_keywords)
    ]
    candidates = [(level, row) for level, row in candidates if _has_sector_data(row)]
    if not candidates:
        return _Component(UNABLE, f"缺少{spec.label}午间板块数据。", (spec.missing_label,), spec.label)
    return _score_sector_group(spec.label, candidates)


def _eval_direct_sector_mentions(
    ctx: Any,
    text: str,
    *,
    exclude_chain_specs: list[_ChainSpec],
) -> list[_Component]:
    excluded_keywords: set[str] = set()
    for spec in exclude_chain_specs:
        excluded_keywords.update(spec.sector_keywords)

    out: list[_Component] = []
    used_names: set[str] = set()
    for level, row in _sector_rows(ctx):
        name = getattr(row, "name", "") or ""
        if not name or name in used_names:
            continue
        if name not in text:
            continue
        if any(k in name for k in excluded_keywords):
            continue
        used_names.add(name)
        if not _has_sector_data(row):
            out.append(_Component(
                UNABLE,
                f"缺少{name}午间实时涨幅、成交额或上涨占比。",
                (f"{name}申万 {level} 实时涨幅、成交额或上涨占比",),
                name,
            ))
            continue
        out.append(_score_sector_group(name, [(level, row)]))
    return out


def _eval_overall_rotation(ctx: Any) -> _Component:
    l1 = [(level, row) for level, row in _sector_rows(ctx) if level == "L1" and _has_sector_data(row)]
    l2 = [(level, row) for level, row in _sector_rows(ctx) if level == "L2" and _has_sector_data(row)]
    missing: list[str] = []
    if not l1:
        missing.append("申万 L1 实时轮动涨幅/广度")
    if not l2:
        missing.append("SW L2 主线实时涨幅、成交额或上涨占比")
    if not l1 and not l2:
        return _Component(UNABLE, "缺少 L1/L2 轮动数据。", tuple(missing), "L1/L2轮动")

    sample = []
    if l1:
        sample.extend(sorted(l1, key=lambda x: _sector_sort_key(x[1]), reverse=True)[:2])
    if l2:
        sample.extend(sorted(l2, key=lambda x: _sector_sort_key(x[1]), reverse=True)[:3])
    result = _score_sector_group("L1/L2轮动", sample)
    if missing and result.status == VALIDATED:
        result = _Component(PARTIAL, result.evidence, tuple(missing), result.target)
    elif missing:
        result = _Component(result.status, result.evidence, tuple(missing), result.target)
    return result


def _sector_matches(row: Any, keywords: tuple[str, ...]) -> bool:
    name = getattr(row, "name", "") or ""
    code = getattr(row, "code", "") or ""
    return any(k and (k in name or k in code) for k in keywords)


def _sector_sort_key(row: Any) -> tuple[float, float, float]:
    pct = _to_float(getattr(row, "pct_change", None))
    up = _to_float(getattr(row, "up_ratio", None))
    amount = _to_float(getattr(row, "amount_yuan", None))
    return (
        pct if pct is not None else -999.0,
        up if up is not None else -1.0,
        amount if amount is not None else -1.0,
    )


def _sector_score(level: str, row: Any) -> int:
    score = 0
    pct = _to_float(getattr(row, "pct_change", None))
    up_ratio = _to_float(getattr(row, "up_ratio", None))
    rank = getattr(row, "rank", None)
    if pct is not None:
        if pct >= 1.5:
            score += 2
        elif pct > 0:
            score += 1
        elif pct <= -1.0:
            score -= 2
        elif pct < 0:
            score -= 1
    if up_ratio is not None:
        if up_ratio >= 0.60:
            score += 1
        elif up_ratio <= 0.40:
            score -= 1
    if isinstance(rank, int) and rank > 0:
        if (level == "L1" and rank <= 5) or (level == "L2" and rank <= 3):
            score += 1
        elif level == "L1" and rank >= 24:
            score -= 1
    return score


def _score_sector_group(label: str, rows: list[tuple[str, Any]]) -> _Component:
    rows = sorted(rows, key=lambda x: _sector_sort_key(x[1]), reverse=True)
    scores = [_sector_score(level, row) for level, row in rows]
    best = max(scores) if scores else -99
    positive_count = sum(
        1 for _, row in rows
        if (_to_float(getattr(row, "pct_change", None)) or -999.0) > 0
    )
    if best >= 3 and positive_count >= 1:
        status = VALIDATED
    elif best >= 1 or positive_count > 0:
        status = PARTIAL
    else:
        status = FALSIFIED

    bits: list[str] = []
    source_bits: list[str] = []
    for level, row in rows[:4]:
        name = getattr(row, "name", "") or getattr(row, "code", "")
        rank = getattr(row, "rank", None)
        rank_text = f"{level}#{rank}" if isinstance(rank, int) and rank > 0 else level
        amount = _fmt_amount(getattr(row, "amount_yuan", None))
        amount_text = f"，成交额{amount}" if amount else ""
        up_ratio = _to_float(getattr(row, "up_ratio", None))
        up_text = f"，上涨占比{up_ratio * 100:.0f}%" if up_ratio is not None else ""
        bits.append(f"{name} {_fmt_pct(getattr(row, 'pct_change', None))}({rank_text}{amount_text}{up_text})")
        note = _source_note(row)
        if note:
            source_bits.append(note)
    source_suffix = f"；来源：{'；'.join(dict.fromkeys(source_bits))}" if source_bits else ""
    return _Component(status, f"{label}：{'；'.join(bits)}{source_suffix}。", (), label)


def _combine_components(hyp: dict[str, Any], components: list[_Component]) -> dict[str, Any]:
    evaluated = [c for c in components if c.status != UNABLE]
    missing = _unique_missing(components)

    if not evaluated:
        status = UNABLE
    elif any(c.status == FALSIFIED for c in evaluated) and any(c.status in (VALIDATED, PARTIAL) for c in evaluated):
        status = PARTIAL
    elif all(c.status == VALIDATED for c in evaluated) and not missing:
        status = VALIDATED
    elif all(c.status == FALSIFIED for c in evaluated):
        status = FALSIFIED if not missing else PARTIAL
    else:
        status = PARTIAL

    evidence = _join_evidence(components, status)
    missing_reason = _missing_reason(components, missing) if missing else ""
    if status == UNABLE and missing_reason:
        evidence = missing_reason
    elif missing_reason:
        evidence = f"{evidence} 另有缺口：{missing_reason}"

    lesson = _lesson(status, missing)
    return {
        "hypothesis": hyp.get("hypothesis") or "",
        "review_result": status,
        "review_result_display": DISPLAY[status],
        "evidence_text": evidence,
        "lesson": lesson,
        "missing_inputs": missing,
        "missing_reason": missing_reason,
    }


def _unique_missing(components: list[_Component]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for c in components:
        for item in c.missing_inputs:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
    return out


def _join_evidence(components: list[_Component], status: str) -> str:
    usable = [c.evidence for c in components if c.evidence and (status == UNABLE or c.status != UNABLE)]
    if not usable:
        usable = [c.evidence for c in components if c.evidence]
    text = " ".join(usable)
    # Keep the review table concise; the underlying row still carries the full
    # missing_inputs list for renderer display.
    return re.sub(r"\s+", " ", text).strip()[:260]


def _missing_reason(components: list[_Component], missing: list[str]) -> str:
    targets = [c.target for c in components if c.status == UNABLE and c.target]
    target_text = "、".join(dict.fromkeys(targets)) or "该假设"
    return f"缺少{'、'.join(missing)}，无法验证{target_text}。"


def _lesson(status: str, missing: list[str]) -> str:
    if status == VALIDATED:
        return "午后观察能否保持到收盘。"
    if status == FALSIFIED:
        return "午后若无修复，早报假设需降权。"
    if status == UNABLE:
        return "补齐缺失数据后再判断。"
    if missing:
        return "已有部分证据，缺口补齐后再定性。"
    return "证据分化，下午看扩散与承接。"
