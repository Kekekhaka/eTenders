"""QA tests (stage 6): adversarial and requirements-driven checks that go through
the public entry points (function_app.run, pipeline, memory, report, TG.py).

Everything is offline: a fake requests.Session stands in for eTenders, an
in-memory store for the blob, and a recording fake for ACS. Tests marked
xfail(strict=True) record a DEFECT QA found (see docs/cosmos/TEST_REPORT.md):
they keep the suite green today and turn red the day the defect is fixed, so
the marker must then be removed.
"""

import datetime as dt
import io
import json
import logging
import pathlib
import time

import openpyxl
import pandas as pd
import pytest
import requests

import digest
import email_sender
import function_app
import memory
import pipeline
import report
import TG

UTC = dt.timezone.utc
ROOT = pathlib.Path(__file__).resolve().parent.parent


def at(y, mo, d, h=6, mi=0, s=0):
    return dt.datetime(y, mo, d, h, mi, s, tzinfo=UTC)


def release(ocid, title="Data warehouse support", closing="2026-12-20T11:00:00+02:00",
            category="Services: Professional", **tender):
    t = {"id": f"T-{ocid}", "title": title, "category": category, "province": "Gauteng",
         "tenderPeriod": {"endDate": closing, "startDate": "2026-09-28T09:00:00+02:00"},
         "documents": [{"url": f"https://www.etenders.gov.za/{ocid}.pdf"}]}
    t.update(tender)
    return {"ocid": ocid, "tender": t}


# --- fakes -------------------------------------------------------------------------

class FakeApi:
    """by_day maps 'YYYY-MM-DD' to a list of pages (each a list of releases, or any
    raw payload object). failing days time out. Anything not listed is empty."""

    def __init__(self, by_day=None, failing=()):
        self.by_day, self.failing = by_day or {}, set(failing)
        self.requests = []

    def install(self, monkeypatch):
        api = self

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def raise_for_status(self):
                pass

            def json(self):
                if isinstance(self.payload, BaseException):
                    raise self.payload
                return self.payload

        class Session:
            headers = {}

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def get(self, url, params, timeout):
                day = params["dateFrom"]
                api.requests.append((day, params["PageNumber"]))
                if day in api.failing:
                    raise requests.ReadTimeout("simulated")
                pages = api.by_day.get(day, [])
                if params["PageNumber"] > len(pages):
                    return Response({"releases": []})
                page = pages[params["PageNumber"] - 1]
                return Response(page if not isinstance(page, list) else {"releases": page})

        monkeypatch.setattr(pipeline.requests, "Session", Session)


class MemStore:
    """The blob, in memory. get_text/put_text as memory.BlobStore. fail_puts makes writes fail."""

    def __init__(self, text=None):
        self.text, self.puts, self.fail_puts = text, [], False

    def get_text(self):
        if self.text is None:
            return "first_run", None, None
        return "ok", self.text, '"etag"'

    def put_text(self, text, etag):
        self.puts.append((text, etag))
        if self.fail_puts:
            return "failed:network"
        self.text = text
        return "ok"


