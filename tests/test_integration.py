"""End-to-end tests: function_app.run with the REAL pipeline, memory, report
and email_sender code. Fakes sit only at the edges: the eTenders API (a fake
requests.Session), Blob storage (a fake HTTP object behind the real
BlobStore), ACS (a fake EmailClient) and the heartbeat (a fake ping).

They also check that both halves honour the contract (API_CONTRACT v0.2)."""

import ast
import base64
import dataclasses
import datetime as dt
import inspect
import io
import json
import pathlib

import openpyxl
import pytest
import requests

import digest
import email_sender
import function_app
import memory
import pipeline
import report

ROOT = pathlib.Path(__file__).resolve().parent.parent
RUN_1 = dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.timezone.utc)   # window 2026-09-26 .. 2026-10-01
RUN_2 = RUN_1 + dt.timedelta(hours=12)
BLOB_URL = "https://example.blob.core.windows.net/digest-state/memory.json"


def release(ocid, title="Data warehouse support", closing="2026-10-20T11:00:00+02:00",
            category="Services: Professional"):
    return {"ocid": ocid, "tender": {
        "id": f"T-{ocid}", "title": title, "mainProcurementCategory": category, "additionalProcurementCategories": ["consultingServices"],
        "tenderPeriod": {"endDate": closing, "startDate": "2026-09-28T09:00:00+02:00"},
        "documents": [{"url": f"https://www.etenders.gov.za/{ocid}.pdf"}],
    }}


# --- edge fakes --------------------------------------------------------------------

class FakeApi:
    """The eTenders API: page 1 of a day holds its releases, page 2 is empty.
    A day in `failing` times out on every attempt."""

    def __init__(self, by_day, failing=()):
        self.by_day, self.failing = by_day, set(failing)

    def session(self):
        api = self

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                return self.payload

        class Session:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url, params, timeout):
                day = params["dateFrom"]
                if day in api.failing:
                    raise requests.ReadTimeout("simulated slow API")
                page = api.by_day.get(day, []) if params["PageNumber"] == 1 else []
                return Response({"releases": page})

        return Session()


class FakeBlobService:
    """Blob REST for one blob, with ETags and conditional PUTs."""

    def __init__(self, container_exists=True):
        self.container_exists = container_exists
        self.body, self.etag, self.puts = None, None, []

    class _Response:
        def __init__(self, status, body=b"", headers=None):
            self.status_code, self._body, self.headers = status, body, headers or {}

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def iter_content(self, chunk_size):
            yield self._body

    def get(self, url, headers, timeout, stream):
        assert headers["Authorization"] == "Bearer fake-token"
        if not self.container_exists:
            return self._Response(404, headers={"x-ms-error-code": "ContainerNotFound"})
        if self.body is None:
            return self._Response(404, headers={"x-ms-error-code": "BlobNotFound"})
        return self._Response(200, self.body, {"ETag": self.etag})

    def put(self, url, data, headers, timeout):
        self.puts.append(headers)
        if headers.get("If-None-Match") == "*" and self.body is not None:
            return self._Response(409, headers={"x-ms-error-code": "BlobAlreadyExists"})
        if "If-Match" in headers and headers["If-Match"] != self.etag:
            return self._Response(412)
        self.body = data
        self.etag = f'"0x{len(self.puts)}"'
        return self._Response(201)

    def store(self):
        return memory.BlobStore(BLOB_URL, get_token=lambda: ("fake-token", "ok"), http=self)


class FakeAcs:
    """ACS: records every message. With digest_times_out, the digest poller never
    finishes inside the timeout; the failure notice still goes through."""

    def __init__(self):
        self.sent, self.digest_times_out = [], False

    def client_class(self):
        acs = self

        class Poller:
            def __init__(self, finished):
                self.finished = finished

            def result(self, timeout):
                return {"status": "Succeeded"} if self.finished else None

            def done(self):
                return self.finished

        class Client:
            @classmethod
            def from_connection_string(cls, conn):
                return cls()

            def begin_send(self, message):
                is_digest = not message["content"]["subject"].startswith("eTenders digest FAILED")
                finished = not (is_digest and acs.digest_times_out)
                if finished:
                    acs.sent.append(message)
                return Poller(finished)

        return Client


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("ACS_CONNECTION_STRING", "endpoint=https://x/;accesskey=fake")
    monkeypatch.setenv("SENDER_EMAIL", "digest@example.com")
    monkeypatch.setenv("RECIPIENT_EMAIL", "op@example.com")
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    monkeypatch.setattr(pipeline, "RETRY_BACKOFF_SECONDS", 0)
    acs = FakeAcs()
    monkeypatch.setattr(email_sender, "EmailClient", acs.client_class())

    class World:
        blob = FakeBlobService()
        digests = []
        pings = []

        def run(self, run_at, api):
            monkeypatch.setattr(pipeline.requests, "Session", api.session)

            def send_report(d):
                self.digests.append(d)
                return email_sender.send_report(d)

            def ping(url, body):
                self.pings.append((url, body))
                return "ok"

            return function_app.run(run_at, self.blob.store(), send_report,
                                    email_sender.send_failure_notice, ping, run_id="it")

    w = World()
    w.acs = acs
    return w


