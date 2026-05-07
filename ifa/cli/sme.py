"""`ifa sme ...` CLI for Smart Money Enhanced MVP-1."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

import typer
from rich.console import Console
from rich.table import Table

from ifa.config import get_settings
from ifa.core.db import get_engine
from ifa.families.sme.data.calendar import latest_trade_date, parse_date


console = Console()
app = typer.Typer(no_args_is_help=True, help="Smart Money Enhanced — production-grade orderflow research.")
etl_app = typer.Typer(no_args_is_help=True, help="SME ETL and daily orchestration.")
compute_app = typer.Typer(no_args_is_help=True, help="SME compute subcommands.")
tune_app = typer.Typer(no_args_is_help=True, help="SME tuning and OOS readiness tools.")
app.add_typer(etl_app, name="etl")
app.add_typer(compute_app, name="compute")
app.add_typer(tune_app, name="tune")


def _print_json(payload: dict) -> None:
    # Machine JSON must bypass Rich; Rich line wrapping can insert literal
    # newlines inside long Chinese strings and break third-party parsers.
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")


def _date_or_latest(engine, value: str | None) -> dt.date:
    parsed = parse_date(value)
    return parsed or latest_trade_date(engine)


def _sme_standard_output_path(*, report_date: dt.date, run_mode: str, output_format: str) -> Path:
    settings = get_settings()
    if run_mode not in {"production", "manual", "test"}:
        raise typer.BadParameter("--run-mode must be production, manual, or test")
    suffix = "html" if output_format == "html" else "md"
    bjt_now = dt.datetime.now(tz=ZoneInfo("Asia/Shanghai"))
    if run_mode in {"production", "manual"}:
        out_dir = Path(settings.output_root) / run_mode / report_date.strftime("%Y%m%d") / "sme"
    else:
        out_dir = Path(settings.output_root) / "test"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"CN_sme_brief_{report_date.strftime('%Y%m%d')}_{bjt_now.strftime('%H%M')}.{suffix}"


@app.command("init-schema")
def init_schema() -> None:
    """Apply Alembic migrations so the `sme` schema exists."""
    cmd = [sys.executable, "-m", "alembic", "upgrade", "head"]
    result = subprocess.run(cmd, check=False)
    if result.returncode != 0:
        raise typer.Exit(result.returncode)
    console.print("[green]Schema is up to date.[/green]")


@app.command("doctor")
def doctor(
    check: str = typer.Option("schema,sources,units", "--check", help="schema,sources,units,contracts,data"),
    date: str | None = typer.Option(None, "--date", help="YYYY-MM-DD for data checks"),
    source_mode: str = typer.Option("prefer_smartmoney", "--source-mode"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run SME health checks."""
    from sqlalchemy import text

    from ifa.families.sme.data.contracts import run_basic_contracts
    from ifa.families.sme.data.source_resolver import validate_sources
    from ifa.families.sme.data.units import seed_unit_registry, validate_unit_registry

    engine = get_engine()
    checks = {c.strip() for c in check.split(",") if c.strip()}
    results: list[dict] = []
    exit_code = 0

    if "schema" in checks:
        with engine.connect() as conn:
            exists = conn.execute(text("SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name='sme')")).scalar()
        status = "ok" if exists else "blocked"
        results.append({"check": "schema", "status": status, "message": "sme schema exists" if exists else "sme schema missing"})
        if not exists:
            exit_code = max(exit_code, 2)

    if "sources" in checks:
        errors = validate_sources(engine, source_mode=source_mode)
        results.append({"check": "sources", "status": "ok" if not errors else "blocked", "message": "; ".join(errors) or "all core sources available"})
        if errors:
            exit_code = max(exit_code, 3)

    if "units" in checks:
        try:
            seed_unit_registry(engine)
            errors = validate_unit_registry(engine)
        except Exception as exc:  # noqa: BLE001
            errors = [f"{type(exc).__name__}: {exc}"]
        results.append({"check": "units", "status": "ok" if not errors else "blocked", "message": "; ".join(errors) or "unit registry ok"})
        if errors:
            exit_code = max(exit_code, 2)

    if "contracts" in checks or "data" in checks:
        d = _date_or_latest(engine, date) if date is not None else None
        try:
            contract_results = run_basic_contracts(engine, d)
            for r in contract_results:
                results.append({"check": r.name, "status": r.status, "message": r.message})
                if r.status == "blocked":
                    exit_code = max(exit_code, 2)
        except Exception as exc:  # noqa: BLE001
            results.append({"check": "contracts", "status": "degraded", "message": f"{type(exc).__name__}: {exc}"})
            exit_code = max(exit_code, 1)

    overall_status = "blocked" if exit_code else ("degraded" if any(r["status"] == "degraded" for r in results) else "ok")
    payload = {"status": overall_status, "checks": results}
    if json_out:
        _print_json(payload)
    else:
        table = Table(title="SME doctor")
        table.add_column("Check")
        table.add_column("Status")
        table.add_column("Message")
        for r in results:
            table.add_row(r["check"], r["status"], r["message"])
        console.print(table)
    if exit_code:
        raise typer.Exit(exit_code)