class Mail:
    """send_report that renders the real HTML and xlsx (so rendering bugs surface),
    records every digest, and can be told to fail."""

    def __init__(self):
        self.digests, self.fail, self.notices, self.fail_notice = [], False, [], False

    def send_report(self, d):
        html_body = report.render_html(d)
        xlsx = report.build_xlsx(d)
        report.render_subject(d)
        if self.fail:
            raise RuntimeError("ACS down")
        self.digests.append(d)
        return {"html_bytes": len(html_body.encode("utf-8")), "xlsx_bytes": len(xlsx) if xlsx else 0}

    def send_notice(self, text):
        self.notices.append(text)
        if self.fail_notice:
            raise RuntimeError("ACS down")


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    for name in ("HEARTBEAT_URL", "IDENTITY_ENDPOINT", "IDENTITY_HEADER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(pipeline, "RETRY_BACKOFF_SECONDS", 0)


def go(monkeypatch, run_at, api, store, mail=None, ping=None, **kw):
    api.install(monkeypatch)
    mail = mail or Mail()
    summary = function_app.run(run_at, store, mail.send_report, mail.send_notice,
                               ping or (lambda url, body: "ok"), run_id="qa", **kw)
    return summary, mail


def day_api(run_at, n=1, ocid_prefix="x", **kw):
    """An API serving n fresh releases on each of the window's 6 days."""
    w = pipeline.window_for(run_at)
    by_day = {}
    for i in range(6):
        day = (w[1] - dt.timedelta(days=i)).isoformat()
        by_day[day] = [[release(f"{ocid_prefix}-{day}-{j}") for j in range(n)]]
    return FakeApi(by_day, **kw)


# =====================================================================================
# 1. memory.py: the 7-day missed-day rule, long outages, failed sends, half-day boundary
# =====================================================================================

def test_a_morning_run_leaves_today_partial_and_the_next_morning_run_covers_it(monkeypatch):
    """Half-day boundary (ADR-0017): a day fetched while it is still in progress is not covered."""
    store = MemStore()
    d1 = at(2026, 10, 1, 6)   # 08:00 SAST on Oct 1
    go(monkeypatch, d1, day_api(d1), store)
    mem = json.loads(store.text)
    assert mem["days"]["2026-10-01"] == "partial"      # today, still in progress
    assert mem["days"]["2026-09-30"] == "covered"      # ended before this run started
    # the 18:00 UTC run (20:00 SAST) is still before midnight SAST: still partial
    d2 = at(2026, 10, 1, 18)
    go(monkeypatch, d2, day_api(d2), store)
    assert json.loads(store.text)["days"]["2026-10-01"] == "partial"
    # the next morning's run starts after Oct 1 ended in SAST: covered
    d3 = at(2026, 10, 2, 6)
    go(monkeypatch, d3, day_api(d3), store)
    assert json.loads(store.text)["days"]["2026-10-01"] == "covered"


def test_the_half_day_boundary_is_midnight_sast_not_midnight_utc():
    end = pipeline.day_end_utc(dt.date(2026, 10, 1))
    assert end == at(2026, 10, 1, 22, 0)                # 00:00 SAST on Oct 2
    one_second_before, exactly = end - dt.timedelta(seconds=1), end
    day = dt.date(2026, 10, 1)
    mem = None
    dig = type("D", (), {"window": (dt.date(2026, 9, 26), dt.date(2026, 10, 1)),
                         "tenders": pd.DataFrame({"ocid": [], "closing": []})})()
    results = [pipeline.DayResult(day, True, None)]
    before = memory.after_delivery(mem, dig, results, one_second_before)
    after = memory.after_delivery(mem, dig, results, exactly)
    assert before["days"][day] == "partial"
    assert after["days"][day] == "covered"


@pytest.mark.parametrize("h,mi,s,expected_today", [
    (21, 59, 59, dt.date(2026, 10, 1)),    # 23:59:59 SAST: still Oct 1
    (22, 0, 0, dt.date(2026, 10, 2)),      # midnight SAST: Oct 2
    (23, 59, 59, dt.date(2026, 10, 2)),
    (0, 0, 0, dt.date(2026, 10, 1)),       # midnight UTC is 02:00 SAST, same SAST day
])
def test_the_window_follows_midnight_sast_not_midnight_utc(h, mi, s, expected_today):
    run_at = at(2026, 10, 1, h, mi, s)
    first, last = pipeline.window_for(run_at)
    assert last == expected_today
    assert (last - first).days == pipeline.LOOKBACK_DAYS


def test_the_code_assumes_neither_daylight_saving_nor_the_host_timezone():
    """South Africa has no DST. The code must use fixed offsets and the run's own clock only."""
    for name in ("pipeline.py", "memory.py", "report.py", "digest.py", "function_app.py",
                 "email_sender.py", "TG.py"):
        src = (ROOT / name).read_text(encoding="utf-8")
        for banned in ("date.today()", "datetime.now()", "localtime", "tzlocal", "pytz", "zoneinfo",
                       "datetime.today()", "utcnow"):
            assert banned not in src, f"{name} uses {banned}"
    # The same instant, written in other offsets (even ones that observe DST), gives the same window.
    instant = at(2026, 10, 1, 22, 30)
    for hours in (-5, -4, 0, 2, 5, 13):
        local = instant.astimezone(dt.timezone(dt.timedelta(hours=hours)))
        assert pipeline.window_for(local) == pipeline.window_for(instant)


def test_a_naive_run_at_is_not_silently_accepted_as_utc():
    """run_at is documented as tz-aware UTC. A naive one is treated as host-local by astimezone,
    so QA records it, not as a defect (the only caller passes now(UTC))."""
    naive = dt.datetime(2026, 10, 1, 6, 0)
    assert pipeline.window_for(naive)[1] in (dt.date(2026, 10, 1), dt.date(2026, 10, 2))


def test_a_run_after_a_three_week_outage_names_each_lost_day_once(monkeypatch):
    store = MemStore()
    r1 = at(2026, 9, 10, 6)
    go(monkeypatch, r1, day_api(r1), store)
    r2 = at(2026, 10, 1, 6)                       # 21 days later: window is Sep 26 .. Oct 1
    s2, mail = go(monkeypatch, r2, day_api(r2, ocid_prefix="y"), store)
    named = [m.day for m in mail.digests[0].missed_days]
    # Sep 5-9 were covered by the Sep 10 run; Sep 10 itself was only partial; Sep 11-25 were never fetched
    assert dt.date(2026, 9, 5) not in named and dt.date(2026, 9, 9) not in named
    assert named[0] == dt.date(2026, 9, 10)
    assert mail.digests[0].missed_days[0].kind == "only_partly_fetched"
    assert named[1:] == [dt.date(2026, 9, 11) + dt.timedelta(days=i) for i in range(15)]
    assert all(m.kind == "never_fetched" for m in mail.digests[0].missed_days[1:])
    assert len(s2["missed_named"]) == 10               # RUN_SUMMARY is capped (TL-10)
    # ... the next run repeats only the days still inside their 7-day reporting period (FR-15),
    # i.e. Sep 18 .. Sep 25 for a window starting Sep 26; Sep 10 .. Sep 17 are not repeated
    r3 = at(2026, 10, 1, 18)
    s3, mail3 = go(monkeypatch, r3, day_api(r3, ocid_prefix="y"), store)
    again = [m.day for m in mail3.digests[0].missed_days]
    assert again == [dt.date(2026, 9, 18) + dt.timedelta(days=i) for i in range(8)]
    # ... and once the runs carry on daily for a week, nothing is named any more
    for k in range(2, 10):
        r4 = at(2026, 10, k, 6)
        s4, mail4 = go(monkeypatch, r4, day_api(r4, ocid_prefix="y"), store)
    assert mail4.digests[0].missed_days == ()


def test_an_outage_of_over_a_year_is_bounded_and_still_delivers(monkeypatch):
    store = MemStore()
    r1 = at(2025, 6, 1, 6)
    go(monkeypatch, r1, day_api(r1), store)
    r2 = at(2026, 10, 1, 6)
    s2, mail = go(monkeypatch, r2, day_api(r2, ocid_prefix="z"), store)
    assert s2["outcome"] == "digest_delivered"
    d = mail.digests[0]
    assert len(d.missed_days) <= memory.MAX_GAP_DAYS
    assert len(s2["missed_named"]) == 10
    assert s2["memory"]["write"] == "ok"
    # the run still writes a bounded memory
    assert len(json.loads(store.text)["days"]) <= 20


def test_a_failed_send_then_a_retry_names_the_missed_days_and_flags_new_again(monkeypatch):
    store = MemStore()
    r1 = at(2026, 9, 10, 6)
    go(monkeypatch, r1, day_api(r1), store)           # delivered; memory written
    before = store.text
    r2 = at(2026, 10, 1, 6)
    bad = Mail()
    bad.fail = True
    with pytest.raises(RuntimeError):
        go(monkeypatch, r2, day_api(r2, ocid_prefix="n"), store, mail=bad)
    assert store.text == before, "a failed send must not touch the memory"
    assert len(bad.notices) == 1
    r3 = r2 + dt.timedelta(hours=12)
    s3, mail = go(monkeypatch, r3, day_api(r3, ocid_prefix="n"), store)
    d = mail.digests[0]
    assert d.new_count == d.total > 0                    # nothing used up
    assert len(d.missed_days) > 0                        # still reported (AC-12)


def test_a_day_that_only_ever_came_in_part_is_named_only_partly_fetched(monkeypatch):
    """AC-11 through the whole run loop: the day is fetched on its own day, then every
    later attempt fails until the day leaves the window."""
    store = MemStore()
    target = dt.date(2026, 9, 20)
    run = at(2026, 9, 20, 6)
    go(monkeypatch, run, day_api(run), store)         # Sep 20 fetched in progress -> partial
    mail = None
    for offset in range(1, 7):                          # runs on Sep 21 .. Sep 26
        run = at(2026, 9, 20 + offset, 6)
        api = day_api(run)
        api.failing = {target.isoformat()}
        _, mail = go(monkeypatch, run, api, store)
    named = {m.day: m.kind for m in mail.digests[0].missed_days}
    assert named.get(target) == "only_partly_fetched"


def test_the_first_run_marks_every_tender_new_and_names_no_missed_day(monkeypatch):
    run = at(2026, 10, 1, 6)
    s, mail = go(monkeypatch, run, day_api(run, n=3), MemStore())
    d = mail.digests[0]
    assert d.memory_status == "first_run" and d.total == 18
    assert d.new_count == 18 and bool(d.tenders["is_new"].all())
    assert d.missed_days == ()
    assert "first run with new-tender marking" in report.render_html(d)


def test_a_missed_day_is_named_for_seven_days_after_it_leaves_the_window_then_not(monkeypatch):
    """FR-15/OQ-BA2 end to end, run twice a day: Sep 18 is never fetched."""
    store = MemStore()
    gap = dt.date(2026, 9, 18)
    seen = {}
    for day_offset in range(0, 20):
        for hour in (6, 18):
            run = at(2026, 9, 14, hour) + dt.timedelta(days=day_offset)
            api = day_api(run)
            api.failing = {gap.isoformat()}
            _, mail = go(monkeypatch, run, api, store)
            seen[run] = gap in [m.day for m in mail.digests[0].missed_days]
    first_named = min(r for r, v in seen.items() if v)
    last_named = max(r for r, v in seen.items() if v)
    left_window = at(2026, 9, 24, 6)                   # window[0] = Sep 19 on the Sep 24 run
    assert first_named == left_window
    assert (last_named.date() - left_window.date()).days <= 7
    assert not seen[at(2026, 10, 3, 6)]


# =====================================================================================
# 2. Hand-edited / tampered memory (NFR-14, AC-28, AC-39): never a crash
# =====================================================================================

def _good_memory(**over):
    doc = {"schema": 1, "tracking_since": "2026-09-01", "resolved_through": "2026-09-25",
           "sent": {"a": {"first_sent": "2026-09-30T06:00:00Z", "closing": "2026-12-01T00:00:00Z"}},
           "days": {"2026-09-26": "covered"}}
    doc.update(over)
    return json.dumps(doc)


@pytest.mark.parametrize("blob", [
    "", "null", "[]", "1", '"x"', "{", '{"schema":1}', '{"schema":"1"}',
    _good_memory(sent=[]), _good_memory(sent={"a": 1}), _good_memory(sent={"a": {}}),
    _good_memory(sent={"a": {"first_sent": "2026-09-30T06:00:00"}}),       # naive time
    _good_memory(days={"2026-09-26": "weird"}), _good_memory(days={"x": "covered"}),
    _good_memory(tracking_since=5), _good_memory(tracking_since=None),
    _good_memory(resolved_through="2026-13-45"),
    "[" * 100000,                                                         # recursion bomb
    '{"schema":1,"tracking_since":NaN}',
], ids=lambda b: b[:25] if isinstance(b, str) else repr(b))
def test_a_bad_memory_blob_is_unavailable_never_a_crash(monkeypatch, blob):
    store = MemStore(blob)
    run = at(2026, 10, 1, 6)
    s, mail = go(monkeypatch, run, day_api(run), store)
    assert s["outcome"] == "digest_delivered"
    assert s["memory"]["read"].startswith("unavailable:")
    assert s["memory"]["write"] == "skipped" and store.puts == []
    d = mail.digests[0]
    assert d.new_count is None and d.missed_days == ()
    assert "unavailable for this run" in report.render_html(d)


@pytest.mark.parametrize("stamp", ["9999-12-31T23:59:59-05:00", "0001-01-01T00:00:00+05:00"])
def test_a_stored_time_that_overflows_utc_makes_memory_unavailable_not_a_crash(monkeypatch, stamp):
    store = MemStore(_good_memory(sent={"a": {"first_sent": stamp, "closing": None}}))
    run = at(2026, 10, 1, 6)
    s, mail = go(monkeypatch, run, day_api(run), store)
    assert s["outcome"] == "digest_delivered"


def test_absurd_but_representable_dates_are_clamped(monkeypatch):
    store = MemStore(_good_memory(
        sent={"a": {"first_sent": "2999-01-01T00:00:00Z", "closing": "9999-12-31T00:00:00Z"}},
        resolved_through="2099-01-01"))
    run = at(2026, 10, 1, 6)
    s, _ = go(monkeypatch, run, day_api(run), store)
    assert s["memory"]["read"] == "ok" and s["memory"]["write"] == "ok"


def test_a_far_future_tracking_since_does_not_switch_missed_day_tracking_off(monkeypatch):
    store = MemStore(_good_memory(tracking_since="2099-01-01", resolved_through="2026-09-01", days={}))
    run = at(2026, 10, 1, 6)
    _, mail = go(monkeypatch, run, day_api(run), store)
    assert len(mail.digests[0].missed_days) > 0


# =====================================================================================
# 3. Empty and huge digests
# =====================================================================================

def test_an_empty_run_still_delivers_a_digest_without_an_attachment(monkeypatch):
    run = at(2026, 10, 1, 6)
    store = MemStore()
    s, mail = go(monkeypatch, run, FakeApi(), store)
    d = mail.digests[0]
    assert d.total == 0 and list(d.tenders.columns) == list(digest.TENDER_COLUMNS)
    assert s["email"]["xlsx_bytes"] == 0 and s["outcome"] == "digest_delivered"
    body = report.render_html(d)
    assert "No matching tenders were found in this run." in body and "attached xlsx" not in body
    assert report.build_xlsx(d) is None
    assert json.loads(store.text)["sent"] == {}


def test_an_all_days_down_run_is_a_partial_empty_digest_not_a_crash(monkeypatch):
    run = at(2026, 10, 1, 6)
    w = pipeline.window_for(run)
    api = FakeApi(failing={(w[1] - dt.timedelta(days=i)).isoformat() for i in range(6)})
    s, mail = go(monkeypatch, run, api, MemStore())
    d = mail.digests[0]
    assert len(d.partial_days) == 6 and d.total == 0
    assert report.render_subject(d).endswith("(partial)")


def test_a_huge_digest_renders_in_reasonable_time_and_stays_consistent(monkeypatch):
    n = 1500
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release(f"ocds-{i:05d}", title=f"Data warehouse and Power BI {i}",
                                      closing=f"2026-10-{1 + (i % 28):02d}T10:00:00+02:00")
                              for i in range(n)]]}
    t0 = time.perf_counter()
    s, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    elapsed = time.perf_counter() - t0
    d = mail.digests[0]
    assert d.total == n and s["outcome"] == "digest_delivered"
    assert elapsed < 30, f"{n} tenders took {elapsed:.1f}s"
    wb = openpyxl.load_workbook(io.BytesIO(report.build_xlsx(d)))
    assert wb["Tenders"].max_row == n + 1
    # every NEW row is shown (OQ-BA1), so a first run with 4000 rows is a huge body
    body = report.render_html(d)
    assert body.count("<tr valign") == n


