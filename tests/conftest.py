"""Shared test setup.

The operator tunes LOOKBACK_DAYS, CLOSING_SOON_DAYS and MISSED_REPORT_DAYS in
pipeline.py (AC-24). Many tests work out dates by hand, so they run with the
values they were written for; the code reads these names at call time, so the
logic under test is the same. Mark a test `real_tuning` to see the
configured values instead.
"""

import pytest

import pipeline

TEST_TUNING = {"LOOKBACK_DAYS": 5, "CLOSING_SOON_DAYS": 7, "MISSED_REPORT_DAYS": 7}


def pytest_configure(config):
    config.addinivalue_line("markers", "real_tuning: use pipeline.py's configured day values")


# Settings the AI step reads; a developer's shell may have them set.
AI_SETTINGS = ("LLM_ENABLED", "CF_ACCOUNT_ID", "CF_API_TOKEN",
               "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_KEY",
               "BRIEFS_ENABLED")


@pytest.fixture(autouse=True)
def _no_ai_settings(monkeypatch):
    for name in AI_SETTINGS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _test_tuning(request, monkeypatch):
    if request.node.get_closest_marker("real_tuning") is None:
        for name, value in TEST_TUNING.items():
            monkeypatch.setattr(pipeline, name, value)