@app.command("status")
def status(json_out: bool = typer.Option(False, "--json")) -> None:
    """Show latest SME table status."""
    from sqlalchemy import text

    engine = get_engine()
    tables = [
        "sme_sw_member_daily",
        "sme_stock_orderflow_daily",
        "sme_sector_orderflow_daily",
        "sme_sector_diffusion_daily",
        "sme_sector_state_daily",
        "sme_labels_daily",
        "sme_market_structure_daily",
        "sme_strategy_eval_daily",
    ]
    rows = []
    with engine.connect() as conn:
        for table in tables:
            latest_col = "trade_date"
            q = text(f"SELECT COUNT(*) AS n, MAX({latest_col}) AS latest FROM sme.{table}")
            n, latest = conn.execute(q).one()
            rows.append({"table": table, "rows": int(n), "latest": latest})
        storage = conn.execute(text("""
            SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='sme' AND c.relkind='r'
        """)).scalar_one()
    payload = {"status": "ok", "tables": rows, "storage_gb": round(int(storage) / 1024**3, 3)}
    if json_out:
        _print_json(payload)
    else:
        table = Table(title="SME status")
        table.add_column("Table")
        table.add_column("Rows", justify="right")
        table.add_column("Latest")
        for r in rows:
            table.add_row(r["table"], f"{r['rows']:,}", str(r["latest"]))
        console.print(table)
        console.print(f"Storage: {payload['storage_gb']} GB")


