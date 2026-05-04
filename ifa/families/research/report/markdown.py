"""Markdown renderer — terminal-friendly preview of a ResearchReport.

Used in smoketests / manual verification. The HTML renderer is the actual
deliverable; this one's optimized for monospace readability.
"""
from __future__ import annotations

from typing import Any

_STATUS_ICON = {
    "green": "🟢",
    "yellow": "🟡",
    "red": "🔴",
    "unknown": "⬜",
}


def render_markdown(report: dict[str, Any]) -> str:
    parts: list[str] = []
    parts.append(f"# {report['title']}")
    parts.append(f"_{report['subtitle_en']}_  · 数据截止 {report['data_cutoff_bjt']} · {report['template_version']}")
    parts.append("")

    score = report.get("overall_score")
    score_str = f"{score:.1f}" if score is not None else "N/A"
    icon = _STATUS_ICON.get(report["overall_status"], "?")
    parts.append(f"## 综合评分 {score_str}  {icon} {report['overall_label_zh']}")
    parts.append("")

    for s in report["sections"]:
        renderer = _RENDERERS.get(s["type"])
        if renderer is None:
            continue
        parts.append(renderer(s))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


# ─── Section renderers ────────────────────────────────────────────────────────

def _r_overview(s: dict) -> str:
    lines = ["### 公司概况"]
    lines.append(f"- **代码 / 名称**: {s['ts_code']} · {s.get('name') or '—'}")
    if s.get("sw_l1") or s.get("sw_l2"):
        lines.append(f"- **申万行业**: {s.get('sw_l1') or '—'} / {s.get('sw_l2') or '—'}")
    if s.get("industry"):
        lines.append(f"- **TS 行业**: {s['industry']}")
    if s.get("list_date"):
        lines.append(f"- **上市日期**: {s['list_date']}")
    if s.get("employees"):
        lines.append(f"- **员工数**: {s['employees']}")
    if s.get("latest_period"):
        lines.append(f"- **最新报告期**: {s['latest_period']}")
    if s.get("main_business"):
        lines.append(f"- **主营**: {s['main_business']}")
    if s.get("missing_apis"):
        lines.append(f"- **缺失数据源**: {', '.join(s['missing_apis'])}")
    return "\n".join(lines)


def _r_radar(s: dict) -> str:
    lines = ["### 5 维评分"]
    if s.get("narrative"):
        lines.append(f"> {s['narrative']}")
        lines.append("")
    lines.append("| 维度 | 得分 | 状态 | 权重覆盖 |")
    lines.append("|---|---:|---|---:|")
    for fam in s["families"]:
        score = fam["score"]
        score_str = f"{score:.1f}" if score is not None else "—"
        icon = _STATUS_ICON.get(fam["status"], "?")
        cov = f"{fam['weight_coverage']*100:.0f}%"
        lines.append(f"| {fam['label_zh']} | {score_str} | {icon} | {cov} |")
    return "\n".join(lines)


def _r_factor_table(s: dict) -> str:
    icon = _STATUS_ICON.get(s["family_status"], "?")
    score = s.get("family_score")
    score_str = f"{score:.1f}" if score is not None else "—"
    lines = [f"### {s['family_label_zh']}（{icon} {score_str}）"]
    if s.get("narrative"):
        lines.append(f"> {s['narrative']}")
        lines.append("")
    lines.append("| 因子 | 值 | 状态 | 同业 | 备注 |")
    lines.append("|---|---:|:-:|:-:|---|")
    for r in s["rows"]:
        ic = _STATUS_ICON.get(r["status"], "?")
        notes = "; ".join(r["notes"]) if r["notes"] else ""
        peer = _format_peer(r.get("peer_rank"), r.get("peer_percentile"))
        lines.append(f"| {r['name_zh']} | {r['value']} | {ic} | {peer} | {notes} |")
    return "\n".join(lines)


def _format_peer(peer_rank, peer_percentile) -> str:
    if peer_rank is None:
        return "—"
    rank, total = peer_rank
    pct = f"{peer_percentile:.0f}%" if peer_percentile is not None else "—"
    return f"{rank}/{total} ({pct})"


def _r_trend_grid(s: dict) -> str:
    lines = ["### 关键序列趋势"]
    lines.append("| 指标 | 趋势 | 斜率 | 期数 |")
    lines.append("|---|:-:|---:|---:|")
    for it in s["entries"]:
        slope = it["slope_pct_per_period"]
        slope_str = f"{slope:+.1f}%/期" if slope is not None else "—"
        lines.append(
            f"| {it['label']} | {it['arrow']} {it['label_zh']} | "
            f"{slope_str} | {it['n_periods']} |"
        )
    return "\n".join(lines)


def _r_red_flags(s: dict) -> str:
    if not s["flags"]:
        return "### 风险提示\n无 RED/YELLOW 信号。"
    lines = [f"### 风险提示（共 {s['count']} 条）"]
    for f in s["flags"]:
        ic = _STATUS_ICON.get(f["status"], "?")
        notes = " · ".join(f["notes"]) if f["notes"] else ""
        lines.append(
            f"- {ic} **{f['family_label_zh']}** / {f['factor_zh']} ({f['factor']})"
            f" = {f['value']}{(' — ' + notes) if notes else ''}"
        )
    return "\n".join(lines)


