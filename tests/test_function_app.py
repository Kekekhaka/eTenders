"""Tests for function_app.py: the run's orchestration, RUN_SUMMARY, the
heartbeat, redaction and the timer registration. The API, Blob storage, ACS
and Healthchecks are all fakes; nothing leaves this machine."""

import datetime as dt
import json
import logging
import pathlib

import pandas as pd
import pytest
import requests

import email_sender
import function_app
import memory
import pipeline
from pipeline import DayResult

UTC = dt.timezone.utc
RUN_AT = dt.datetime(2026, 9, 30, 6, 0, tzinfo=UTC)
HC_URL = "https://hc-ping.com/0f3c7a52-1111-2222-3333-444455556666"
WINDOW_DAYS = [dt.date(2026, 9, 30) - dt.timedelta(days=i) for i in range(6)]
ALL_OK = [DayResult(day, True, None) for day in WINDOW_DAYS]


def release(ocid, closing="2026-10-20T09:00:00Z"):
    return {"ocid": ocid, "tender": {"id": ocid, "title": "data warehouse",
                                     "category": "Services: Professional",
                                     "tenderPeriod": {"endDate": closing}}}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("HEARTBEAT_URL", "IDENTITY_ENDPOINT", "IDENTITY_HEADER"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def api(monkeypatch):
    """Replace the API: set .releases and .days for what fetch_releases returns."""
    class Api:
        releases = [release("C")]
        days = ALL_OK
        error = None

    def fake_fetch(date_from, date_to):
        if Api.error:
            raise Api.error
        return list(Api.releases), list(Api.days)

    monkeypatch.setattr(pipeline, "fetch_releases", fake_fetch)
    return Api


class Mailer:
    """Fake send_report / send_failure_notice. fail_digest / fail_notice make them raise."""

    def __init__(self, fail_digest=False, fail_notice=False):
        self.fail_digest, self.fail_notice = fail_digest, fail_notice
        self.digests, self.notices = [], []

    def send_report(self, d):
        self.digests.append(d)
        if self.fail_digest:
            raise TimeoutError("ACS did not confirm the send within 90 s")
        return {"html_bytes": 1234, "xlsx_bytes": 5678}

    def send_failure_notice(self, text):
        self.notices.append(text)
        if self.fail_notice:
            raise RuntimeError("ACS send finished with status Failed")


class Pinger:
    def __init__(self, result="ok"):
        self.result, self.calls = result, []

    def __call__(self, url, body):
        self.calls.append((url, body))
        return self.result


class CountingStore(memory.FileStore):
    """A FileStore that counts writes."""

    def __init__(self, path):
        super().__init__(path)
        self.writes = 0

    def put_text(self, text, etag):
        self.writes += 1
        return super().put_text(text, etag)


def run(store, mailer=None, ping=None, run_at=RUN_AT, **kwargs):
    mailer = mailer or Mailer()
    return function_app.run(run_at, store, mailer.send_report, mailer.send_failure_notice,
                            ping or Pinger(), run_id="inv-1", **kwargs)


def summaries(caplog):
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("RUN_SUMMARY ")]
    return [json.loads(line[len("RUN_SUMMARY "):]) for line in lines], lines


# --- registration and configuration (AC-37, AC-38) ---------------------------------

def test_the_function_app_registers_one_timer():
    # AC-37: imported above with no Azure settings set.
    functions = function_app.app.get_functions()
    assert [f.get_function_name() for f in functions] == ["etenders_digest"]
    trigger = functions[0].get_trigger()
    assert trigger.schedule == "0 0 6,18 * * *"
    assert trigger.run_on_startup is False


def test_the_timer_runs_with_the_blob_store_and_the_invocation_id(monkeypatch):
    seen = {}
    monkeypatch.setattr(function_app, "run", lambda *args, **kwargs: seen.update(args=args, kwargs=kwargs))

    class Timer:
        past_due = True

    class Context:
        invocation_id = "inv-42"

    # The decorated object forwards calls to our function. (Don't call
    # app.get_functions() twice in one process: the second call raises.)
    function_app.etenders_digest(Timer(), Context())
    run_at, store, send_report, send_notice, ping = seen["args"]
    assert run_at.tzinfo is not None and run_at.utcoffset() == dt.timedelta(0)
    assert isinstance(store, memory.BlobStore) and store.url == memory.MEMORY_URL
    assert (send_report, send_notice, ping) == (email_sender.send_report, email_sender.send_failure_notice,
                                                function_app.ping_heartbeat)
    assert seen["kwargs"] == {"run_id": "inv-42", "past_due": True}