def three_days_api(**kw):
    return FakeApi({
        "2026-10-01": [release("ocds-a", title="Power BI dashboards")],
        "2026-09-30": [release("ocds-b"), release("ocds-c", closing="2026-10-03T12:00:00+02:00")],
        "2026-09-28": [release("ocds-d", title="General consulting", closing=None)],
    }, **kw)


def attachment_rows(message):
    [att] = message["attachments"]
    ws = openpyxl.load_workbook(io.BytesIO(base64.b64decode(att["contentInBase64"])))["Tenders"]
    rows = list(ws.iter_rows(values_only=True))
    return [dict(zip(rows[0], r)) for r in rows[1:]]


def check_digest_invariants(d):
    """API_CONTRACT 2.1 to 2.4, on a Digest the real pipeline built."""
    assert d.run_at.tzinfo is not None and d.run_at.utcoffset() == dt.timedelta(0)
    assert tuple(d.tenders.columns) == digest.TENDER_COLUMNS
    assert list(d.tenders.index) == list(range(d.total))
    assert d.total == len(d.tenders)
    assert d.closing_soon_count == int(d.tenders["closing_soon"].sum())
    assert d.closing_soon_days == pipeline.CLOSING_SOON_DAYS
    assert d.memory_status in digest.MEMORY_STATUSES and d.heartbeat in digest.HEARTBEAT_STATES
    if d.memory_status == "unavailable":
        assert d.new_count is None and not d.tenders["is_new"].any() and d.missed_days == ()
    else:
        assert d.new_count == int(d.tenders["is_new"].sum())
    assert [p.day for p in d.partial_days] == sorted((p.day for p in d.partial_days), reverse=True)
    assert all(d.window[0] <= p.day <= d.window[1] for p in d.partial_days)
    assert all(digest.is_safe_url(u) for urls in d.tenders["document_urls"] for u in urls)
    # FR-13 order: NEW first, then score descending.
    keys = list(zip(~d.tenders["is_new"], -d.tenders["score"]))
    assert keys == sorted(keys)


# --- scenarios -----------------------------------------------------------------------

def test_first_run_marks_everything_new_and_writes_memory_after_delivery(world):
    summary = world.run(RUN_1, three_days_api())

    assert summary["outcome"] == "digest_delivered"
    assert summary["memory"] == {"read": "first_run", "write": "ok"}
    assert world.blob.puts[0].get("If-None-Match") == "*"
    stored = json.loads(world.blob.body)
    assert set(stored["sent"]) == {"ocds-a", "ocds-b", "ocds-c", "ocds-d"}

    [d] = world.digests
    check_digest_invariants(d)
    assert d.total == d.new_count == 4
    [message] = world.acs.sent
    assert message["content"]["subject"] == "eTenders digest - 4 tender(s), 4 new"
    assert "This is the first run with new-tender marking." in message["content"]["html"]
    rows = attachment_rows(message)
    assert all(r["new"] for r in rows)
    # AC-19: the xlsx and the HTML list the tenders in the same order as the Digest.
    order = list(d.tenders["tender_number"])
    assert [r["tender_number"] for r in rows] == order
    html = message["content"]["html"]
    assert [html.index(t) for t in order] == sorted(html.index(t) for t in order)


def test_second_run_with_the_same_releases_marks_nothing_new(world):
    world.run(RUN_1, three_days_api())
    summary = world.run(RUN_2, three_days_api())

    assert summary["memory"] == {"read": "ok", "write": "ok"}
    assert world.blob.puts[1].get("If-Match") == '"0x1"'
    d = world.digests[1]
    check_digest_invariants(d)
    assert (d.total, d.new_count) == (4, 0)
    message = world.acs.sent[1]
    assert message["content"]["subject"] == "eTenders digest - 4 tender(s), 0 new"
    assert "first run" not in message["content"]["html"]
    assert not any(r["new"] for r in attachment_rows(message))


