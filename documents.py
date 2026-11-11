"""Tender documents: find the scope of work and return it as plain text (ADR-0030).

A survey of 19 AI-approved tenders (November 2026) found the scope of work
in a readable PDF for 15, in a Word file for 1, and only in scanned PDFs
(no text, would need OCR) for 3. The scope sat on pages 2 to 21 of the main
document, or in a separate file titled "Scope of work", "Terms of
reference" or "Specification". So: try the files whose title says scope
first, and read from the first page that mentions the scope.

Everything downloaded is untrusted. Only https links on the eTenders portal
are fetched, each body is capped, only PDF and Word files are read, and a
broken file is skipped, never raised. Nothing is written to disk.
"""

import io
import logging
import re
import time
import zipfile
from urllib.parse import urlsplit

import requests
from pypdf import PdfReader

from digest import is_safe_url, strip_unwritable

logger = logging.getLogger(__name__)

ALLOWED_HOST = "www.etenders.gov.za"
MAX_BYTES = 15_000_000          # largest file read; the survey's largest was 9 MB
MAX_FILES = 8                   # files tried per tender; one survey tender had 16
MAX_PAGES = 80                  # pages read per PDF
MAX_WORDS = 6_000               # about 8,000 tokens, well inside Llama 3.3 70B's 24,000
REQUEST_TIMEOUT_S = 30

SCOPE = re.compile(r"terms of reference|scope of (the )?works?|scope of services|"
                   r"technical specification|deliverables", re.IGNORECASE)
SCOPE_TITLE = re.compile(r"scope|terms of reference|\btor\b|specification", re.IGNORECASE)
NOISE_TITLE = re.compile(r"user guide|help ?desk|e-?tendering help", re.IGNORECASE)
_XML_TAG = re.compile(r"<[^>]+>")


def _allowed(url) -> bool:
    return (is_safe_url(url) and urlsplit(url).scheme == "https"
            and urlsplit(url).hostname == ALLOWED_HOST)


def _download(http, url: str, deadline: float) -> bytes | None:
    """The file's bytes, or None (not allowed, too big, out of time, or any error)."""
    remaining = deadline - time.monotonic()
    if remaining <= 1 or not _allowed(url):
        return None
    try:
        with http.get(url, stream=True, timeout=min(REQUEST_TIMEOUT_S, remaining),
                      allow_redirects=False) as r:
            if r.status_code != 200:
                return None
            body = b""
            for chunk in r.iter_content(64 * 1024):
                body += chunk
                if len(body) > MAX_BYTES or time.monotonic() > deadline:
                    return None
            return body
    except requests.RequestException:
        return None


def _pages(body: bytes) -> list[str] | None:
    """The text of each page (a Word file counts as one page), or None when
    the file is neither a readable PDF nor a Word document."""
    try:
        if body[:5] == b"%PDF-":
            reader = PdfReader(io.BytesIO(body))
            return [reader.pages[i].extract_text() or "" for i in range(min(len(reader.pages), MAX_PAGES))]
        if body[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(body)) as z:
                info = z.getinfo("word/document.xml")
                if info.file_size > MAX_BYTES:  # a zip bomb unpacks far bigger than it downloads
                    return None
                xml = z.read(info).decode("utf-8", "replace")
            return [_XML_TAG.sub(" ", xml.replace("</w:p>", "\n"))]
    except Exception:  # a hostile or broken file must never stop the digest
        return None
    return None


def _ranked(docs: list[dict]) -> list[dict]:
    """Files whose title says scope first, then the rest; help guides left out."""
    usable = [d for d in docs if isinstance(d, dict) and not NOISE_TITLE.search(d.get("title") or "")]
    return sorted(usable, key=lambda d: not SCOPE_TITLE.search(d.get("title") or ""))[:MAX_FILES]


def scope_text(docs: list[dict], deadline: float, http=requests) -> tuple[str | None, str]:
    """(text, status) for one tender's documents (dicts with title and url).

    status is "ok" (text starts at the scope of work), "no_scope" (readable,
    but no scope heading found: the start of the first readable file),
    "scanned" (files have no text), "out_of_time", "unreadable" or "no_documents"."""
    fallback, scanned, tried, timed_out = None, False, 0, False
    for doc in _ranked(docs):
        if time.monotonic() >= deadline:
            timed_out = True
            break
        body = _download(http, doc.get("url"), deadline)
        if body is None:
            continue
        tried += 1
        pages = _pages(body)
        if not pages:
            continue
        if sum(len(p.split()) for p in pages) < 50:
            scanned = True
            continue
        start = next((i for i, p in enumerate(pages) if SCOPE.search(p)), None)
        if start is not None:
            return _clip(pages[start:]), "ok"
        if fallback is None:
            fallback = _clip(pages)
    if fallback is not None:
        return fallback, "no_scope"
    if scanned:
        return None, "scanned"
    if timed_out:
        return None, "out_of_time"
    return None, "unreadable" if docs else "no_documents"


def _clip(pages: list[str]) -> str:
    words = " ".join(pages).split()[:MAX_WORDS]
    return strip_unwritable(" ".join(words))
