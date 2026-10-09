"""Run memory: which tenders were already sent, and which publication days
were covered, kept between runs in one small JSON document (ADR-0016).

In Azure it is a private blob, read and written over the Blob REST API with
the Function App's managed identity (plain `requests`, no SDK). TG.py uses a
local file instead (FileStore), so a local run can never change the
production memory (FR-22).

The rules:
- It is a first run only when the blob doesn't exist (404 + BlobNotFound) or
  the local file is absent. Any other read problem makes the memory
  "unavailable": nothing is marked NEW, a banner says so, nothing is written.
- The blob is validated before use (TL-2): at most 5 MB, JSON, schema 1,
  typed values, absurd dates clamped. Any error means "unavailable".
- It is written only after the digest is delivered (function_app.run).
- Helpers return a status string. They never raise, and never put a token,
  header, URL or response body into a status or a log line (TL-4).

The details are in docs/cosmos/API_CONTRACT.md sections 3.3, 4.2 and 4.3.
"""

import datetime as dt
import json
import logging
import os
import time

import pandas as pd
import requests

import pipeline
from digest import MissedDay

logger = logging.getLogger(__name__)

MEMORY_URL = "https://etendersfuncstorage.blob.core.windows.net/digest-state/memory.json"
MAX_BYTES = 5 * 1024 * 1024
WARN_BYTES = 2 * 1024 * 1024
BLOB_API_VERSION = "2021-08-06"
SCHEMA = 1

STORAGE_RESOURCE = "https://storage.azure.com/"
TOKEN_TIMEOUT_S = 5
BLOB_TIMEOUT_S = 10
SAVE_RETRY_DELAY_S = 5

# Retention (OQ-BA4): keep a sent ocid for 90 days, or until 30 days after it
# closes, whichever is later.
SENT_KEEP_DAYS = 90
SENT_KEEP_AFTER_CLOSING_DAYS = 30
# AI verdicts (classify.py) are kept this long; by then the tender has left the window.
AI_KEEP_DAYS = 14
AI_MODELS = ("llama", "nano")
# Clamps for absurd stored dates (AC-39).
CLOSING_CLAMP_DAYS = 365
# How far back a gap in the runs is still classified, day by day. It bounds
# the work (and the banner) if resolved_through is ever absurd.
MAX_GAP_DAYS = 366

DAY_STATES = ("partial", "covered")
UTC = dt.timezone.utc
ONE_DAY = dt.timedelta(days=1)


# --- token and stores --------------------------------------------------------

def mi_token(resource: str = STORAGE_RESOURCE) -> tuple[str | None, str]:
    """A token for `resource` (Blob storage by default) from the managed
    identity: (token, "ok"), or (None, "no_identity") when the identity isn't
    configured, or (None, "token") on any failure. Never raises; never logs
    the token, the identity header or the response body."""
    endpoint = os.environ.get("IDENTITY_ENDPOINT", "").strip()
    header = os.environ.get("IDENTITY_HEADER", "").strip()
    if not endpoint or not header:
        return None, "no_identity"
    try:
        r = requests.get(
            endpoint,
            params={"resource": resource, "api-version": "2019-08-01"},
            headers={"X-IDENTITY-HEADER": header},
            timeout=TOKEN_TIMEOUT_S,
        )
        body = r.json() if r.status_code == 200 else None
    except (requests.RequestException, ValueError):
        return None, "token"
    token = body.get("access_token") if isinstance(body, dict) else None
    if not isinstance(token, str) or not token:
        return None, "token"
    return token, "ok"


