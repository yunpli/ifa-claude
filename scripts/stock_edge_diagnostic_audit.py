"""Static audit for the Stock Edge diagnostic MVP.

This script checks that the repo still exposes the intended diagnostic product
surface without touching the database, production YAML, or delivery crons.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_PERSPECTIVES = {
    "stock_edge_sector_cycle": ("Stock Edge / sector-cycle", "ifa/families/stock/diagnostic/service.py"),
    "ta": ("TA", "ifa/families/stock/diagnostic/service.py"),
    "ningbo": ("Ningbo", "ifa/families/stock/diagnostic/service.py"),
    "research_news": ("Research / news / theme", "ifa/families/stock/diagnostic/service.py"),
    "risk": ("Risk", "ifa/families/stock/diagnostic/service.py"),
}

EXPECTED_SURFACES = {
    "diagnostic_models": "ifa/families/stock/diagnostic/models.py",
    "diagnostic_service": "ifa/families/stock/diagnostic/service.py",
    "diagnostic_delivery": "ifa/families/stock/diagnostic/delivery.py",
    "diagnostic_persistence": "ifa/families/stock/diagnostic/persistence.py",
    "diagnostic_delivery_cli": "scripts/stock_edge_diagnostic_delivery.py",
    "diagnose_cli": "ifa/cli/stock.py",
    "diagnostic_tests": "tests/stock/test_diagnostic.py",
    "theme_heat_model": "ifa/families/stock/theme_heat.py",
    "theme_heat_migration": "alembic/versions/o3p4q5r6s7t8_stock_theme_heat_weekly.py",
    "diagnostic_runs_migration": "alembic/versions/p0q1r2s3t4u5_stock_diagnostic_runs.py",
    "diagnostic_sector_leader_migration": "alembic/versions/p4q5r6s7t8u9_stock_diagnostic_persistence.py",
    "product_definition": "docs/stock_edge_diagnostic_product_definition.md",
    "implementation_audit": "docs/stock_edge_diagnostic_implementation_audit.md",
}

FORBIDDEN_MUTATION_TOKENS = (
    "--auto-promote",
    "--apply-to-baseline",
    "SME_TUNE_APPLY_PROMOTION=1",
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _defined_functions(rel: str) -> set[str]:
    tree = ast.parse(_read(rel), filename=rel)
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def main() -> int:
    checks: list[dict[str, object]] = []

    for name, rel in EXPECTED_SURFACES.items():
        path = ROOT / rel
        checks.append({"check": f"surface:{name}", "ok": path.exists(), "path": rel})

    service_text = _read("ifa/families/stock/diagnostic/service.py")
    for key, (label, rel) in REQUIRED_PERSPECTIVES.items():
        checks.append({
            "check": f"perspective:{key}",
            "ok": key in service_text,
            "label": label,
            "path": rel,
        })

    service_functions = _defined_functions("ifa/families/stock/diagnostic/service.py")
    for fn in ("build_diagnostic_report", "synthesize_diagnostic", "render_markdown", "diagnostic_manifest_payload"):
        checks.append({"check": f"function:{fn}", "ok": fn in service_functions, "path": "ifa/families/stock/diagnostic/service.py"})
    adapter_functions = {
        key: _defined_functions(rel)
        for key, rel in (
            ("stock_edge_sector_cycle", "ifa/families/stock/diagnostic/adapters/stock_edge_sector_cycle.py"),
            ("ta", "ifa/families/stock/diagnostic/adapters/ta.py"),
            ("ningbo", "ifa/families/stock/diagnostic/adapters/ningbo.py"),
            ("research_news", "ifa/families/stock/diagnostic/adapters/research_news.py"),
            ("risk", "ifa/families/stock/diagnostic/adapters/risk.py"),
        )
    }
    for key, funcs in adapter_functions.items():
        checks.append({"check": f"adapter:{key}:collect", "ok": "collect" in funcs, "path": f"ifa/families/stock/diagnostic/adapters/{key}.py"})
    persistence_functions = _defined_functions("ifa/families/stock/diagnostic/persistence.py")
    checks.append({
        "check": "function:persist_diagnostic_run",
        "ok": "persist_diagnostic_run" in persistence_functions,
        "path": "ifa/families/stock/diagnostic/persistence.py",
    })
    delivery_functions = _defined_functions("ifa/families/stock/diagnostic/delivery.py")
    for fn in ("build_telegram_delivery_payload", "write_delivery_payload"):
        checks.append({"check": f"function:{fn}", "ok": fn in delivery_functions, "path": "ifa/families/stock/diagnostic/delivery.py"})

    cli_text = _read("ifa/cli/stock.py")
    checks.append({"check": "cli:diagnose-command", "ok": '@app.command("diagnose")' in cli_text, "path": "ifa/cli/stock.py"})
    checks.append({"check": "cli:full-stock-edge-flag", "ok": "--full-stock-edge" in cli_text, "path": "ifa/cli/stock.py"})
    checks.append({"check": "cli:persist-db-switch", "ok": "--persist-db/--no-persist-db" in cli_text, "path": "ifa/cli/stock.py"})

    diagnostic_impl = "\n".join(
        _read(rel)
        for rel in (
            "ifa/families/stock/diagnostic/models.py",
            "ifa/families/stock/diagnostic/service.py",
            "ifa/families/stock/diagnostic/persistence.py",
            "ifa/families/stock/diagnostic/delivery.py",
        )
    )
    for token in FORBIDDEN_MUTATION_TOKENS:
        checks.append({"check": f"no-production-mutation:{token}", "ok": token not in diagnostic_impl, "path": "ifa/families/stock/diagnostic"})

    payload = {
        "status": "ok" if all(c["ok"] for c in checks) else "failed",
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for c in checks if c["ok"]),
            "failed": sum(1 for c in checks if not c["ok"]),
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
