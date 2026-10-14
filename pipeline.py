"""Core eTenders logic, shared by the local runner (TG.py) and the Azure
Function (function_app.py): fetch the releases, validate them, select and
flag the tenders, and build the Digest that report.py renders.

Everything the API sends is untrusted (ADR-0023): it is type-checked here
before any other code sees it. The rules are in docs/cosmos/API_CONTRACT.md."""

import datetime as dt
import logging
import math
import re
import time
from dataclasses import dataclass

import pandas as pd
import requests

from digest import (NOT_AI_CHECKED, TENDER_COLUMNS, Digest, MissedDay, PartialDay, is_safe_url,
                    strip_unwritable)

logger = logging.getLogger(__name__)

BASE = "https://ocds-api.etenders.gov.za/api/OCDSReleases"

# The days the API's dateFrom/dateTo refer to are assumed to be SAST days.
# This is the ONLY place that assumption is made (ADR-0017, V-02).
API_DAY_TZ = dt.timezone(dt.timedelta(hours=2), "SAST")

# --- Tuning: every knob the operator turns is in this file (NFR-09) ---------
LOOKBACK_DAYS = 4          # the window is today minus 4 days .. today: 5 days
CLOSING_SOON_DAYS = 5      # "closing soon" means closing within this many days
MISSED_REPORT_DAYS = 5     # name a permanently missed day for this many days

# Response time scales with the number of releases returned, and the API's
# throughput swings wildly: ~17 releases/sec when healthy, ~0.75/sec when
# degraded. At the bad end a whole 5-day window cannot be fetched inside the
# Consumption plan's 10-minute ceiling at any timeout, so the window is
# fetched one day at a time, newest first, under a wall-clock budget. Runs
# happen twice daily, so older days have already been collected by earlier
# runs and are the right thing to drop when time runs out.
#
# PageSize is an upper bound only: a page returns an arbitrary number of
# releases below it, so the end of the data is an empty page, never a short one.
#
# A power of two, so a failing page can be split into exact halves (ADR-0029).
PAGE_SIZE = 1024
MAX_PAGES = 50
# The API answers HTTP 500 with an empty body when a page holds a record it cannot
# serve (seen October 2026: 1 to 3 such records on a day, 100+ fine ones). A
# failing page is split in halves until the bad record is alone, and only that
# record is skipped. Both limits stop a server that is simply down from being
# hammered: past either, the day fails as it did before.
MAX_SKIPPED_PER_DAY = 20
MAX_SPLIT_REQUESTS = 120
# Total wall-clock allowed for fetching, leaving room under host.json's
# functionTimeout (00:10:00, the max on Consumption) for scoring and email.
FETCH_BUDGET_SECONDS = 420
REQUEST_TIMEOUT = 150
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 10
# An ocid is a memory key and a dedupe key; anything longer is not a real one.
MAX_OCID_CHARS = 200
MAX_DOCUMENT_URLS = 3

KEYWORDS = {
    # core strengths
    "data warehouse": 10, "data engineering": 10, "business intelligence": 10,
    "power bi": 10, "data scientist": 10, "data analytics": 8,
    "data analyst": 10, "data pipeline": 9, "BI developer": 10,
    "ETL developer": 9, "ETL engineer": 9, "data architect": 10,
    "data modeler": 10, "data modeling": 10, "data migration": 9,
    "data migration engineer": 9, "data migration developer": 9,
    "data governance": 8, "data quality": 8, "data steward": 8,
    "master data": 8, "data management": 8, "data integration": 9,
    "data warehouse architect": 10, "azure": 8, "databricks": 9,
    "Azure data factory": 9, "Microsoft fabric": 7, "dashboards": 8, "reporting": 8, "data visualization": 9,
    "reporting solutions": 8, "sql server": 8, "sql": 8, "python": 8,
    "power automate": 7, "power apps": 7, "power platform": 7, "data lake": 9, "data ops": 8,
    "ssis": 8, "ssis developer": 8, "ssis engineer": 8, "ssis architect": 8, "playwright": 7,
    "test automation": 7, "test automation engineer": 7,"database developer": 8,
    "database engineer": 8, "database architect": 8, "software engineer": 7, "software developer": 7,
    "software architect": 7, "platform engineer": 7, "platform architect": 7, "platform developer": 7,
    # adjacent
    "robotics process automation": 7, "rpa": 7, "machine learning": 5, "ml": 5,
    "api": 7, "api development": 7, "api integration": 7, "api engineer": 7,
    "artificial intelligence": 4, "ai": 4, "sap": 3,
    # generic buckets
    #"activities auxiliary to financial service and insurance activities": 3,
    #"architectural and engineering activities; technical testing and analysis": 3,
    #"computer programming, consultancy and related activities": 3,
    #"information service activities": 3,
    #"information and communication": 3,
    #"other professional, scientific and technical activities": 3,
    #"other service activities": 3,
    #"professional, scientific and technical activities": 3,
    #"programming and broadcasting activities": 3,
    #"services: professional": 3,
    #"scientific and technical activities": 3,
}