def test_missing_container_means_memory_unavailable_and_nothing_written(world):
    world.blob.container_exists = False
    summary = world.run(RUN_1, three_days_api())

    assert summary["outcome"] == "digest_delivered"
    assert summary["memory"] == {"read": "unavailable:ContainerNotFound", "write": "skipped"}
    assert world.blob.puts == []
    [d] = world.digests
    check_digest_invariants(d)
    assert d.new_count is None and d.total == 4
    [message] = world.acs.sent
    assert message["content"]["subject"] == "eTenders digest - 4 tender(s), new unknown"
    assert ("New-tender marking and missed-day tracking were unavailable for this run."
            in message["content"]["html"])


def test_a_failing_api_day_gives_a_partial_digest_and_keeps_the_other_days(world):
    summary = world.run(RUN_1, three_days_api(failing={"2026-09-30"}))

    assert summary["outcome"] == "digest_delivered"
    assert {"day": "2026-09-30", "ok": False, "reason": "ReadTimeout"} in summary["days"]
    [d] = world.digests
    check_digest_invariants(d)
    assert set(d.tenders["ocid"]) == {"ocds-a", "ocds-d"}
    [message] = world.acs.sent
    assert message["content"]["subject"].endswith(" (partial)")
    html = message["content"]["html"]
    assert "Partial digest." in html and "2026-09-30 (ReadTimeout)" in html


def test_an_acs_timeout_is_not_delivery_so_the_next_run_still_marks_new(world):
    world.acs.digest_times_out = True
    with pytest.raises(RuntimeError, match="TimeoutError"):
        world.run(RUN_1, three_days_api())

    assert world.blob.puts == [] and world.blob.body is None      # nothing remembered
    [notice] = world.acs.sent
    assert notice["content"]["subject"] == "eTenders digest FAILED"

    world.acs.digest_times_out = False
    summary = world.run(RUN_2, three_days_api())                   # AC-06
    assert summary["memory"]["read"] == "first_run"
    assert world.digests[-1].new_count == 4


def test_malformed_releases_are_dropped_and_the_digest_still_goes_out(world):
    api = FakeApi({
        "2026-10-01": [release("ocds-good")],
        "2026-09-29": [
            "x",                                       # not an object: dropped
            {"ocid": 123, "tender": {}},               # bad ocid: dropped
            {"ocid": "ocds-str", "tender": "x"},       # bad tender: blank fields, filtered out
            release("ocds-vt", title="bad\x0btitle"),  # control character: stripped
        ],
    })
    summary = world.run(RUN_1, api)

    assert summary["outcome"] == "digest_delivered"
    assert summary["dropped_releases"] == 2
    [d] = world.digests
    check_digest_invariants(d)
    assert set(d.tenders["ocid"]) == {"ocds-good", "ocds-vt"}
    [message] = world.acs.sent
    html = message["content"]["html"]
    assert "2 malformed release(s) from the eTenders API" in html
    assert "\x0b" not in html
    assert all("\x0b" not in str(v) for r in attachment_rows(message) for v in r.values())


# --- the contract between the two halves --------------------------------------

def test_digest_has_exactly_the_contracted_fields():
    assert [f.name for f in dataclasses.fields(digest.Digest)] == [
        "run_at", "window", "tenders", "total", "new_count", "closing_soon_count",
        "closing_soon_days", "partial_days", "missed_days", "memory_status",
        "heartbeat", "dropped_releases"]
    assert [f.name for f in dataclasses.fields(digest.PartialDay)] == ["day", "reason"]
    assert [f.name for f in dataclasses.fields(digest.MissedDay)] == ["day", "kind", "left_window_on"]


def test_report_has_exactly_three_public_functions_taking_a_digest():
    public = {name for name, obj in vars(report).items()
              if inspect.isfunction(obj) and obj.__module__ == "report" and not name.startswith("_")}
    assert public == {"render_subject", "render_html", "build_xlsx"}
    for name in public:
        assert list(inspect.signature(getattr(report, name)).parameters) == ["d"]


def test_report_imports_nothing_from_the_backend():
    tree = ast.parse((ROOT / "report.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add((node.module or "").split(".")[0])
    assert not imported & {"pipeline", "memory", "email_sender", "function_app", "TG", "requests", "azure"}


def test_backend_and_frontend_share_one_is_safe_url():
    assert pipeline.is_safe_url is digest.is_safe_url
    assert report.is_safe_url is digest.is_safe_url
