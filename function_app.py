"""The Azure Function: a timer that runs the eTenders digest twice a day.

run() is the whole run, written so tests can drive it with fakes:
read the memory, fetch, build the digest, send it, save the memory (only
after delivery), ping the optional heartbeat, and log one RUN_SUMMARY line.

Secrets (TL-4, ADR-0024): the helpers that talk to secret-bearing endpoints
return a status and never raise. The one top-level handler applies redact()
to the failure-notice text and to the logged traceback, and re-raises a
RuntimeError holding only that redacted text. Never use logging.exception or
exc_info here: they would log the raw text.
"""

import datetime as dt
import json
import logging
import os
import re
import time
import traceback

import azure.functions as func
import requests

import classify
import email_sender
import memory
import pipeline
from digest import is_safe_url

# Library loggers can echo request URLs and headers at DEBUG/INFO (AC-38).
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

HEARTBEAT_PREFIX = "https://hc-ping.com/"
HEARTBEAT_TIMEOUT_S = 5
REASON_CHARS = 60        # a day's reason in RUN_SUMMARY is cut to this length
MISSED_NAMED_CAP = 10    # at most this many missed days are listed in RUN_SUMMARY
AI_LAST_START_S = 480    # no AI call starts later than this into the run (timeout is 600 s)

app = func.FunctionApp()


# --- redaction ---------------------------------------------------------------