class BlobStore:
    """memory.json in Blob storage, reached with a bearer token.
    One instance per run: the token is fetched at most once."""

    def __init__(self, url: str, get_token=mi_token, http=requests):
        self.url = url
        self._get_token = get_token
        self._http = http
        self._token = None
        self._token_status = None

    def _auth_headers(self) -> dict | None:
        """The common headers, or None when there is no token (then no blob request is made)."""
        if self._token_status is None:
            self._token, self._token_status = self._get_token()
        if self._token is None:
            return None
        return {"Authorization": f"Bearer {self._token}", "x-ms-version": BLOB_API_VERSION}

    def get_text(self) -> tuple[str, str | None, str | None]:
        """(status, text, etag). status is "ok", "first_run" or "unavailable:<reason>"."""
        headers = self._auth_headers()
        if headers is None:
            return f"unavailable:{self._token_status}", None, None
        try:
            with self._http.get(self.url, headers=headers, timeout=BLOB_TIMEOUT_S, stream=True) as r:
                if r.status_code == 404:
                    code = r.headers.get("x-ms-error-code")
                    if code == "BlobNotFound":
                        return "first_run", None, None
                    if code == "ContainerNotFound":
                        return "unavailable:ContainerNotFound", None, None
                if r.status_code != 200:
                    return f"unavailable:http_{r.status_code}", None, None
                body = b""
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                etag = r.headers.get("ETag")
        except requests.Timeout:
            return "unavailable:timeout", None, None
        except requests.RequestException:
            return "unavailable:network", None, None
        status, text = _decode(body)
        return status, text, etag

    def put_text(self, text: str, etag: str | None) -> str:
        """Write text. etag None means "create only" (first run).
        Returns "ok", "conflict" or "failed:<reason>"."""
        headers = self._auth_headers()
        if headers is None:
            return f"failed:{self._token_status}"
        headers["x-ms-blob-type"] = "BlockBlob"
        headers["Content-Type"] = "application/json"
        if etag:
            headers["If-Match"] = etag
        else:
            headers["If-None-Match"] = "*"
        try:
            r = self._http.put(self.url, data=text.encode("utf-8"), headers=headers,
                               timeout=BLOB_TIMEOUT_S)
        except requests.Timeout:
            return "failed:timeout"
        except requests.RequestException:
            return "failed:network"
        if r.status_code == 201:
            return "ok"
        if r.status_code == 412 or (r.status_code == 409
                                    and r.headers.get("x-ms-error-code") == "BlobAlreadyExists"):
            return "conflict"
        return f"failed:http_{r.status_code}"


class FileStore:
    """A local JSON file, for TG.py and tests. An absent file is a first run."""

    def __init__(self, path: str):
        self.path = path

    def get_text(self) -> tuple[str, str | None, str | None]:
        try:
            with open(self.path, "rb") as f:
                body = f.read(MAX_BYTES + 1)
        except FileNotFoundError:
            return "first_run", None, None
        except OSError:
            return "unavailable:file", None, None
        status, text = _decode(body)
        return status, text, None

    def put_text(self, text: str, etag: str | None) -> str:
        """Write a temporary file, then swap it in, so a crash never leaves half a file."""
        tmp = self.path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp, self.path)
        except OSError:
            return "failed:file"
        return "ok"


def _decode(body: bytes) -> tuple[str, str | None]:
    """(status, text) for a body read with a MAX_BYTES + 1 cap."""
    if len(body) > MAX_BYTES:
        return "unavailable:too_large", None
    try:
        return "ok", body.decode("utf-8")
    except UnicodeDecodeError:
        return "unavailable:bad_json", None


# --- reading and validating (TL-2) ------------------------------------------

def _run_date(run_at: dt.datetime) -> dt.date:
    return run_at.astimezone(pipeline.API_DAY_TZ).date()


def _date(x) -> dt.date:
    return dt.date.fromisoformat(x)  # TypeError unless a str, ValueError if malformed


def _time(x) -> dt.datetime:
    value = dt.datetime.fromisoformat(x)
    if value.tzinfo is None:
        raise ValueError("a stored time must have a UTC offset")
    return value.astimezone(UTC)


def _dict(x) -> dict:
    if not isinstance(x, dict):
        raise TypeError("expected an object")
    return x


def _sent(x) -> dict:
    sent = {}
    for ocid, entry in _dict(x).items():
        entry = _dict(entry)
        closing = entry.get("closing")
        sent[ocid] = {"first_sent": _time(entry["first_sent"]),
                      "closing": None if closing is None else _time(closing)}
    return sent


def _days(x) -> dict:
    days = {}
    for day, state in _dict(x).items():
        if state not in DAY_STATES:
            raise ValueError("unexpected day state")
        days[_date(day)] = state
    return days


def _ai(x) -> dict:
    ai = {}
    for ocid, entry in _dict(x).items():
        entry = _dict(entry)
        if not isinstance(entry["v"], bool) or entry["by"] not in AI_MODELS:
            raise ValueError("unexpected AI verdict")
        ai[ocid] = {"v": entry["v"], "by": entry["by"], "on": _date(entry["on"])}
    return ai