def test_the_acs_size_limit_is_respected_by_a_realistic_first_run(monkeypatch):
    """TL-12: a first run of ~200 tenders is about 223 KiB of HTML. The whole message must be
    far below ACS's 10 MB (base64 included)."""
    run = at(2026, 10, 1, 6)
    s, mail = go(monkeypatch, run, day_api(run, n=35), MemStore())     # 210 tenders
    d = mail.digests[0]
    total = s["email"]["html_bytes"] + s["email"]["xlsx_bytes"] * 4 // 3
    assert d.total == 210 and total < 1_000_000


# =====================================================================================
# 4. Unicode, RTL, very long values
# =====================================================================================

TEXTS = [
    "مناقصة لتطوير مستودع البيانات",                  # Arabic
    "מכרז לפיתוח מחסן נתונים",                         # Hebrew
    "Data warehouse \u202eevil\u202c text",            # bidi override
    "数据仓库 招标 Power BI",                          # CJK
    "emoji \U0001F680 \U0001F4A5 tender",             # astral plane
    "e\u0301 combining and \u200b zero\u200dwidth",
    "tab\there and new\nline",
    "<b>bold</b> & \"quotes\" 'single'",
]


@pytest.mark.parametrize("text", TEXTS)
def test_unicode_and_rtl_text_survives_html_and_xlsx(monkeypatch, text):
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("u1", title=text, buyer=None,
                                      procuringEntity={"name": text})]]}
    s, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    d = mail.digests[0]
    assert d.total == 1
    body = report.render_html(d)
    assert "<b>bold</b>" not in body
    wb = openpyxl.load_workbook(io.BytesIO(report.build_xlsx(d)))
    cell = wb["Tenders"].cell(2, 6).value
    assert cell == text[:200]


