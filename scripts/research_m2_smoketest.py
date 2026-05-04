"""M2.4 — Research family smoke test.

Resolves 3 test stocks → fetch_all (from cache) → load_company_snapshot
→ runs all 5 compute_<family> → prints factor values/status for human verification.

Usage:
    uv run python scripts/research_m2_smoketest.py
"""
from __future__ import annotations

import sys
from datetime import date

from ifa.core.db import get_engine
from ifa.core.report.timezones import bjt_now
from ifa.families.research.analyzer.balance import compute_balance
from ifa.families.research.analyzer.cash_quality import compute_cash_quality
from ifa.families.research.analyzer.data import load_company_snapshot
from ifa.families.research.analyzer.factors import FactorResult, FactorStatus, load_params
from ifa.families.research.analyzer.governance import compute_governance
from ifa.families.research.analyzer.growth import compute_growth
from pathlib import Path

from ifa.families.research.analyzer.peer import attach_peer_ranks
from ifa.families.research.analyzer.persistence import persist_all_families
from ifa.families.research.analyzer.profitability import compute_profitability
from ifa.families.research.analyzer.scoring import score_results
from ifa.families.research.analyzer.timeline import build_timeline
from ifa.families.research.analyzer.trends import classify_trend_from_params
from ifa.families.research.report import build_research_report, render_markdown
from ifa.families.research.report.html import HtmlRenderer
from ifa.families.research.resolver import CompanyNotFoundError, resolve

_TMP_DIR = Path(__file__).resolve().parent.parent / "tmp"

TEST_STOCKS = [
    "智微智能",   # 001339.SZ
    "致尚科技",   # 301486.SZ
    "鹏鼎控股",   # 002938.SZ
]

STATUS_ICON = {
    FactorStatus.GREEN: "🟢",
    FactorStatus.YELLOW: "🟡",
    FactorStatus.RED: "🔴",
    FactorStatus.UNKNOWN: "⬜",
}


def run_smoketest() -> None:
    engine = get_engine()
    params = load_params()
    cutoff = bjt_now().date()

    for name in TEST_STOCKS:
        print(f"\n{'='*60}")
        print(f"  {name}")
        print(f"{'='*60}")

        try:
            company = resolve(name, engine)
        except CompanyNotFoundError:
            print(f"  [SKIP] 未找到公司: {name}")
            continue
        except Exception as e:
            print(f"  [ERROR] resolve 失败: {e}")
            continue

        print(f"  ts_code: {company.ts_code}  exchange: {company.exchange}")

        try:
            snap = load_company_snapshot(engine, company, data_cutoff_date=cutoff)
        except Exception as e:
            print(f"  [ERROR] load_company_snapshot 失败: {e}")
            continue

        print(f"  latest_period: {snap.latest_period}")
        print(f"  missing_apis:  {snap.missing_apis or '(none)'}")

        results_by_family = {
            "profitability": compute_profitability(snap, params),
            "growth":        compute_growth(snap, params),
            "cash_quality":  compute_cash_quality(snap, params),
            "balance":       compute_balance(snap, params),
            "governance":    compute_governance(snap, params),
        }

        # Persist BEFORE peer rank so the peer scan can find this stock too.
        n_persisted = persist_all_families(engine, company.ts_code, results_by_family)
        # Attach peer percentiles by reading research.factor_value × sw_member_monthly.
        for results in results_by_family.values():
            attach_peer_ranks(engine, results, snap)
        # Re-persist with peer rank attached (idempotent upsert).
        persist_all_families(engine, company.ts_code, results_by_family)
        print(f"  [DB] persisted {n_persisted} factor rows + peer ranks")
        family_labels = {
            "profitability": "A 盈利能力",
            "growth":        "B 增长",
            "cash_quality":  "C 现金质量",
            "balance":       "D 资产负债",
            "governance":    "E 治理披露",
        }

        for fam, results in results_by_family.items():
            print(f"\n  [{family_labels[fam]}]")
            for r in results:
                icon = STATUS_ICON.get(r.status, "?")
                val_str = f"{float(r.value):.2f}" if r.value is not None else "N/A"
                unit = r.spec.unit if r.spec.unit != "categorical" else ""
                notes_str = " | ".join(r.notes) if r.notes else ""
                notes_display = f"  ← {notes_str}" if notes_str else ""
                print(f"    {icon} {r.spec.name:<22} {val_str:>10} {unit:<4}{notes_display}")

        # Trends (sample series)
        print(f"\n  [Trends — 关键序列趋势]")
        trend_targets = [
            ("营收", snap.revenue_series),
            ("净利", snap.n_income_series),
            ("ROE", snap.roe_series),
            ("毛利率", snap.gpm_series),
            ("CFO", snap.cfo_series),
        ]
        for label, ts in trend_targets:
            if ts is None or not ts.values:
                print(f"    {label:<6}  (无数据)")
                continue
            tr = classify_trend_from_params(ts.values, params)
            slope_str = f"{tr.slope_pct_per_period:+.1f}%/期" if tr.slope_pct_per_period is not None else "—"
            print(f"    {label:<6}  {tr.arrow} {tr.label_zh}  ({slope_str}, n={tr.n_periods})")

        # 5-dim scoring
        scoring = score_results(results_by_family, params)
        print(f"\n  [5维评分 — 总分 {scoring.overall_score if scoring.overall_score is None else f'{scoring.overall_score:.1f}'}  {scoring.overall_label_zh}]")
        for fam_name, fs in scoring.families.items():
            score_str = f"{fs.score:.1f}" if fs.score is not None else "N/A"
            cov_str = f"{fs.weight_coverage*100:.0f}%"
            icon = STATUS_ICON.get(fs.status, "?")
            print(f"    {icon} {fs.label_zh:<4}  {score_str:>5}  (权重覆盖 {cov_str})")

        # Timeline preview
        timeline = build_timeline(snap)
        print(f"\n  [Timeline — 最近 5 条]")
        for ev in timeline[:5]:
            print(f"    {ev.publish_time}  [{ev.event_type:<12}] {ev.title[:60]}")

        # ── M3: build & render report ────────────────────────────────
        report = build_research_report(snap, results_by_family, scoring, params)
        _TMP_DIR.mkdir(parents=True, exist_ok=True)
        html = HtmlRenderer().render(report=report)
        html_path = _TMP_DIR / f"research_{company.ts_code}.html"
        html_path.write_text(html, encoding="utf-8")
        md_path = _TMP_DIR / f"research_{company.ts_code}.md"
        md_path.write_text(render_markdown(report), encoding="utf-8")
        print(f"\n  [Report] HTML → {html_path.relative_to(_TMP_DIR.parent)}"
              f"  ·  MD → {md_path.relative_to(_TMP_DIR.parent)}")

    print(f"\n{'='*60}")
    print("  Smoketest complete.")


if __name__ == "__main__":
    run_smoketest()
