"""Tests for the AI second look: classify.py, and how pipeline, memory,
report and function_app use it. Cloudflare, Azure OpenAI and the managed
identity are fakes; nothing leaves this machine."""

import datetime as dt
import io
import json
import threading

import openpyxl
import pytest
import requests

import classify
import function_app
import memory
import pipeline
import report

UTC = dt.timezone.utc
RUN_AT = dt.datetime(2026, 10, 1, 6, 0, tzinfo=UTC)
NANO_ENDPOINT = "https://etenders-ai.openai.azure.com"


def tender(ocid, title="Business intelligence developer"):
    return {"ocid": ocid, "title": title, "buyer": "Dept", "category": "services",
            "description": "Build Qlik dashboards"}


class Reply:
    def __init__(self, status=200, body=None):
        self.status_code, self.body, self.headers = status, body, {}

    def json(self):
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def cf_reply(text):
    return Reply(200, {"success": True, "result": {"response": text}})


def nano_reply(text, prompt_tokens=250, completion_tokens=10):
    return Reply(200, {"choices": [{"message": {"content": text}}],
                       "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}})


class Http:
    """Fake requests: cloudflare and nano are callables (body -> Reply or exception)."""

    def __init__(self, cloudflare=lambda body: cf_reply("YES"), nano=lambda body: nano_reply("YES")):
        self.cloudflare, self.nano = cloudflare, nano
        self.calls, self.lock = [], threading.Lock()

    def post(self, url, headers=None, json=None, timeout=None):
        kind = "cloudflare" if "api.cloudflare.com" in url else "nano"
        with self.lock:
            self.calls.append((kind, url, headers))
        result = (self.cloudflare if kind == "cloudflare" else self.nano)(json)
        if isinstance(result, Exception):
            raise result
        return result

    def count(self, kind):
        return sum(1 for c in self.calls if c[0] == kind)


def token_ok():
    return "mi-token-for-openai", "ok"


@pytest.fixture
def both(monkeypatch):
    monkeypatch.setenv("CF_ACCOUNT_ID", "acct123")
    monkeypatch.setenv("CF_API_TOKEN", "cf-secret-token-value")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", NANO_ENDPOINT)


def run_classify(http, items, **kw):
    return classify.classify(items, http=http, get_token=token_ok, **kw)


# --- classify.py --------------------------------------------------------------

def test_it_is_off_unless_llm_enabled_is_true(monkeypatch):
    assert classify.enabled() is False
    monkeypatch.setenv("LLM_ENABLED", "yes")
    assert classify.enabled() is False
    monkeypatch.setenv("LLM_ENABLED", "True")
    assert classify.enabled() is True


def test_llama_answers_and_nano_is_never_called(both):
    http = Http(cloudflare=lambda body: cf_reply("NO" if "Fence" in body["messages"][0]["content"] else "Yes."))
    verdicts, stats = run_classify(http, [tender("a"), tender("b", title="Fence repairs")])
    assert verdicts == {"a": {"v": True, "by": "llama"}, "b": {"v": False, "by": "llama"}}
    assert http.count("nano") == 0
    assert stats["llama"] == 2 and stats["nano"] == 0 and stats["unclassified"] == 0
    assert stats["flagged"] == 1


def test_the_cloudflare_call_carries_the_model_and_the_tender_text(both):
    http = Http()
    run_classify(http, [tender("a")])
    kind, url, headers = http.calls[0]
    assert url.endswith(f"/accounts/acct123/ai/run/{classify.CF_MODEL}")
    assert headers == {"Authorization": "Bearer cf-secret-token-value"}


def test_when_the_free_allowance_runs_out_the_rest_go_to_nano_with_the_identity_token(both):
    http = Http(cloudflare=lambda body: Reply(429, {"errors": ["daily limit"]}))
    verdicts, stats = run_classify(http, [tender(str(i)) for i in range(10)])
    assert all(v == {"v": True, "by": "nano"} for v in verdicts.values()) and len(verdicts) == 10
    assert stats["cf_off"] == "http_429"
    # One 429 switches Cloudflare off; at most the calls already in flight still go out.
    assert http.count("cloudflare") <= classify.WORKERS
    for kind, url, headers in http.calls:
        if kind == "nano":
            assert url.startswith(NANO_ENDPOINT + "/openai/deployments/gpt-5-nano/chat/completions")
            assert headers == {"Authorization": "Bearer mi-token-for-openai"}  # no api-key


def test_network_errors_switch_cloudflare_off_after_three(both):
    http = Http(cloudflare=lambda body: requests.ConnectionError("boom"))
    verdicts, stats = run_classify(http, [tender(str(i)) for i in range(20)])
    assert len(verdicts) == 20 and stats["nano"] == 20
    assert stats["cf_off"] == "ConnectionError"
    assert http.count("cloudflare") < 3 + classify.WORKERS


def test_an_unclear_answer_is_asked_again_of_nano(both):
    http = Http(cloudflare=lambda body: cf_reply("Maybe"), nano=lambda body: nano_reply("NO"))
    verdicts, _ = run_classify(http, [tender("a")])
    assert verdicts == {"a": {"v": False, "by": "nano"}}


def test_nano_stops_at_the_cost_cap_and_the_rest_stay_unclassified(both, monkeypatch):
    monkeypatch.setattr(classify, "WORKERS", 1)  # one at a time, so the count is exact
    http = Http(cloudflare=lambda body: Reply(429, {}),
                nano=lambda body: nano_reply("YES", prompt_tokens=0, completion_tokens=10_000))
    # Each call costs 10,000 * R6.40 / 1M = R0.064, so the cap of R0.20 allows 4 calls.
    verdicts, stats = run_classify(http, [tender(str(i)) for i in range(10)])
    assert http.count("nano") == 4 and len(verdicts) == 4
    assert stats["unclassified"] == 6 and stats["nano_zar"] == pytest.approx(0.256)


def test_without_settings_no_call_is_made():
    http = Http()
    verdicts, stats = run_classify(http, [tender("a")])
    assert verdicts == {} and http.calls == []
    assert stats["cf_off"] == "not_configured" and stats["nano_off"] == "not_configured"


def test_a_non_https_endpoint_is_not_used(monkeypatch):
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "http://etenders-ai.openai.azure.com")
    http = Http()
    verdicts, stats = run_classify(http, [tender("a")])
    assert verdicts == {} and http.calls == [] and stats["nano_off"] == "not_configured"


def test_without_an_identity_token_nano_is_skipped(both):
    http = Http(cloudflare=lambda body: Reply(500, {}))
    verdicts, stats = classify.classify([tender("a")], http=http,
                                        get_token=lambda: (None, "no_identity"))
    assert verdicts == {} and http.count("nano") == 0
    assert stats["nano_off"] == "token:no_identity"


def test_nothing_starts_after_the_time_budget(both):
    http = Http()
    verdicts, stats = run_classify(http, [tender("a"), tender("b")], budget_s=0)
    assert verdicts == {} and http.calls == [] and stats["unclassified"] == 2


def test_a_broken_reply_is_never_raised(both):
    http = Http(cloudflare=lambda body: Reply(200, ValueError("not json")),
                nano=lambda body: Reply(200, {"choices": []}))
    verdicts, stats = run_classify(http, [tender("a")])
    assert verdicts == {} and stats["unclassified"] == 1


# --- pipeline -----------------------------------------------------------------

def release(ocid, title, closing="2026-12-20T11:00:00+02:00", consulting=False):
    t = {"id": f"T-{ocid}", "title": title, "description": f"About {title}",
         "mainProcurementCategory": "services",
         "additionalProcurementCategories": ["consultingServices"] if consulting else [],
         "tenderPeriod": {"endDate": closing}}
    return {"ocid": ocid, "tender": t}


RELEASES = [
    release("kw", "Data warehouse support"),                  # keyword hit: already kept
    release("cons", "Panel of advisors", consulting=True),    # consulting: already kept
    release("plain", "Qlik developers"),                      # no keyword: a candidate
    release("plain", "Qlik developers"),                      # duplicate
    release("closed", "Qlik developers", closing="2026-09-01T11:00:00+02:00"),
]


def test_candidates_are_the_open_tenders_the_rules_leave_out():
    out = pipeline.ai_candidates(RELEASES, RUN_AT)
    assert [c["ocid"] for c in out] == ["plain"]
    assert out[0]["description"] == "About Qlik developers" and out[0]["title"] == "Qlik developers"


def test_an_ai_flagged_tender_is_kept_with_its_label():
    d = pipeline.build_digest(RELEASES, [], None, "first_run", "off", RUN_AT,
                              ai_flags={"plain": "Llama", "kw": "nano"})
    flags = dict(zip(d.tenders["ocid"], d.tenders["ai_flag"]))
    assert flags == {"kw": None, "cons": None, "plain": "Llama"}  # keywords win: no label on "kw"


def test_without_ai_flags_the_digest_is_unchanged():
    d = pipeline.build_digest(RELEASES, [], None, "first_run", "off", RUN_AT)
    assert sorted(d.tenders["ocid"]) == ["cons", "kw"] and d.tenders["ai_flag"].isna().all()


# --- memory -------------------------------------------------------------------

def test_verdicts_are_saved_dated_and_forgotten_after_two_weeks(tmp_path):
    store = memory.FileStore(str(tmp_path / "m.json"))
    d = pipeline.build_digest(RELEASES, [], None, "first_run", "off", RUN_AT)
    saved = memory.after_delivery(None, d, [], RUN_AT, {"plain": {"v": True, "by": "llama"}})
    assert saved["ai"] == {"plain": {"v": True, "by": "llama", "on": dt.date(2026, 10, 1)}}
    assert memory.save(store, saved, None) == "ok"

    loaded, status, _ = memory.load(store, RUN_AT)
    assert status == "ok" and loaded["ai"] == saved["ai"]

    later = RUN_AT + dt.timedelta(days=memory.AI_KEEP_DAYS + 1)
    assert memory.after_delivery(loaded, d, [], later)["ai"] == {}


def test_an_old_memory_without_verdicts_still_loads(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": 1, "tracking_since": "2026-09-20",
                                "resolved_through": "2026-09-25", "sent": {}, "days": {}}))
    loaded, status, _ = memory.load(memory.FileStore(str(path)), RUN_AT)
    assert status == "ok" and loaded["ai"] == {}