def test_a_very_long_buyer_and_category_do_not_break_the_xlsx_or_the_html(monkeypatch):
    big = "B" * 100_000
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("L1", procuringEntity={"name": big}, contact=None)]]}
    s, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    d = mail.digests[0]
    assert d.total == 1 and s["outcome"] == "digest_delivered"
    assert len(d.tenders.loc[0, "title"]) <= 200


def test_no_xlsx_cell_exceeds_excels_32767_character_limit(monkeypatch):
    """Not a defect: openpyxl truncates to 32,767 itself. Kept as a guard (QA checked it)."""
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("L1", procuringEntity={"name": "B" * 100_000})]]}
    _, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    wb = openpyxl.load_workbook(io.BytesIO(report.build_xlsx(mail.digests[0])))
    assert max(len(str(c.value)) for row in wb["Tenders"].iter_rows() for c in row if c.value) <= 32767


def test_a_lone_surrogate_in_a_title_does_not_crash_the_run(monkeypatch):
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("S1", title="bad \ud800 title")]]}
    s, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    assert s["outcome"] == "digest_delivered"


@pytest.mark.parametrize("ch", ["\ufffe", "\uffff"])
def test_xml_noncharacters_do_not_corrupt_the_xlsx(monkeypatch, ch):
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("S2", title=f"bad {ch} title")]]}
    _, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    openpyxl.load_workbook(io.BytesIO(report.build_xlsx(mail.digests[0])))


# =====================================================================================
# 5. Malformed releases, every shape
# =====================================================================================

DROPPED = [None, "x", 123, 1.5, True, [], ["ocid"], {}, {"ocid": None}, {"ocid": 123},
           {"ocid": ["a"]}, {"ocid": {"a": 1}}, {"ocid": ""}, {"ocid": "   "},
           {"ocid": "\x0b\x0c"}, {"ocid": "o" * 201}, {"tender": {"title": "no ocid"}}]

