"""Tests for pipeline.py. The network is replaced with fakes, so these run
offline in a second and can gate every deploy."""

import datetime as dt

import pandas as pd
import pytest
import requests
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE

import pipeline
from digest import TENDER_COLUMNS, MissedDay, PartialDay
from pipeline import DayResult

UTC = dt.timezone.utc
RUN_AT = dt.datetime(2026, 9, 30, 6, 0, tzinfo=UTC)  # a scheduled run: 08:00 SAST

SA_PROVINCES = {
    "eastern cape", "free state", "gauteng", "kwazulu-natal", "limpopo",
    "mpumalanga", "north west", "northern cape", "western cape", "national",
}


def release(ocid, title="", category="Services: Professional", province="Gauteng",
            closing=RUN_AT + dt.timedelta(days=10), number=None, **tender):
    """A minimal OCDS release holding only the fields the pipeline reads.
    closing may be a datetime, a raw string, or None for no closing date."""
    if isinstance(closing, dt.datetime):
        closing = closing.isoformat()
    t = {"id": number or ocid, "title": title, "category": category, "province": province,
         "tenderPeriod": {"endDate": closing} if closing is not None else {}}
    t.update(tender)
    return {"ocid": ocid, "tender": t}


class FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            r = requests.Response()
            r.status_code = self.status_code
            raise requests.HTTPError(f"{self.status_code} error", response=r)

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeSession:
    """Serves a list of payloads in order, or by day via a function."""

    def __init__(self, payloads=None, by_params=None):
        self._payloads = iter(payloads or [])
        self._by_params = by_params
        self.headers = {}
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get(self, url, params=None, timeout=None):
        self.calls.append(dict(params or {}))
        if self._by_params:
            return self._by_params(params)
        return FakeResponse(next(self._payloads))


def fake_api(monkeypatch, pages_by_day):
    """Make fetch_releases talk to a fake API. pages_by_day maps an ISO day to
    the payloads of its pages (each a dict, or an int HTTP status, or an
    exception to raise); days not listed answer with an empty page."""
    monkeypatch.setattr(pipeline, "RETRY_BACKOFF_SECONDS", 0)

    def answer(params):
        pages = pages_by_day.get(params["dateFrom"], [])
        page = params["PageNumber"]
        payload = pages[page - 1] if page <= len(pages) else {"releases": []}
        if isinstance(payload, Exception):
            raise payload
        if isinstance(payload, int):
            return FakeResponse({}, status=payload)
        return FakeResponse(payload)

    session = FakeSession(by_params=answer)
    monkeypatch.setattr(pipeline.requests, "Session", lambda: session)
    return session


# --- scoring ---------------------------------------------------------------

def test_score_ignores_keywords_hidden_inside_other_words():
    # "ai" sits inside "maintenance", "api" inside "capital", "ssis" inside
    # "assistance". Substring matching once scored this chairs tender at 18.
    assert pipeline.score("Maintenance of chairs; capital assistance available")[0] == 0


def test_score_matches_whole_words_regardless_of_case():
    total, matched = pipeline.score("Build a POWER BI solution")
    assert total == pipeline.KEYWORDS["power bi"]
    assert matched == "power bi"


def test_score_matches_a_phrase_split_across_lines():
    # Document text wraps mid-phrase; the match has to survive the line break.
    total, _ = pipeline.score("design a data\nwarehouse")
    assert total == pipeline.KEYWORDS["data warehouse"]


# --- configuration (AC-24) ---------------------------------------------------

def test_configured_provinces_are_spelled_the_way_the_api_spells_them():
    # "guateng" once silently discarded every Gauteng tender.
    assert pipeline.PROVINCES <= SA_PROVINCES


