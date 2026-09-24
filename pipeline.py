"""Core eTenders OCDS fetch/score/flatten logic, shared by the CLI script
(TG.py) and the Azure Function (function_app.py)."""

import datetime as dt
import re
import time

import pandas as pd
import requests

BASE = "https://ocds-api.etenders.gov.za/api/OCDSReleases"

LOOKBACK_DAYS = 5
# Response time scales with the number of releases returned, and the API's
# throughput swings wildly: ~17 releases/sec when healthy, ~0.75/sec when
# degraded. At the bad end a whole 5-day window cannot be fetched inside the
# Consumption plan's 10-minute ceiling at any timeout, so the window is
# fetched one day at a time, newest first, under a wall-clock budget. Runs
# happen twice daily, so older days have already been collected by earlier
# runs and are the right thing to drop when time runs out.
CHUNK_DAYS = 1
# PageSize is an upper bound only: a page returns an arbitrary number of
# releases below it, so the end of the data is an empty page, never a short one.
PAGE_SIZE = 2000
MAX_PAGES = 50
# Total wall-clock allowed for fetching, leaving room under host.json's
# functionTimeout (00:10:00, the max on Consumption) for scoring and email.
FETCH_BUDGET_SECONDS = 420
REQUEST_TIMEOUT = 150
MAX_RETRIES = 2
RETRY_BACKOFF_SECONDS = 10

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

# The main volume control. Tender titles are opaque reference codes and the
# descriptions are thin, so category is the only metadata that discriminates;
# keyword scoring on metadata alone finds almost nothing.
CATEGORIES = {
    # unambiguously IT / data
    "information and communication",
    "information service activities",
    "computer programming, consultancy and related activities",
    "programming and broadcasting activities",
    "supplies: computer equipment",
    # professional / technical consulting, where data work tends to be advertised
    "services: professional",
    "professional, scientific and technical activities",
    "other professional, scientific and technical activities",
    "other service activities",
    "scientific and technical activities",
    "activities of head offices; management consultancy activities",
    "architectural and engineering activities; technical testing and analysis",
    "scientific research and development",
}

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


def _get_page(session: requests.Session, params: dict, deadline: float) -> dict:
    """GET one page. Retries transient failures (timeouts, 5xx, and the HTTP 200
    non-JSON error pages the API serves under load), never past the deadline."""
    last_exc = None
    for attempt in range(1, MAX_RETRIES + 1):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            r = session.get(BASE, params=params, timeout=min(REQUEST_TIMEOUT, remaining))
            r.raise_for_status()
            return r.json()
        except (requests.RequestException, ValueError) as exc:
            last_exc = exc
            if attempt < MAX_RETRIES:
                time.sleep(min(RETRY_BACKOFF_SECONDS * attempt,
                               max(deadline - time.monotonic(), 0)))
    raise last_exc or TimeoutError("fetch budget exhausted")


def _fetch_window(session: requests.Session, date_from: str, date_to: str,
                  deadline: float) -> list:
    """Page through one date window and return its releases."""
    out, page = [], 1
    while page <= MAX_PAGES:
        payload = _get_page(session, {
            "PageNumber": page,
            "PageSize": PAGE_SIZE,
            "dateFrom": date_from,
            "dateTo": date_to,
        }, deadline)
        releases = payload.get("releases", [])
        if not releases:
            break
        out.extend(releases)
        page += 1
    return out


def _day_chunks(date_from: str, date_to: str) -> list:
    """Date window split into CHUNK_DAYS-sized pieces, newest first."""
    start = dt.date.fromisoformat(date_from)
    end = dt.date.fromisoformat(date_to)
    chunks = []
    upper = end
    while upper >= start:
        lower = max(start, upper - dt.timedelta(days=CHUNK_DAYS - 1))
        chunks.append((lower.isoformat(), upper.isoformat()))
        upper = lower - dt.timedelta(days=1)
    return chunks