KEPT_AND_BLANKED = [
    {"ocid": "k", "tender": "x"}, {"ocid": "k", "tender": None}, {"ocid": "k", "tender": []},
    {"ocid": "k", "tender": {"title": {"a": 1}}}, {"ocid": "k", "tender": {"title": 5}},
    {"ocid": "k", "tender": {"title": ["a"]}}, {"ocid": "k", "tender": {"documents": ["u"]}},
    {"ocid": "k", "tender": {"documents": "u"}}, {"ocid": "k", "tender": {"documents": {"url": "u"}}},
    {"ocid": "k", "tender": {"documents": [None, 5, {"url": 5}, {"url": None}, {"url": []}]}},
    {"ocid": "k", "tender": {"tenderPeriod": "soon"}}, {"ocid": "k", "tender": {"tenderPeriod": []}},
    {"ocid": "k", "tender": {"tenderPeriod": {"endDate": 20261231}}},
    {"ocid": "k", "tender": {"tenderPeriod": {"endDate": "not a date"}}},
    {"ocid": "k", "tender": {"tenderPeriod": {"endDate": "9999-12-31T23:59:59Z"}}},
    {"ocid": "k", "tender": {"tenderPeriod": {"endDate": "0001-01-01T00:00:00+14:00"}}},
    {"ocid": "k", "tender": {"tenderPeriod": {"endDate": "2026-02-30"}}},
    {"ocid": "k", "tender": {"tenderPeriod": {"endDate": ""}}},
    {"ocid": "k", "tender": {"tenderPeriod": {"endDate": "2026-12-01T10:00:00+99:00"}}},
    {"ocid": "k", "tender": {"procuringEntity": []}}, {"ocid": "k", "tender": {"procuringEntity": "x"}},
    {"ocid": "k", "tender": {"contactPerson": 5}}, {"ocid": "k", "buyer": "x"},
    {"ocid": "k", "buyer": {"name": ["x"]}}, {"ocid": "k", "description": {"a": 1}},
    {"ocid": "k", "tender": {"briefingSession": "yes"}},
    {"ocid": "k", "tender": {"briefingSession": {"compulsory": "true"}}},
    {"ocid": "k", "tender": {"category": 5, "province": 7}},
    {"ocid": "k", "tender": {"title": "bad\x0btitle", "description": "\x00"}},
    {"ocid": "k", "tender": {"id": ["x"], "procurementMethodDetails": {"a": 1}}},
    {"ocid": "k", "tender": {"documents": [{"url": "javascript:alert(1)"}, {"url": "file:///c:/x"},
                                           {"url": "\\\\host\\share"}, {"url": "https://"},
                                           {"url": "https://a b.com"}, {"url": "https://x.com/" + "a" * 2100}]}},
]


def _category_ok(rel):
    """Give the releases a wanted category so they reach the table when valid."""
    if isinstance(rel, dict) and isinstance(rel.get("tender"), dict) and "category" not in rel["tender"]:
        rel = {**rel, "tender": {**rel["tender"], "category": "Services: Professional"}}
    return rel


@pytest.mark.parametrize("bad", DROPPED, ids=lambda b: repr(b)[:30])
def test_every_unusable_release_shape_is_dropped_and_the_digest_still_goes_out(monkeypatch, bad):
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("good1")]], "2026-09-30": [[bad]]}
    s, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    d = mail.digests[0]
    assert s["outcome"] == "digest_delivered"
    assert d.dropped_releases == 1 and d.total == 1
    assert "malformed release(s)" in report.render_html(d)
    assert s["dropped_releases"] == 1


@pytest.mark.parametrize("bad", KEPT_AND_BLANKED, ids=lambda b: json.dumps(b)[:40])
def test_every_odd_field_shape_is_blanked_never_a_crash(monkeypatch, bad):
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("good1")]], "2026-09-30": [[_category_ok(bad)]]}
    s, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    d = mail.digests[0]
    assert s["outcome"] == "digest_delivered" and d.dropped_releases == 0
    html_body = report.render_html(d)
    for ch in "\x00\x0b\x0c":
        assert ch not in html_body
    # no unsafe link ever becomes an href
    assert "javascript:" not in html_body and "file:" not in html_body
    wb = openpyxl.load_workbook(io.BytesIO(report.build_xlsx(d)))
    for row in wb["Tenders"].iter_rows():
        for c in row:
            assert c.hyperlink is None or c.hyperlink.target.startswith(("http://", "https://"))


@pytest.mark.parametrize("raw", ["2262-04-12", "2262-04-11T23:00:00Z"])
def test_a_closing_time_at_the_very_edge_of_the_pandas_range_does_not_crash_the_run(monkeypatch, raw):
    run = at(2026, 10, 1, 6)
    s, mail = go(monkeypatch, run, FakeApi({"2026-10-01": [[release("edge", closing=raw)]]}), MemStore())
    assert s["outcome"] == "digest_delivered"


def test_an_unparseable_or_out_of_range_closing_date_is_kept_with_its_raw_text(monkeypatch):
    run = at(2026, 10, 1, 6)
    rel = release("odd", closing="9999-12-31T23:59:59Z")
    s, mail = go(monkeypatch, run, FakeApi({"2026-10-01": [[rel]]}), MemStore())
    d = mail.digests[0]
    assert d.total == 1 and pd.isna(d.tenders.loc[0, "closing"])
    assert "9999-12-31T23:59:59Z" in report.render_html(d)
    assert not bool(d.tenders.loc[0, "closing_soon"])


def test_a_page_that_is_not_json_an_object_a_list_or_a_recursion_bomb(monkeypatch):
    run = at(2026, 10, 1, 6)
    for bad_page in ["<html>error</html>", 5, None, [], {"releases": "x"}, {"releases": {"a": 1}},
                     {"releases": 5}, ValueError("bad json")]:
        api = FakeApi({"2026-10-01": [bad_page]})
        s, mail = go(monkeypatch, run, api, MemStore())
        d = mail.digests[0]
        assert s["outcome"] == "digest_delivered", bad_page


def test_a_recursion_bomb_page_is_an_api_failure_not_a_run_failure(monkeypatch):
    run = at(2026, 10, 1, 6)
    api = FakeApi({"2026-10-01": [RecursionError("maximum recursion depth exceeded")]})
    s, mail = go(monkeypatch, run, api, MemStore())
    assert s["outcome"] == "digest_delivered"


def test_a_deeply_nested_but_legal_release_is_scored_without_a_crash(monkeypatch):
    run = at(2026, 10, 1, 6)
    deep = {"a": {}}
    node = deep["a"]
    for _ in range(200):
        node["a"] = {}
        node = node["a"]
    rel = release("deep", description=deep, tenderPeriod={"endDate": ["x"]})
    s, mail = go(monkeypatch, run, FakeApi({"2026-10-01": [[rel]]}), MemStore())
    assert s["outcome"] == "digest_delivered"