def test_tuning_constants_exist_and_have_a_sensible_shape():
    # Shape only: the operator edits the values (AC-24), so none is pinned here.
    for name in ("LOOKBACK_DAYS", "CLOSING_SOON_DAYS", "MISSED_REPORT_DAYS"):
        assert isinstance(getattr(pipeline, name), int) and getattr(pipeline, name) > 0
    for name in ("CATEGORIES", "PROVINCES"):
        values = getattr(pipeline, name)
        assert all(v == v.lower() and v.strip() == v for v in values)
    assert pipeline.CATEGORIES and pipeline.KEYWORDS
    assert all(w > 0 for w in pipeline.KEYWORDS.values())
    assert len(pipeline.KEYWORDS) == len({k.lower() for k in pipeline.KEYWORDS})


# --- the run clock and the window (ADR-0017) --------------------------------

@pytest.mark.parametrize("hour", [6, 18])
def test_the_window_at_the_scheduled_runs_ends_today(hour):
    run_at = dt.datetime(2026, 9, 30, hour, 0, tzinfo=UTC)
    assert pipeline.window_for(run_at) == (dt.date(2026, 9, 25), dt.date(2026, 9, 30))


def test_the_window_uses_the_api_day_timezone_not_utc():
    # 22:30 UTC on the 30th is already 00:30 SAST on 1 October.
    run_at = dt.datetime(2026, 9, 30, 22, 30, tzinfo=UTC)
    assert pipeline.window_for(run_at) == (dt.date(2026, 9, 26), dt.date(2026, 10, 1))


def test_a_day_ends_at_midnight_sast():
    assert pipeline.day_end_utc(dt.date(2026, 9, 30)) == dt.datetime(2026, 9, 30, 22, 0, tzinfo=UTC)


# --- fetching ----------------------------------------------------------------

def test_days_are_fetched_newest_first_each_once(monkeypatch):
    asked = []

    def fake_day(session, day, deadline):
        asked.append(day)
        return [], True

    monkeypatch.setattr(pipeline, "_fetch_day", fake_day)
    _, results = pipeline.fetch_releases(dt.date(2026, 9, 20), dt.date(2026, 9, 22))
    assert asked == ["2026-09-22", "2026-09-21", "2026-09-20"]
    assert results == [DayResult(dt.date(2026, 9, d), True, None) for d in (22, 21, 20)]


def test_pagination_stops_on_an_empty_page_not_a_short_one(monkeypatch):
    # The API returns fewer rows than PageSize on pages that are not the last.
    # Stopping at the first short page once dropped ~85% of the tenders.
    pages = iter([
        [release(f"a{i}") for i in range(100)],
        [release(f"b{i}") for i in range(72)],
        [],
    ])
    monkeypatch.setattr(pipeline, "_get_page", lambda *args: next(pages))
    got, complete = pipeline._fetch_day(None, "2026-09-24", deadline=float("inf"))
    assert len(got) == 172
    assert complete


def test_a_day_cut_short_by_the_page_limit_is_kept_but_not_ok(monkeypatch):
    monkeypatch.setattr(pipeline, "MAX_PAGES", 3)
    fake_api(monkeypatch, {"2026-09-24": [{"releases": [release(f"p{i}")]} for i in range(5)]})
    releases, results = pipeline.fetch_releases(dt.date(2026, 9, 24), dt.date(2026, 9, 24))
    assert [r["ocid"] for r in releases] == ["p0", "p1", "p2"]
    assert results == [DayResult(dt.date(2026, 9, 24), False, "page limit")]


def test_a_non_json_error_page_is_retried(monkeypatch):
    # Under load the API returns HTTP 200 with a body that is not a JSON object.
    monkeypatch.setattr(pipeline, "RETRY_BACKOFF_SECONDS", 0)
    session = FakeSession([["not", "an", "object"], {"releases": []}])
    assert pipeline._get_page(session, {}, deadline=float("inf")) == []
    assert len(session.calls) == 2