# Each required top-level key, and how to read it. Other keys are ignored.
_CONVERTERS = (
    ("tracking_since", _date),
    ("resolved_through", _date),
    ("sent", _sent),
    ("days", _days),
)


def load(store, run_at: dt.datetime) -> tuple[dict | None, str, str | None]:
    """(memory, status, etag). status is "ok", "first_run" or
    "unavailable:<reason>"; memory is None unless status is "ok".
    Never raises: a bad blob makes the memory unavailable, never a crash."""
    status, text, etag = store.get_text()
    if status != "ok":
        if status != "first_run":
            logger.warning("Run memory is unavailable: %s", status)
        return None, status, None
    if len(text) > WARN_BYTES:
        logger.warning("Run memory is %d characters; the hard limit is %d bytes", len(text), MAX_BYTES)

    try:
        raw = json.loads(text)
    except (ValueError, RecursionError):
        return _unavailable("bad_json")
    if not isinstance(raw, dict) or raw.get("schema") != SCHEMA:
        return _unavailable("schema")

    memory = {"schema": SCHEMA}
    for key, convert in _CONVERTERS:
        try:
            memory[key] = convert(raw[key])
        except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
            return _unavailable(f"invalid:{key}")

    # Optional and added later: a bad "ai" section only costs a re-classification.
    try:
        memory["ai"] = _ai(raw.get("ai", {}))
    except (TypeError, ValueError, KeyError, AttributeError, OverflowError):
        logger.warning("Run memory: the AI verdicts are invalid and were dropped")
        memory["ai"] = {}

    _clamp(memory, run_at)
    return memory, "ok", etag


def _unavailable(reason: str) -> tuple[None, str, None]:
    logger.warning("Run memory is unavailable: %s", reason)
    return None, f"unavailable:{reason}", None


def _clamp(memory: dict, run_at: dt.datetime) -> None:
    """Pull absurd stored dates back into range (AC-39). Not an error."""
    run_date = _run_date(run_at)
    latest_closing = run_at + dt.timedelta(days=CLOSING_CLAMP_DAYS)
    for entry in memory["sent"].values():
        entry["first_sent"] = min(entry["first_sent"], run_at)
        if entry["closing"] is not None:
            entry["closing"] = min(entry["closing"], latest_closing)
    for entry in memory.get("ai", {}).values():  # a future date would never expire
        entry["on"] = min(entry["on"], run_date)
    earliest = run_date - dt.timedelta(days=MAX_GAP_DAYS)
    memory["resolved_through"] = min(max(memory["resolved_through"], earliest), run_date)
    # Tracking cannot start after the first day not yet resolved (QA-D2).
    memory["tracking_since"] = min(memory["tracking_since"], memory["resolved_through"] + ONE_DAY)


# --- the memory logic (pure) -------------------------------------------------

def _report_from(window: tuple[dt.date, dt.date]) -> dt.date:
    """The oldest day still in its reporting period: a day is named for
    MISSED_REPORT_DAYS days after it leaves the window (OQ-BA2)."""
    return window[0] - dt.timedelta(days=pipeline.MISSED_REPORT_DAYS + 1)


def missed_days(memory: dict | None, window: tuple[dt.date, dt.date]) -> list[MissedDay]:
    """The permanently missed days this digest names, oldest first (FR-15).

    A day outside the window that was never covered is named while it is in
    its reporting period. However old it is, it is also named if no delivered
    digest has named it yet (the days after resolved_through; AC-12). Days
    before tracking_since are never named. No memory (first run or
    unavailable) means none."""
    if memory is None:
        return []
    day = max(memory["tracking_since"],
              min(memory["resolved_through"] + ONE_DAY, _report_from(window)))
    out = []
    while day < window[0]:
        state = memory["days"].get(day)
        if state != "covered":
            kind = "only_partly_fetched" if state == "partial" else "never_fetched"
            out.append(MissedDay(day, kind, day + dt.timedelta(days=pipeline.LOOKBACK_DAYS + 1)))
        day += ONE_DAY
    return out