def fetch_releases(date_from: str, date_to: str) -> tuple:
    """Fetch the window a chunk at a time, newest first, within a wall-clock
    budget. Returns (releases, missing) where missing lists the chunks that
    timed out or errored, so callers can report incomplete coverage."""
    session = requests.Session()
    session.headers.update({"Accept": "application/json"})
    deadline = time.monotonic() + FETCH_BUDGET_SECONDS

    seen, out, missing = set(), [], []
    for chunk_from, chunk_to in _day_chunks(date_from, date_to):
        label = chunk_from if chunk_from == chunk_to else f"{chunk_from}..{chunk_to}"
        if time.monotonic() >= deadline:
            missing.append((label, "out of time"))
            continue
        try:
            for rel in _fetch_window(session, chunk_from, chunk_to, deadline):
                ocid = rel.get("ocid")
                if ocid in seen:
                    continue
                seen.add(ocid)
                out.append(rel)
        except Exception as exc:
            missing.append((label, type(exc).__name__))
    return out, missing


def _compile(keyword: str) -> re.Pattern:
    """Whole-word match, tolerating any run of whitespace inside a phrase so
    that line-wrapped document text still matches."""
    body = r"\s+".join(re.escape(word) for word in keyword.split())
    return re.compile(rf"(?<!\w){body}(?!\w)", re.IGNORECASE)


_KEYWORD_PATTERNS = [(k, w, _compile(k)) for k, w in KEYWORDS.items()]


def score(text: str) -> tuple:
    """Return (total score, matched keywords) for a blob of tender text."""
    hits = [(k, w) for k, w, pattern in _KEYWORD_PATTERNS if pattern.search(text)]
    return sum(w for _, w in hits), ", ".join(k for k, _ in sorted(hits, key=lambda h: -h[1]))


def blockers(text: str) -> str:
    low = text.lower()
    found = [label for pat, label in BLOCKER_PATTERNS if re.search(pat, low)]
    return "; ".join(found)


def flatten(rel: dict) -> dict:
    t = rel.get("tender") or {}
    period = t.get("tenderPeriod") or {}
    docs = t.get("documents") or []
    contact = t.get("contactPerson") or {}
    entity = t.get("procuringEntity") or {}

    blob = " ".join(str(x) for x in [
        t.get("title", ""), t.get("description", ""), t.get("category", ""),
        t.get("eligibilityCriteria", ""), t.get("specialConditions", ""),
        rel.get("description", ""),
    ])

    s, matched = score(blob)
    return {
        "score": s,
        "matched_keywords": matched,
        "blockers": blockers(blob),
        "tender_number": t.get("id"),
        "title": (t.get("title") or "")[:200],
        "buyer": entity.get("name") or (rel.get("buyer") or {}).get("name"),
        "province": t.get("province"),
        "category": t.get("category"),
        "method": t.get("procurementMethodDetails"),
        "closing": period.get("endDate"),
        "published": period.get("startDate"),
        "contact": contact.get("email"),
        "briefing_compulsory": (t.get("briefingSession") or {}).get("compulsory"),
        "documents": " | ".join(d.get("url", "") for d in docs[:3]),
        "ocid": rel.get("ocid"),
    }


def build_matches(lookback_days: int = LOOKBACK_DAYS) -> tuple:
    """Fetch, score and filter releases. Returns (DataFrame, missing) where
    missing lists date chunks the API could not serve this run."""
    today = dt.date.today()
    date_from = (today - dt.timedelta(days=lookback_days)).isoformat()
    date_to = today.isoformat()

    releases, missing = fetch_releases(date_from, date_to)
    df = pd.DataFrame(flatten(r) for r in releases)
    if df.empty:
        return df, missing

    # Keep only tenders still open.
    df["closing_dt"] = pd.to_datetime(df["closing"], errors="coerce", utc=True)
    now = pd.Timestamp.now(tz="UTC")
    df = df[df["closing_dt"].isna() | (df["closing_dt"] >= now)]

    # Province filter.
    if PROVINCES:
        prov = df["province"].fillna("").str.lower()
        df = df[prov.isin(PROVINCES) | (prov == "")]

    if CATEGORIES:
        df = df[df["category"].fillna("").str.lower().isin(CATEGORIES)]

    # Score orders the list rather than gating it: an IT-category tender with an
    # opaque title scores zero yet is exactly what we must not drop.
    df = df.sort_values("score", ascending=False)
    df = df.drop(columns=["closing_dt"])
    return df, missing