def test_releases_that_is_not_a_list_is_retried_as_an_api_failure(monkeypatch):
    monkeypatch.setattr(pipeline, "RETRY_BACKOFF_SECONDS", 0)
    session = FakeSession([{"releases": "x"}, {"releases": [release("a")]}])
    assert [r["ocid"] for r in pipeline._get_page(session, {}, deadline=float("inf"))] == ["a"]
    assert len(session.calls) == 2


@pytest.mark.parametrize("payload", [{}, {"releases": None}, {"releases": []}])
def test_missing_null_or_empty_releases_ends_the_pages(monkeypatch, payload):
    assert pipeline._get_page(FakeSession([payload]), {}, deadline=float("inf")) == []


def test_a_page_whose_releases_is_not_a_list_makes_that_day_partial(monkeypatch):
    # AC-29, last row: the other day's tender is still listed.
    fake_api(monkeypatch, {
        "2026-09-30": [{"releases": [release("good")]}],
        "2026-09-29": [{"releases": {"not": "a list"}}, {"releases": {"not": "a list"}}],
    })
    releases, results = pipeline.fetch_releases(dt.date(2026, 9, 29), dt.date(2026, 9, 30))
    assert [r["ocid"] for r in releases] == ["good"]
    assert results == [DayResult(dt.date(2026, 9, 30), True, None),
                       DayResult(dt.date(2026, 9, 29), False, "ValueError")]


def test_an_http_error_is_reported_as_http_and_its_code(monkeypatch):
    fake_api(monkeypatch, {"2026-09-30": [503, 503]})
    _, results = pipeline.fetch_releases(dt.date(2026, 9, 30), dt.date(2026, 9, 30))
    assert results == [DayResult(dt.date(2026, 9, 30), False, "http_503")]


def test_days_not_reached_in_the_budget_are_out_of_time(monkeypatch):
    session = fake_api(monkeypatch, {})
    monkeypatch.setattr(pipeline, "FETCH_BUDGET_SECONDS", 0)
    releases, results = pipeline.fetch_releases(dt.date(2026, 9, 29), dt.date(2026, 9, 30))
    assert releases == []
    assert [r.reason for r in results] == ["out of time", "out of time"]
    assert session.calls == []


def test_a_failing_day_is_reported_without_losing_the_other_days(monkeypatch):
    def fake_day(session, day, deadline):
        if day == "2026-09-23":
            raise requests.ReadTimeout("simulated slow API")
        return [release(f"ocds-{day}"), release("ocds-shared")], True

    monkeypatch.setattr(pipeline, "_fetch_day", fake_day)
    releases, results = pipeline.fetch_releases(dt.date(2026, 9, 22), dt.date(2026, 9, 24))

    assert [r for r in results if not r.ok] == [DayResult(dt.date(2026, 9, 23), False, "ReadTimeout")]
    ocids = [r["ocid"] for r in releases]
    assert "ocds-2026-09-24" in ocids and "ocds-2026-09-22" in ocids
    assert ocids.count("ocds-shared") == 1


def test_the_dedupe_passes_malformed_releases_through_without_crashing(monkeypatch):
    # A non-dict release once crashed rel.get("ocid") here (S-04).
    bad = ["x", {"tender": {}}, {"ocid": 123}, {"ocid": "o" * 201}]
    monkeypatch.setattr(pipeline, "_fetch_day",
                        lambda s, day, d: (bad + [release("good"), release("good")], True))
    releases, _ = pipeline.fetch_releases(dt.date(2026, 9, 30), dt.date(2026, 9, 30))
    assert releases == bad + [release("good")]


def test_a_bug_in_our_code_is_not_disguised_as_an_api_outage(monkeypatch):
    def broken_day(session, day, deadline):
        raise KeyError("releases")

    monkeypatch.setattr(pipeline, "_fetch_day", broken_day)
    with pytest.raises(KeyError):
        pipeline.fetch_releases(dt.date(2026, 9, 24), dt.date(2026, 9, 24))


