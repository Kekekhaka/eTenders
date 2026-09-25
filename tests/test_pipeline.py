"""Tests for pipeline.py. The network is replaced with fakes, so these run
offline in a second and can gate every deploy."""

import datetime as dt

import pytest
import requests

import pipeline

SA_PROVINCES = {
    "eastern cape", "free state", "gauteng", "kwazulu-natal", "limpopo",
    "mpumalanga", "north west", "northern cape", "western cape", "national",
}


def release(ocid, title="", category="Services: Professional",
            province="Gauteng", closing_in_days=10):
    """A minimal OCDS release holding only the fields the pipeline reads."""
    closing = dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=closing_in_days)
    return {
        "ocid": ocid,
        "tender": {
            "id": ocid,
            "title": title,
            "category": category,
            "province": province,
            "tenderPeriod": {"endDate": closing.isoformat()},
        },
    }


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, payloads):
        self._payloads = iter(payloads)

    def get(self, *args, **kwargs):
        return FakeResponse(next(self._payloads))


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


# --- configuration ---------------------------------------------------------

def test_configured_provinces_are_spelled_the_way_the_api_spells_them():
    # "guateng" once silently discarded every Gauteng tender.
    assert pipeline.PROVINCES <= SA_PROVINCES


# --- fetching --------------------------------------------------------------

def test_day_chunks_run_newest_first_and_cover_each_day_once(monkeypatch):
    monkeypatch.setattr(pipeline, "CHUNK_DAYS", 2)
    assert pipeline._day_chunks("2026-09-20", "2026-09-24") == [
        ("2026-09-23", "2026-09-24"),
        ("2026-09-21", "2026-09-22"),
        ("2026-09-20", "2026-09-20"),
    ]


def test_pagination_stops_on_an_empty_page_not_a_short_one(monkeypatch):
    # The API returns fewer rows than PageSize on pages that are not the last.
    # Stopping at the first short page once dropped ~85% of the tenders.
    pages = iter([
        {"releases": [release(f"a{i}") for i in range(100)]},
        {"releases": [release(f"b{i}") for i in range(72)]},
        {"releases": []},
    ])
    monkeypatch.setattr(pipeline, "_get_page", lambda *args: next(pages))
    got = pipeline._fetch_window(None, "2026-09-24", "2026-09-24", deadline=float("inf"))
    assert len(got) == 172


def test_a_non_json_error_page_is_retried(monkeypatch):
    # Under load the API returns HTTP 200 with a body that is not a JSON object.
    monkeypatch.setattr(pipeline, "RETRY_BACKOFF_SECONDS", 0)
    session = FakeSession([["not", "an", "object"], {"releases": []}])
    assert pipeline._get_page(session, {}, deadline=float("inf")) == {"releases": []}


def test_a_failing_day_is_reported_without_losing_the_other_days(monkeypatch):
    def fake_window(session, date_from, date_to, deadline):
        if date_from == "2026-09-23":
            raise requests.ReadTimeout("simulated slow API")
        return [release(f"ocds-{date_from}"), release("ocds-shared")]

    monkeypatch.setattr(pipeline, "CHUNK_DAYS", 1)
    monkeypatch.setattr(pipeline, "_fetch_window", fake_window)
    releases, missing = pipeline.fetch_releases("2026-09-22", "2026-09-24")

    assert missing == [("2026-09-23", "ReadTimeout")]
    ocids = [r["ocid"] for r in releases]
    assert "ocds-2026-09-24" in ocids and "ocds-2026-09-22" in ocids
    assert ocids.count("ocds-shared") == 1


def test_a_bug_in_our_code_is_not_disguised_as_an_api_outage(monkeypatch):
    def broken_window(session, date_from, date_to, deadline):
        raise KeyError("releases")

    monkeypatch.setattr(pipeline, "_fetch_window", broken_window)
    with pytest.raises(KeyError):
        pipeline.fetch_releases("2026-09-24", "2026-09-24")


# --- selecting the digest --------------------------------------------------

def test_select_tenders_keeps_open_wanted_tenders_best_first(monkeypatch):
    monkeypatch.setattr(pipeline, "PROVINCES", set())
    df = pipeline.select_tenders([
        release("weak", title="general consulting"),
        release("strong", title="data warehouse and power bi"),
        release("closed", title="data warehouse", closing_in_days=-1),
        release("catering", title="data warehouse", category="Supplies: General"),
    ])
    # "weak" scores zero but stays: score orders the list, it does not gate it.
    assert list(df["ocid"]) == ["strong", "weak"]