def _r_timeline(s: dict) -> str:
    if not s["events"]:
        return "### 近期披露 (0 条)"
    extracted_n = sum(1 for e in s["events"] if e.get("is_extracted"))
    suffix = f" · {extracted_n} 条已结构化" if extracted_n else ""
    lines = [f"### 近期披露（最近 {len(s['events'])} 条{suffix}）"]
    pol_icon = {"positive": "↑", "negative": "↓", "neutral": "○"}
    imp_icon = {"high": "●●●", "medium": "●●", "low": "●"}
    for e in s["events"]:
        url = f" [PDF]({e['source_url']})" if e.get("source_url") else ""
        meta = ""
        if e.get("is_extracted"):
            p = pol_icon.get(e.get("polarity"), "")
            i = imp_icon.get(e.get("importance"), "")
            meta = f" {p}{i}".strip()
        lines.append(f"- `{e['publish_time']}` **[{e['event_type']}]**{meta} {e['title']}{url}")
    return "\n".join(lines)


def _r_tensions(s: dict) -> str:
    if not s.get("entries"):
        return ""
    sev_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    lines = [f"### 横切张力（共 {s['count']} 条）"]
    for t in s["entries"]:
        ic = sev_icon.get(t.get("severity"), "?")
        title = t.get("title", "")
        ev = t.get("evidence", [])
        ev_str = f" `{' '.join(ev)}`" if ev else ""
        lines.append(f"- {ic} **{title}**{ev_str}")
        if t.get("description"):
            lines.append(f"  {t['description']}")
    return "\n".join(lines)


def _r_analyst_coverage(s: dict) -> str:
    lines = [f"### 研报覆盖（§10 · 共 {s['total_reports']} 份）"]
    if s.get("coverage_gap_warning"):
        lines.append(f"> ⚠ 覆盖减弱：最近 {s['days_since_latest']} 天无新研报")
    elif s.get("latest_report_date"):
        lines.append(f"> 最新研报：{s['latest_report_date']}（{s['days_since_latest']} 天前）")

    if s.get("reports_by_month"):
        bars = " ".join(f"{m['month'][-2:]}月={m['count']}" for m in s["reports_by_month"])
        lines.append("")
        lines.append(f"**月度量**: {bars}")

    if s.get("top_institutions"):
        lines.append("")
        lines.append("**Top 机构**:")
        for inst in s["top_institutions"]:
            lines.append(f"- {inst['name']} · {inst['count']} 份 · 最新 {inst['latest_date']}")

    if s.get("themes"):
        lines.append("")
        lines.append("**研究主题（LLM 聚类）**:")
        sent_icon = {"bullish": "↑ 看多", "cautious": "→ 中性", "bearish": "↓ 看空"}
        for th in s["themes"]:
            ic = sent_icon.get(th.get("sentiment"), "?")
            lines.append(f"- **{th.get('label', '?')}** · {th.get('count', 0)} 份 · {ic}")
            for t in th.get("representative_titles", [])[:2]:
                lines.append(f"  > {t}")
    return "\n".join(lines)


def _r_investor_concerns(s: dict) -> str:
    if not s.get("entries"):
        return ""
    pol_label = {"concern": "🔴 担忧", "curious": "🔵 探索", "positive": "🟢 积极"}
    lines = [f"### 投资者关切（IRM 聚类，{s['count']} 主题）"]
    for th in s["entries"]:
        pol = pol_label.get(th.get("polarity"), "?")
        lines.append(
            f"- **{th.get('label', '?')}** · {th.get('count', 0)} 问 · "
            f"回复率 {th.get('is_answered_pct', 0)}% · {pol}"
        )
        for q in th.get("representative", [])[:3]:
            lines.append(f"  > {q}")
    return "\n".join(lines)


def _r_watchpoints(s: dict) -> str:
    if not s.get("entries"):
        return "### 关注点\n暂无 LLM 综合关注点。"
    severity_icon = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    lines = [f"### 关注点（LLM 综合 · 共 {s['count']} 条）"]
    for w in s["entries"]:
        ic = severity_icon.get(w.get("severity"), "?")
        lines.append(f"- {ic} **{w.get('category', '?')} · {w.get('title', '')}**")
        if w.get("description"):
            lines.append(f"  {w['description']}")
        if w.get("what_to_watch"):
            lines.append(f"  _下一观察点 → {w['what_to_watch']}_")
    return "\n".join(lines)


def _r_disclaimer(s: dict) -> str:
    # Markdown is for terminal preview — full 10-paragraph dump would dominate.
    # Render a compact version: "see full disclaimer below" + the first paragraph.
    lines = ["### 完整免责声明 / Full Disclaimer"]
    lines.append(f"_本报告共含 10 段中英对照免责声明（HTML 版可展开查看）_")
    if s.get("paragraphs_zh"):
        lines.append("")
        lines.append("> " + s["paragraphs_zh"][0][:200] + "…")
    return "\n".join(lines)


_RENDERERS = {
    "research_overview": _r_overview,
    "research_radar": _r_radar,
    "research_factor_table": _r_factor_table,
    "research_trend_grid": _r_trend_grid,
    "research_red_flags": _r_red_flags,
    "research_tensions": _r_tensions,
    "research_watchpoints": _r_watchpoints,
    "research_investor_concerns": _r_investor_concerns,
    "research_analyst_coverage": _r_analyst_coverage,
    "research_timeline": _r_timeline,
    "research_disclaimer": _r_disclaimer,
}