# --- validating one release (AC-29, AC-21b, NFR-14a) --------------------------

@pytest.mark.parametrize("rel", [
    "x",
    {"tender": {"title": "no ocid"}},
    {"ocid": 123},
    {"ocid": ""},
    {"ocid": "o" * 201},
    None,
    ["a", "list"],
])
def test_a_release_without_a_usable_ocid_is_rejected(rel):
    assert pipeline.flatten(rel) is None


def test_an_ocid_of_200_characters_is_kept():
    assert pipeline.flatten({"ocid": "o" * 200})["ocid"] == "o" * 200


@pytest.mark.parametrize("rel", [
    {"ocid": "a", "tender": "x"},
    {"ocid": "a", "tender": {"title": {"an": "object"}, "tenderPeriod": "x",
                             "procuringEntity": ["x"], "contactPerson": 5,
                             "briefingSession": "yes", "documents": "x"}},
    {"ocid": "a", "tender": {"documents": ["u", None, {"url": 5}]}},
])
def test_fields_of_the_wrong_type_become_blank_and_the_release_is_kept(rel):
    row = pipeline.flatten(rel)
    assert row["ocid"] == "a"
    assert pd.isna(row["title"]) and pd.isna(row["buyer"]) and pd.isna(row["contact"])
    assert pd.isna(row["closing"])
    assert row["briefing_compulsory"] is None
    assert row["document_urls"] == []


def test_xlsx_illegal_characters_are_stripped_from_text():
    row = pipeline.flatten(release("a", title="bad\x0btitle",
                                   procuringEntity={"name": "Dept\x01 of\x1f IT"}))
    assert row["title"] == "badtitle"
    assert row["buyer"] == "Dept of IT"


def test_only_the_first_three_safe_document_urls_are_kept():
    docs = [{"url": u} for u in [
        "javascript:alert(1)", "https://a.example/1.pdf", "file://host/share",
        "\\\\host\\share", "https:// no-host", "https://b.example/2.pdf",
        "https://" + "x" * 2000, "http://c.example/3.pdf", "https://d.example/4.pdf",
    ]]
    row = pipeline.flatten(release("a", documents=docs))
    assert row["document_urls"] == ["https://a.example/1.pdf", "https://b.example/2.pdf",
                                    "http://c.example/3.pdf"]


def test_only_a_real_boolean_counts_as_a_compulsory_briefing():
    assert pipeline.flatten(release("a", briefingSession={"compulsory": True}))["briefing_compulsory"] is True
    assert pipeline.flatten(release("a", briefingSession={"compulsory": "true"}))["briefing_compulsory"] is None


def test_the_buyer_falls_back_to_the_release_buyer():
    rel = release("a")
    rel["buyer"] = {"name": "Fallback Dept"}
    assert pipeline.flatten(rel)["buyer"] == "Fallback Dept"


def test_a_closing_time_without_an_offset_is_read_in_sast():
    row = pipeline.flatten(release("a", closing="2026-10-05T11:00:00"))
    assert row["closing"] == pd.Timestamp("2026-10-05T09:00:00Z")
    assert row["closing_raw"] == "2026-10-05T11:00:00"


def test_an_unreadable_closing_date_is_blank_but_its_text_is_kept():
    row = pipeline.flatten(release("a", closing="next Friday"))
    assert pd.isna(row["closing"])
    assert row["closing_raw"] == "next Friday"


def test_a_column_mixing_offset_and_naive_dates_keeps_both():
    # pandas 3 blanks the offset value when it parses such a column in one go.
    df, _ = pipeline.select_tenders([
        release("naive", closing="2026-10-05T11:00:00"),
        release("offset", closing="2026-10-05T11:00:00+02:00"),
        release("zulu", closing="2026-10-05T09:00:00Z"),
    ], RUN_AT)
    assert str(df["closing"].dtype) == "datetime64[ns, UTC]"
    assert set(df["closing"]) == {pd.Timestamp("2026-10-05T09:00:00Z")}