# Empty = nationwide. Populate with lowercase province names to narrow.
PROVINCES = set()

# The main volume control. Titles are opaque codes, so a tender is kept when
# it is open AND (an additionalProcurementCategories entry is in this set,
# matched case-insensitively, OR its keyword score is above 0).
KEEP_ADDITIONAL_CATEGORIES = {"consultingservices"}

# Phrases that usually mean an accreditation or track record gate.
BLOCKER_PATTERNS = [
    (r"\boriginal equipment manufactur", "OEM/OSM accreditation required"),
    (r"\baccredited (reseller|partner|distributor)", "Accredited partner required"),
    (r"\bcertified .{0,30}(partner|solution provider)", "Certified partner required"),
    (r"\bcidb\b", "CIDB grading required"),
    (r"\breference letter", "Verifiable reference letters required"),
    (r"\b(three|3) .{0,20}(years|reference)", "Multi-year track record required"),
    (r"\bcompulsory briefing", "Compulsory briefing session"),
    (r"\baudited (annual )?financial", "Audited financial statements required"),
    (r"\bjoint venture\b", "Joint venture contemplated"),
    (r"\bsubcontract", "Subcontracting contemplated (opportunity)"),
]


@dataclass(frozen=True)
class DayResult:
    """How the fetch of one publication day went. Backend-internal."""

    day: dt.date
    ok: bool            # every page fetched, without an error and without hitting MAX_PAGES
    reason: str | None  # None when ok; else "out of time", "page limit", "http_<code>" or an exception name
    skipped: int = 0    # records the API could not serve and that were left out (the day is still ok)


def window_for(run_at: dt.datetime) -> tuple[dt.date, dt.date]:
    """The publication days a run asks for, inclusive: LOOKBACK_DAYS before
    today .. today, where today is run_at's date in API_DAY_TZ."""
    today = run_at.astimezone(API_DAY_TZ).date()
    return today - dt.timedelta(days=LOOKBACK_DAYS), today


def day_end_utc(day: dt.date) -> dt.datetime:
    """When `day` ends: 00:00 on the next day in API_DAY_TZ, as a UTC time."""
    next_midnight = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(0), tzinfo=API_DAY_TZ)
    return next_midnight.astimezone(dt.timezone.utc)


def _reason(exc: Exception) -> str:
    """A short, secret-free name for an API failure: http_<code> or the class name."""
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"http_{exc.response.status_code}"
    return type(exc).__name__


def _get_page(session: requests.Session, params: dict, deadline: float,
              attempts: int | None = None, pause: float | None = None,
              quiet: bool = False) -> list:
    """GET one page and return its releases ([] at the end of the data).

    Retries transient failures (timeouts, 5xx, the HTTP 200 non-JSON error
    pages the API serves under load, and a `releases` that is not a list),
    never past the deadline. attempts and pause default to MAX_RETRIES and
    RETRY_BACKOFF_SECONDS, read when called."""
    attempts = MAX_RETRIES if attempts is None else attempts
    pause = RETRY_BACKOFF_SECONDS if pause is None else pause
    last_exc = None
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            r = session.get(BASE, params=params, timeout=min(REQUEST_TIMEOUT, remaining))
            r.raise_for_status()
            payload = r.json()
            if not isinstance(payload, dict):
                raise ValueError(f"expected a JSON object, got {type(payload).__name__}")
            releases = payload.get("releases")
            if releases is None:
                return []
            if not isinstance(releases, list):
                raise ValueError(f"expected releases to be a list, got {type(releases).__name__}")
            return releases
        except (requests.RequestException, ValueError, RecursionError) as exc:  # a hostile page can nest too deep
            last_exc = exc
            if not quiet:
                logger.warning("Attempt %d/%d failed for %s: %s",
                               attempt, attempts, params.get("dateFrom"), _reason(exc))
            if attempt < attempts:
                time.sleep(min(pause * attempt, max(deadline - time.monotonic(), 0)))
    raise last_exc or TimeoutError("fetch budget exhausted")


