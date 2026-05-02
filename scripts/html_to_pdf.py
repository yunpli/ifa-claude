#!/usr/bin/env python3
"""Standalone CLI to convert iFA HTML reports to PDF.

Usage:
    uv run python scripts/html_to_pdf.py FILE [FILE ...]
    uv run python scripts/html_to_pdf.py --output /path/to/dir FILE [FILE ...]
    uv run python scripts/html_to_pdf.py --all-today
    uv run python scripts/html_to_pdf.py --all-today --out-root ~/claude/ifaenv/out
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

# Allow running directly without installing the package
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from ifa.core.render.pdf import html_to_pdf


def _fmt_size(n_bytes: int) -> str:
    if n_bytes >= 1_000_000:
        return f"{n_bytes / 1_000_000:.1f}MB"
    if n_bytes >= 1_000:
        return f"{n_bytes / 1_000:.0f}KB"
    return f"{n_bytes}B"


def _find_today_html(out_root: Path) -> list[Path]:
    """Scan out_root recursively for HTML files named with today's date."""
    today = dt.date.today().strftime("%Y%m%d")
    files = sorted(out_root.rglob(f"CN_*_{today}_*.html"))
    return files


def _resolve_pdf_path(html_path: Path, output: str | None) -> Path | None:
    """Return the target PDF path given --output (dir or file), or None for default."""
    if output is None:
        return None  # default: same location as HTML

    out = Path(output).expanduser().resolve()
    if out.suffix.lower() == ".pdf":
        return out
    # Treat as directory
    out.mkdir(parents=True, exist_ok=True)
    return out / html_path.with_suffix(".pdf").name


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Convert iFA HTML reports to PDF via Chrome headless.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "html_files",
        nargs="*",
        metavar="HTML_FILE",
        help="One or more HTML report files to convert.",
    )
    parser.add_argument(
        "-o", "--output",
        metavar="DIR_OR_FILE",
        help="Output directory (or specific .pdf path for a single file). "
             "Default: same directory as the HTML file.",
    )
    parser.add_argument(
        "--all-today",
        action="store_true",
        help="Convert ALL HTML reports generated today (scans --out-root).",
    )
    parser.add_argument(
        "--out-root",
        default="~/claude/ifaenv/out",
        metavar="DIR",
        help="Root directory to scan when using --all-today. "
             "Default: ~/claude/ifaenv/out",
    )
    args = parser.parse_args()

    # Build list of HTML files to process
    files: list[Path] = []

    if args.all_today:
        root = Path(args.out_root).expanduser().resolve()
        if not root.exists():
            print(f"Error: --out-root directory not found: {root}", file=sys.stderr)
            return 1
        files = _find_today_html(root)
        if not files:
            today_str = dt.date.today().strftime("%Y%m%d")
            print(f"No HTML reports found for today ({today_str}) under {root}")
            return 0

    for f in args.html_files:
        p = Path(f).expanduser().resolve()
        if not p.exists():
            print(f"Warning: file not found, skipping: {p}", file=sys.stderr)
            continue
        files.append(p)

    if not files:
        parser.print_help()
        return 1

    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique_files: list[Path] = []
    for f in files:
        if f not in seen:
            seen.add(f)
            unique_files.append(f)
    files = unique_files

    n = len(files)
    print(f"Converting {n} HTML report{'s' if n != 1 else ''} to PDF...")

    errors: list[tuple[str, str]] = []
    converted = 0

    for html_path in files:
        pdf_target = _resolve_pdf_path(html_path, args.output)
        try:
            result_path = html_to_pdf(html_path, pdf_target)
            size_str = _fmt_size(result_path.stat().st_size)
            print(f"  ✓ {result_path.name}  ({size_str})")
            converted += 1
        except subprocess.TimeoutExpired:  # type: ignore[name-defined]
            msg = "Chrome timeout"
            print(f"  ✗ {html_path.name} — {msg}")
            errors.append((html_path.name, msg))
        except Exception as exc:
            msg = str(exc).split("\n")[0]
            print(f"  ✗ {html_path.name} — {msg}")
            errors.append((html_path.name, msg))

    # Summary
    failed = len(errors)
    if failed == 0:
        print(f"\nDone: {converted} converted")
    else:
        print(f"\nDone: {converted} converted, {failed} failed")
        for name, reason in errors:
            print(f"  [FAILED] {name}: {reason}", file=sys.stderr)

    return 0 if failed == 0 else 1


# subprocess is needed for the TimeoutExpired catch; import it here so the
# name is available in main() without a local import in the except clause.
import subprocess  # noqa: E402 (late import intentional for clarity)

if __name__ == "__main__":
    sys.exit(main())
