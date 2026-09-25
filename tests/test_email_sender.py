"""Tests for email_sender.py. Nothing is actually sent."""

import pandas as pd
import pytest

import email_sender


def one_tender(**fields):
    row = {"score": 9, "tender_number": "T1", "buyer": "Dept",
           "closing": "2026-10-01", "title": "t"}
    row.update(fields)
    return pd.DataFrame([row])


def test_tender_fields_are_html_escaped():
    body = email_sender.build_summary_html(
        one_tender(title="<script>alert(1)</script>", buyer="Dept & Co"))
    assert "<script>" not in body
    assert "&lt;script&gt;" in body
    assert "Dept &amp; Co" in body


def test_missing_values_render_as_blank_cells():
    assert "None" not in email_sender.build_summary_html(one_tender(closing=None))


def test_partial_digest_names_the_missing_dates():
    body = email_sender.build_summary_html(pd.DataFrame(), [("2026-09-23", "ReadTimeout")])
    assert "Partial digest" in body
    assert "2026-09-23 (ReadTimeout)" in body


def test_complete_digest_has_no_partial_warning():
    assert "Partial digest" not in email_sender.build_summary_html(one_tender(), [])


def test_failure_notice_escapes_the_error_text(monkeypatch):
    sent = {}
    monkeypatch.setattr(email_sender, "_send", lambda subject, body: sent.update(body=body))
    email_sender.send_failure_notice(ValueError("<bad> & worse"))
    assert "&lt;bad&gt; &amp; worse" in sent["body"]


def test_missing_setting_fails_with_a_clear_message(monkeypatch):
    monkeypatch.delenv("SENDER_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="SENDER_EMAIL"):
        email_sender._setting("SENDER_EMAIL")


def test_recipients_are_split_on_commas(monkeypatch):
    monkeypatch.setenv("RECIPIENT_EMAIL", "a@x.com, b@y.com,")
    assert email_sender._recipients() == ["a@x.com", "b@y.com"]