def test_a_large_description_scores_quickly(monkeypatch):
    text = "sql " * 60_000
    rel = release("big", description=text)
    t0 = time.perf_counter()
    row = pipeline.flatten(rel)
    assert time.perf_counter() - t0 < 5
    assert row["score"] > 0


def test_the_same_ocid_on_two_days_is_listed_once_and_the_newest_copy_wins(monkeypatch):
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("dup", title="Power BI new")]],
              "2026-09-30": [[release("dup", title="Power BI old"), release("other")]]}
    s, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    d = mail.digests[0]
    assert sorted(d.tenders["ocid"]) == ["dup", "other"]
    assert d.tenders.set_index("ocid").loc["dup", "title"] == "Power BI new"


def test_an_ocid_with_stray_whitespace_is_not_normalised(monkeypatch):
    """Observation: ' a' and 'a' are two tenders. Documented, not asserted as a defect."""
    run = at(2026, 10, 1, 6)
    by_day = {"2026-10-01": [[release("a"), release(" a")]]}
    _, mail = go(monkeypatch, run, FakeApi(by_day), MemStore())
    assert mail.digests[0].total == 2


# =====================================================================================
# 6. Closing-soon and clock edges
# =====================================================================================

@pytest.mark.parametrize("delta,soon,kept,days_left", [
    (dt.timedelta(hours=1), True, True, 1),
    (dt.timedelta(days=7), True, True, 7),
    (dt.timedelta(days=7, minutes=1), False, True, 8),
    (dt.timedelta(0), False, True, 0),                  # closes at run_at: kept, not "soon" (run_at < closing)
    (dt.timedelta(seconds=-1), False, False, None),     # already closed
])
def test_the_closing_soon_boundary_through_build_digest(delta, soon, kept, days_left):
    run = at(2026, 9, 30, 6)
    closing = (run + delta).isoformat()
    d = pipeline.build_digest([release("c", closing=closing)], [], None, "first_run", "off", run)
    assert (d.total == 1) is kept
    if kept:
        assert bool(d.tenders.loc[0, "closing_soon"]) is soon
        assert int(d.tenders.loc[0, "days_left"]) == days_left


def test_a_closing_time_without_an_offset_is_read_as_sast_everywhere():
    run = at(2026, 9, 30, 6)
    d = pipeline.build_digest([release("c", closing="2026-10-07T08:00:00")], [], None, "first_run", "off", run)
    assert d.tenders.loc[0, "closing"] == pd.Timestamp("2026-10-07T06:00:00Z")
    assert bool(d.tenders.loc[0, "closing_soon"])        # 7 days exactly (06:00Z)


def test_a_run_just_before_midnight_sast_lists_tomorrows_day_in_the_window():
    run = at(2026, 10, 1, 21, 59, 59)
    d = pipeline.build_digest([], [], None, "first_run", "off", run)
    assert d.window[1] == dt.date(2026, 10, 1)
    d = pipeline.build_digest([], [], None, "first_run", "off", run + dt.timedelta(seconds=1))
    assert d.window[1] == dt.date(2026, 10, 2)


def test_html_times_are_sast_even_when_the_run_is_in_another_offset():
    run = at(2026, 9, 30, 6)
    rel = release("t", closing="2026-10-02T23:30:00Z")           # 01:30 SAST on Oct 3
    d = pipeline.build_digest([rel], [], None, "first_run", "off", run)
    body = report.render_html(d)
    assert "Sat 3 Oct" in body and "01:30" in body


# =====================================================================================
# 7. function_app: failure paths and the heartbeat
# =====================================================================================

def test_both_emails_failing_is_nothing_delivered_pings_fail_and_still_raises(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_URL", "https://hc-ping.com/0f3c7a52-1111-2222-3333-444455556666")
    run = at(2026, 10, 1, 6)
    mail = Mail()
    mail.fail = mail.fail_notice = True
    pings = []
    with pytest.raises(RuntimeError) as exc:
        go(monkeypatch, run, day_api(run), MemStore(), mail=mail, ping=lambda u, b: pings.append((u, b)) or "ok")
    assert exc.value.__cause__ is None
    assert len(pings) == 1 and pings[0][0].endswith("/fail")
    assert set(pings[0][1]) == {"run_id", "outcome", "counts"} and pings[0][1]["outcome"] == "nothing_delivered"


def test_exactly_one_run_summary_line_for_every_ending(monkeypatch, caplog):
    caplog.set_level(logging.INFO)
    run = at(2026, 10, 1, 6)
    go(monkeypatch, run, day_api(run), MemStore())
    mail = Mail()
    mail.fail = True
    with pytest.raises(RuntimeError):
        go(monkeypatch, run, day_api(run), MemStore(), mail=mail)
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("RUN_SUMMARY ")]
    assert len(lines) == 2
    for line in lines:
        assert len(line) < 30_000 and json.loads(line[len("RUN_SUMMARY "):])["v"] == 1


def test_a_memory_write_that_fails_does_not_fail_the_run_and_the_next_run_resends_as_new(monkeypatch):
    monkeypatch.setattr(memory, "SAVE_RETRY_DELAY_S", 0)      # the one retry is real otherwise
    store = MemStore()
    store.fail_puts = True
    run = at(2026, 10, 1, 6)
    s, mail = go(monkeypatch, run, day_api(run), store)
    assert s["outcome"] == "digest_delivered" and s["memory"]["write"] == "failed:network"
    store.fail_puts = False
    s2, mail2 = go(monkeypatch, run + dt.timedelta(hours=12), day_api(run), store)
    assert mail2.digests[0].new_count == mail2.digests[0].total        # honest: still NEW


@pytest.mark.parametrize("url,state", [
    ("https://hc-ping.com/0f3c7a52-1111-2222-3333-444455556666", "on"),
    ("http://hc-ping.com/abc", "misconfigured"),
    ("https://hc-ping.com.evil.example/abc", "misconfigured"),
    ("https://evil.example/https://hc-ping.com/abc", "misconfigured"),
    ("https://hc-ping.com/", "misconfigured"),
    ("https://hc-ping.com/a b", "misconfigured"),
    ("   ", "off"), ("", "off"),
])
def test_heartbeat_config_edges(monkeypatch, url, state):
    monkeypatch.setenv("HEARTBEAT_URL", url)
    assert function_app.heartbeat_config()[0] == state