# --- selecting the tenders ----------------------------------------------------

def test_select_tenders_keeps_open_wanted_tenders(monkeypatch):
    monkeypatch.setattr(pipeline, "PROVINCES", set())
    df, dropped = pipeline.select_tenders([
        release("weak", title="general consulting"),
        release("strong", title="data warehouse and power bi"),
        release("closed", title="data warehouse", closing=RUN_AT - dt.timedelta(minutes=1)),
        release("catering", title="data warehouse", category="Supplies: General"),
    ], RUN_AT)
    # "weak" scores zero but stays: score orders the list, it does not gate it.
    assert sorted(df["ocid"]) == ["strong", "weak"]
    assert dropped == 0


def test_a_tender_closing_exactly_at_run_time_is_kept_and_one_before_is_dropped():
    # AC-15 and FR-04, on the single run clock.
    df, _ = pipeline.select_tenders([
        release("at", closing=RUN_AT),
        release("before", closing=RUN_AT - dt.timedelta(seconds=1)),
    ], RUN_AT)
    assert list(df["ocid"]) == ["at"]


def test_a_tender_with_no_closing_date_is_kept():
    df, _ = pipeline.select_tenders([release("open-ended", closing=None)], RUN_AT)
    assert list(df["ocid"]) == ["open-ended"]


def test_malformed_releases_are_dropped_counted_or_blanked():
    # AC-29: each example row, at the selection step.
    df, dropped = pipeline.select_tenders([
        release("valid"),
        "x",                                     # dropped
        {"tender": {"category": "Services: Professional"}},  # no ocid: dropped
        {"ocid": 123},                           # dropped
        {"ocid": "o" * 201},                     # dropped
        release("title-object", title={"x": 1}),
        release("docs-u", documents=["u"]),
        release("vt", title="bad\x0btitle"),
    ], RUN_AT)
    assert dropped == 4
    assert sorted(df["ocid"]) == ["docs-u", "title-object", "valid", "vt"]
    for col in ("ocid", "title", "buyer", "category", "closing_raw"):
        for value in df[col].dropna():
            assert not ILLEGAL_CHARACTERS_RE.search(value)


def test_a_release_whose_tender_is_a_string_is_kept_but_filtered_by_category():
    # Its category is blank, so the category filter (not validation) removes it.
    df, dropped = pipeline.select_tenders([{"ocid": "a", "tender": "x"}], RUN_AT)
    assert dropped == 0 and df.empty


# --- building the digest ----------------------------------------------------

def build(releases, memory=None, status="first_run", days=(), run_at=RUN_AT):
    return pipeline.build_digest(list(releases), list(days), memory, status, "off", run_at)


def stored_memory(sent=(), resolved_through=dt.date(2026, 9, 24)):
    return {"schema": 1, "tracking_since": dt.date(2026, 9, 1), "resolved_through": resolved_through,
            "sent": {o: {"first_sent": RUN_AT - dt.timedelta(days=1), "closing": None} for o in sent},
            "days": {}}


def test_an_empty_digest_still_has_every_column():
    d = build([])
    assert tuple(d.tenders.columns) == TENDER_COLUMNS
    assert d.total == 0 and d.closing_soon_count == 0
    assert d.window == (dt.date(2026, 9, 25), dt.date(2026, 9, 30))
    assert d.run_at == RUN_AT


@pytest.mark.parametrize("offset, flag, days_left", [
    (dt.timedelta(hours=1), True, 1),
    (dt.timedelta(days=7), True, 7),
    (dt.timedelta(days=7, minutes=1), False, 8),
])
def test_the_closing_soon_boundary(offset, flag, days_left):
    # AC-13
    d = build([release("a", closing=RUN_AT + offset)])
    row = d.tenders.iloc[0]
    assert bool(row["closing_soon"]) is flag
    assert row["days_left"] == days_left
    assert d.closing_soon_count == int(flag)
    assert d.closing_soon_days == 7


