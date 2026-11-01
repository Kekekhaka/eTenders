"""Tests for memory.py. The identity endpoint and Blob storage are fakes;
nothing here touches the network."""

import datetime as dt
import json

import pandas as pd
import pytest
import requests

import memory
import pipeline
from digest import MissedDay
from pipeline import DayResult

UTC = dt.timezone.utc
RUN_AT = dt.datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
D = dt.date


# --- fakes ---------------------------------------------------------------------

class FakeBlobResponse:
    def __init__(self, status=200, body=b"", headers=None):
        self.status_code = status
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size=1):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def json(self):
        return json.loads(self._body)


class FakeHttp:
    """Stands in for the requests module inside BlobStore and records every call."""

    def __init__(self, get=None, put=None):
        self._get, self._put = get, put
        self.calls = []

    def _answer(self, answer):
        if isinstance(answer, Exception):
            raise answer
        return answer

    def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return self._answer(self._get)

    def put(self, url, **kwargs):
        self.calls.append(("PUT", url, kwargs))
        return self._answer(self._put)


def token_ok():
    return "tok", "ok"


def blob(http, get_token=token_ok):
    return memory.BlobStore(memory.MEMORY_URL, get_token=get_token, http=http)


class TextStore:
    """A store holding fixed text, recording writes."""

    def __init__(self, text=None, status="ok"):
        self.text, self.status, self.writes = text, status, []

    def get_text(self):
        return self.status, self.text, '"etag-1"' if self.status == "ok" else None

    def put_text(self, text, etag):
        self.writes.append((text, etag))
        return "ok"


def stored(**changes):
    """A valid stored memory document (DESIGN_SPEC 5.1), as a dict."""
    doc = {
        "schema": 1,
        "tracking_since": "2026-09-20",
        "resolved_through": "2026-09-24",
        "sent": {"ocds-1": {"first_sent": "2026-09-28T06:01:00Z", "closing": "2026-10-15T09:00:00Z"},
                 "ocds-2": {"first_sent": "2026-09-28T06:01:00Z", "closing": None}},
        "days": {"2026-09-29": "partial", "2026-09-28": "covered"},
    }
    doc.update(changes)
    return doc


def load_doc(doc, run_at=RUN_AT):
    return memory.load(TextStore(json.dumps(doc)), run_at)


# --- the managed-identity token (AC-36) -------------------------------------------

def set_identity(monkeypatch):
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://localhost:8081/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", "identity-header-value")


def test_no_identity_configured_means_no_token_and_no_request(monkeypatch):
    monkeypatch.delenv("IDENTITY_ENDPOINT", raising=False)
    monkeypatch.delenv("IDENTITY_HEADER", raising=False)
    monkeypatch.setattr(memory.requests, "get", lambda *a, **k: pytest.fail("no request expected"))
    assert memory.mi_token() == (None, "no_identity")


def test_the_token_is_requested_with_the_identity_header(monkeypatch):
    set_identity(monkeypatch)
    seen = {}

    def fake_get(url, **kwargs):
        seen.update(kwargs, url=url)
        return FakeBlobResponse(200, json.dumps({"access_token": "tok"}).encode())

    monkeypatch.setattr(memory.requests, "get", fake_get)
    assert memory.mi_token() == ("tok", "ok")
    assert seen["url"] == "http://localhost:8081/msi/token"
    assert seen["headers"] == {"X-IDENTITY-HEADER": "identity-header-value"}
    assert seen["params"] == {"resource": "https://storage.azure.com/", "api-version": "2019-08-01"}
    assert seen["timeout"] == 5


