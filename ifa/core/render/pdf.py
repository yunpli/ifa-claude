"""HTML-to-PDF conversion for iFA reports using Chrome headless."""
from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

# ── Print CSS injected into every report ─────────────────────────────────────

_PRINT_CSS = """
/* === iFA PDF Print Override === */
@page {
  size: A4;
  margin: 12mm 14mm 14mm 14mm;
}
@media print {
  * { -webkit-print-color-adjust: exact !important; print-color-adjust: exact !important; }

  html, body {
    background: white !important;
    font-size: 13px !important;
  }

  .ifa-page {
    max-width: 100% !important;
    box-shadow: none !important;
    padding: 0 !important;
    margin: 0 auto !important;
    border: none !important;
  }

  /* Hide screen-only elements */
  .run-mode-badge, .no-print { display: none !important; }

  /* Section spacing */
  .ifa-section {
    margin: 16px 0 !important;
    page-break-inside: avoid;
    break-inside: avoid;
  }

  /* Let long tables flow across pages */
  table {
    page-break-inside: auto;
    break-inside: auto;
    font-size: 11px !important;
    width: 100% !important;
  }
  tr { page-break-inside: avoid; break-inside: avoid; }

  /* Cards and metrics stay together */
  .metric-card, .kv-grid, .metric-row {
    page-break-inside: avoid;
    break-inside: avoid;
  }

  /* Section headers: avoid orphan header at bottom of page */
  .ifa-section-head {
    page-break-after: avoid;
    break-after: avoid;
  }

  /* Compact headings for print */
  h1 { font-size: 18px !important; }
  h2 { font-size: 15px !important; }
  h3 { font-size: 13px !important; }

  /* Force all <details> open — Top-5 drill-downs must show in PDF */
  details { display: block !important; }
  details > * { display: block !important; }
  summary { list-style: none !important; cursor: default !important; }
  summary::marker, summary::-webkit-details-marker { display: none !important; }

  /* Links: keep clean for financial report */
  a[href]:after { content: "" !important; }

  /* Preserve background colors for colored cells */
  .up, .up-bg, [class*="up-"] { background-color: #ecfdf5 !important; }
  .down, .down-bg, [class*="down-"] { background-color: #fef2f2 !important; }
}
"""

# ── Chrome detection ──────────────────────────────────────────────────────────

_CHROME_CANDIDATES = [
    # macOS
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    # Linux (via PATH)
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    # Windows
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome() -> str:
    """Return the path to a Chrome/Chromium binary.

    Raises RuntimeError if none is found.
    """
    for candidate in _CHROME_CANDIDATES:
        # Absolute path — check existence directly
        p = Path(candidate)
        if p.is_absolute():
            if p.exists():
                return str(p)
        else:
            # Relative name — search PATH
            found = shutil.which(candidate)
            if found:
                return found
    raise RuntimeError(
        "Chrome not found. Install Google Chrome or Chromium.\n"
        "Expected locations checked:\n"
        + "\n".join(f"  {c}" for c in _CHROME_CANDIDATES)
    )


# ── Core conversion function ──────────────────────────────────────────────────

def html_to_pdf(html_path: Path, pdf_path: Path | None = None) -> Path:
    """Convert an iFA HTML report to PDF using Chrome headless.

    - Injects print-optimized CSS overlay into a temp copy of the HTML
    - Uses Chrome headless --print-to-pdf
    - pdf_path defaults to same location as html_path with .pdf extension
    - Returns the path to the generated PDF
    - Raises RuntimeError if Chrome not found or conversion fails
    """
    html_path = Path(html_path).resolve()
    if not html_path.exists():
        raise FileNotFoundError(f"HTML file not found: {html_path}")

    if pdf_path is None:
        pdf_path = html_path.with_suffix(".pdf")
    else:
        pdf_path = Path(pdf_path).resolve()

    chrome = find_chrome()

    # Read the original HTML
    source = html_path.read_text(encoding="utf-8")

    # Force-open all <details> elements so Top-5 drills show in PDF
    # <details> and <details open> → <details open>
    patched = re.sub(r"<details(\s[^>]*)?>", lambda m: f"<details open{m.group(1) or ''}>", source)

    # Inject print CSS before </head>
    injected_style = f"<style>{_PRINT_CSS}</style>"
    if "</head>" in patched:
        patched = patched.replace("</head>", f"{injected_style}\n</head>", 1)
    else:
        # Fallback: prepend to document
        patched = injected_style + "\n" + patched

    # Write patched HTML to a temp file
    with tempfile.NamedTemporaryFile(
        suffix=".html",
        mode="w",
        encoding="utf-8",
        delete=False,
    ) as tmp:
        tmp.write(patched)
        temp_path = tmp.name

    try:
        cmd = [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--no-sandbox",
            "--run-all-compositor-stages-before-draw",
            "--no-pdf-header-footer",
            f"--print-to-pdf={pdf_path}",
            f"file://{temp_path}",
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )

        if result.returncode != 0:
            raise RuntimeError(
                f"Chrome exited with code {result.returncode}.\n"
                f"stderr: {result.stderr[:2000]}"
            )

        if not pdf_path.exists():
            raise RuntimeError(
                f"Chrome completed but PDF not found at {pdf_path}.\n"
                f"stderr: {result.stderr[:2000]}"
            )

        size = pdf_path.stat().st_size
        if size < 1000:
            raise RuntimeError(
                f"PDF suspiciously small ({size} bytes): {pdf_path}"
            )

    finally:
        # Always clean up the temp file
        Path(temp_path).unlink(missing_ok=True)

    return pdf_path
