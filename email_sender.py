"""Send the tender-matches report by email via Azure Communication Services."""

import base64
import io
import os

import pandas as pd
from azure.communication.email import EmailClient


def _connection_string() -> str:
    conn = os.environ.get("ACS_CONNECTION_STRING")
    if not conn:
        raise RuntimeError("ACS_CONNECTION_STRING app setting is not configured")
    return conn


def _recipients() -> list:
    raw = os.environ.get("RECIPIENT_EMAIL", "")
    addresses = [a.strip() for a in raw.split(",") if a.strip()]
    if not addresses:
        raise RuntimeError("RECIPIENT_EMAIL app setting is not configured")
    return addresses


def build_summary_html(df: pd.DataFrame, missing: list = None) -> str:
    warning = ""
    if missing:
        gaps = ", ".join(f"{label} ({reason})" for label, reason in missing)
        warning = (
            "<p style='background:#fff3cd;padding:8px;border:1px solid #e0a800'>"
            f"<b>Partial digest.</b> The eTenders API could not serve: {gaps}. "
            "Tenders published on those dates are missing from this email.</p>"
        )

    if df.empty:
        return warning + "<p>No matching tenders were found in this run.</p>"

    rows = df[["score", "tender_number", "buyer", "closing", "title"]].head(25)
    table_rows = "".join(
        f"<tr><td>{r.score}</td><td>{r.tender_number}</td><td>{r.buyer}</td>"
        f"<td>{r.closing}</td><td>{r.title}</td></tr>"
        for r in rows.itertuples(index=False)
    )
    return warning + f"""
    <p>{len(df)} tender(s) found. Top {len(rows)} shown below;
    the full list is attached as an Excel file.</p>
    <table border="1" cellpadding="4" cellspacing="0">
      <tr><th>Score</th><th>Tender #</th><th>Buyer</th><th>Closing</th><th>Title</th></tr>
      {table_rows}
    </table>
    """


def _send(subject: str, html: str, attachments: list = None) -> None:
    client = EmailClient.from_connection_string(_connection_string())
    message = {
        "senderAddress": os.environ["SENDER_EMAIL"],
        "recipients": {"to": [{"address": a} for a in _recipients()]},
        "content": {
            "subject": subject,
            "html": html,
        },
        "attachments": attachments or [],
    }
    poller = client.begin_send(message)
    poller.result()


def send_report(df: pd.DataFrame, subject: str, missing: list = None) -> None:
    attachments = []
    if not df.empty:
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        attachments.append({
            "name": "etenders_matches.xlsx",
            "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "contentInBase64": base64.b64encode(buf.getvalue()).decode("ascii"),
        })

    _send(subject, build_summary_html(df, missing), attachments)


def send_failure_notice(error: Exception) -> None:
    """Best-effort alert so a broken run isn't silently missed."""
    subject = "eTenders digest FAILED"
    html = (
        f"<p>The eTenders digest run failed before it could produce a report.</p>"
        f"<pre>{type(error).__name__}: {error}</pre>"
        f"<p>Check the Azure Function's Application Insights logs for the full traceback.</p>"
    )
    _send(subject, html)
