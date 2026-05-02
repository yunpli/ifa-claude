# PDF Export Tool

V2.1 ships a built-in HTML → PDF converter so reports can be archived, printed, and read on phones without losing layout. The same code path is reachable two ways: an inline `--generate-pdf` flag on every generate command, and a standalone `scripts/html_to_pdf.py` for backfilling.

---

## Why PDF

- **Mobile-friendly.** Long Jinja2-rendered HTML reports rely on wide tables; PDF reflow is more reliable across iOS / Android mail clients than HTML.
- **Print-friendly.** Customers print evening reports for next-morning meetings. PDF is the only format that gives consistent page breaks.
- **Archivable.** A PDF is a frozen artifact. The HTML can be regenerated, but the PDF is the immutable record of "what we sent on this date".
- **Email distribution.** Some compliance pipelines require PDF over HTML.

---

## How it works

Implementation lives in `ifa/core/render/pdf.py`. The flow:

1. **Locate Chrome.** `find_chrome()` walks a list of candidate paths covering macOS (`/Applications/Google Chrome.app/...`), Linux (`google-chrome`, `google-chrome-stable`, `chromium`), and Windows (`Program Files\Google\Chrome\...`).
2. **Inject print CSS.** A print-targeted CSS block is injected into a `<style>` tag at the bottom of the HTML. It sets A4 page size, 12/14mm margins, hides `.run-mode-badge` and `.no-print` elements, sets `page-break-inside: avoid` on `.ifa-section` / `.metric-card` / `tr`, and forces `-webkit-print-color-adjust: exact` so coloured tone-cards print correctly.
3. **Force-open `<details>` blocks.** The Top-5 individual-stock drill-downs in §03/§04 are collapsed by default in HTML. Print CSS appends `details { open: true } details > summary { display: none }` so they all expand into the printed page — otherwise the PDF would lose the per-sector stock detail.
4. **Write a temp HTML file** with the augmented content.
5. **Invoke Chrome headless:** `chrome --headless --disable-gpu --no-sandbox --print-to-pdf=<output> --print-to-pdf-no-header --virtual-time-budget=10000 <temp-html>`.
6. **Return the PDF path.** Default location is alongside the HTML file (`foo.html` → `foo.pdf`).

---

## Standalone use

```bash
# Single file, output next to the HTML
uv run python scripts/html_to_pdf.py path/to/CN_Market_Evening_20260430_xyz.html

# Multiple files
uv run python scripts/html_to_pdf.py file1.html file2.html file3.html

# Output to a specific directory
uv run python scripts/html_to_pdf.py -o ~/Desktop/iFA-PDFs file1.html file2.html

# Output to a specific PDF path (single-file only)
uv run python scripts/html_to_pdf.py -o /tmp/report.pdf single.html
```

### `--all-today` batch mode

Convert every HTML report generated today across all run modes:

```bash
uv run python scripts/html_to_pdf.py --all-today
uv run python scripts/html_to_pdf.py --all-today --out-root ~/claude/ifaenv/out
```

Scans `--out-root` recursively for files matching `CN_*_<YYYYMMDD>_*.html` where the date is today's. Useful as a daily cron task after the evening reports finish.

---

## Inline `--generate-pdf` flag

Every generate command supports `--generate-pdf`. When set, the PDF is written immediately after the HTML and its path is logged.

```bash
ifa generate market     --slot evening --report-date 2026-04-30 --user default --generate-pdf
ifa generate macro      --slot evening --report-date 2026-04-30                --generate-pdf
ifa generate asset      --slot evening --report-date 2026-04-30                --generate-pdf
ifa generate tech       --slot evening --report-date 2026-04-30 --user default --generate-pdf
ifa smartmoney evening                  --report-date 2026-04-30                --generate-pdf
```

The PDF lands at the same path as the HTML, with the extension swapped.

---

## Chrome detection paths

The candidate list in `pdf.py` (in resolution order):

```
/Applications/Google Chrome.app/Contents/MacOS/Google Chrome   (macOS)
/Applications/Chromium.app/Contents/MacOS/Chromium             (macOS)
google-chrome                                                  (Linux PATH)
google-chrome-stable                                           (Linux PATH)
chromium                                                       (Linux PATH)
chromium-browser                                               (Linux PATH)
C:\Program Files\Google\Chrome\Application\chrome.exe          (Windows)
C:\Program Files (x86)\Google\Chrome\Application\chrome.exe    (Windows)
```

If none resolves, `find_chrome()` raises with the full candidate list in the message.

---

## Troubleshooting

**"Chrome not found"** — install Google Chrome (or Chromium). On macOS the default Homebrew install path is detected automatically. On Linux servers without a desktop, install `google-chrome-stable` from Google's apt repo, or `chromium` from your distro.

**PDF is blank or missing sections** — the `<details>` force-open injection assumes the HTML uses native `<details>` blocks for collapsibles. If a section was rewritten to use a JS-toggled `<div hidden>` pattern, the print CSS will not unhide it. Either restore the `<details>` pattern or add a `@media print { .your-class { display: block !important; } }` override.

**PDF too large (>10MB)** — usually caused by inline base64-encoded images. The default tone-card SVGs are < 5KB each and should not blow this up. Check whether a section template embedded a TuShare-fetched image at full resolution.

**Missing CJK glyphs / boxes instead of 中文** — Chrome's bundled font fallback handles CJK on macOS and Windows out of the box. On bare Linux servers install `fonts-noto-cjk` (Debian/Ubuntu) or `google-noto-sans-cjk-fonts` (RHEL/Fedora) and rerun. There is no Chrome flag to fix this — it is a font-availability issue.

**Chrome timeout** — the default `--virtual-time-budget=10000` allows 10 s for any in-page JS to settle. Reports are static HTML so this is normally instant; a timeout typically means Chrome itself failed to launch (sandbox issue, missing `--no-sandbox` flag inside Docker, etc.). Run the underlying Chrome command manually to see the real error.

**`--all-today` finds nothing** — the file name pattern is strict: `CN_<Family>_<Slot>_<YYYYMMDD>_<runid>.html`. If a report writer changed the naming convention, update the glob in `_find_today_html()`.
