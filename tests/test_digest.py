"""Tests for digest.py, the Backend-to-Frontend contract: it imports, its
objects cannot be changed once built, and is_safe_url lets only safe links through."""

import dataclasses
import datetime as dt

import pandas as pd
import pytest

import digest

RUN_AT = dt.datetime(2026, 9, 30, 6, 0, tzinfo=dt.timezone.utc)


def empty_digest():
    return digest.Digest(
        run_at=RUN_AT,
        window=(dt.date(2026, 9, 25), dt.date(2026, 9, 30)),
        tenders=pd.DataFrame(columns=list(digest.TENDER_COLUMNS)),
        total=0,
        new_count=0,
        closing_soon_count=0,
        closing_soon_days=7,
        partial_days=(),
        missed_days=(),
        memory_status="first_run",
        heartbeat="off",
        dropped_releases=0,
    )


@pytest.mark.parametrize("obj, field", [
    (empty_digest(), "total"),
    (digest.PartialDay(dt.date(2026, 9, 29), "ReadTimeout"), "reason"),
    (digest.MissedDay(dt.date(2026, 9, 18), "never_fetched", dt.date(2026, 9, 24)), "kind"),
])
def test_contract_objects_cannot_be_changed_once_built(obj, field):
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(obj, field, None)


def test_tender_columns_are_unique():
    assert len(set(digest.TENDER_COLUMNS)) == len(digest.TENDER_COLUMNS)


def test_display_timezone_is_sast():
    assert RUN_AT.astimezone(digest.DISPLAY_TZ).hour == 8


@pytest.mark.parametrize("url", [
    "https://www.etenders.gov.za/doc.pdf",
    "http://example.com/a?b=c&d=e",
    "HTTPS://Example.com/x",
    "https://example.com/" + "a" * (digest.MAX_URL_CHARS - 20),  # exactly at the limit
])
def test_safe_urls_are_accepted(url):
    assert len(url) <= digest.MAX_URL_CHARS
    assert digest.is_safe_url(url)


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "file://host/share",
    "\\\\host\\share",                  # a UNC path
    "ftp://example.com/doc.pdf",
    "https://",                          # no host
    "https:///path-only",                # empty host
    "https://a b.com",                   # whitespace
    "https://example.com/\x00",          # control character
    "https://exa\tmple.com",             # tab
    "https://example.com/\x7f",          # DEL
    "https://example.com/" + "a" * digest.MAX_URL_CHARS,  # too long
    "http://[::1",                       # malformed host: urlsplit raises
    None,
    123,
    "",
])
def test_unsafe_urls_are_rejected(url):
    assert not digest.is_safe_url(url)
