"""Tender documents: the text a brief is written from (ADR-0030).

A survey of 19 AI-approved tenders (November 2026) found the scope of work
in a readable PDF for 15, in a Word file for 1, and only in scanned PDFs
(no text, would need OCR) for 3. The scope sat on pages 2 to 21 of the main
document, or in a separate file titled "Scope of work", "Terms of
reference" or "Specification". Requirements and evaluation criteria are
spread over the same file or sit in other files (returnable schedules,
SBD forms, functionality criteria).

So every readable file is read (up to MAX_FILES), and two texts are made:
- summary: the cover page (duration, closing, briefing) and the pages from
  the scope of work on;
- requirements: the cover page and every page that mentions a requirement,
  a returnable, or how bids are evaluated, from all the files;
plus a checklist of key items found by plain pattern matching, so a
requirement the model leaves out is still shown.

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
MAX_FILES = 8                   # files read per tender; one survey tender had 16
MAX_PAGES = 80                  # pages read per PDF
# Llama 3.3 70B on Cloudflare takes 24,000 tokens, prompt plus answer. About
# 1.33 tokens a word, so each text stays well inside it with room to answer.
SUMMARY_WORDS = 5_000
REQUIREMENT_WORDS = 12_000          # about 16,000 tokens plus a 1,000-token answer
REQUEST_TIMEOUT_S = 30

SCOPE = re.compile(r"terms of reference|scope of (the )?works?|scope of services|"
                   r"technical specification|deliverables", re.IGNORECASE)
REQUIREMENT = re.compile(
    r"mandatory|returnable|compulsory|disqualif|must (be )?submit|reference letters?|"
    r"letters? of reference|references|evaluation criteria|functionality|threshold|"
    r"\b80/20\b|\b90/10\b|specific goals|preference points|\bSBD\b|\bCSD\b|"
    r"central supplier database|tax (clearance|compliance)|B-?BBEE|accredit|certif|"
    r"\bCIDB\b|good standing|audited financial|experience", re.IGNORECASE)
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


def tender_text(docs: list[dict], deadline: float, http=requests) -> tuple[dict | None, str]:
    """(texts, status) for one tender's documents (dicts with title and url).

    texts has "summary", "requirements" (both plain text) and "checklist" (a
    list of key items found in the documents). status is "ok" (a scope of
    work was found), "no_scope" (readable, but no scope heading), "scanned"
    (files have no text), "out_of_time", "unreadable" or "no_documents"."""
    readable, scanned, timed_out = [], False, False
    for doc in _ranked(docs):
        if time.monotonic() >= deadline:
            timed_out = True
            break
        body = _download(http, doc.get("url"), deadline)
        pages = _pages(body) if body is not None else None
        if not pages:
            continue
        if sum(len(p.split()) for p in pages) < 50:
            scanned = True
            continue
        readable.append(pages)
    if not readable:
        if scanned:
            return None, "scanned"
        if timed_out:
            return None, "out_of_time"
        return None, "unreadable" if docs else "no_documents"

    cover = readable[0][0]
    summary, status = readable[0], "no_scope"
    for n, pages in enumerate(readable):
        start = next((i for i, p in enumerate(pages) if SCOPE.search(p)), None)
        if start is not None:
            # The cover page carries the duration, closing date and briefing details
            # (the TCTA tender of November 2026 said "within 3 months" only there).
            summary = ([] if (n, start) == (0, 0) else [cover]) + pages[start:]
            status = "ok"
            break
    others = [p for pages in readable for p in pages if p is not cover]
    texts = {"summary": _clip(summary, SUMMARY_WORDS),
             "requirements": _clip([cover] + _requirement_pages(others, REQUIREMENT_WORDS - len(cover.split())),
                                   REQUIREMENT_WORDS),
             "checklist": checklist(" ".join(p for pages in readable for p in pages))}
    return texts, status


def _requirement_pages(pages: list[str], words: int) -> list[str]:
    """The pages that say most about requirements and evaluation, as many as fit
    in `words`, kept in document order (a page cut in half loses its list)."""
    scored = [(len({m.group(0).lower() for m in REQUIREMENT.finditer(p)}), i)
              for i, p in enumerate(pages) if REQUIREMENT.search(p)]
    chosen, used = [], 0
    for _, i in sorted(scored, key=lambda s: (-s[0], s[1])):
        size = len(pages[i].split())
        if used + size <= words:
            chosen.append(i)
            used += size
    return [pages[i] for i in sorted(chosen)]


def _clip(pages: list[str], words: int) -> str:
    return strip_unwritable(" ".join(" ".join(pages).split()[:words]))


_NUMBERS = {"one": "1", "two": "2", "three": "3", "four": "4", "five": "5", "six": "6"}


def checklist(text: str) -> list[str]:
    """Key requirements found in the text by plain pattern matching. It does
    not judge anything; it only shows what the documents mention, so the
    reader can see if the brief left one out."""
    text = " ".join(text.split())
    found = []
    refs = re.search(r"\b(\d+|one|two|three|four|five|six)\s*(\(\d+\)\s*)?(contactable\s+|signed\s+|client\s+)*"
                     r"(reference letters?|letters? of reference|references)", text, re.IGNORECASE)
    if refs:
        count = _NUMBERS.get(refs.group(1).lower(), refs.group(1))
        found.append(f"{count} reference letter{'' if count == '1' else 's'}")
    elif re.search(r"reference letters?|letters? of reference", text, re.IGNORECASE):
        found.append("reference letters")
    if re.search(r"(?<!non-)(?<!non )compulsory (briefing|site|clarification)", text, re.IGNORECASE):
        found.append("a compulsory briefing or site meeting is mentioned")
    threshold = re.search(r"functionality.{0,200}?(minimum|threshold|at least)\D{0,40}?(\d{2,3})\s*(%|percent|points)",
                          text, re.IGNORECASE)
    if threshold:
        found.append(f"functionality threshold {threshold.group(2)}{'%' if threshold.group(3) != 'points' else ' points'}")
    elif re.search(r"functionality", text, re.IGNORECASE):
        found.append("functionality evaluation")
    # The standard SBD 6.1 form describes both systems, so prefer the sentence
    # that says which one applies to this tender.
    applies = re.search(r"applicable preference point system for this (tender|bid|quotation) is the (80/20|90/10)",
                        text, re.IGNORECASE)
    systems = [s for s in ("80/20", "90/10") if re.search(rf"\b{s}\b", text)]
    if applies:
        found.append(f"{applies.group(2)} preference points")
    elif len(systems) == 1:
        found.append(f"{systems[0]} preference points")
    elif systems:
        found.append("80/20 or 90/10 preference points (the form lists both: check which applies)")
    if re.search(r"\bCSD\b|central supplier database", text, re.IGNORECASE):
        found.append("CSD registration")
    if re.search(r"tax (clearance|compliance)|\bSARS\b", text, re.IGNORECASE):
        found.append("tax compliance (SARS)")
    if re.search(r"B-?BBEE", text, re.IGNORECASE):
        found.append("B-BBEE certificate or affidavit")
    forms = sorted({m.group(1) for m in re.finditer(r"\bSBD\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)},
                   key=lambda s: [float(x) for x in s.split(".")])
    if forms:
        found.append("SBD " + ", ".join(forms))
    if re.search(r"accredit", text, re.IGNORECASE):
        found.append("accreditation")
    if re.search(r"\bCIDB\b", text):
        found.append("CIDB grading")
    if re.search(r"good standing|\bCOIDA\b", text, re.IGNORECASE):
        found.append("letter of good standing (COIDA)")
    if re.search(r"audited financial statements", text, re.IGNORECASE):
        found.append("audited financial statements")
    return found
