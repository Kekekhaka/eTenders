"""Send email through Azure Communication Services (ACS). Transport only:
report.py decides what the digest says and how it looks.

A send counts as delivered only when the ACS poller is done() and its status
is "Succeeded" (API_CONTRACT 4.4). In azure-core, poller.result(timeout=...)
does NOT raise when the time runs out; it just returns, so done() must be
checked. Anything short of a confirmed send raises, and the caller treats it
as "not delivered".
"""

import base64
import html
import os

from azure.communication.email import EmailClient

import report

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DIGEST_TIMEOUT_S = 90
NOTICE_TIMEOUT_S = 30


def _setting(name: str) -> str:
    """Read a required app setting, failing with a clear message if it is unset."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} app setting is not configured")
    return value


def _recipients() -> list[str]:
    return [a.strip() for a in _setting("RECIPIENT_EMAIL").split(",") if a.strip()]


def _send(subject: str, body: str, attachments: list[dict], timeout_s: int) -> None:
    """Send one email and wait up to timeout_s for ACS to confirm it.
    Raises unless the send is confirmed."""
    client = EmailClient.from_connection_string(_setting("ACS_CONNECTION_STRING"))
    message = {
        "senderAddress": _setting("SENDER_EMAIL"),
        "recipients": {"to": [{"address": a} for a in _recipients()]},
        "content": {"subject": subject, "html": body},
        "attachments": attachments,
    }
    poller = client.begin_send(message)
    result = poller.result(timeout=timeout_s)
    if not poller.done():
        raise TimeoutError(f"ACS did not confirm the send within {timeout_s} s")
    status = result.get("status") if result is not None else None
    if status != "Succeeded":
        raise RuntimeError(f"ACS send finished with status {status}")


def send_report(d) -> dict:
    """Render the digest and send it. Returns the sizes for RUN_SUMMARY:
    {"html_bytes": int, "xlsx_bytes": int} (0 when there is no attachment).
    Raises when the send isn't confirmed."""
    subject = report.render_subject(d)
    body = report.render_html(d)
    xlsx = report.build_xlsx(d)

    attachments = []
    if xlsx is not None:
        attachments.append({
            "name": "etenders_matches.xlsx",
            "contentType": XLSX_MIME,
            "contentInBase64": base64.b64encode(xlsx).decode("ascii"),
        })
    _send(subject, body, attachments, DIGEST_TIMEOUT_S)
    return {"html_bytes": len(body.encode("utf-8")), "xlsx_bytes": len(xlsx) if xlsx else 0}


def send_failure_notice(error_text: str) -> None:
    """A best-effort alert so a broken run isn't silently missed.
    error_text arrives already redacted (function_app.redact); it is escaped here.
    Raises when the send isn't confirmed."""
    body = (
        "<p>The eTenders digest run failed.</p>"
        f"<pre>{html.escape(error_text, quote=True)}</pre>"
        "<p>Check the Azure Function's Application Insights logs (RUN_SUMMARY and the "
        "error logged just before it) for the details.</p>"
    )
    _send("eTenders digest FAILED", body, [], NOTICE_TIMEOUT_S)
