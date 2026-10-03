"""The contract between the backend and the frontend.

pipeline.build_digest() returns a Digest; report.py renders it into the
email subject, the HTML body and the xlsx. This module holds types and
constants, plus two shared checks, is_safe_url and strip_unwritable (Team
Leader decision TL-1). It does no I/O. The rules for every field (types,
nulls, ordering, invariants) are in docs/cosmos/API_CONTRACT.md section 2.

Owned by the Lead Engineer. Frontend and Backend read it but never edit it;
ask for a change instead, so both sides move together.
"""

import datetime as dt
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import pandas as pd
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

# Times are shown to the operator in SAST. South Africa has no daylight
# saving, so a fixed offset is exact and needs no tzdata (ADR-0017).
DISPLAY_TZ = dt.timezone(dt.timedelta(hours=2), "SAST")

# Allowed values of the string fields below.
MEMORY_STATUSES = ("ok", "first_run", "unavailable")
HEARTBEAT_STATES = ("on", "off", "misconfigured")
MISSED_KINDS = ("never_fetched", "only_partly_fetched")

# The columns of Digest.tenders, always all present and in this order,
# even when there are no rows. Types are in API_CONTRACT.md section 2.3.
TENDER_COLUMNS = (
    "ocid",
    "tender_number",
    "title",
    "buyer",
    "province",
    "category",
    "method",
    "contact",
    "matched_keywords",
    "blockers",
    "closing",
    "published",
    "closing_raw",
    "days_left",
    "closing_soon",
    "is_new",
    "score",
    "briefing_compulsory",
    "document_urls",
)


@dataclass(frozen=True)
class PartialDay:
    """A day inside the window that this run could not fetch completely (FR-08)."""

    day: dt.date
    reason: str  # "out of time" | "page limit" | "http_<code>" | an exception class name


@dataclass(frozen=True)
class MissedDay:
    """A day that left the window without ever being covered (FR-15)."""

    day: dt.date
    kind: str  # one of MISSED_KINDS
    left_window_on: dt.date


# eq=False because pandas refuses to turn "DataFrame == DataFrame" into one
# True/False, so a generated __eq__ would raise. Compare fields in tests.
@dataclass(frozen=True, eq=False)
class Digest:
    """Everything one email says. Built by the backend, only read by the frontend."""

    run_at: dt.datetime  # UTC, tz-aware: the run's single clock
    window: tuple[dt.date, dt.date]  # (first day, last day), inclusive, in the API's day timezone
    tenders: pd.DataFrame  # TENDER_COLUMNS, in the FR-13 order, index 0..n-1
    total: int  # len(tenders)
    new_count: int | None  # rows with is_new; None when memory_status == "unavailable"
    closing_soon_count: int  # rows with closing_soon
    closing_soon_days: int  # the pipeline.CLOSING_SOON_DAYS used for the flag, for the wording
    partial_days: tuple[PartialDay, ...]  # newest day first
    missed_days: tuple[MissedDay, ...]  # oldest day first
    memory_status: str  # one of MEMORY_STATUSES
    heartbeat: str  # one of HEARTBEAT_STATES
    dropped_releases: int  # malformed API releases left out (FR-23)


MAX_URL_CHARS = 2000


def is_safe_url(u) -> bool:
    """True only for an http(s) URL with a host, at most MAX_URL_CHARS long,
    with no whitespace or control characters (NFR-08f, ADR-0021). Used in
    pipeline.flatten and again wherever report.py makes a link."""
    if not isinstance(u, str) or len(u) > MAX_URL_CHARS:
        return False
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in u):
        return False
    try:
        parts = urlsplit(u)
        return parts.scheme.lower() in ("http", "https") and bool(parts.hostname)
    except ValueError:  # urlsplit rejects some malformed hosts, e.g. "http://[::1"
        return False


# xlsx cannot hold the control characters openpyxl knows, lone surrogates, or
# U+FFFE/U+FFFF; one of them would corrupt the file or fail the encode (QA-D3).
_UNWRITABLE = re.compile(ILLEGAL_CHARACTERS_RE.pattern + "|[\ud800-\udfff\ufffe\uffff]")


def strip_unwritable(text: str) -> str:
    """text without the characters the xlsx and the email cannot carry."""
    return _UNWRITABLE.sub("", text)
