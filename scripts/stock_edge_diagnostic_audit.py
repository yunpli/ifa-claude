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
    "diagnose_cli": "ifa/cli/stock.py",
    "diagnostic_tests": "tests/stock/test_diagnostic.py",
    "theme_heat_model": "ifa/families/stock/theme_heat.py",
    "theme_heat_migration": "alembic/versions/o3p4q5r6s7t8_stock_theme_heat_weekly.py",
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
    for fn in (
        "build_diagnostic_report",
        "synthesize_diagnostic",
        "render_markdown",
        "_stock_edge_perspective",
        "_ta_perspective",
        "_ningbo_perspective",
        "_research_perspective",
        "_risk_perspective",
    ):
        checks.append({"check": f"function:{fn}", "ok": fn in service_functions, "path": "ifa/families/stock/diagnostic/service.py"})

    cli_text = _read("ifa/cli/stock.py")
    checks.append({"check": "cli:diagnose-command", "ok": '@app.command("diagnose")' in cli_text, "path": "ifa/cli/stock.py"})
    checks.append({"check": "cli:full-stock-edge-flag", "ok": "--full-stock-edge" in cli_text, "path": "ifa/cli/stock.py"})

    combined_doc = _read("docs/stock_edge_diagnostic_product_definition.md") + "\n" + _read("docs/stock_edge_diagnostic_implementation_audit.md")
    for token in FORBIDDEN_MUTATION_TOKENS:
        checks.append({"check": f"no-production-mutation:{token}", "ok": token not in combined_doc, "path": "docs/stock_edge_diagnostic_*.md"})

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
