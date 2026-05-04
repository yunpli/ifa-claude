"""Initialize the 30-stock golden set for Research V2.2 regression.

What this script does:
  1. Picks 30 stocks that are diverse across boards (SH/SZ main, ChiNext,
     STAR, Beijing) × score range (top / middle / bottom 10) × industry families.
  2. Generates a deep+LLM report for each into `tests/golden_set/reports/`.
  3. Writes a blank annotation template `tests/golden_set/research_v22.json`
     with one entry per stock for the user to fill in.

Run:
    uv run python scripts/research_golden_set_init.py

After completion, the user fills `research_v22.json` (~2-3 min/stock = 1.5h)
and then runs `scripts/research_regression.py` to compare system output
vs annotations.

The annotation schema is intentionally minimal — 4 questions per stock:
  Q1 verdict_alignment: agree / disagree / partial
  Q2 dimension_disagreements: dict of {family: should_be_X | "ok"}
  Q3 your_watchpoints: 3 free-text concerns
  Q4 system_watchpoints_wrong: list of 1-indexed positions of bad watchpoints

Total per stock: ~3 minutes.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from sqlalchemy import text

from ifa.core.db import get_engine

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_DIR = ROOT / "tests" / "golden_set"
REPORTS_DIR = GOLDEN_DIR / "reports"
ANNOTATIONS_PATH = GOLDEN_DIR / "research_v22.json"

# Target distribution (sums to 30):
#   SSE 主板 (60xxxx but not 688): 6
#   SSE 科创板 (688xxx):           6
#   SZSE 主板 (00xxxx):            6
#   SZSE 创业板 (300xxx):          6
#   BSE 北交所 (8/4/9...):         6
BOARD_TARGETS = {
    "sse_main":     {"count": 6, "sql_filter": "ps.ts_code LIKE '60%' AND ps.ts_code NOT LIKE '688%'"},
    "sse_star":     {"count": 6, "sql_filter": "ps.ts_code LIKE '688%'"},
    "szse_main":    {"count": 6, "sql_filter": "(ps.ts_code LIKE '000%' OR ps.ts_code LIKE '001%' OR ps.ts_code LIKE '002%')"},
    "szse_chinext": {"count": 6, "sql_filter": "(ps.ts_code LIKE '300%' OR ps.ts_code LIKE '301%')"},
    "bse":          {"count": 6, "sql_filter": "ci.exchange = 'BSE'"},
}


def _pick_for_board(engine, sql_filter: str, n: int) -> list[dict]:
    """Pick n stocks: aim for ~equal split of high-score / mid-score / low-score."""
    sql = text(f"""
        WITH per_factor AS (
            SELECT fv.ts_code, fv.family,
                   CASE fv.status
                     WHEN 'green' THEN 80.0
                     WHEN 'yellow' THEN 50.0
                     WHEN 'red' THEN 20.0
                   END AS base,
                   fv.peer_percentile AS pct
            FROM research.factor_value fv
            WHERE fv.value IS NOT NULL
        ),
        per_blend AS (
            SELECT ts_code, family,
                   CASE WHEN base IS NOT NULL AND pct IS NOT NULL
                          THEN 0.5*base + 0.5*pct
                        ELSE base END AS s
            FROM per_factor
        ),
        per_fam AS (
            SELECT ts_code, family, AVG(s) AS family_score
            FROM per_blend WHERE s IS NOT NULL GROUP BY ts_code, family
        ),
        per_stock AS (
            SELECT ts_code, AVG(family_score) AS overall
            FROM per_fam GROUP BY ts_code HAVING COUNT(*) >= 4
        )
        SELECT ps.ts_code, ci.name, sm.l1_name AS l1_name, sm.l2_name AS l2_name,
               ci.exchange, ps.overall
        FROM per_stock ps
        JOIN research.company_identity ci ON ps.ts_code = ci.ts_code
        LEFT JOIN smartmoney.sw_member_monthly sm ON ps.ts_code = sm.ts_code
          AND sm.snapshot_month = date_trunc('month', CURRENT_DATE)::date
        WHERE {sql_filter}
        ORDER BY ps.overall DESC
    """)
    with engine.connect() as conn:
        rows = conn.execute(sql).fetchall()

    if not rows:
        return []

    total = len(rows)
    if total <= n:
        return [_row_to_dict(r) for r in rows]

    # Stratified pick: top n/3, middle n/3, bottom n/3
    third = max(1, n // 3)
    top = rows[:third]
    bot = rows[-third:]
    mid_start = (total - (n - 2 * third)) // 2
    mid = rows[mid_start:mid_start + (n - 2 * third)]
    return [_row_to_dict(r) for r in top + mid + bot]


def _row_to_dict(r) -> dict:
    return {
        "ts_code": r[0],
        "name": r[1],
        "sw_l1": r[2],
        "sw_l2": r[3],
        "exchange": r[4],
        "overall_score": float(r[5]),
    }


def main() -> None:
    engine = get_engine()
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("picking 30 stocks across 5 boards…")
    selected: list[dict] = []
    for board, cfg in BOARD_TARGETS.items():
        picks = _pick_for_board(engine, cfg["sql_filter"], cfg["count"])
        log.info("  %s: %d stocks", board, len(picks))
        for p in picks:
            p["board"] = board
        selected.extend(picks)

    log.info("total: %d stocks", len(selected))

    # Generate one report per stock (deep + LLM); save HTML+MD.
    # Use in-process builder (subprocess spawn is ~20× slower per stock).
    from ifa.families.research.analyzer.balance import compute_balance
    from ifa.families.research.analyzer.cash_quality import compute_cash_quality
    from ifa.families.research.analyzer.data import load_company_snapshot
    from ifa.families.research.analyzer.factors import load_params
    from ifa.families.research.analyzer.governance import compute_governance
    from ifa.families.research.analyzer.growth import compute_growth
    from ifa.families.research.analyzer.peer import attach_peer_ranks
    from ifa.families.research.analyzer.profitability import compute_profitability
    from ifa.families.research.analyzer.scoring import score_results
    from ifa.families.research.report.builder import build_research_report
    from ifa.families.research.report.html import HtmlRenderer
    from ifa.families.research.report.llm_aug import LLMAugmenter
    from ifa.families.research.report.markdown import render_markdown
    from ifa.families.research.resolver import resolve
    from ifa.core.report.timezones import bjt_now

    log.info("generating deep reports (~10s each, 30 stocks ≈ 5 min cold)…")
    params = load_params()
    augmenter = LLMAugmenter(cache_engine=engine)
    html_renderer = HtmlRenderer()

    for i, s in enumerate(selected, start=1):
        log.info("  [%d/%d] %s · %s", i, len(selected), s["ts_code"], s["name"])
        try:
            company = resolve(s["ts_code"], engine)
            snap = load_company_snapshot(engine, company,
                                         data_cutoff_date=bjt_now().date())
            results = {
                "profitability": compute_profitability(snap, params),
                "growth": compute_growth(snap, params),
                "cash_quality": compute_cash_quality(snap, params),
                "balance": compute_balance(snap, params),
                "governance": compute_governance(snap, params),
            }
            for r_list in results.values():
                attach_peer_ranks(engine, r_list, snap)
            scoring = score_results(results, params)
            report = build_research_report(
                snap, results, scoring, params,
                tier="deep", augmenter=augmenter, engine=engine,
            )
            stamp = bjt_now().strftime("%Y%m%d")
            base = REPORTS_DIR / f"Stock-Analysis-{s['ts_code']}-{stamp}-deep"
            base.with_suffix(".html").write_text(
                html_renderer.render(report=report), encoding="utf-8"
            )
            base.with_suffix(".md").write_text(
                render_markdown(report), encoding="utf-8"
            )
            s["report_html_path"] = str(base.with_suffix(".html").relative_to(ROOT))
            s["report_md_path"] = str(base.with_suffix(".md").relative_to(ROOT))
            s["report_status"] = "ok"
        except Exception as e:
            log.warning("    failed: %s", str(e)[:200])
            s["report_status"] = f"failed: {str(e)[:80]}"

    # Build annotation template
    annotations = []
    for s in selected:
        annotations.append({
            "ts_code": s["ts_code"],
            "name": s["name"],
            "board": s["board"],
            "sw_l2": s.get("sw_l2"),
            "system_overall_score": round(s["overall_score"], 1),
            "report_html": s.get("report_html_path", ""),
            "report_md":   s.get("report_md_path", ""),
            "_INSTRUCTIONS": (
                "Open the report (HTML or MD) and answer 4 questions. "
                "Leave fields blank only if you genuinely don't know."
            ),
            "verdict_alignment": "<agree | disagree | partial>",
            "dimension_disagreements": {
                "profitability": "ok",
                "growth": "ok",
                "cash_quality": "ok",
                "balance": "ok",
                "governance": "ok",
            },
            "your_watchpoints": ["", "", ""],
            "system_watchpoints_wrong_indices": [],
            "notes": "",
        })

    ANNOTATIONS_PATH.write_text(
        json.dumps(annotations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("annotation template → %s", ANNOTATIONS_PATH)
    log.info("reports → %s", REPORTS_DIR)
    log.info("DONE. Fill in the JSON file (~3 min/stock) then run "
             "scripts/research_regression.py")


if __name__ == "__main__":
    main()