def _is_server_error(exc: Exception) -> bool:
    return (isinstance(exc, requests.HTTPError) and exc.response is not None
            and exc.response.status_code >= 500)


class _SplitState:
    """What splitting one day's failing pages has used so far."""

    def __init__(self):
        self.requests = 0
        self.answered = 0  # requests the server answered with a page, even an empty one
        self.skipped = 0


def _fetch_block(session: requests.Session, day: str, page: int, size: int,
                 deadline: float, state: _SplitState) -> list:
    """The records of page `page` of size `size`, with bad records left out.

    Page p of size S holds records (p-1)*S+1 to p*S, so pages 2p-1 and 2p of
    size S/2 hold exactly its two halves. A failing block is split until the
    record that fails is alone; that record is skipped. Raises the server
    error when a limit is reached, so the day fails as it would have before."""
    state.requests += 1
    try:
        got = _get_page(session, {"PageNumber": page, "PageSize": size,
                                  "dateFrom": day, "dateTo": day},
                        deadline, attempts=2 if size == 1 else 1, pause=0, quiet=True)
        state.answered += 1
        return got
    except requests.HTTPError as exc:
        if not _is_server_error(exc) or state.requests >= MAX_SPLIT_REQUESTS:
            raise
        if size == 1:
            if state.skipped >= MAX_SKIPPED_PER_DAY:
                raise
            state.skipped += 1
            logger.warning("%s: record %d cannot be served by the API; skipped", day, page)
            return []
        half = size // 2
        return (_fetch_block(session, day, 2 * page - 1, half, deadline, state)
                + _fetch_block(session, day, 2 * page, half, deadline, state))


def _fetch_day(session: requests.Session, day: str,
               deadline: float) -> tuple[list, bool, int]:
    """Page through one publication day until an empty page.
    Returns (releases, complete, skipped); complete is False when MAX_PAGES cut
    it short, and skipped counts records the API could not serve (ADR-0029)."""
    out, state = [], _SplitState()
    for page in range(1, MAX_PAGES + 1):
        try:
            releases = _get_page(session, {
                "PageNumber": page,
                "PageSize": PAGE_SIZE,
                "dateFrom": day,
                "dateTo": day,
            }, deadline)
        except requests.HTTPError as exc:
            if not _is_server_error(exc):
                raise
            logger.warning("%s page %d keeps failing (%s); splitting it to find the bad record",
                           day, page, _reason(exc))
            half = PAGE_SIZE // 2
            releases = (_fetch_block(session, day, 2 * page - 1, half, deadline, state)
                        + _fetch_block(session, day, 2 * page, half, deadline, state))
            if state.answered == 0:
                raise  # the server answered nothing at all: it is down, not serving a bad record
        if not releases:
            return out, True, state.skipped
        out.extend(releases)
    return out, False, state.skipped


def fetch_releases(date_from: dt.date, date_to: dt.date) -> tuple[list, list[DayResult]]:
    """Fetch the window one day at a time, newest first, within a wall-clock
    budget. Returns (releases, day_results), with one DayResult per day,
    newest first.

    Releases are deduplicated by ocid, keeping the newest day's copy. Items
    that are not a dict with a valid ocid pass through untouched, so that
    select_tenders drops and counts them (FR-23).

    Only API failures are absorbed here. A bug in our own code still raises,
    so it fails the run loudly instead of posing as an API outage."""
    deadline = time.monotonic() + FETCH_BUDGET_SECONDS
    days_newest_first = [date_to - dt.timedelta(days=i) for i in range((date_to - date_from).days + 1)]
    seen, releases, results = set(), [], []

    with requests.Session() as session:
        session.headers.update({"Accept": "application/json"})
        for day in days_newest_first:
            if time.monotonic() >= deadline:
                results.append(DayResult(day, False, "out of time"))
                continue
            try:
                day_releases, complete, skipped = _fetch_day(session, day.isoformat(), deadline)
            except (requests.RequestException, ValueError, TimeoutError, RecursionError) as exc:
                logger.warning("Giving up on %s: %s", day, _reason(exc))
                results.append(DayResult(day, False, _reason(exc)))
                continue
            if complete:
                results.append(DayResult(day, True, None, skipped))
            else:
                logger.warning("%s hit the %d-page limit; it is kept but not complete", day, MAX_PAGES)
                results.append(DayResult(day, False, "page limit", skipped))
            for rel in day_releases:
                ocid = _ocid(rel)
                if ocid is None:
                    releases.append(rel)  # invalid: flatten rejects it, select_tenders counts it
                elif ocid not in seen:
                    seen.add(ocid)
                    releases.append(rel)

    return releases, results