def test_bad_verdicts_are_dropped_without_losing_the_memory(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"schema": 1, "tracking_since": "2026-09-20",
                                "resolved_through": "2026-09-25", "sent": {}, "days": {},
                                "ai": {"x": {"v": "yes", "by": "gpt-9", "on": "soon"}}}))
    loaded, status, _ = memory.load(memory.FileStore(str(path)), RUN_AT)
    assert status == "ok" and loaded["ai"] == {}


def test_the_identity_token_names_the_resource_it_is_for(monkeypatch):
    monkeypatch.setenv("IDENTITY_ENDPOINT", "http://localhost:8081/msi/token")
    monkeypatch.setenv("IDENTITY_HEADER", "identity-header-value")
    seen = {}

    def fake_get(url, params, headers, timeout):
        seen.update(params)
        return Reply(200, {"access_token": "t"})

    monkeypatch.setattr(memory.requests, "get", fake_get)
    assert memory.mi_token(classify.NANO_RESOURCE) == ("t", "ok")
    assert seen["resource"] == "https://cognitiveservices.azure.com"
    memory.mi_token()
    assert seen["resource"] == "https://storage.azure.com/"


# --- report -------------------------------------------------------------------

def test_the_email_and_xlsx_show_which_model_flagged_a_tender():
    d = pipeline.build_digest(RELEASES, [], None, "first_run", "off", RUN_AT,
                              ai_flags={"plain": "nano"})
    body = report.render_html(d)
    assert "AI-flagged (nano)" in body and "less accurate" in body
    ws = openpyxl.load_workbook(io.BytesIO(report.build_xlsx(d)))["Tenders"]
    headers = [c.value for c in ws[1]]
    values = [ws.cell(r, headers.index("ai_flag") + 1).value for r in range(2, ws.max_row + 1)]
    assert {v for v in values if v} == {"nano"}