@app.command("market-structure")
def market_structure(
    date: str = typer.Option("auto", "--date", help="YYYY-MM-DD or auto"),
    top_n: int = typer.Option(8, "--top-n", min=3, max=20),
    client: bool = typer.Option(False, "--client", help="Return customer-facing conclusions only; hide process/evidence."),
    llm_narrative: bool = typer.Option(False, "--llm-narrative", help="Use LLM to polish client conclusions without changing facts."),
    persist: bool = typer.Option(False, "--persist", help="Persist snapshot to sme.sme_market_structure_daily for tuning/backtests."),
    params_profile: str | None = typer.Option(None, "--params-profile", help="Named YAML parameter profile, e.g. baseline or mvp1_ytd_candidate."),
    params_path: str | None = typer.Option(None, "--params-path", help="Optional market-structure YAML path."),
    external_summary: str | None = typer.Option(
        None,
        "--external-summary",
        help="Optional LLM/web summary of current macro, policy, FX, oil, geopolitics, earnings, or industry events.",
    ),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Explain daily market structure from SME orderflow, not index moves alone."""
    from ifa.families.sme.analysis.market_structure import add_llm_narrative, build_market_structure_snapshot, persist_market_structure_snapshot

    engine = get_engine()
    d = _date_or_latest(engine, date)
    payload = build_market_structure_snapshot(
        engine,
        trade_date=d,
        top_n=top_n,
        external_summary=external_summary,
        params_profile=params_profile,
        params_path=params_path,
    )
    if llm_narrative and payload.get("client_conclusion"):
        payload["client_conclusion"] = add_llm_narrative(payload["client_conclusion"])
    if persist:
        payload["persisted_rows"] = persist_market_structure_snapshot(engine, payload)
    if json_out:
        _print_json(payload["client_conclusion"] if client and "client_conclusion" in payload else payload)
        return

    if client and payload.get("status") == "ok":
        conclusion = payload["client_conclusion"]
        console.print(f"[bold]{conclusion['title']}[/bold] {conclusion['date']}")
        console.print(conclusion["bottom_line"])
        console.print(f"市场判断：{conclusion['capital_state']}")
        console.print(f"当前重点：{'、'.join(conclusion['focus_now']) or '暂无'}")
        console.print(f"二级观察：{'、'.join(conclusion['secondary_watch']) or '暂无'}")
        console.print(f"防御/脱敏：{'、'.join(conclusion['defensive_or_desensitized']) or '暂无'}")
        console.print(f"修复弹性：{'、'.join(conclusion['repair_candidates']) or '暂无'}")
        console.print(f"回避/减仓：{'、'.join(conclusion['avoid_or_reduce']) or '暂无'}")
        console.print(f"拥挤风险：{'、'.join(conclusion['crowding_risk']) or '暂无'}")
        if conclusion.get("who_is_buying"):
            console.print(f"谁在买：{'；'.join(conclusion['who_is_buying'])}")
        if conclusion.get("who_is_selling"):
            console.print(f"谁在卖：{'；'.join(conclusion['who_is_selling'])}")
        if conclusion.get("llm_narrative", {}).get("text"):
            console.print(f"一句话解读：{conclusion['llm_narrative']['text']}")
        for name, text in conclusion["scenario"].items():
            console.print(f"{name}：{text}")
        return

    overview = payload.get("market_overview", {})
    breadth = overview.get("breadth", {})
    table = Table(title=f"SME market structure {payload.get('trade_date')}")
    table.add_column("Section")
    table.add_column("Signal")
    table.add_column("Evidence")
    table.add_row(
        "Market",
        payload.get("capital_state", {}).get("primary_state", "unknown"),
        f"up/down={breadth.get('up_count')}/{breadth.get('down_count')}, amount={breadth.get('amount_bn_yuan')}bn",
    )
    for item in payload.get("flow_inflows", [])[:5]:
        table.add_row("Inflow", item["l2_name"], f"{item['main_net_bn_yuan']}bn; {item.get('inflow_type')}; {item['reasons'][0]}")
    for item in payload.get("flow_outflows", [])[:5]:
        table.add_row("Outflow", item["l2_name"], f"{item['main_net_bn_yuan']}bn; {item.get('outflow_type')}; {item['reasons'][0]}")
    for item in payload.get("strong_return_weak_flow", [])[:3]:
        table.add_row("Crowding Risk", item["l2_name"], f"ret={item['return_1d']}%, main_ratio={item['main_net_ratio']}")
    for item in payload.get("suppressed_repair", [])[:3]:
        table.add_row("Repair Elasticity", item["l2_name"], f"ret={item['return_1d']}%, main={item['main_net_bn_yuan']}bn")
    console.print(table)


@app.command("brief")
def brief_cmd(
    date: str = typer.Option("auto", "--date", help="YYYY-MM-DD or auto"),
    params_profile: str | None = typer.Option(None, "--params-profile", help="Named YAML parameter profile."),
    external_summary: str | None = typer.Option(None, "--external-summary", help="Optional external-variable summary."),
    output_format: str = typer.Option("md", "--format", help="Output format: md or html."),
    run_mode: str = typer.Option("production", "--run-mode", help="production, manual, or test. Used for standard IFA output paths."),
    output: str | None = typer.Option(None, "--output", help="Optional output path. Defaults to IFA standard out/<run_mode>/<YYYYMMDD>/sme/."),
    stdout: bool = typer.Option(False, "--stdout", help="Print rendered brief instead of only writing the output file."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Generate a simple conclusion-only market-structure brief."""
    from ifa.families.sme.analysis.market_structure import (
        build_client_brief,
        build_market_structure_snapshot,
        render_client_brief_html,
        render_client_brief_markdown,
    )

    engine = get_engine()
    d = _date_or_latest(engine, date)
    snapshot = build_market_structure_snapshot(
        engine,
        trade_date=d,
        params_profile=params_profile,
        external_summary=external_summary,
    )
    brief = build_client_brief(snapshot)
    if json_out:
        _print_json(brief)
        return
    fmt = output_format.lower().strip()
    if fmt not in {"md", "markdown", "html"}:
        raise typer.BadParameter("--format must be md or html")
    fmt = "md" if fmt == "markdown" else fmt
    rendered = render_client_brief_html(brief) if fmt == "html" else render_client_brief_markdown(brief)
    out_path = Path(output) if output else _sme_standard_output_path(report_date=d, run_mode=run_mode, output_format=fmt)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    if stdout:
        console.print(rendered)
    console.print(f"Wrote {out_path}")


@app.command("tuning-ready")
def tuning_ready(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option("auto", "--end"),
    min_sample_days: int = typer.Option(60, "--min-sample-days"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Check whether SME has enough persisted outcomes for tuning/OOS work."""
    from sqlalchemy import text

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    with engine.connect() as conn:
        labels = conn.execute(text("""
            SELECT horizon, COUNT(DISTINCT trade_date)::int AS days, MAX(trade_date) AS latest
            FROM sme.sme_labels_daily
            WHERE trade_date BETWEEN :start AND :end
            GROUP BY horizon
            ORDER BY horizon
        """), {"start": s, "end": e}).mappings().all()
        eval_rows = conn.execute(text("""
            SELECT horizon, bucket, COUNT(DISTINCT trade_date)::int AS sample_days,
                   SUM(signal_count)::int AS signals,
                   AVG(avg_signal_score)::float AS avg_signal_score,
                   AVG(success_rate)::float AS avg_success_rate
            FROM sme.sme_strategy_eval_daily
            WHERE trade_date BETWEEN :start AND :end
            GROUP BY horizon, bucket
            ORDER BY horizon, bucket
        """), {"start": s, "end": e}).mappings().all()
        snapshot_days = conn.execute(text("""
            SELECT COUNT(DISTINCT trade_date)::int
            FROM sme.sme_market_structure_daily
            WHERE trade_date BETWEEN :start AND :end
        """), {"start": s, "end": e}).scalar_one()
    eval_payload = [dict(r) for r in eval_rows]
    ready_horizons = sorted({
        int(r["horizon"])
        for r in eval_payload
        if int(r["sample_days"] or 0) >= min_sample_days
    })
    payload = {
        "status": "ok" if ready_horizons else "degraded",
        "start": s,
        "end": e,
        "min_sample_days": min_sample_days,
        "market_structure_snapshot_days": int(snapshot_days or 0),
        "label_coverage": [dict(r) for r in labels],
        "ready_horizons": ready_horizons,
        "eval_summary": eval_payload,
        "notes": [
            "调参应优先看 OOS/OOC avg_signal_score 和 success_rate，不看内部规则数量。",
            "若 ready_horizons 为空，先补 market_structure 快照或等待 labels 成熟。",
        ],
    }
    if json_out:
        _print_json(payload)
        return
    table = Table(title=f"SME tuning readiness {s} → {e}")
    table.add_column("H", justify="right")
    table.add_column("Bucket")
    table.add_column("Days", justify="right")
    table.add_column("Signals", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Success", justify="right")
    for r in eval_payload:
        table.add_row(
            str(r["horizon"]),
            r["bucket"],
            str(r["sample_days"]),
            str(r["signals"]),
            f"{(r['avg_signal_score'] or 0):.3f}",
            f"{(r['avg_success_rate'] or 0):.2%}",
        )
    console.print(table)
    console.print(f"Status: {payload['status']}; ready horizons: {ready_horizons}; snapshots: {payload['market_structure_snapshot_days']}")


@etl_app.command("audit")
def etl_audit(
    start: str | None = typer.Option(None, "--start"),
    end: str | None = typer.Option(None, "--end"),
    date: str | None = typer.Option(None, "--date"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Audit source coverage and SME storage."""
    from ifa.families.sme.db.audit import audit_sources, audit_storage

    engine = get_engine()
    d = parse_date(date)
    s = d or _date_or_latest(engine, start)
    e = d or _date_or_latest(engine, end)
    counts = audit_sources(engine, start=s, end=e)
    storage = audit_storage(engine, audit_date=e)
    payload = {"status": "success", "start": s, "end": e, "row_counts": counts, "storage": storage}
    _print_json(payload) if json_out else console.print(payload)


@etl_app.command("validate-backfill")
def etl_validate_backfill(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option("auto", "--end", help="YYYY-MM-DD or auto"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Validate historical SME backfill coverage, quality, alignment, and storage."""
    from sqlalchemy import text

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    tables = [
        ("sme_sw_member_daily", "quality_flag"),
        ("sme_stock_orderflow_daily", "quality_flag"),
        ("sme_sector_orderflow_daily", "quality_flag"),
        ("sme_sector_diffusion_daily", "quality_flag"),
        ("sme_sector_state_daily", "quality_flag"),
        ("sme_labels_daily", "label_quality_flag"),
        ("sme_market_structure_daily", "quality_flag"),
        ("sme_strategy_eval_daily", "quality_flag"),
    ]
    table_payload: list[dict] = []
    with engine.connect() as conn:
        expected_days = conn.execute(text("""
            SELECT COUNT(DISTINCT trade_date)
            FROM smartmoney.raw_moneyflow
            WHERE trade_date BETWEEN :start AND :end
        """), {"start": s, "end": e}).scalar_one()
        for table_name, quality_col in tables:
            row = conn.execute(text(f"""
                SELECT COUNT(*) AS rows,
                       COUNT(DISTINCT trade_date) AS dates,
                       MIN(trade_date) AS min_date,
                       MAX(trade_date) AS max_date
                FROM sme.{table_name}
                WHERE trade_date BETWEEN :start AND :end
            """), {"start": s, "end": e}).one()
            row_map = row._mapping
            quality_rows = conn.execute(text(f"""
                SELECT {quality_col}, COUNT(*)
                FROM sme.{table_name}
                WHERE trade_date BETWEEN :start AND :end
                GROUP BY {quality_col}
                ORDER BY {quality_col}
            """), {"start": s, "end": e}).fetchall()
            storage = conn.execute(text("""
                SELECT pg_total_relation_size(c.oid), pg_relation_size(c.oid), pg_indexes_size(c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname='sme' AND c.relname=:table_name
            """), {"table_name": table_name}).one()
            table_payload.append({
                "table": table_name,
                "rows": int(row_map["rows"]),
                "distinct_trade_dates": int(row_map["dates"]),
                "expected_source_trade_dates": int(expected_days),
                "min_date": row_map["min_date"],
                "max_date": row_map["max_date"],
                "quality": {str(flag): int(count) for flag, count in quality_rows},
                "total_bytes": int(storage[0]),
                "table_bytes": int(storage[1]),
                "index_bytes": int(storage[2]),
            })

        null_label_rows = conn.execute(text("""
            SELECT COUNT(*)
            FROM sme.sme_labels_daily
            WHERE trade_date BETWEEN :start AND :end
              AND (
                  future_return IS NULL
                  OR future_excess_return_vs_market IS NULL
                  OR future_excess_return_vs_l1 IS NULL
              )
        """), {"start": s, "end": e}).scalar_one()

        mismatches = conn.execute(text("""
            WITH dates AS (
                SELECT DISTINCT trade_date
                FROM sme.sme_sector_orderflow_daily
                WHERE trade_date BETWEEN :start AND :end
            ),
            counts AS (
                SELECT d.trade_date,
                       (SELECT COUNT(*) FROM sme.sme_sector_orderflow_daily x WHERE x.trade_date=d.trade_date) AS sector_rows,
                       (SELECT COUNT(*) FROM sme.sme_sector_diffusion_daily x WHERE x.trade_date=d.trade_date) AS diffusion_rows,
                       (SELECT COUNT(*) FROM sme.sme_sector_state_daily x WHERE x.trade_date=d.trade_date) AS state_rows
                FROM dates d
            )
            SELECT trade_date, sector_rows, diffusion_rows, state_rows
            FROM counts
            WHERE sector_rows <> diffusion_rows OR sector_rows <> state_rows
            ORDER BY trade_date
            LIMIT 20
        """), {"start": s, "end": e}).fetchall()
        total_storage = conn.execute(text("""
            SELECT COALESCE(SUM(pg_total_relation_size(c.oid)), 0)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname='sme' AND c.relkind='r'
        """)).scalar_one()

    blocked = bool(mismatches) or int(null_label_rows) > 0 or any(t["rows"] == 0 for t in table_payload[:5])
    degraded = any(t["quality"].get("degraded", 0) > 0 for t in table_payload)
    payload = {
        "status": "blocked" if blocked else ("degraded" if degraded else "ok"),
        "start": s,
        "end": e,
        "expected_source_trade_dates": int(expected_days),
        "tables": table_payload,
        "alignment_mismatches": [
            {"trade_date": d, "sector_rows": int(a), "diffusion_rows": int(b), "state_rows": int(c)}
            for d, a, b, c in mismatches
        ],
        "null_label_rows": int(null_label_rows),
        "sme_total_storage_bytes": int(total_storage),
        "sme_total_storage_gb": round(int(total_storage) / 1024**3, 3),
    }
    if json_out:
        _print_json(payload)
    else:
        table = Table(title=f"SME backfill validation {s} → {e}")
        table.add_column("Table")
        table.add_column("Rows", justify="right")
        table.add_column("Dates", justify="right")
        table.add_column("Min")
        table.add_column("Max")
        table.add_column("Quality")
        table.add_column("GB", justify="right")
        for item in table_payload:
            table.add_row(
                item["table"],
                f"{item['rows']:,}",
                f"{item['distinct_trade_dates']:,}/{item['expected_source_trade_dates']:,}",
                str(item["min_date"]),
                str(item["max_date"]),
                json.dumps(item["quality"], ensure_ascii=False),
                f"{item['total_bytes'] / 1024**3:.3f}",
            )
        console.print(table)
        console.print(f"Status: {payload['status']}; total SME storage: {payload['sme_total_storage_gb']} GB; alignment mismatches: {len(mismatches)}; null labels: {int(null_label_rows)}")
    if blocked:
        raise typer.Exit(2)


@etl_app.command("backfill")
def etl_backfill(
    start: str = typer.Option(..., "--start", help="YYYY-MM-DD"),
    end: str = typer.Option("auto", "--end", help="YYYY-MM-DD or auto"),
    run_mode: str = typer.Option("manual", "--run-mode"),
    source_mode: str = typer.Option("prefer_smartmoney", "--source-mode"),
    labels: bool = typer.Option(True, "--labels/--no-labels"),
    workers: int = typer.Option(1, "--workers", help="Reserved for future chunk parallelism; MVP-1 runs sequentially."),
    resume: bool = typer.Option(True, "--resume/--no-resume", help="MVP-1 upserts are idempotent; kept for CLI contract."),
    max_storage_gb: float = typer.Option(10.0, "--max-storage-gb"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Backfill SME MVP-1 derived tables from local smartmoney sources."""
    from ifa.families.sme.etl.runner import backfill

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    payload = backfill(engine, start=s, end=e, run_mode=run_mode, source_mode=source_mode, include_labels=labels)
    _print_json(payload) if json_out else console.print(payload)


@etl_app.command("incremental")
def etl_incremental(
    as_of: str = typer.Option("auto", "--as-of", help="YYYY-MM-DD or auto"),
    run_mode: str = typer.Option("production", "--run-mode"),
    source_mode: str = typer.Option("prefer_smartmoney", "--source-mode"),
    labels: bool = typer.Option(True, "--labels/--no-labels"),
    compute: bool = typer.Option(True, "--compute/--no-compute", help="Reserved; MVP-1 incremental always computes when not dry-run."),
    fail_on_core_missing: bool = typer.Option(True, "--fail-on-core-missing/--allow-core-missing"),
    predict: bool = typer.Option(False, "--predict", help="Reserved for MVP-2."),
    report: bool = typer.Option(False, "--report", help="Reserved for MVP-2."),
    export: bool = typer.Option(False, "--export", help="Reserved for MVP-2."),
    dry_run: bool = typer.Option(False, "--dry-run"),
    force: bool = typer.Option(False, "--force", help="Recompute even when core SME tables already have rows for the target date."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Run one daily SME incremental job."""
    from ifa.families.sme.etl.runner import incremental

    engine = get_engine()
    target = _date_or_latest(engine, as_of)
    if dry_run:
        payload = {"status": "dry_run", "as_of_trade_date": target, "source_mode": source_mode, "run_mode": run_mode}
    else:
        payload = incremental(engine, as_of=target, run_mode=run_mode, source_mode=source_mode, include_labels=labels, force=force)
    _print_json(payload) if json_out else console.print(payload)


@compute_app.command("membership")
def compute_membership_cmd(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option("auto", "--end"),
    source_mode: str = typer.Option("prefer_smartmoney", "--source-mode"),
) -> None:
    from ifa.families.sme.features.membership import compute_membership

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    console.print({"rows": compute_membership(engine, start=s, end=e, source_mode=source_mode)})


def _date_option(value: str) -> dt.date:
    parsed = parse_date(value)
    if parsed is None:
        raise typer.BadParameter("date must be YYYY-MM-DD")
    return parsed


@compute_app.command("stock-flow")
def compute_stock_flow_cmd(date: str = typer.Option(..., "--date"), source_mode: str = typer.Option("prefer_smartmoney", "--source-mode")) -> None:
    from ifa.families.sme.features.stock_orderflow import compute_stock_orderflow
    console.print({"rows": compute_stock_orderflow(get_engine(), trade_date=_date_option(date), source_mode=source_mode)})


@compute_app.command("sector-flow")
def compute_sector_flow_cmd(date: str = typer.Option(..., "--date"), source_mode: str = typer.Option("prefer_smartmoney", "--source-mode")) -> None:
    from ifa.families.sme.features.sector_orderflow import compute_sector_orderflow
    console.print({"rows": compute_sector_orderflow(get_engine(), trade_date=_date_option(date), source_mode=source_mode)})


@compute_app.command("diffusion")
def compute_diffusion_cmd(date: str = typer.Option(..., "--date")) -> None:
    from ifa.families.sme.features.diffusion import compute_diffusion
    console.print({"rows": compute_diffusion(get_engine(), trade_date=_date_option(date))})


@compute_app.command("state")
def compute_state_cmd(date: str = typer.Option(..., "--date")) -> None:
    from ifa.families.sme.features.state_machine import compute_state
    console.print({"rows": compute_state(get_engine(), trade_date=_date_option(date))})


@compute_app.command("market-structure")
def compute_market_structure_cmd(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option("auto", "--end"),
    params_profile: str | None = typer.Option(None, "--params-profile", help="Named YAML parameter profile."),
    params_path: str | None = typer.Option(None, "--params-path", help="Optional market-structure YAML path."),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Persist market-structure snapshots for a date range."""
    from ifa.families.sme.analysis.market_structure import build_market_structure_snapshot, persist_market_structure_snapshot
    from ifa.families.sme.data.calendar import trading_dates

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    rows = 0
    dates = trading_dates(engine, s, e)
    for d in dates:
        rows += persist_market_structure_snapshot(
            engine,
            build_market_structure_snapshot(engine, trade_date=d, params_profile=params_profile, params_path=params_path),
        )
    payload = {"status": "success", "start": s, "end": e, "dates": len(dates), "rows": rows, "params_profile": params_profile}
    _print_json(payload) if json_out else console.print(payload)


@compute_app.command("strategy-eval")
def compute_strategy_eval_cmd(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option("auto", "--end"),
    horizons: str = typer.Option("1,3,5,10,20", "--horizons"),
    summarize: bool = typer.Option(True, "--summarize/--no-summarize"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Join persisted strategy snapshots to forward labels for tuning/OOS."""
    from ifa.families.sme.analysis.strategy_eval import compute_strategy_eval, summarize_strategy_eval

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    hs = tuple(int(x.strip()) for x in horizons.split(",") if x.strip())
    rows = compute_strategy_eval(engine, start=s, end=e, horizons=hs)
    payload = {"status": "success", "start": s, "end": e, "horizons": hs, "rows": rows}
    if summarize:
        payload["summary"] = summarize_strategy_eval(engine, start=s, end=e)
    if json_out:
        _print_json(payload)
        return
    table = Table(title=f"SME strategy eval {s} → {e}")
    table.add_column("Bucket")
    table.add_column("H", justify="right")
    table.add_column("Days", justify="right")
    table.add_column("Signals", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Success", justify="right")
    for r in payload.get("summary", [])[:36]:
        table.add_row(
            r["bucket"],
            str(r["horizon"]),
            str(r["sample_days"]),
            str(r["signal_count"]),
            f"{(r['avg_signal_score'] or 0):.3f}",
            f"{(r['avg_success_rate'] or 0):.2%}",
        )
    console.print(table)


@tune_app.command("bucket-review")
def tune_bucket_review_cmd(
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option("auto", "--end"),
    min_sample_days: int = typer.Option(60, "--min-sample-days"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Create an outcome-first tuning artifact for bucket weights/thresholds."""
    from ifa.families.sme.tuning.bucket_review import build_bucket_review

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    payload = build_bucket_review(engine, start=s, end=e, min_sample_days=min_sample_days)
    if json_out:
        _print_json(payload)
        return
    table = Table(title=f"SME bucket review {s} → {e}")
    table.add_column("Bucket")
    table.add_column("Action")
    table.add_column("Score", justify="right")
    table.add_column("Success", justify="right")
    actions = {r["bucket"]: r for r in payload["recommendations"]}
    for row in payload["bucket_scores"]:
        table.add_row(
            row["bucket"],
            actions.get(row["bucket"], {}).get("action", ""),
            f"{row['avg_signal_score']:.3f}",
            f"{row['avg_success_rate']:.2%}",
        )
    console.print(table)
    console.print(f"Next tuning decision: {payload['next_tuning_decision']}")


@tune_app.command("promote-profile")
def tune_promote_profile_cmd(
    candidate_profile: str = typer.Option(..., "--candidate-profile"),
    start: str = typer.Option(..., "--start"),
    end: str = typer.Option("auto", "--end"),
    min_sample_days: int = typer.Option(60, "--min-sample-days"),
    apply: bool = typer.Option(False, "--apply", help="Write active_profile to YAML if promotion gates pass."),
    params_path: str | None = typer.Option(None, "--params-path"),
    json_out: bool = typer.Option(False, "--json"),
) -> None:
    """Automatically gate a tuned profile and optionally promote it into YAML."""
    from ifa.families.sme.tuning.promotion import apply_active_profile, build_promotion_decision

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    decision = build_promotion_decision(
        engine,
        candidate_profile=candidate_profile,
        start=s,
        end=e,
        min_sample_days=min_sample_days,
    )
    if apply and decision["status"] == "promote":
        decision["applied"] = apply_active_profile(candidate_profile=candidate_profile, path=params_path)
    elif apply:
        decision["applied"] = None
        decision["apply_reason"] = "promotion gates did not pass"
    if json_out:
        _print_json(decision)
    else:
        console.print(decision)


@app.command("labels")
def labels_cmd(start: str = typer.Option(..., "--start"), end: str = typer.Option("auto", "--end"), json_out: bool = typer.Option(False, "--json")) -> None:
    from ifa.families.sme.labels.forward import compute_labels

    engine = get_engine()
    s = parse_date(start)
    if s is None:
        raise typer.BadParameter("--start must be YYYY-MM-DD")
    e = _date_or_latest(engine, end)
    payload = {"status": "success", "rows": compute_labels(engine, start=s, end=e), "start": s, "end": e}
    _print_json(payload) if json_out else console.print(payload)