def test_no_closing_date_is_listed_but_never_closing_soon():
    # AC-14
    d = build([release("a", closing=None)])
    row = d.tenders.iloc[0]
    assert not row["closing_soon"]
    assert pd.isna(row["days_left"]) and pd.isna(row["closing"])


def test_new_tenders_come_first_then_every_tie_break():
    # AC-04: B is new; A was sent before. Then score, closing (none last),
    # tender number (none last) and ocid.
    soon, later = RUN_AT + dt.timedelta(days=2), RUN_AT + dt.timedelta(days=9)
    releases = [
        release("A", title="data warehouse power bi"),          # old, high score
        release("B", title="general"),                          # new, score 0
        release("t-none", title="sql", closing=None),           # old, score 8
        release("t-later", title="sql", closing=later),
        release("t-soon-z", title="sql", closing=soon, number="Z1"),
        release("t-soon-a", title="sql", closing=soon, number="A1"),
        release("t-soon-nonum2", title="sql", closing=soon),
        release("t-soon-nonum1", title="sql", closing=soon),
    ]
    for r in releases[-2:]:
        r["tender"]["id"] = None
    memory = stored_memory(sent=[r["ocid"] for r in releases if r["ocid"] != "B"])
    d = build(releases, memory, "ok")
    assert list(d.tenders["ocid"]) == [
        "B", "A", "t-soon-a", "t-soon-z", "t-soon-nonum1", "t-soon-nonum2", "t-later", "t-none"]
    assert list(d.tenders["is_new"]) == [True] + [False] * 7
    assert d.new_count == 1
    assert list(d.tenders.index) == list(range(8))


def test_the_order_does_not_depend_on_the_order_the_api_sent():
    releases = [release(f"o{i}", title="sql") for i in range(6)]
    first = build(releases).tenders["ocid"].tolist()
    assert build(list(reversed(releases))).tenders["ocid"].tolist() == first


def test_on_a_first_run_every_tender_is_new():
    # AC-07
    d = build([release("a"), release("b")], None, "first_run")
    assert d.tenders["is_new"].all() and d.new_count == 2
    assert d.memory_status == "first_run" and d.missed_days == ()


def test_with_memory_unavailable_nothing_is_new_and_no_day_is_missed():
    # AC-08, AC-27
    d = build([release("a")], None, "unavailable:ContainerNotFound")
    assert not d.tenders["is_new"].any()
    assert d.new_count is None
    assert d.memory_status == "unavailable"
    assert d.missed_days == ()


def test_the_missed_days_passed_in_are_carried_on_the_digest():
    missed = [MissedDay(dt.date(2026, 9, 23), "never_fetched", dt.date(2026, 9, 29))]
    d = pipeline.build_digest([], [], stored_memory(), "ok", "off", RUN_AT, missed)
    assert d.missed_days == tuple(missed)
    assert build([]).missed_days == ()


def test_partial_days_are_listed_newest_first_with_their_reason():
    # AC-03
    days = [DayResult(dt.date(2026, 9, 30), True, None),
            DayResult(dt.date(2026, 9, 27), False, "page limit"),
            DayResult(dt.date(2026, 9, 29), False, "ReadTimeout")]
    d = build([release("a")], days=days)
    assert d.partial_days == (PartialDay(dt.date(2026, 9, 29), "ReadTimeout"),
                              PartialDay(dt.date(2026, 9, 27), "page limit"))
    assert d.total == 1


def test_the_counts_match_the_rows():
    d = build([release("a", closing=RUN_AT + dt.timedelta(days=1)), release("b"), "x"])
    assert d.total == len(d.tenders) == 2
    assert d.closing_soon_count == int(d.tenders["closing_soon"].sum()) == 1
    assert d.dropped_releases == 1