def _sent_expiry(entry: dict) -> dt.datetime:
    """When a sent ocid may be forgotten (OQ-BA4)."""
    keep_until = entry["first_sent"] + dt.timedelta(days=SENT_KEEP_DAYS)
    if entry["closing"] is not None:
        keep_until = max(keep_until, entry["closing"] + dt.timedelta(days=SENT_KEEP_AFTER_CLOSING_DAYS))
    return keep_until


def after_delivery(memory: dict | None, digest, day_results: list, run_at: dt.datetime,
                   ai_verdicts: dict | None = None) -> dict:
    """The memory to save once `digest` is delivered. memory None means a
    first run. ai_verdicts are this run's new classify() verdicts. Pure: the
    input memory is not changed."""
    window = digest.window
    if memory is None:
        memory = {"tracking_since": window[0], "resolved_through": window[0] - ONE_DAY,
                  "sent": {}, "days": {}}

    run_date = _run_date(run_at)
    ai = dict(memory.get("ai", {}))
    for ocid, v in (ai_verdicts or {}).items():
        ai[ocid] = {"v": v["v"], "by": v["by"], "on": run_date}
    ai = {ocid: e for ocid, e in ai.items() if e["on"] >= run_date - dt.timedelta(days=AI_KEEP_DAYS)}

    # Every ocid in the delivered digest is now sent. Keep the first time it
    # was sent, and the latest known closing time.
    sent = dict(memory["sent"])
    for ocid, closing in zip(digest.tenders["ocid"], digest.tenders["closing"]):
        closing = None if pd.isna(closing) else closing.to_pydatetime()
        old = sent.get(ocid)
        if old is None:
            sent[ocid] = {"first_sent": run_at, "closing": closing}
        else:
            sent[ocid] = {"first_sent": old["first_sent"],
                          "closing": closing if closing is not None else old["closing"]}
    sent = {ocid: e for ocid, e in sent.items() if run_at <= _sent_expiry(e)}

    # A day fetched completely is covered if it had already ended when the
    # run started, else partial (ADR-0017). A covered day stays covered.
    days = dict(memory["days"])
    for r in day_results:
        if not r.ok or not window[0] <= r.day <= window[1]:
            continue
        if run_at >= pipeline.day_end_utc(r.day):
            days[r.day] = "covered"
        elif days.get(r.day) != "covered":
            days[r.day] = "partial"
    # Keep each day until its reporting period ends, because missed_days reads it.
    days = {day: state for day, state in days.items() if day >= _report_from(window)}

    return {
        "schema": SCHEMA,
        "tracking_since": memory["tracking_since"],
        # This digest named every missed day before its window, and it was delivered (AC-12).
        "resolved_through": max(memory["resolved_through"], window[0] - ONE_DAY),
        "sent": sent,
        "days": days,
        "ai": ai,
    }


# --- writing -----------------------------------------------------------------

def _iso_time(x: dt.datetime) -> str:
    """YYYY-MM-DDTHH:MM:SSZ (isoformat pads the year to 4 digits; strftime may not)."""
    return x.astimezone(UTC).replace(microsecond=0, tzinfo=None).isoformat() + "Z"


def _dumps(memory: dict) -> str:
    """The memory as compact JSON, in the stored format (API_CONTRACT 3.3)."""
    doc = {
        "schema": SCHEMA,
        "tracking_since": memory["tracking_since"].isoformat(),
        "resolved_through": memory["resolved_through"].isoformat(),
        "sent": {ocid: {"first_sent": _iso_time(e["first_sent"]),
                        "closing": None if e["closing"] is None else _iso_time(e["closing"])}
                 for ocid, e in memory["sent"].items()},
        "days": {day.isoformat(): state for day, state in memory["days"].items()},
        "ai": {ocid: {"v": e["v"], "by": e["by"], "on": e["on"].isoformat()}
               for ocid, e in memory.get("ai", {}).items()},
    }
    return json.dumps(doc, separators=(",", ":"), sort_keys=True)


def save(store, memory: dict, etag: str | None) -> str:
    """Write the memory. Returns "ok", "conflict" or "failed:<reason>"; never
    raises. Retries once, after 5 s, on a timeout or network error only."""
    text = _dumps(memory)
    status = store.put_text(text, etag)
    if status in ("failed:timeout", "failed:network"):
        time.sleep(SAVE_RETRY_DELAY_S)
        status = store.put_text(text, etag)
    if status != "ok":
        logger.warning("Run memory was not saved: %s", status)
    return status