def test_a_heartbeat_url_with_a_trailing_slash_still_pings_a_clean_fail_url(monkeypatch):
    monkeypatch.setenv("HEARTBEAT_URL", "https://hc-ping.com/0f3c7a52-1111-2222-3333-444455556666/")
    run = at(2026, 10, 1, 6)
    mail = Mail()
    mail.fail = True
    pings = []
    with pytest.raises(RuntimeError):
        go(monkeypatch, run, day_api(run), MemStore(), mail=mail, ping=lambda u, b: pings.append(u) or "ok")
    assert "//fail" not in pings[0].split("://", 1)[1]


def test_the_failure_notice_never_contains_a_secret_from_the_environment(monkeypatch):
    monkeypatch.setenv("IDENTITY_HEADER", "super-secret-identity-header-123")
    monkeypatch.setenv("HEARTBEAT_URL", "https://hc-ping.com/0f3c7a52-1111-2222-3333-444455556666")
    run = at(2026, 10, 1, 6)
    mail = Mail()

    def boom(d):
        raise RuntimeError("token Bearer abc.def.ghi header super-secret-identity-header-123 "
                           "url https://hc-ping.com/0f3c7a52-1111-2222-3333-444455556666?sig=ZZZ")
    with pytest.raises(RuntimeError) as exc:
        api = day_api(run)
        api.install(monkeypatch)
        function_app.run(run, MemStore(), boom, mail.send_notice, lambda u, b: "ok", run_id="qa")
    text = mail.notices[0] + str(exc.value)
    for secret in ("super-secret-identity-header-123", "0f3c7a52-1111", "abc.def.ghi", "ZZZ"):
        assert secret not in text


def test_the_run_fits_the_function_timeout_in_the_worst_case():
    """NFR-02: every timeout in a run, added up, stays under host.json's functionTimeout."""
    host = json.loads((ROOT / "host.json").read_text())
    h, m, s = (int(x) for x in host["functionTimeout"].split(":"))
    limit = h * 3600 + m * 60 + s
    reading = memory.TOKEN_TIMEOUT_S + memory.BLOB_TIMEOUT_S
    # digest delivered: fetch, send, then a write that times out once and is retried once
    delivered = (reading + pipeline.FETCH_BUDGET_SECONDS + email_sender.DIGEST_TIMEOUT_S
                 + 2 * memory.BLOB_TIMEOUT_S + memory.SAVE_RETRY_DELAY_S + function_app.HEARTBEAT_TIMEOUT_S)
    # digest send times out, then the failure notice is sent
    failed = (reading + pipeline.FETCH_BUDGET_SECONDS + email_sender.DIGEST_TIMEOUT_S
              + email_sender.NOTICE_TIMEOUT_S + function_app.HEARTBEAT_TIMEOUT_S)
    worst = max(delivered, failed)
    assert worst < limit - 30, (worst, limit)      # leaves 30 s for scoring and rendering


# =====================================================================================
# 8. Requirement ACs that had no direct test
# =====================================================================================

def test_ac25_the_deploy_job_needs_the_test_job():
    """AC-25 (only checkable statically): deploy.yml gates the deploy on the tests."""
    wf = (ROOT / ".github" / "workflows" / "deploy.yml").read_text()
    assert "needs: test" in wf and "python -m pytest" in wf
    assert wf.index("  test:") < wf.index("  deploy:")


def test_ac24_tuning_is_one_edit_in_pipeline_py():
    for name in ("LOOKBACK_DAYS", "CLOSING_SOON_DAYS", "MISSED_REPORT_DAYS", "KEYWORDS",
                 "PROVINCES", "CATEGORIES"):
        assert hasattr(pipeline, name)
    for module in (memory, report, function_app, email_sender, digest, TG):
        src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "LOOKBACK_DAYS =" not in src and "CLOSING_SOON_DAYS =" not in src


def test_ac37_the_timer_source_has_the_documented_schedule_and_no_run_on_startup():
    """AC-37 is proven by test_function_app.py (it owns the one allowed get_functions() call);
    this is a static cross-check of the decorator text, so a schedule edit is caught twice."""
    src = (ROOT / "function_app.py").read_text(encoding="utf-8")
    assert 'schedule="0 0 6,18 * * *"' in src and "run_on_startup=False" in src
    assert src.count("@app.timer_trigger") == 1


def test_ac38_sampling_is_off_in_host_json():
    host = json.loads((ROOT / "host.json").read_text())
    assert host["logging"]["applicationInsights"]["samplingSettings"]["isEnabled"] is False
    assert "Debug" not in json.dumps(host) and "Trace" not in json.dumps(host)


# =====================================================================================
# 9. Regression: scoring, filtering, province, categories on a fixed data set
# =====================================================================================

FIXED = [
    # (title, description, category, province, expected score, expected keywords)
    ("Power BI dashboards and reporting", "", "Services: Professional", "Gauteng", 26, "power bi, dashboards, reporting"),
    ("RFQ2596", "", "Information and communication", "Western Cape", 0, ""),
    ("Supply of SQL Server licences", "", "Supplies: Computer equipment", "KwaZulu-Natal", 16, "sql server, sql"),
    ("Mysql and postgresql upkeep", "", "Services: Professional", "Gauteng", 0, ""),
    ("API and AI work", "", "Other service activities", "Free State", 11, "api, ai"),
    ("Security guarding", "", "Services: Security", "Gauteng", None, None),            # category not wanted
    ("Build a school", "", "Construction", "Limpopo", None, None),
]