@pytest.mark.parametrize("answer", [
    requests.ReadTimeout("slow"),
    requests.ConnectionError("down"),
    FakeBlobResponse(500, b"{}"),
    FakeBlobResponse(200, b'{"token_type": "Bearer"}'),
    FakeBlobResponse(200, b'{"access_token": ""}'),
    FakeBlobResponse(200, b"not json"),
    FakeBlobResponse(200, b'["a list"]'),
])
def test_any_token_failure_is_reported_as_token(monkeypatch, answer):
    set_identity(monkeypatch)

    def fake_get(url, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(memory.requests, "get", fake_get)
    assert memory.mi_token() == (None, "token")


# --- BlobStore ---------------------------------------------------------------------

@pytest.mark.parametrize("token_status", ["no_identity", "token"])
def test_without_a_token_no_blob_request_is_made(token_status):
    # AC-36: a failed token never turns into a blob call.
    http = FakeHttp(get=FakeBlobResponse(200, b"{}"), put=FakeBlobResponse(201))
    store = blob(http, get_token=lambda: (None, token_status))
    assert store.get_text() == (f"unavailable:{token_status}", None, None)
    assert store.put_text("{}", None) == f"failed:{token_status}"
    assert http.calls == []


def test_the_token_is_fetched_once_per_store():
    calls = []

    def counting_token():
        calls.append(1)
        return "tok", "ok"

    store = blob(FakeHttp(get=FakeBlobResponse(200, b"{}"), put=FakeBlobResponse(201)), counting_token)
    store.get_text()
    store.put_text("{}", '"e"')
    assert len(calls) == 1


def test_a_read_sends_the_bearer_token_and_version_and_returns_the_etag():
    http = FakeHttp(get=FakeBlobResponse(200, b'{"schema": 1}', {"ETag": '"0x8D"'}))
    assert blob(http).get_text() == ("ok", '{"schema": 1}', '"0x8D"')
    method, url, kwargs = http.calls[0]
    assert (method, url) == ("GET", memory.MEMORY_URL)
    assert kwargs["headers"] == {"Authorization": "Bearer tok", "x-ms-version": "2021-08-06"}
    assert kwargs["timeout"] == 10 and kwargs["stream"] is True
    assert "verify" not in kwargs


@pytest.mark.parametrize("answer, status", [
    (FakeBlobResponse(404, headers={"x-ms-error-code": "BlobNotFound"}), "first_run"),
    (FakeBlobResponse(404, headers={"x-ms-error-code": "ContainerNotFound"}), "unavailable:ContainerNotFound"),
    (FakeBlobResponse(404), "unavailable:http_404"),
    (FakeBlobResponse(403, headers={"x-ms-error-code": "AuthorizationPermissionMismatch"}), "unavailable:http_403"),
    (FakeBlobResponse(500), "unavailable:http_500"),
    (FakeBlobResponse(200, b"x" * (memory.MAX_BYTES + 1)), "unavailable:too_large"),
    (FakeBlobResponse(200, b"\xff\xfe not utf-8"), "unavailable:bad_json"),
    (requests.ReadTimeout("slow"), "unavailable:timeout"),
    (requests.ConnectionError("down"), "unavailable:network"),
])
def test_only_blob_not_found_is_a_first_run(answer, status):
    # AC-07 precision and AC-27: everything else is "unavailable".
    got_status, text, etag = blob(FakeHttp(get=answer)).get_text()
    assert got_status == status
    assert text is None and etag is None


@pytest.mark.parametrize("etag, condition", [('"0x8D"', ("If-Match", '"0x8D"')), (None, ("If-None-Match", "*"))])
def test_a_write_is_conditional(etag, condition):
    http = FakeHttp(put=FakeBlobResponse(201))
    assert blob(http).put_text('{"a":1}', etag) == "ok"
    _, url, kwargs = http.calls[0]
    assert kwargs["headers"][condition[0]] == condition[1]
    assert kwargs["headers"]["x-ms-blob-type"] == "BlockBlob"
    assert kwargs["headers"]["Authorization"] == "Bearer tok"
    assert kwargs["data"] == b'{"a":1}' and kwargs["timeout"] == 10


@pytest.mark.parametrize("answer, status", [
    (FakeBlobResponse(412), "conflict"),
    (FakeBlobResponse(409, headers={"x-ms-error-code": "BlobAlreadyExists"}), "conflict"),
    (FakeBlobResponse(409), "failed:http_409"),
    (FakeBlobResponse(500), "failed:http_500"),
    (requests.ReadTimeout("slow"), "failed:timeout"),
    (requests.ConnectionError("down"), "failed:network"),
])
def test_write_failures_become_statuses(answer, status):
    assert blob(FakeHttp(put=answer)).put_text("{}", '"e"') == status


# --- FileStore (TG.py) -------------------------------------------------------------

def test_a_missing_local_file_is_a_first_run(tmp_path):
    assert memory.FileStore(str(tmp_path / "m.json")).get_text() == ("first_run", None, None)


def test_the_local_file_is_replaced_whole(tmp_path):
    path = tmp_path / "m.json"
    store = memory.FileStore(str(path))
    assert store.put_text('{"a":1}', None) == "ok"
    assert store.get_text() == ("ok", '{"a":1}', None)
    assert [p.name for p in tmp_path.iterdir()] == ["m.json"]


def test_an_oversized_local_file_is_unavailable(tmp_path):
    path = tmp_path / "m.json"
    path.write_bytes(b" " * (memory.MAX_BYTES + 1))
    assert memory.FileStore(str(path)).get_text()[0] == "unavailable:too_large"


# --- load: the lighter validation (TL-2, AC-28, AC-39) -----------------------------

def test_a_valid_memory_loads_with_typed_values():
    mem, status, etag = load_doc(stored())
    assert (status, etag) == ("ok", '"etag-1"')
    assert mem["tracking_since"] == D(2026, 9, 20)
    assert mem["sent"]["ocds-1"]["closing"] == dt.datetime(2026, 10, 15, 9, 0, tzinfo=UTC)
    assert mem["sent"]["ocds-2"]["closing"] is None
    assert mem["days"] == {D(2026, 9, 29): "partial", D(2026, 9, 28): "covered"}


@pytest.mark.parametrize("status", ["first_run", "unavailable:ContainerNotFound", "unavailable:token"])
def test_a_store_status_other_than_ok_passes_straight_through(status):
    assert memory.load(TextStore(status=status), RUN_AT) == (None, status, None)


def corrupt_first_sent(doc):
    doc["sent"]["ocds-1"]["first_sent"] = 12345
    return doc


def corrupt_closing(doc):
    doc["sent"]["ocds-1"]["closing"] = "not-a-date"
    return doc


@pytest.mark.parametrize("text, status", [
    ("{not json", "unavailable:bad_json"),
    pytest.param("[" * 100_000, "unavailable:bad_json", id="too-deep"),  # still no crash
    (json.dumps(stored(schema=2)), "unavailable:schema"),
    (json.dumps(["schema", 1]), "unavailable:schema"),
    (json.dumps(stored(sent=["ocds-1"])), "unavailable:invalid:sent"),
    (json.dumps(corrupt_first_sent(stored())), "unavailable:invalid:sent"),
    (json.dumps(corrupt_closing(stored())), "unavailable:invalid:sent"),
    (json.dumps(stored(tracking_since=None)), "unavailable:invalid:tracking_since"),
    (json.dumps(stored(days={"2026-09-29": "done"})), "unavailable:invalid:days"),
    (json.dumps(stored(sent={"ocds-1": {"first_sent": "2026-09-28T06:01:00", "closing": None}})),
     "unavailable:invalid:sent"),  # a time with no UTC offset
])
def test_an_invalid_memory_is_unavailable_never_a_crash(text, status):
    # AC-28 rows (the 5 MB row is covered by the store tests above).
    assert memory.load(TextStore(text), RUN_AT) == (None, status, None)


def test_a_missing_top_level_key_is_named():
    doc = stored()
    del doc["days"]
    assert load_doc(doc)[1] == "unavailable:invalid:days"


def test_an_extra_top_level_key_is_ignored_and_not_written_back():
    # TL-2, O-6: the read succeeds.
    mem, status, _ = load_doc(stored(surprise={"x": 1}))
    assert status == "ok"
    assert "surprise" not in mem
    assert "surprise" not in memory._dumps(mem)


def test_absurd_dates_are_clamped_not_fatal():
    # AC-39
    doc = stored()
    doc["sent"]["ocds-1"]["closing"] = "9999-12-31T00:00:00Z"
    doc["sent"]["ocds-2"]["first_sent"] = "2027-01-01T00:00:00Z"
    mem, status, _ = load_doc(doc)
    assert status == "ok"
    assert mem["sent"]["ocds-1"]["closing"] == RUN_AT + dt.timedelta(days=365)
    assert mem["sent"]["ocds-2"]["first_sent"] == RUN_AT


def test_an_absurd_resolved_through_is_clamped_to_a_bounded_range():
    mem, status, _ = load_doc(stored(resolved_through="9999-12-31", tracking_since="0001-01-01"))
    assert status == "ok" and mem["resolved_through"] == D(2026, 9, 30)
    mem, status, _ = load_doc(stored(resolved_through="0001-01-01", tracking_since="0001-01-01"))
    assert status == "ok" and mem["resolved_through"] == D(2026, 9, 30) - dt.timedelta(days=366)


# --- missed days and the memory after delivery (FR-14, FR-15) -----------------------

WINDOW = (D(2026, 9, 25), D(2026, 9, 30))


def mem(resolved_through=D(2026, 9, 24), tracking_since=D(2026, 9, 1), days=None, sent=None):
    return {"schema": 1, "tracking_since": tracking_since, "resolved_through": resolved_through,
            "sent": sent or {}, "days": days or {}}


def at(day, hour):
    return dt.datetime.combine(day, dt.time(hour), tzinfo=UTC)


def named_on(m, run_at):
    return [x.day for x in memory.missed_days(m, pipeline.window_for(run_at))]


class FakeDigest:
    def __init__(self, window=WINDOW, rows=()):
        self.window = window
        self.tenders = pd.DataFrame(
            {"ocid": [o for o, _ in rows],
             "closing": pd.Series([c for _, c in rows], dtype="datetime64[ns, UTC]")})


def test_no_memory_means_no_missed_days():
    assert memory.missed_days(None, WINDOW) == []


def test_a_day_that_was_never_fetched_is_named_when_it_leaves_the_window():
    # AC-09: the first run on 2026-09-26 finds 2026-09-20 outside the window.
    run_at = at(D(2026, 9, 26), 6)
    window = pipeline.window_for(run_at)
    assert window[0] == D(2026, 9, 21)
    named = memory.missed_days(mem(resolved_through=D(2026, 9, 19), tracking_since=D(2026, 9, 20)), window)
    assert named == [MissedDay(D(2026, 9, 20), "never_fetched", D(2026, 9, 26))]


def test_a_covered_day_is_not_named():
    # AC-10
    m = mem(resolved_through=D(2026, 9, 19), tracking_since=D(2026, 9, 20), days={D(2026, 9, 20): "covered"})
    assert named_on(m, at(D(2026, 9, 26), 6)) == []


def test_a_day_fetched_only_while_in_progress_is_named_only_partly_fetched():
    # AC-11: fetched by the 06:00 run on the day itself; every later attempt failed.
    m = None
    for run_at, ok in [(at(D(2026, 9, 20), 6), True), (at(D(2026, 9, 21), 6), False),
                       (at(D(2026, 9, 23), 18), False)]:
        days = [DayResult(D(2026, 9, 20), ok, None if ok else "ReadTimeout")]
        m = memory.after_delivery(m, FakeDigest(pipeline.window_for(run_at)), days, run_at)
    assert m["days"][D(2026, 9, 20)] == "partial"
    named = memory.missed_days(m, pipeline.window_for(at(D(2026, 9, 26), 6)))
    # Tracking began at the first run's window (2026-09-15); 15..19 were never fetched.
    assert named[-1] == MissedDay(D(2026, 9, 20), "only_partly_fetched", D(2026, 9, 26))
    assert [x.kind for x in named[:-1]] == ["never_fetched"] * 5


def test_a_day_is_covered_only_by_a_run_that_started_after_it_ended():
    day = D(2026, 9, 29)
    ok = [DayResult(day, True, None)]
    before = memory.after_delivery(mem(), FakeDigest(), ok, at(day, 18))  # 20:00 SAST, still the 29th
    assert before["days"][day] == "partial"
    after = memory.after_delivery(before, FakeDigest(), ok, at(D(2026, 9, 30), 6))
    assert after["days"][day] == "covered"
    # A covered day stays covered when a later early fetch would say "partial";
    # a failed fetch or a page-limit day records nothing.
    again = memory.after_delivery(after, FakeDigest(), [DayResult(day, False, "page limit")],
                                  at(D(2026, 9, 30), 18))
    assert again["days"][day] == "covered"
    limit = memory.after_delivery(mem(), FakeDigest(), [DayResult(day, False, "page limit")],
                                  at(D(2026, 9, 30), 6))
    assert day not in limit["days"]


def test_a_gap_with_no_runs_is_still_classified_day_by_day():
    run_at = at(D(2026, 10, 10), 6)
    window = pipeline.window_for(run_at)  # 2026-10-05 .. 2026-10-10
    m = mem(resolved_through=D(2026, 9, 24),
            days={D(2026, 9, 26): "covered", D(2026, 9, 30): "partial"})
    named = memory.missed_days(m, window)
    assert [x.day for x in named] == [D(2026, 9, d) for d in (25, 27, 28, 29, 30)] + \
        [D(2026, 10, d) for d in (1, 2, 3, 4)]
    assert named[4] == MissedDay(D(2026, 9, 30), "only_partly_fetched", D(2026, 10, 6))
    # Once a delivered digest has named them, the gap is resolved.
    saved = memory.after_delivery(m, FakeDigest(window), [], run_at)
    assert saved["resolved_through"] == D(2026, 10, 4)


def test_days_before_tracking_began_are_never_named():
    m = mem(resolved_through=D(2026, 9, 1), tracking_since=D(2026, 9, 23))
    assert [x.day for x in memory.missed_days(m, WINDOW)] == [D(2026, 9, 23), D(2026, 9, 24)]


def test_a_missed_day_is_named_for_seven_days_after_it_left_the_window_then_forgotten():
    # OQ-BA2. 2026-09-18 left the window on 2026-09-24, so it is named through 2026-10-01.
    # There is no stored "missed" map: the day stays in the memory for 8 days after it leaves the window.
    m = mem(resolved_through=D(2026, 9, 24), tracking_since=D(2026, 9, 18))
    assert D(2026, 9, 18) in named_on(m, at(D(2026, 10, 1), 18))
    assert D(2026, 9, 18) not in named_on(m, at(D(2026, 10, 2), 6))


def test_a_day_stays_in_memory_through_its_reporting_period_then_is_pruned():
    m = mem(days={D(2026, 9, 16): "covered", D(2026, 9, 17): "covered", D(2026, 9, 25): "covered"})
    saved = memory.after_delivery(m, FakeDigest(), [], RUN_AT)  # the window starts 09-25
    assert list(saved["days"]) == [D(2026, 9, 17), D(2026, 9, 25)]


def test_a_missed_day_named_in_a_digest_that_failed_to_send_is_named_again():
    # AC-12: the failed run never saved, so the next run sees the same memory.
    m = mem(resolved_through=D(2026, 9, 19), tracking_since=D(2026, 9, 20))
    assert named_on(m, at(D(2026, 9, 26), 6)) == [D(2026, 9, 20)]
    second = at(D(2026, 9, 26), 18)
    assert named_on(m, second) == [D(2026, 9, 20)]
    # The second digest is delivered and saved. The day is still named for 7 days, then forgotten.
    saved = memory.after_delivery(m, FakeDigest(pipeline.window_for(second)), [], second)
    assert D(2026, 9, 20) in named_on(saved, at(D(2026, 9, 27), 6))
    assert D(2026, 9, 20) in named_on(saved, at(D(2026, 10, 3), 6))  # left 09-26, named through 10-03
    assert D(2026, 9, 20) not in named_on(saved, at(D(2026, 10, 4), 6))


def test_a_first_run_starts_tracking_at_its_window():
    saved = memory.after_delivery(None, FakeDigest(rows=[("a", pd.NaT)]), [], RUN_AT)
    assert saved["tracking_since"] == WINDOW[0]
    assert saved["resolved_through"] == WINDOW[0] - dt.timedelta(days=1)
    assert saved["sent"] == {"a": {"first_sent": RUN_AT, "closing": None}}
    assert set(saved) == {"schema", "tracking_since", "resolved_through", "sent", "days", "ai"}


def test_sent_keeps_the_first_time_and_refreshes_the_closing():
    closing = pd.Timestamp("2026-10-20T09:00:00Z")
    before = mem(sent={"a": {"first_sent": RUN_AT - dt.timedelta(days=2), "closing": None}})
    saved = memory.after_delivery(before, FakeDigest(rows=[("a", closing), ("b", pd.NaT)]), [], RUN_AT)
    assert saved["sent"]["a"] == {"first_sent": RUN_AT - dt.timedelta(days=2),
                                  "closing": closing.to_pydatetime()}
    assert saved["sent"]["b"]["first_sent"] == RUN_AT
    assert before["sent"]["a"]["closing"] is None  # the input is not changed


def test_sent_ocids_are_kept_90_days_or_until_closing_plus_30_whichever_is_later():
    # OQ-BA4
    old = RUN_AT - dt.timedelta(days=91)
    m = mem(sent={
        "expired": {"first_sent": old, "closing": RUN_AT - dt.timedelta(days=31)},
        "no-closing-expired": {"first_sent": old, "closing": None},
        "kept-by-closing": {"first_sent": old, "closing": RUN_AT - dt.timedelta(days=29)},
        "kept-by-age": {"first_sent": RUN_AT - dt.timedelta(days=89), "closing": None},
    })
    saved = memory.after_delivery(m, FakeDigest(), [], RUN_AT)
    assert sorted(saved["sent"]) == ["kept-by-age", "kept-by-closing"]


def test_a_tender_is_new_only_once():
    # AC-05: B is NEW at 06:00, that digest is delivered, and B is not NEW at 18:00.
    rel = {"ocid": "B", "tender": {"id": "B", "additionalProcurementCategories": ["consultingServices"],
                                   "tenderPeriod": {"endDate": "2026-10-20T09:00:00Z"}}}
    morning = at(D(2026, 9, 30), 6)
    d1 = pipeline.build_digest([rel], [], None, "first_run", "off", morning)
    assert d1.tenders["is_new"].tolist() == [True]
    store = TextStore(status="first_run")
    assert memory.save(store, memory.after_delivery(None, d1, [], morning), None) == "ok"

    evening = at(D(2026, 9, 30), 18)
    loaded, status, _ = memory.load(TextStore(store.writes[0][0]), evening)
    assert status == "ok"
    d2 = pipeline.build_digest([rel], [], loaded, status, "off", evening)
    assert d2.tenders["is_new"].tolist() == [False]
    assert d2.new_count == 0


# --- save -----------------------------------------------------------------------------

class SequenceStore:
    def __init__(self, *answers):
        self.answers, self.writes = list(answers), []

    def put_text(self, text, etag):
        self.writes.append((text, etag))
        return self.answers.pop(0)


def saved_memory():
    return memory.after_delivery(None, FakeDigest(rows=[("a", pd.NaT)]), [], RUN_AT)


@pytest.mark.parametrize("first", ["failed:network", "failed:timeout"])
def test_save_retries_once_after_a_network_error(monkeypatch, first):
    sleeps = []
    monkeypatch.setattr(memory.time, "sleep", sleeps.append)
    store = SequenceStore(first, "ok")
    assert memory.save(store, saved_memory(), '"e"') == "ok"
    assert len(store.writes) == 2 and sleeps == [5]


@pytest.mark.parametrize("answer", ["failed:http_500", "conflict", "failed:token"])
def test_save_does_not_retry_other_failures(monkeypatch, answer):
    monkeypatch.setattr(memory.time, "sleep", lambda s: pytest.fail("no retry expected"))
    store = SequenceStore(answer)
    assert memory.save(store, saved_memory(), None) == answer
    assert len(store.writes) == 1


def test_save_and_load_round_trip_through_a_file(tmp_path):
    store = memory.FileStore(str(tmp_path / "m.json"))
    original = memory.after_delivery(mem(days={D(2026, 9, 28): "partial"}),
        FakeDigest(rows=[("a", pd.Timestamp("2026-10-20T09:00:00Z"))]),
        [DayResult(D(2026, 9, 29), True, None)], RUN_AT)
    assert memory.save(store, original, None) == "ok"
    text = (tmp_path / "m.json").read_text(encoding="utf-8")
    assert '"tracking_since":"2026-09-01"' in text and '"missed"' not in text
    loaded, status, _ = memory.load(store, RUN_AT)
    assert status == "ok"
    assert loaded == original


# --- Monday to Friday runs (ADR-0002) ------------------------------------------------

@pytest.mark.real_tuning
def test_runs_on_weekdays_only_never_name_a_weekend_day_as_missed():
    """Three weeks of runs at 06:00 and 18:00 UTC, Monday to Friday, every fetch
    completing. Saturday and Sunday are never run on; Monday's window reaches them."""
    mem_state = None
    start = dt.date(2026, 10, 5)  # a Monday
    runs = 0
    for offset in range(21):
        day = start + dt.timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        for hour in (6, 18):
            run_at = dt.datetime(day.year, day.month, day.day, hour, tzinfo=UTC)
            window = pipeline.window_for(run_at)
            results = [DayResult(window[1] - dt.timedelta(days=i), True, None)
                       for i in range((window[1] - window[0]).days + 1)]
            if mem_state is not None:
                assert memory.missed_days(mem_state, window) == [], (run_at, window)
            mem_state = memory.after_delivery(mem_state, FakeDigest(window=window), results, run_at)
            runs += 1
            if day.weekday() == 0 and hour == 6:  # Monday morning reaches back over the weekend
                for back in (1, 2):
                    assert mem_state["days"][day - dt.timedelta(days=back)] == "covered"
    assert runs == 30
