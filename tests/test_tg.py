"""Tests for TG.py, the local runner (FR-22). The API is a fake and report.py's
functions are replaced, so these run offline and write only to tmp_path."""

import datetime as dt
import json

import pytest

import memory
import pipeline
import TG
from pipeline import DayResult


def release(ocid):
    return {"ocid": ocid, "tender": {"id": ocid, "title": "power bi", "category": "Services: Professional",
                                     "tenderPeriod": {"endDate": "2099-01-01T00:00:00Z"}}}


@pytest.fixture
def local(tmp_path, monkeypatch):
    """Fake API, fake report, and production memory made impossible to reach."""
    def no_production(*args, **kwargs):
        raise AssertionError("TG.py must never touch the production memory")

    monkeypatch.setattr(memory, "BlobStore", no_production)
    monkeypatch.setattr(memory, "mi_token", no_production)
    monkeypatch.setattr(pipeline, "fetch_releases", lambda a, b: (
        [release("A")], [DayResult(b, True, None), DayResult(a, False, "ReadTimeout")]))
    digests = []
    monkeypatch.setattr(TG.report, "build_xlsx", lambda d: digests.append(d) or b"xlsx-bytes")
    monkeypatch.setattr(TG.report, "render_html", lambda d: "<p>preview</p>")
    monkeypatch.setattr(TG.report, "render_subject", lambda d: "subject")
    paths = {name: str(tmp_path / name) for name in ("m.json", "x.xlsx", "p.html")}
    return paths, digests


def test_the_local_runner_writes_the_xlsx_the_preview_and_a_local_memory(local, capsys):
    paths, digests = local
    TG.main(paths["m.json"], paths["x.xlsx"], paths["p.html"])
    with open(paths["x.xlsx"], "rb") as f:
        assert f.read() == b"xlsx-bytes"
    with open(paths["p.html"], encoding="utf-8") as f:
        assert f.read() == "<p>preview</p>"
    with open(paths["m.json"], encoding="utf-8") as f:
        assert list(json.load(f)["sent"]) == ["A"]
    assert digests[0].heartbeat == "off"
    assert "not fully fetched (ReadTimeout)" in capsys.readouterr().out


def test_a_second_local_run_remembers_what_the_first_one_saw(local):
    paths, digests = local
    TG.main(paths["m.json"], paths["x.xlsx"], paths["p.html"])
    TG.main(paths["m.json"], paths["x.xlsx"], paths["p.html"])
    assert digests[0].tenders["is_new"].tolist() == [True]
    assert digests[1].tenders["is_new"].tolist() == [False]
    assert digests[1].memory_status == "ok"


def test_an_unreadable_local_memory_is_not_overwritten(local):
    paths, _ = local
    with open(paths["m.json"], "w", encoding="utf-8") as f:
        f.write("{broken")
    TG.main(paths["m.json"], paths["x.xlsx"], paths["p.html"])
    with open(paths["m.json"], encoding="utf-8") as f:
        assert f.read() == "{broken"


def test_the_default_files_are_the_documented_ones():
    assert (TG.MEMORY_FILE, TG.XLSX_FILE, TG.HTML_FILE) == (
        "etenders_memory.json", "etenders_matches.xlsx", "etenders_digest.html")


def test_the_local_runner_uses_one_clock(local):
    # Every date in the digest comes from the run_at captured once at the start.
    paths, digests = local
    TG.main(paths["m.json"], paths["x.xlsx"], paths["p.html"])
    d = digests[0]
    assert d.window == pipeline.window_for(d.run_at)
    assert d.run_at.utcoffset() == dt.timedelta(0)