def test_traces_are_never_sampled_and_noisy_loggers_stay_quiet():
    # AC-38
    host = json.loads((pathlib.Path(__file__).parent.parent / "host.json").read_text())
    assert host["logging"]["applicationInsights"]["samplingSettings"]["isEnabled"] is False
    levels = json.dumps(host.get("logging", {}).get("logLevel", {})).lower()
    assert "debug" not in levels and "trace" not in levels
    assert logging.getLogger("urllib3").getEffectiveLevel() >= logging.WARNING
    assert logging.getLogger("azure").getEffectiveLevel() >= logging.WARNING


# --- redact -----------------------------------------------------------------------

def test_redact_removes_each_kind_of_secret(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_URL", HC_URL)
    monkeypatch.setenv("IDENTITY_HEADER", "e5f6a7b8-identity-header")
    text = function_app.redact(
        f"url {HC_URL}/fail id 0f3c7a52-1111-2222-3333-444455556666 "
        "hdr e5f6a7b8-identity-header Authorization: Bearer abc.def.ghi "
        "jwt eyJhbGciOi.eyJzdWIiOi.c2lnbmF0dXJl "
        '{"access_token": "tok123", "x": 1} '
        "endpoint=https://x.communication.azure.com/;accesskey=KEY123== "
        "https://a.blob.core.windows.net/c/b?sv=2021&se=2026&sp=rw&sig=SIG%3D")
    for secret in ("0f3c7a52", "e5f6a7b8", "abc.def", "eyJ", "tok123", "KEY123", "SIG%3D", "sv=2021"):
        assert secret not in text
    assert "REDACTED" in text


def test_redact_leaves_ordinary_text_alone():
    text = "ValueError: expected releases to be a list, got str"
    assert function_app.redact(text) == text


# --- heartbeat configuration and ping (FR-24, AC-31 to AC-33, AC-40) ---------------------

@pytest.mark.parametrize("value, state", [
    (None, "off"),
    ("  ", "off"),
    ("https://example.com/ping/abc", "misconfigured"),
    ("http://hc-ping.com/abc", "misconfigured"),
    ("https://hc-ping.com/", "misconfigured"),
    ("https://hc-ping.com/a b", "misconfigured"),
    (HC_URL, "on"),
])
def test_heartbeat_config(monkeypatch, value, state):
    if value is not None:
        monkeypatch.setenv("HEARTBEAT_URL", value)
    got_state, url = function_app.heartbeat_config()
    assert got_state == state
    assert url == (HC_URL if state == "on" else None)


def test_the_ping_is_a_post_without_redirects_and_with_a_5_second_timeout(monkeypatch):
    seen = {}

    class Response:
        status_code = 200

    def fake_post(url, **kwargs):
        seen.update(kwargs, url=url)
        return Response()

    monkeypatch.setattr(function_app.requests, "post", fake_post)
    body = {"run_id": "r", "outcome": "digest_delivered", "counts": None}
    assert function_app.ping_heartbeat(HC_URL, body) == "ok"
    assert seen == {"url": HC_URL, "json": body, "timeout": 5, "allow_redirects": False}


@pytest.mark.parametrize("answer, status", [
    (requests.ConnectionError("refused"), "failed:ConnectionError"),
    (requests.ReadTimeout("slow"), "failed:ReadTimeout"),
    (500, "http:500"),
    (302, "http:302"),
])
def test_a_failing_ping_returns_a_status_and_never_raises(monkeypatch, answer, status):
    def fake_post(url, **kwargs):
        if isinstance(answer, Exception):
            raise answer
        return type("Response", (), {"status_code": answer})()

    monkeypatch.setattr(function_app.requests, "post", fake_post)
    assert function_app.ping_heartbeat(HC_URL, {}) == status


# --- run(): outcomes -----------------------------------------------------------------

def test_a_delivered_first_run_saves_the_memory_and_logs_one_summary(tmp_path, api, caplog):
    # AC-01, AC-07, AC-26 (complete), AC-31 (heartbeat off)
    caplog.set_level(logging.INFO)
    store = CountingStore(str(tmp_path / "m.json"))
    mailer, ping = Mailer(), Pinger()
    summary = run(store, mailer, ping)

    d = mailer.digests[0]
    assert d.tenders["is_new"].tolist() == [True] and d.heartbeat == "off"
    assert store.writes == 1 and (tmp_path / "m.json").exists()
    assert ping.calls == [] and mailer.notices == []

    [logged], lines = summaries(caplog)
    assert logged == summary
    assert summary["outcome"] == "digest_delivered" and summary["error"] is None
    assert summary["memory"] == {"read": "first_run", "write": "ok"}
    assert summary["heartbeat"] == "off"
    assert summary["counts"] == {"total": 1, "new": 1, "closing_soon": 0}
    assert summary["email"] == {"html_bytes": 1234, "xlsx_bytes": 5678}
    assert summary["days"] == [{"day": day.isoformat(), "ok": True} for day in WINDOW_DAYS]
    assert summary["window"] == ["2026-09-25", "2026-09-30"]
    assert summary["dropped_releases"] == 0 and summary["missed_named"] == []
    assert summary["run_id"] == "inv-1" and summary["started_at"] == "2026-09-30T06:00:00Z"
    assert summary["past_due"] is False and isinstance(summary["duration_s"], float)
    for dropped in ("new_lag_hours", "became_covered", "newly_missed", "fetch_s", "date_min", "date_max"):
        assert dropped not in lines[0]
    assert "html_rows" not in json.dumps(summary["counts"])


def test_a_partial_digest_records_each_failed_day_and_its_reason(tmp_path, api, caplog):
    # AC-03 and AC-26 (partial)
    caplog.set_level(logging.INFO)
    api.days = [DayResult(WINDOW_DAYS[0], True, None), DayResult(WINDOW_DAYS[1], False, "ReadTimeout")]
    summary = run(memory.FileStore(str(tmp_path / "m.json")))
    assert summary["days"][1] == {"day": "2026-09-29", "ok": False, "reason": "ReadTimeout"}
    assert summary["outcome"] == "digest_delivered"
    assert len(summaries(caplog)[0]) == 1


def test_a_failed_send_does_not_use_up_new(tmp_path, api, caplog):
    # AC-06 and AC-22: the notice is sent, the memory is not written, and C is NEW again next time.
    caplog.set_level(logging.INFO)
    store = CountingStore(str(tmp_path / "m.json"))
    mailer = Mailer()
    mailer.send_report = lambda d: (_ for _ in ()).throw(ConnectionError("ACS is down"))
    with pytest.raises(RuntimeError) as raised:
        run(store, mailer)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    assert store.writes == 0
    assert len(mailer.notices) == 1
    [logged], _ = summaries(caplog)
    assert logged["outcome"] == "failure_notice_delivered"
    assert logged["memory"]["write"] is None
    assert logged["error"] == "ConnectionError"

    retry = Mailer()
    run(store, retry, run_at=RUN_AT + dt.timedelta(hours=12))
    assert retry.digests[0].tenders.loc[0, "ocid"] == "C"
    assert retry.digests[0].tenders.loc[0, "is_new"]


def test_a_crash_sends_a_redacted_notice_and_reraises(tmp_path, api, caplog, monkeypatch):
    # AC-22 and AC-26 (crash, notice delivered)
    caplog.set_level(logging.INFO)
    api.error = KeyError("releases")
    mailer = Mailer()
    with pytest.raises(RuntimeError, match="KeyError"):
        run(memory.FileStore(str(tmp_path / "m.json")), mailer)
    assert mailer.notices == ["KeyError: 'releases'"]
    [logged], _ = summaries(caplog)
    assert logged["outcome"] == "failure_notice_delivered" and logged["error"] == "KeyError"
    assert logged["memory"]["read"] == "first_run" and logged["counts"] is None and logged["email"] is None
    assert not any(r.exc_info for r in caplog.records)  # never logging.exception


@pytest.mark.parametrize("outcome, target", [
    ("digest_delivered", HC_URL),
    ("failure_notice_delivered", HC_URL + "/fail"),
    ("nothing_delivered", HC_URL + "/fail"),
])
def test_the_heartbeat_sends_only_allow_listed_data_to_the_right_target(
        tmp_path, api, caplog, monkeypatch, outcome, target):
    # AC-33 and AC-23 (nothing delivered, heartbeat on)
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HEARTBEAT_URL", HC_URL)
    posts = []
    monkeypatch.setattr(function_app.requests, "post",
                        lambda url, **kw: posts.append((url, kw)) or type("R", (), {"status_code": 200})())
    mailer = Mailer(fail_digest=outcome != "digest_delivered",
                    fail_notice=outcome == "nothing_delivered")
    api.releases = [release("C"), {"ocid": "x", "tender": {"title": "<script>tender text</script>"}}]
    try:
        function_app.run(RUN_AT, memory.FileStore(str(tmp_path / "m.json")), mailer.send_report,
                         mailer.send_failure_notice, function_app.ping_heartbeat, run_id="inv-1")
    except RuntimeError:
        assert outcome != "digest_delivered"

    assert len(posts) == 1
    url, kwargs = posts[0]
    assert url == target
    assert kwargs["allow_redirects"] is False and kwargs["timeout"] == 5
    body = kwargs["json"]
    assert set(body) == {"run_id", "outcome", "counts"}
    assert body["outcome"] == outcome and body["run_id"] == "inv-1"
    assert body["counts"] == {"total": 1, "new": 1, "closing_soon": 0}
    text = json.dumps(body)
    assert "http" not in text and "tender text" not in text and "Timeout" not in text
    [logged], _ = summaries(caplog)
    assert logged["outcome"] == outcome and logged["heartbeat"] == "ok"


@pytest.mark.parametrize("fail", [False, True])
def test_with_the_heartbeat_off_no_ping_is_made(tmp_path, api, caplog, fail):
    # AC-31, both outcomes
    caplog.set_level(logging.INFO)
    ping = Pinger()
    mailer = Mailer(fail_digest=fail)
    try:
        run(memory.FileStore(str(tmp_path / "m.json")), mailer, ping)
    except RuntimeError:
        assert fail
    assert ping.calls == []
    [logged], _ = summaries(caplog)
    assert logged["heartbeat"] == "off"
    assert mailer.digests[0].heartbeat == "off"


def test_a_misconfigured_heartbeat_is_never_pinged_or_shown(tmp_path, api, caplog, monkeypatch):
    # AC-32
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("HEARTBEAT_URL", "https://example.com/ping/abc")
    ping, mailer = Pinger(), Mailer()
    summary = run(memory.FileStore(str(tmp_path / "m.json")), mailer, ping)
    assert ping.calls == []
    assert summary["heartbeat"] == "misconfigured"
    assert mailer.digests[0].heartbeat == "misconfigured"
    assert not any("example.com" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("result", ["failed:ConnectionError", "failed:ReadTimeout", "http:500"])
def test_a_failing_heartbeat_never_breaks_the_run(tmp_path, api, monkeypatch, result):
    # AC-40
    monkeypatch.setenv("HEARTBEAT_URL", HC_URL)
    mailer = Mailer()
    summary = run(memory.FileStore(str(tmp_path / "m.json")), mailer, Pinger(result))
    assert summary["outcome"] == "digest_delivered"
    assert summary["heartbeat"] == result
    assert mailer.notices == []


def test_a_failed_memory_write_after_delivery_is_not_a_failure(api):
    class BrokenWrites:
        def get_text(self):
            return "first_run", None, None

        def put_text(self, text, etag):
            return "failed:http_403"

    mailer = Mailer()
    summary = run(BrokenWrites(), mailer)
    assert summary["outcome"] == "digest_delivered"
    assert summary["memory"]["write"] == "failed:http_403"
    assert mailer.notices == []


def test_a_crash_after_delivery_still_alerts_but_the_digest_counts_as_delivered(
        tmp_path, api, monkeypatch):
    # SF-3: send_report returned, then saving the memory crashed.
    def boom(*args):
        raise ValueError("bug while saving")

    monkeypatch.setattr(memory, "after_delivery", boom)
    monkeypatch.setenv("HEARTBEAT_URL", HC_URL)
    mailer, ping = Mailer(), Pinger()
    with pytest.raises(RuntimeError, match="ValueError"):
        function_app.run(RUN_AT, memory.FileStore(str(tmp_path / "m.json")), mailer.send_report,
                         mailer.send_failure_notice, ping, run_id="inv-1")
    assert len(mailer.digests) == 1 and mailer.notices == ["ValueError: bug while saving"]
    assert ping.calls[0][0] == HC_URL                       # success, not /fail
    assert ping.calls[0][1]["outcome"] == "digest_delivered"


def test_a_late_timer_is_logged_and_recorded(tmp_path, api, caplog):
    caplog.set_level(logging.INFO)
    summary = run(memory.FileStore(str(tmp_path / "m.json")), past_due=True)
    assert summary["past_due"] is True
    assert any("past due" in r.getMessage() for r in caplog.records)


# --- memory that can't be trusted is never written (AC-27, AC-28, AC-36) -------------------

class BlobHttp:
    """Fake HTTP for BlobStore: a fixed GET answer; any PUT is recorded."""

    def __init__(self, get_status, body=b"", headers=None):
        self.get_status, self.body, self.headers = get_status, body, headers or {}
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append("GET")
        owner = self

        class Response:
            status_code = owner.get_status
            headers = owner.headers

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def iter_content(self, chunk_size):
                yield owner.body

        return Response()

    def put(self, url, **kwargs):
        self.calls.append("PUT")
        return type("R", (), {"status_code": 201, "headers": {}})()


@pytest.mark.parametrize("http, read", [
    (BlobHttp(200, b"{not json"), "unavailable:bad_json"),
    (BlobHttp(200, b"x" * (memory.MAX_BYTES + 1)), "unavailable:too_large"),
    (BlobHttp(200, b'{"schema": 2}'), "unavailable:schema"),
    (BlobHttp(200, b'{"schema": 1, "tracking_since": "2026-09-20", "resolved_through": "2026-09-24",'
                   b' "sent": [], "days": {}}'), "unavailable:invalid:sent"),
])
def test_a_memory_that_cannot_be_trusted_is_never_written(api, caplog, http, read):
    # AC-27 and AC-28
    caplog.set_level(logging.INFO)
    store = memory.BlobStore(memory.MEMORY_URL, get_token=lambda: ("tok", "ok"), http=http)
    mailer = Mailer()
    summary = run(store, mailer)
    d = mailer.digests[0]
    assert d.total == 1 and d.new_count is None and not d.tenders["is_new"].any()
    assert d.memory_status == "unavailable" and d.missed_days == ()
    assert summary["memory"] == {"read": read, "write": "skipped"}
    assert summary["outcome"] == "digest_delivered"
    assert http.calls == ["GET"]


@pytest.mark.parametrize("token, read", [((None, "no_identity"), "unavailable:no_identity"),
                                         ((None, "token"), "unavailable:token")])
def test_without_a_token_the_digest_goes_out_with_no_blob_request(api, token, read):
    # AC-36
    http = BlobHttp(200, b"{}")
    mailer = Mailer()
    summary = run(memory.BlobStore(memory.MEMORY_URL, get_token=lambda: token, http=http), mailer)
    assert summary["memory"] == {"read": read, "write": "skipped"}
    assert mailer.digests[0].new_count is None
    assert http.calls == []


def test_a_missed_day_is_named_again_after_a_failed_send(tmp_path, api, caplog):
    # AC-12 end to end, and missed_named in RUN_SUMMARY (TL-10).
    caplog.set_level(logging.INFO)
    store = CountingStore(str(tmp_path / "m.json"))
    early = RUN_AT - dt.timedelta(days=2)  # 2026-09-28: window starts 09-23
    api.days = []  # nothing fetched: every day stays uncovered
    run(store, run_at=early)
    with pytest.raises(RuntimeError):
        run(store, Mailer(fail_digest=True))
    mailer = Mailer()
    summary = run(store, mailer, run_at=RUN_AT + dt.timedelta(hours=1))
    named = [m.day.isoformat() for m in mailer.digests[0].missed_days]
    assert named == ["2026-09-23", "2026-09-24"]
    assert summary["missed_named"] == named
    assert [s["missed_named"] for s in summaries(caplog)[0]] == [[], named, named]


# --- RUN_SUMMARY size (AC-34) ------------------------------------------------------------

def test_the_summary_fits_in_one_trace_at_worst_case(tmp_path, api, caplog, monkeypatch):
    caplog.set_level(logging.INFO)
    monkeypatch.setenv("HEARTBEAT_URL", HC_URL)
    api.days = [DayResult(day, False, "R" * 200) for day in WINDOW_DAYS]
    # A memory with a year-long gap: hundreds of missed days, capped at 10 in the summary.
    stale = memory.after_delivery(None, type("D", (), {"window": (dt.date(2025, 9, 1),) * 2,
                                                       "tenders": api_frame()})(),
                                  [], dt.datetime(2025, 9, 1, 6, tzinfo=UTC))
    store = memory.FileStore(str(tmp_path / "m.json"))
    memory.save(store, stale, None)
    function_app.run(RUN_AT, store, Mailer().send_report, Mailer().send_failure_notice,
                     Pinger("failed:" + "C" * 200), run_id="i" * 200)
    [logged], [line] = summaries(caplog)
    assert len(line) < 30_000 and "\n" not in line
    assert all(len(day["reason"]) <= 60 for day in logged["days"])
    assert len(logged["missed_named"]) == 10


def api_frame():
    return pd.DataFrame({"ocid": pd.Series([], dtype=object),
                         "closing": pd.Series([], dtype="datetime64[ns, UTC]")})


# --- AC-30: no secret leaks (containment plus redact) ----------------------------------------

SECRET_HEADER = "SECRET-identity-header-value"
SECRET_ENDPOINT = "http://localhost:8081/msi/token?x=SECRET-endpoint"
SECRET_HC = "https://hc-ping.com/SECRET-ping-uuid-0000"
SECRET_TOKEN = "SECRET-access-token"
SECRET_KEY = "SECRET-acs-key"


@pytest.mark.parametrize("token_fails", [True, False])
def test_no_secret_leaks_into_logs_email_summary_heartbeat_or_the_raised_error(
        api, caplog, monkeypatch, token_fails):
    caplog.set_level(logging.DEBUG)
    root = logging.getLogger()
    filters_before = (list(root.filters), [list(h.filters) for h in root.handlers])
    monkeypatch.setenv("IDENTITY_ENDPOINT", SECRET_ENDPOINT)
    monkeypatch.setenv("IDENTITY_HEADER", SECRET_HEADER)
    monkeypatch.setenv("HEARTBEAT_URL", SECRET_HC)
    monkeypatch.setenv("ACS_CONNECTION_STRING", f"endpoint=https://x.communication.azure.com/;accesskey={SECRET_KEY}")
    monkeypatch.setenv("SENDER_EMAIL", "DoNotReply@x.azurecomm.net")
    monkeypatch.setenv("RECIPIENT_EMAIL", "me@example.com")

    # Token endpoint and blob: every URL, header and body says SECRET.
    def fake_get(url, **kwargs):
        if url.startswith("http://localhost"):
            if token_fails:
                raise requests.ConnectionError(f"GET {url} with X-IDENTITY-HEADER: {SECRET_HEADER} refused")
            return type("R", (), {"status_code": 200, "json": lambda self: {"access_token": SECRET_TOKEN}})()
        raise requests.ConnectionError(f"GET {url} Authorization: {kwargs['headers']['Authorization']} 403 "
                                       f"body: AuthenticationFailed {SECRET_TOKEN}")

    monkeypatch.setattr(memory.requests, "get", fake_get)

    posts = []

    def fake_post(url, **kwargs):
        posts.append(kwargs["json"])
        raise requests.ConnectionError(f"POST {url} refused; response says {SECRET_HC}")

    monkeypatch.setattr(function_app.requests, "post", fake_post)

    # The pipeline then raises an unexpected error that carries secrets.
    api.error = RuntimeError(f"Bearer {SECRET_TOKEN} while calling {SECRET_HC}?sig={SECRET_KEY}")

    # The failure notice goes through the real email_sender, into a fake ACS client.
    sent = []

    class Poller:
        def result(self, timeout):
            return {"status": "Succeeded"}

        def done(self):
            return True

    class Client:
        def begin_send(self, message):
            sent.append(message)
            return Poller()

    monkeypatch.setattr(email_sender.EmailClient, "from_connection_string", staticmethod(lambda c: Client()))

    # Each helper returns a status, carrying nothing secret.
    assert memory.mi_token()[1] in ("ok", "token")
    assert "SECRET" not in memory.BlobStore(memory.MEMORY_URL).get_text()[0]
    assert function_app.ping_heartbeat(SECRET_HC, {}) == "failed:ConnectionError"
    posts.clear()

    with pytest.raises(RuntimeError) as raised:
        function_app.run(RUN_AT, memory.BlobStore(memory.MEMORY_URL), email_sender.send_report,
                         email_sender.send_failure_notice, function_app.ping_heartbeat, run_id="inv-1")

    assert "SECRET" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__context__ is None
    formatter = logging.Formatter()
    for record in caplog.records:
        assert "SECRET" not in formatter.format(record)
        assert record.exc_info is None
    assert len(sent) == 1
    assert "SECRET" not in json.dumps(sent[0]["content"])
    assert "REDACTED" in sent[0]["content"]["html"]
    [logged], [line] = summaries(caplog)
    assert "SECRET" not in line
    assert logged["outcome"] == "failure_notice_delivered"
    assert len(posts) == 1 and "SECRET" not in json.dumps(posts[0])
    assert (list(root.filters), [list(h.filters) for h in root.handlers]) == filters_before
