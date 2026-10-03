"""Tests for email_sender.py. ACS is a fake and report.py's functions are
replaced, so nothing is rendered or sent. (The HTML tests that used to live
here moved to tests/test_report.py with the HTML itself.)"""

import base64

import pytest

import email_sender

SETTINGS = {
    "ACS_CONNECTION_STRING": "endpoint=https://x.communication.azure.com/;accesskey=k",
    "SENDER_EMAIL": "DoNotReply@x.azurecomm.net",
    "RECIPIENT_EMAIL": "a@x.com, b@y.com",
}


class FakePoller:
    def __init__(self, done=True, status="Succeeded"):
        self._done, self._status = done, status
        self.timeout = None

    def result(self, timeout=None):
        # Like azure-core: returns (does not raise) when the time runs out.
        self.timeout = timeout
        return {"id": "1", "status": self._status} if self._done else None

    def done(self):
        return self._done


class FakeClient:
    def __init__(self, poller):
        self.poller = poller
        self.messages = []

    def begin_send(self, message):
        self.messages.append(message)
        return self.poller


@pytest.fixture
def acs(monkeypatch):
    """Configure the settings and a fake ACS client; returns a function that
    sets how the next send ends and gives back the client."""
    for name, value in SETTINGS.items():
        monkeypatch.setenv(name, value)

    def use(done=True, status="Succeeded"):
        client = FakeClient(FakePoller(done, status))
        monkeypatch.setattr(email_sender.EmailClient, "from_connection_string",
                            staticmethod(lambda conn: client))
        return client

    use()
    return use


@pytest.fixture
def fake_report(monkeypatch):
    rendered = {"xlsx": b"PK-xlsx-bytes"}
    monkeypatch.setattr(email_sender.report, "render_subject", lambda d: "eTenders digest - 2 tender(s), 1 new")
    monkeypatch.setattr(email_sender.report, "render_html", lambda d: "<p>café</p>")
    monkeypatch.setattr(email_sender.report, "build_xlsx", lambda d: rendered["xlsx"])
    return rendered


def test_the_digest_goes_to_every_recipient_with_the_xlsx(acs, fake_report):
    # AC-01
    client = acs()
    sizes = email_sender.send_report(object())
    message = client.messages[0]
    assert message["recipients"]["to"] == [{"address": "a@x.com"}, {"address": "b@y.com"}]
    assert message["content"] == {"subject": "eTenders digest - 2 tender(s), 1 new",
                                  "html": "<p>café</p>"}
    assert message["attachments"] == [{
        "name": "etenders_matches.xlsx",
        "contentType": email_sender.XLSX_MIME,
        "contentInBase64": base64.b64encode(b"PK-xlsx-bytes").decode("ascii"),
    }]
    assert sizes == {"html_bytes": len("<p>café</p>".encode("utf-8")), "xlsx_bytes": 13}
    assert client.poller.timeout == 90


def test_an_empty_digest_is_sent_without_an_attachment(acs, fake_report):
    # AC-02
    fake_report["xlsx"] = None
    client = acs()
    assert email_sender.send_report(object())["xlsx_bytes"] == 0
    assert client.messages[0]["attachments"] == []


@pytest.mark.parametrize("done, status", [(False, None), (True, "Failed"), (True, "Canceled")])
def test_a_send_that_is_not_confirmed_raises(acs, fake_report, done, status):
    # O-2 / AC-06: a timed-out or failed send is not delivered.
    acs(done=done, status=status)
    with pytest.raises((TimeoutError, RuntimeError)):
        email_sender.send_report(object())


def test_the_failure_notice_escapes_the_error_text(acs):
    client = acs()
    email_sender.send_failure_notice("ValueError: <bad> & worse")
    message = client.messages[0]
    assert message["content"]["subject"] == "eTenders digest FAILED"
    assert "ValueError: &lt;bad&gt; &amp; worse" in message["content"]["html"]
    assert message["attachments"] == []
    assert "before it could" not in message["content"]["html"]  # SF-3: it may have been delivered
    assert client.poller.timeout == 30


def test_an_unconfirmed_failure_notice_raises(acs):
    acs(done=False)
    with pytest.raises(TimeoutError):
        email_sender.send_failure_notice("x")


def test_missing_setting_fails_with_a_clear_message(monkeypatch):
    monkeypatch.delenv("SENDER_EMAIL", raising=False)
    with pytest.raises(RuntimeError, match="SENDER_EMAIL"):
        email_sender._setting("SENDER_EMAIL")


def test_recipients_are_split_on_commas(monkeypatch):
    monkeypatch.setenv("RECIPIENT_EMAIL", "a@x.com, b@y.com,")
    assert email_sender._recipients() == ["a@x.com", "b@y.com"]