_SECRET_PATTERNS = (
    (re.compile(r"Bearer\s+\S+", re.IGNORECASE), "Bearer REDACTED"),
    (re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*"), "REDACTED"),  # a JWT
    (re.compile(r"""(access_token["']?\s*[:=]\s*["']?)[^"'\s,}]+""", re.IGNORECASE), r"\1REDACTED"),
    (re.compile(r"(accesskey=)[^;&\s\"']+", re.IGNORECASE), r"\1REDACTED"),
    (re.compile(r"\b(sig|se|sv|sp|si)=[^&\s\"']+"), r"\1=REDACTED"),  # SAS parameters
)
# A configured value shorter than this is not a real secret, and replacing it
# everywhere would garble the text.
_MIN_SECRET_CHARS = 8


def redact(text: str) -> str:
    """text with secrets replaced by REDACTED: the configured HEARTBEAT_URL
    (and its path), the IDENTITY_HEADER and CF_API_TOKEN values, bearer tokens, JWTs,
    access_token values, ACS access keys and SAS signature parameters."""
    heartbeat_url = os.environ.get("HEARTBEAT_URL", "").strip()
    heartbeat_id = heartbeat_url.rstrip("/").rsplit("/", 1)[-1]  # the ping UUID at the end
    secrets = [heartbeat_url, heartbeat_id, os.environ.get("IDENTITY_HEADER", "").strip(),
               os.environ.get("CF_API_TOKEN", "").strip()]
    for secret in secrets:
        if len(secret) >= _MIN_SECRET_CHARS:
            text = text.replace(secret, "REDACTED")
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# --- heartbeat (optional, off by default: TL-9) --------------------------------

def heartbeat_config() -> tuple[str, str | None]:
    """("off", None) when HEARTBEAT_URL is unset, ("misconfigured", None) when
    it isn't an https://hc-ping.com/ URL, else ("on", url)."""
    url = os.environ.get("HEARTBEAT_URL", "").strip().rstrip("/")  # so "/fail" appends cleanly
    if not url:
        return "off", None
    if not url.startswith(HEARTBEAT_PREFIX) or url == HEARTBEAT_PREFIX or not is_safe_url(url):
        logging.warning("HEARTBEAT_URL is set but is not an %s URL; the heartbeat is off", HEARTBEAT_PREFIX)
        return "misconfigured", None
    return "on", url


def ping_heartbeat(url: str, body: dict) -> str:
    """POST the fixed body. Returns "ok", "http:<code>" or "failed:<Class>";
    never raises, and never logs the URL."""
    try:
        r = requests.post(url, json=body, timeout=HEARTBEAT_TIMEOUT_S, allow_redirects=False)
    except requests.RequestException as exc:
        return f"failed:{type(exc).__name__}"
    return "ok" if 200 <= r.status_code < 300 else f"http:{r.status_code}"


# --- the run -------------------------------------------------------------------

def _day_summary(r: pipeline.DayResult) -> dict:
    if r.ok:
        return {"day": r.day.isoformat(), "ok": True}
    return {"day": r.day.isoformat(), "ok": False, "reason": (r.reason or "")[:REASON_CHARS]}


def _classify(releases: list, mem: dict | None, run_at: dt.datetime,
              summary: dict, started: float) -> tuple[dict | None, dict]:
    """The AI step (classify.py, ADR-0028). Returns (verdicts, new): every
    verdict for this run, remembered or new, keyed by ocid (None when the AI
    is off), and the new ones, for the memory."""
    if not classify.enabled():
        summary["ai"] = "off"
        return None, {}
    remembered = mem.get("ai", {}) if mem else {}
    candidates = pipeline.ai_candidates(releases, run_at)
    todo = [t for t in candidates if t["ocid"] not in remembered]
    # Leave time to send the email inside the 10-minute function timeout.
    budget = min(classify.BUDGET_S, AI_LAST_START_S - (time.monotonic() - started))
    new, stats = classify.classify(todo, budget_s=max(budget, 0))
    summary["ai"] = {"remembered": len(candidates) - len(todo), **stats}
    return {**remembered, **new}, new


def _deliver_digest(run_at: dt.datetime, store, send_report, hb_state: str, run_id: str,
                    summary: dict) -> None:
    """Steps 2 to 6: memory, fetch, digest, send, save. Fills in summary as it goes."""
    started = time.monotonic()
    mem, status, etag = memory.load(store, run_at)
    summary["memory"]["read"] = status

    window = pipeline.window_for(run_at)
    summary["window"] = [window[0].isoformat(), window[1].isoformat()]
    logging.info("Fetching eTenders releases for %s to %s", window[0], window[1])
    releases, days = pipeline.fetch_releases(*window)
    summary["days"] = [_day_summary(r) for r in days]

    verdicts, ai_new = _classify(releases, mem, run_at, summary, started)

    missed = memory.missed_days(mem, window)
    d = pipeline.build_digest(releases, days, mem, status, hb_state, run_at, missed, verdicts)
    summary["dropped_releases"] = d.dropped_releases
    summary["counts"] = {"total": d.total, "new": d.new_count, "closing_soon": d.closing_soon_count}
    summary["missed_named"] = [m.day.isoformat() for m in d.missed_days[:MISSED_NAMED_CAP]]

    summary["email"] = send_report(d)
    summary["outcome"] = "digest_delivered"  # send_report returned, so ACS confirmed it

    # Write the memory only after delivery, and never after a bad read (AC-27, AC-28).
    if status in ("ok", "first_run"):
        new_memory = memory.after_delivery(mem, d, days, run_at, ai_new)
        summary["memory"]["write"] = memory.save(store, new_memory, etag)
    else:
        summary["memory"]["write"] = "skipped"


def _try_failure_notice(send_failure_notice, error_text: str) -> bool:
    """Send the failure notice; True if ACS confirmed it."""
    try:
        send_failure_notice(error_text)
        return True
    except Exception as exc:  # best effort: the run is already failing
        logging.error("The failure notice was not delivered either: %s",
                      redact(f"{type(exc).__name__}: {exc}"))
        return False


def run(run_at: dt.datetime, store, send_report, send_failure_notice, ping,
        *, run_id: str = "local", past_due: bool = False) -> dict:
    """One run. run_at is the run's single clock (UTC). Returns the RUN_SUMMARY
    dict; raises RuntimeError (with redacted text only) if the run failed."""
    clock = time.monotonic()
    summary = {
        "v": 1, "run_id": run_id,
        "started_at": run_at.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "duration_s": None, "past_due": past_due, "window": None, "days": None,
        "dropped_releases": None, "counts": None, "missed_named": None, "ai": None,
        "memory": {"read": None, "write": None}, "email": None,
        "outcome": None, "error": None, "heartbeat": None,
    }
    if past_due:
        logging.warning("Timer is past due: this run fired later than scheduled.")
    hb_state, hb_url = heartbeat_config()
    summary["heartbeat"] = hb_state
    error_text = None

    try:
        try:
            _deliver_digest(run_at, store, send_report, hb_state, run_id, summary)
        except Exception as exc:  # the run's one top-level handler (FR-10)
            error_text = redact(f"{type(exc).__name__}: {exc}")
            summary["error"] = type(exc).__name__
            logging.error("eTenders digest run failed:\n%s", redact(traceback.format_exc()))
            notice_delivered = _try_failure_notice(send_failure_notice, error_text)
            if summary["outcome"] is None:
                summary["outcome"] = "failure_notice_delivered" if notice_delivered else "nothing_delivered"

        if hb_state == "on":
            target = hb_url if summary["outcome"] == "digest_delivered" else hb_url + "/fail"
            body = {"run_id": run_id, "outcome": summary["outcome"], "counts": summary["counts"]}
            summary["heartbeat"] = ping(target, body)
    finally:
        summary["duration_s"] = round(time.monotonic() - clock, 1)
        logging.info("RUN_SUMMARY %s", json.dumps(summary, separators=(",", ":")))

    if error_text is not None:
        # Raised outside the except block, so no traceback chain carries the raw error.
        raise RuntimeError(error_text) from None
    return summary


# 06:00 and 18:00 UTC = 08:00 and 20:00 SAST (UTC+2), twice daily.
@app.timer_trigger(schedule="0 0 6,18 * * *", arg_name="timer", run_on_startup=False)
def etenders_digest(timer: func.TimerRequest, context: func.Context) -> None:
    run(
        dt.datetime.now(dt.timezone.utc),
        memory.BlobStore(memory.MEMORY_URL),
        email_sender.send_report,
        email_sender.send_failure_notice,
        ping_heartbeat,
        run_id=context.invocation_id,
        past_due=timer.past_due,
    )