def test_no_ai_note_when_nothing_was_ai_flagged():
    d = pipeline.build_digest(RELEASES, [], None, "first_run", "off", RUN_AT)
    assert "AI-flagged" not in report.render_html(d)


def test_the_ai_label_colour_meets_wcag_aa_contrast():
    def lum(hex_colour):
        ch = [int(hex_colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in ch]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    hi, lo = sorted((lum(report._PAPER), lum(report._AI_BG)), reverse=True)
    assert (hi + 0.05) / (lo + 0.05) >= 4.5


# --- function_app -------------------------------------------------------------

class Mailer:
    def __init__(self):
        self.digests = []

    def send_report(self, d):
        self.digests.append(d)
        return {"html_bytes": 1, "xlsx_bytes": 1}

    def send_failure_notice(self, text):
        raise AssertionError(text)


def test_a_run_uses_remembered_verdicts_and_saves_new_ones(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "true")
    monkeypatch.setattr(pipeline, "fetch_releases", lambda a, b: (
        RELEASES + [release("old", "Qlik Sense upgrade")], []))
    asked = []

    def fake_classify(items, budget_s):
        asked.extend(t["ocid"] for t in items)
        return {"plain": {"v": True, "by": "llama"}}, {"llama": 1}

    monkeypatch.setattr(classify, "classify", fake_classify)
    store = memory.FileStore(str(tmp_path / "m.json"))
    memory.save(store, {"schema": 1, "tracking_since": dt.date(2026, 9, 20),
                        "resolved_through": dt.date(2026, 9, 25), "sent": {}, "days": {},
                        "ai": {"old": {"v": True, "by": "nano", "on": dt.date(2026, 9, 30)}}}, None)
    mailer = Mailer()
    summary = function_app.run(RUN_AT, store, mailer.send_report, mailer.send_failure_notice,
                               lambda url, body: "ok")

    assert asked == ["plain"]  # "old" was remembered, so it was not asked again
    flags = dict(zip(mailer.digests[0].tenders["ocid"], mailer.digests[0].tenders["ai_flag"]))
    assert flags["plain"] == "Llama" and flags["old"] == "nano"
    assert summary["ai"] == {"remembered": 1, "llama": 1}
    saved, _, _ = memory.load(store, RUN_AT)
    assert set(saved["ai"]) == {"old", "plain"}


def test_with_the_switch_off_no_ai_step_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "fetch_releases", lambda a, b: (RELEASES, []))
    monkeypatch.setattr(classify, "classify", lambda *a, **k: pytest.fail("classify was called"))
    mailer = Mailer()
    summary = function_app.run(RUN_AT, memory.FileStore(str(tmp_path / "m.json")),
                               mailer.send_report, mailer.send_failure_notice, lambda u, b: "ok")
    assert summary["ai"] == "off"


def test_the_cloudflare_token_is_redacted(monkeypatch):
    monkeypatch.setenv("CF_API_TOKEN", "cf-secret-token-value")
    assert "cf-secret" not in function_app.redact("failed with cf-secret-token-value in it")


# --- the configured day values ------------------------------------------------

@pytest.mark.real_tuning
def test_the_window_has_lookback_plus_one_days_with_the_configured_values():
    first, last = pipeline.window_for(RUN_AT)
    assert (last - first).days == pipeline.LOOKBACK_DAYS