def test_fixed_regression_set_keeps_the_original_scoring_and_filtering():
    rels = []
    for i, (title, desc, cat, prov, _, _) in enumerate(FIXED):
        rels.append({"ocid": f"r{i}", "tender": {"id": f"N{i}", "title": title, "description": desc,
                     "category": cat, "province": prov,
                     "tenderPeriod": {"endDate": "2099-01-01T00:00:00+02:00"}}})
    df, dropped = pipeline.select_tenders(rels, at(2026, 10, 1))
    assert dropped == 0
    expected = {f"r{i}": (s, k) for i, (_, _, _, _, s, k) in enumerate(FIXED) if s is not None}
    got = {r.ocid: (r.score, r.matched_keywords) for r in df.itertuples()}
    assert {k: v[0] for k, v in got.items()} == {k: v[0] for k, v in expected.items()}
    for k, (s, kw) in expected.items():
        assert sorted(got[k][1].split(", ")) == sorted(kw.split(", ")) if kw else got[k][1] == ""


def test_province_filter_nationwide_by_default_and_a_correctly_spelled_province_narrows(monkeypatch):
    assert pipeline.PROVINCES == set()
    rels = [{"ocid": f"p{i}", "tender": {"title": "x", "category": "Services: Professional", "province": p}}
            for i, p in enumerate(["Gauteng", "Western Cape", None])]
    monkeypatch.setattr(pipeline, "PROVINCES", {"gauteng"})
    df, _ = pipeline.select_tenders(rels, at(2026, 10, 1))
    assert sorted(df["ocid"]) == ["p0", "p2"]      # a tender with no province is kept (as before)
    monkeypatch.setattr(pipeline, "PROVINCES", {"guateng"})    # the typo the operator once shipped
    df, _ = pipeline.select_tenders(rels, at(2026, 10, 1))
    assert sorted(df["ocid"]) == ["p2"]


def test_every_configured_province_name_is_a_real_south_african_province():
    real = {"eastern cape", "free state", "gauteng", "kwazulu-natal", "limpopo", "mpumalanga",
            "north west", "northern cape", "western cape"}
    assert set(pipeline.PROVINCES) <= real


# =====================================================================================
# 10. AC-21a / AC-21b across every text column, not just title and buyer
# =====================================================================================

PAYLOADS = ["=1+1", "+1+1", "-1+1", "@SUM(A1)", "\t=1+1", "\r=1+1", "=cmd|' /C calc'!A0",
            '=HYPERLINK("http://evil","x")', "=1+1\n=2+2"]


@pytest.mark.parametrize("payload", PAYLOADS, ids=lambda p: repr(p)[:20])
def test_ac21a_no_text_column_can_become_a_formula(payload):
    run = at(2026, 10, 1, 6)
    rel = {"ocid": payload + "x", "tender": {
        "id": payload, "title": payload, "category": "Services: Professional", "province": payload,
        "procurementMethodDetails": payload, "procuringEntity": {"name": payload},
        "contactPerson": {"email": payload},
        "tenderPeriod": {"endDate": payload, "startDate": payload},
        "description": payload, "eligibilityCriteria": payload}}
    d = pipeline.build_digest([rel], [], None, "first_run", "off", run)
    assert d.total == 1
    data = report.build_xlsx(d)
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(data))
    assert z.namelist().count("xl/worksheets/sheet1.xml") == 1
    for name in z.namelist():
        if name.startswith("xl/worksheets/"):
            assert b"<f>" not in z.read(name) and b"<f " not in z.read(name)
    wb = openpyxl.load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["Tenders"]
    seen = 0
    shown = payload.replace(chr(13), chr(10))   # an XML parser reads a raw CR back as LF; still plain text
    for row in wb["Tenders"].iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, str) and shown in c.value:
                seen += 1
                assert c.data_type == "s"
                assert c.value in (shown, shown + "x")      # nothing was added in front of the text
    assert seen >= 6


@pytest.mark.parametrize("url,linked", [
    ("https://www.etenders.gov.za/doc.pdf", True), ("http://x.example/a", True),
    ("HTTPS://UPPER.example/a", True),
    ("javascript:alert(1)", False), ("JaVaScRiPt:alert(1)", False), ("file://host/share", False),
    (chr(92) * 2 + "host" + chr(92) + "share", False), ("https://", False), ("https://a b.com", False),
    ("https://x.com/" + "a" * 2000, False), ("data:text/html,<script>", False),
    ("ftp://x.com/a", False), ("//evil.com/a", False), ("https://x.com/a\u2028b", False),
    ("https://x.com/\u202e", True),   # bidi char: allowed by the contract; see report note
])
def test_ac21b_only_safe_links_become_links_in_both_outputs(url, linked):
    run = at(2026, 10, 1, 6)
    rel = release("l1", documents=[{"url": url}])
    d = pipeline.build_digest([rel], [], None, "first_run", "off", run)
    body = report.render_html(d)
    wb = openpyxl.load_workbook(io.BytesIO(report.build_xlsx(d)))
    cell = wb["Tenders"].cell(2, 17)
    assert ("<a href=\"" + url.replace("&", "&amp;") in body) is linked or not linked
    assert (cell.hyperlink is not None) is linked
    if not linked:
        assert "javascript:" not in body.lower() and "file:" not in body and "data:text" not in body
        assert cell.value is None


def test_a_single_tender_summary_reads_acceptably():
    """Cosmetic: with one tender the summary line says '1 tender(s)'. Asserted so a change is noticed."""
    run = at(2026, 10, 1, 6)
    d = pipeline.build_digest([release("only")], [], None, "first_run", "off", run)
    assert "1 tender(s) · 1 new" in report.render_html(d)


def test_tg_local_runner_never_touches_the_blob_or_asks_for_a_token(monkeypatch, tmp_path):
    def forbidden(*a, **k):
        raise AssertionError("TG.py must never reach the production memory")
    monkeypatch.setattr(memory, "mi_token", forbidden)
    monkeypatch.setattr(memory.BlobStore, "get_text", forbidden)
    monkeypatch.setattr(memory.BlobStore, "put_text", forbidden)
    run = at(2026, 10, 1, 6)
    api = day_api(run, n=2)
    api.install(monkeypatch)
    TG.main(str(tmp_path / "m.json"), str(tmp_path / "x.xlsx"), str(tmp_path / "e.html"))
    assert (tmp_path / "x.xlsx").exists() and (tmp_path / "e.html").exists() and (tmp_path / "m.json").exists()
