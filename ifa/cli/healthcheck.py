"""`ifa healthcheck` — pings primary LLM, fallback LLM, TuShare, and the database.

Returns non-zero exit code if any required check fails.
"""
from __future__ import annotations

import typer
from rich.console import Console
from rich.table import Table

from ifa.config import get_settings
from ifa.core.db import ping_database
from ifa.core.llm import LLMClient
from ifa.core.tushare import TuShareClient

console = Console()

_LLM_PROBE_QUESTION = "请你对结构心相关手术作简介"


def _check_llm_endpoint(client: LLMClient, endpoint: str) -> tuple[bool, str]:
    try:
        resp = client.chat(
            messages=[{"role": "user", "content": _LLM_PROBE_QUESTION}],
            max_tokens=120,
            force_endpoint=endpoint,
        )
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    snippet = resp.content.strip().replace("\n", " ")[:60]
    return True, f"model={resp.model} latency={resp.latency_seconds:.2f}s · {snippet}…"


def _check_tushare(client: TuShareClient) -> tuple[bool, str]:
    try:
        df = client.stock_basic_sample(n=3)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    if df.empty:
        return False, "stock_basic returned 0 rows (token may lack permission)"
    sample = ", ".join(f"{r.ts_code} {r.name}" for r in df.itertuples())
    return True, f"stock_basic ok · {sample}"


def healthcheck_command(
    skip_db: bool = typer.Option(False, "--skip-db", help="Skip the PostgreSQL ping (e.g. before cluster is up)."),
) -> None:
    settings = get_settings()
    table = Table(title="iFA healthcheck", show_lines=True)
    table.add_column("Component", style="bold")
    table.add_column("Status")
    table.add_column("Detail", overflow="fold")

    overall_ok = True
    llm = LLMClient(settings)

    # Primary LLM
    ok, msg = _check_llm_endpoint(llm, "primary")
    overall_ok &= ok
    table.add_row(f"LLM primary ({settings.llm_primary_model})",
                  "[green]OK[/green]" if ok else "[red]FAIL[/red]", msg)

    # Fallback LLM
    ok, msg = _check_llm_endpoint(llm, "fallback")
    overall_ok &= ok
    table.add_row(f"LLM fallback ({settings.llm_fallback_model})",
                  "[green]OK[/green]" if ok else "[red]FAIL[/red]", msg)

    # TuShare
    ok, msg = _check_tushare(TuShareClient(settings))
    overall_ok &= ok
    table.add_row("TuShare Pro", "[green]OK[/green]" if ok else "[red]FAIL[/red]", msg)

    # Database
    if skip_db:
        table.add_row(f"PostgreSQL ({settings.active_database})", "[yellow]SKIP[/yellow]", "skipped via --skip-db")
    else:
        ok, msg = ping_database(settings)
        overall_ok &= ok
        table.add_row(
            f"PostgreSQL ({settings.active_database} @ {settings.pg_host}:{settings.pg_port})",
            "[green]OK[/green]" if ok else "[red]FAIL[/red]",
            msg,
        )

    console.print(table)
    console.print(f"\nrun_mode = [bold]{settings.run_mode.value}[/bold]")
    if not overall_ok:
        raise typer.Exit(code=1)