def _compile(keyword: str) -> re.Pattern:
    """Whole-word match, tolerating any run of whitespace inside a phrase so
    that line-wrapped document text still matches."""
    body = r"\s+".join(re.escape(word) for word in keyword.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


_KEYWORD_PATTERNS = [(k, w, _compile(k)) for k, w in KEYWORDS.items()]


def score(text: str) -> tuple[int, str]:
    """Return (total score, matched keywords) for a blob of tender text."""
    hits = [(k, w) for k, w, pattern in _KEYWORD_PATTERNS if pattern.search(text)]
    return sum(w for _, w in hits), ", ".join(k for k, _ in sorted(hits, key=lambda h: -h[1]))


def blockers(text: str) -> str:
    low = text.lower()
    found = [label for pat, label in BLOCKER_PATTERNS if re.search(pat, low)]
    return "; ".join(found)


# --- validating untrusted API data (ADR-0023, API_CONTRACT 4.1) -------------

def _obj(x) -> dict:
    """x if it is a dict, else an empty dict, so a wrong type reads as blank."""
    return x if isinstance(x, dict) else {}


def _text(x) -> str | None:
    """x as text with the characters xlsx cannot hold removed, or None when x
    is not a string or is blank."""
    if not isinstance(x, str):
        return None
    x = strip_unwritable(x)
    return x if x.strip() else None


def _ocid(rel) -> str | None:
    """The release's ocid, or None when the release must be dropped (FR-23)."""
    if not isinstance(rel, dict):
        return None
    ocid = _text(rel.get("ocid"))
    if ocid is None or len(ocid) > MAX_OCID_CHARS:
        return None
    return ocid


# A day short of the end of pandas' range, so converting to SAST cannot overflow (QA-D5).
_LATEST_TIME = pd.Timestamp.max.tz_localize("UTC") - pd.Timedelta(days=1)


def _parse_time(text: str | None) -> pd.Timestamp:
    """An ISO 8601 date or time as a UTC Timestamp, or NaT if it cannot be read.
    A value with no offset is read in API_DAY_TZ. Each value is parsed on its
    own: pandas blanks offset values when a column mixes the two kinds."""
    if text is None:
        return pd.NaT
    try:
        value = dt.datetime.fromisoformat(text.strip())
        if value.tzinfo is None:
            value = value.replace(tzinfo=API_DAY_TZ)
        ts = pd.Timestamp(value).tz_convert("UTC").as_unit("ns")
        return ts if ts <= _LATEST_TIME else pd.NaT
    except (ValueError, OverflowError):  # OutOfBoundsDatetime is a ValueError
        return pd.NaT


def _document_urls(docs) -> list[str]:
    """The first MAX_DOCUMENT_URLS document URLs that are safe to link to."""
    if not isinstance(docs, list):
        return []
    urls = []
    for doc in docs:
        url = _text(_obj(doc).get("url"))
        if url is not None and is_safe_url(url):
            urls.append(url)
            if len(urls) == MAX_DOCUMENT_URLS:
                break
    return urls


def _has_kept_category(rel: object) -> bool:
    """True when additionalProcurementCategories is a list holding a wanted
    category. Any other shape (string, None, numbers) means no match."""
    cats = _obj(_obj(rel).get("tender")).get("additionalProcurementCategories")
    if not isinstance(cats, list):
        return False
    return any(isinstance(c, str) and c.strip().lower() in KEEP_ADDITIONAL_CATEGORIES
               for c in cats)


def flatten(rel: object) -> dict | None:
    """One release as a row of plain, validated values, or None when the
    release must be dropped (not a dict, or no usable ocid). A field of the
    wrong type becomes blank; it never raises."""
    ocid = _ocid(rel)
    if ocid is None:
        return None
    t = _obj(rel.get("tender"))
    period = _obj(t.get("tenderPeriod"))
    briefing = _obj(t.get("briefingSession")).get("compulsory")

    title = _text(t.get("title"))
    category = _text(t.get("mainProcurementCategory"))
    score_text = " ".join(x for x in [
        title, _text(t.get("description")),
        _text(t.get("eligibilityCriteria")), _text(t.get("specialConditions")),
        _text(rel.get("description")),
    ] if x)
    s, matched = score(score_text)
    closing_raw = _text(period.get("endDate"))

    return {
        "ocid": ocid,
        "tender_number": _text(t.get("id")),
        "title": title[:200] if title else None,
        "buyer": (_text(_obj(t.get("procuringEntity")).get("name"))
                  or _text(_obj(rel.get("buyer")).get("name"))),
        "province": None,  # the API no longer supplies a province
        "category": category,
        "method": _text(t.get("procurementMethodDetails")),
        "contact": _text(_obj(t.get("contactPerson")).get("email")),
        "matched_keywords": matched,
        "blockers": blockers(score_text),
        "closing": _parse_time(closing_raw),
        "published": _parse_time(_text(period.get("startDate"))),
        "closing_raw": closing_raw,
        "score": s,
        "briefing_compulsory": briefing if isinstance(briefing, bool) else None,
        "document_urls": _document_urls(t.get("documents")),
    }


# The columns flatten produces: TENDER_COLUMNS without the flags added later.
_FLAT_COLUMNS = [c for c in TENDER_COLUMNS
                 if c not in ("days_left", "closing_soon", "is_new", "ai_flag")]

AI_DESCRIPTION_CHARS = 600
# ai_flag values: who decided that a tender is in the digest (ADR-0028);
# NOT_AI_CHECKED (from digest.py) marks the keyword fallback.
AI_LABELS = {"llama": "Llama", "nano": "nano"}


def ai_candidates(releases: list, run_at: dt.datetime) -> list[dict]:
    """Every open tender, for classify.py: dicts of ocid, title, buyer,
    category and description, one per ocid. The AI decides for all of them."""
    out, seen = [], set()
    for rel in releases:
        row = flatten(rel)
        if row is None or row["ocid"] in seen:
            continue
        seen.add(row["ocid"])
        if not pd.isna(row["closing"]) and row["closing"] < run_at:
            continue  # closed
        description = _text(_obj(rel.get("tender")).get("description")) or ""
        out.append({"ocid": row["ocid"], "title": row["title"], "buyer": row["buyer"],
                    "category": row["category"],
                    "description": description[:AI_DESCRIPTION_CHARS]})
    return out


def select_tenders(releases: list, run_at: dt.datetime,
                   ai: dict | None = None) -> tuple[pd.DataFrame, int]:
    """The digest's open tenders and the dropped count; see _select."""
    kept, _, dropped = _select(releases, run_at, ai)
    return kept, dropped


def _select(releases: list, run_at: dt.datetime,
            ai: dict | None) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Validate the releases and choose the open tenders (in PROVINCES, when set).

    ai is None when the AI step is off: keep a consulting-category tender or a
    keyword hit. Otherwise ai maps ocid to {"v": relevant, "by": model} and
    the AI decides (ADR-0028): a YES is kept, a NO is not, and a tender with
    no verdict falls back to its keyword hit. Returns (kept, rejected,
    dropped): rejected holds the keyword hits the AI said NO to, for the
    xlsx's second sheet; dropped counts the releases flatten rejected. The
    rows are not flagged or sorted yet; build_digest does that."""
    rows, keep, dropped = [], [], 0
    for rel in releases:
        row = flatten(rel)
        if row is None:
            dropped += 1
        else:
            rows.append(row)
            keep.append(_has_kept_category(rel))
    if dropped:
        logger.warning("Dropped %d malformed release(s) from the API", dropped)

    df = pd.DataFrame(rows, columns=_FLAT_COLUMNS)
    df["_keep"] = pd.Series(keep, index=df.index, dtype=bool)
    for col in ("closing", "published"):
        df[col] = pd.to_datetime(df[col], utc=True).astype("datetime64[ns, UTC]")
    df["score"] = df["score"].astype(int)

    # FR-04: keep a tender that closes at or after run_at, or has no closing date.
    df = df[df["closing"].isna() | (df["closing"] >= run_at)]

    if PROVINCES:
        province = df["province"].fillna("").str.lower()
        df = df[province.isin(PROVINCES) | (province == "")]

    keyword_hit = df["score"] > 0
    if ai is None:
        keep = df["_keep"] | keyword_hit
        df["ai_flag"] = pd.Series(None, index=df.index, dtype=object)
        rejected = df.iloc[0:0]
    else:
        verdict = [ai.get(o) for o in df["ocid"]]
        checked = pd.Series([v is not None for v in verdict], index=df.index, dtype=bool)
        said_yes = pd.Series([bool(v and v["v"]) for v in verdict], index=df.index, dtype=bool)
        df["ai_flag"] = pd.Series([AI_LABELS.get(v["by"]) if v else NOT_AI_CHECKED for v in verdict],
                                  index=df.index, dtype=object)
        keep = said_yes | (~checked & keyword_hit)
        rejected = df[checked & ~said_yes & keyword_hit]
    df = df.drop(columns="_keep")
    return (df[keep].reset_index(drop=True), rejected.drop(columns="_keep").reset_index(drop=True),
            dropped)


def _days_left(closing: pd.Series, run_at: dt.datetime) -> pd.Series:
    """Whole days until closing, rounded up; <NA> when there is no closing date."""
    days = (closing - pd.Timestamp(run_at)) / pd.Timedelta(days=1)
    return pd.Series([pd.NA if pd.isna(x) else math.ceil(x) for x in days],
                     index=closing.index, dtype="Int64")


def _flag_and_sort(df: pd.DataFrame, memory: dict | None, status: str,
                   run_at: dt.datetime) -> pd.DataFrame:
    """Add the NEW, CLOSING SOON and days-left columns, then sort (FR-13)."""
    df = df.copy()
    if status == "ok":
        df["is_new"] = ~df["ocid"].isin(list(memory["sent"]))
    else:
        df["is_new"] = pd.Series(status == "first_run", index=df.index, dtype=bool)
    soon_limit = run_at + dt.timedelta(days=CLOSING_SOON_DAYS)
    df["closing_soon"] = (df["closing"] > run_at) & (df["closing"] <= soon_limit)
    df["days_left"] = _days_left(df["closing"], run_at)

    # FR-13: NEW first, then score, closing (none last), tender number (none
    # last), ocid. The ocid is unique, so the order is fully determined.
    df = df.sort_values(
        ["is_new", "score", "closing", "tender_number", "ocid"],
        ascending=[False, False, True, True, True],
        na_position="last", kind="stable",
    )
    return df[list(TENDER_COLUMNS)].reset_index(drop=True)


def build_digest(releases: list, day_results: list[DayResult], memory: dict | None,
                 memory_status: str, heartbeat: str, run_at: dt.datetime,
                 missed: list[MissedDay] = (), ai: dict | None = None) -> Digest:
    """Everything one email says, computed once from one clock (run_at).
    Pure: no I/O. memory_status is memory.load's status; any
    "unavailable:<reason>" becomes "unavailable" here (the reason goes to
    RUN_SUMMARY only). `missed` is memory.missed_days(...), passed in so that
    this module never imports memory.py. ai: the AI verdicts, see _select."""
    window = window_for(run_at)
    status = "unavailable" if memory_status.startswith("unavailable") else memory_status
    df, rejected, dropped = _select(releases, run_at, ai)
    df = _flag_and_sort(df, memory, status, run_at)

    partial = sorted((PartialDay(r.day, r.reason) for r in day_results
                      if not r.ok and window[0] <= r.day <= window[1]),
                     key=lambda p: p.day, reverse=True)

    return Digest(
        run_at=run_at,
        window=window,
        tenders=df,
        total=len(df),
        new_count=None if status == "unavailable" else int(df["is_new"].sum()),
        closing_soon_count=int(df["closing_soon"].sum()),
        closing_soon_days=CLOSING_SOON_DAYS,
        partial_days=tuple(partial),
        missed_days=tuple(missed),
        memory_status=status,
        heartbeat=heartbeat,
        dropped_releases=dropped,
        ai_rejected=None if ai is None else _flag_and_sort(rejected, memory, status, run_at),
        skipped_releases=sum(r.skipped for r in day_results if window[0] <= r.day <= window[1]),
    )
