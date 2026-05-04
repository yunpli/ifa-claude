"""PDF download + text extraction for research reports and announcements.

Supported URL patterns:
  · cninfo: https://static.cninfo.com.cn/finalpage/YYYY-MM-DD/<id>.PDF
  · dfcfw:  https://pdf.dfcfw.com/pdf/...  (research reports, direct link)
  · generic: any .pdf URL (best-effort)

Extraction pipeline:
  1. HTTP GET (httpx, 30s timeout, redirect follow)
  2. pdfplumber: extract text page by page
  3. Post-process: strip headers/footers, merge CJK broken lines
  4. Quality check: if extracted < 200 chars → mark extractable=False (scan)

Returns: PdfResult dataclass with text, page_count, extractable flag
"""
from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import pdfplumber

log = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/pdf,*/*",
}
_TIMEOUT = 30.0
_MIN_CHARS_EXTRACTABLE = 200

# Footer/header patterns to strip (page numbers, company watermarks, etc.)
_STRIP_PATTERNS = [
    re.compile(r"^[—\-–]\s*\d+\s*[—\-–]\s*$", re.M),           # page numbers
    re.compile(r"^\s*第\s*\d+\s*页\s*$", re.M),                   # 第N页
    re.compile(r"^\s*请务必阅读正文之后.*$", re.M),               # disclaimer watermark
    re.compile(r"^\s*本报告仅供.*$", re.M),                       # report disclaimer line
    re.compile(r"^\s*证券研究报告\s*$", re.M),
]


@dataclass
class PdfResult:
    url: str
    text: str
    page_count: int
    extractable: bool
    error: str | None = None


def fetch_and_extract(url: str) -> PdfResult:
    """Download PDF from *url* and return extracted text."""
    try:
        raw = _download(url)
    except Exception as exc:
        log.warning("PDF download failed: %s — %s", url, exc)
        return PdfResult(url=url, text="", page_count=0, extractable=False, error=str(exc))

    try:
        return _extract(url, raw)
    except Exception as exc:
        log.warning("PDF extraction failed: %s — %s", url, exc)
        return PdfResult(url=url, text="", page_count=0, extractable=False, error=str(exc))


def _download(url: str) -> bytes:
    with httpx.Client(follow_redirects=True, timeout=_TIMEOUT, headers=_HEADERS) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.content


def _extract(url: str, raw: bytes) -> PdfResult:
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        page_count = len(pdf.pages)
        pages: list[str] = []
        for page in pdf.pages:
            text = page.extract_text() or ""
            pages.append(text)

    full_text = "\n".join(pages)
    full_text = _post_process(full_text)
    extractable = len(full_text.strip()) >= _MIN_CHARS_EXTRACTABLE

    return PdfResult(
        url=url,
        text=full_text,
        page_count=page_count,
        extractable=extractable,
    )


def _post_process(text: str) -> str:
    # Strip known header/footer patterns
    for pat in _STRIP_PATTERNS:
        text = pat.sub("", text)

    # Merge broken CJK lines: if a line ends mid-sentence without punctuation
    # and the next line starts with a CJK char, join them.
    lines = text.split("\n")
    merged: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i].rstrip()
        if (
            line
            and i + 1 < len(lines)
            and lines[i + 1]
            and _is_cjk_continuation(line, lines[i + 1])
        ):
            merged.append(line + lines[i + 1].lstrip())
            i += 2
        else:
            merged.append(line)
            i += 1

    # Collapse 3+ blank lines → 2
    text = "\n".join(merged)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_CJK_RANGE = re.compile(r"[一-鿿㐀-䶿]")
_SENTENCE_END = re.compile(r"[。！？；…]$")


def _is_cjk_continuation(prev_line: str, next_line: str) -> bool:
    """True if prev_line likely continues on next_line (CJK broken line)."""
    if not prev_line or not next_line:
        return False
    if _SENTENCE_END.search(prev_line):
        return False
    if not _CJK_RANGE.search(prev_line[-1]):
        return False
    if not _CJK_RANGE.search(next_line[0]):
        return False
    return True


def cninfo_pdf_url(ann_url: str) -> str | None:
    """Attempt to convert a cninfo announcement page URL to its PDF direct URL.

    cninfo pages: https://www.cninfo.com.cn/new/disclosure/detail?...
    Direct PDFs:  https://static.cninfo.com.cn/finalpage/YYYY-MM-DD/<id>.PDF

    Returns None if the URL doesn't look like a cninfo announcement.
    """
    parsed = urlparse(ann_url)
    if "cninfo.com.cn" not in parsed.netloc:
        return None
    # Heuristic: many cninfo PDFs are accessible by replacing the domain
    # and path pattern. Without scraping the page we can't reliably transform
    # the URL — caller should scrape or use the direct ann URL.
    return None


def is_direct_pdf(url: str) -> bool:
    """True if URL points directly to a PDF (not a web page)."""
    return url.lower().endswith(".pdf") or "pdf.dfcfw.com" in url
