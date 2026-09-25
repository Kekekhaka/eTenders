"""Send the tender digest by email via Azure Communication Services."""

import base64
import html
import io
import os

import pandas as pd
from azure.communication.email import EmailClient

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
TABLE_ROWS = 25


def _setting(name: str) -> str:
    """Read a required app setting, failing with a clear message if it is unset."""
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} app setting is not configured")
    return value


def _recipients() -> list[str]:
    return [a.strip() for a in _setting("RECIPIENT_EMAIL").split(",") if a.strip()]


def _cell(value) -> str:
    """HTML-safe text. Tender fields come from an external API, so a stray '<'
    or '&' in a title must not break (or inject into) the email markup."""
    return html.escape("" if pd.isna(value) else str(value))


def build_summary_html(df: pd.DataFrame, missing: list[tuple[str, str]] | None = None) -> str:
    warning = ""
    if missing:
        gaps = ", ".join(f"{_cell(label)} ({_cell(reason)})" for label, reason in missing)
        warning = (
            "<p style='background:#fff3cd;padding:8px;border:1px solid #e0a800'>"
            f"<b>Partial digest.</b> The eTenders API could not serve: {gaps}. "
            "Tenders published on those dates are missing from this email.</p>"
        )

    if df.empty:
        return warning + "<p>No matching tenders were found in this run.</p>"

    rows = df[["score", "tender_number", "buyer", "closing", "title"]].head(TABLE_ROWS)
    table_rows = "".join(
        f"<tr><td>{_cell(r.score)}</td><td>{_cell(r.tender_number)}</td>"
        f"<td>{_cell(r.buyer)}</td><td>{_cell(r.closing)}</td><td>{_cell(r.title)}</td></tr>"
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


def _send(subject: str, body: str, attachments: list[dict] | None = None) -> None:
    client = EmailClient.from_connection_string(_setting("ACS_CONNECTION_STRING"))
    message = {
        "senderAddress": _setting("SENDER_EMAIL"),
        "recipients": {"to": [{"address": a} for a in _recipients()]},
        "content": {"subject": subject, "html": body},
        "attachments": attachments or [],
    }
    client.begin_send(message).result()


def send_report(df: pd.DataFrame, subject: str,
                missing: list[tuple[str, str]] | None = None) -> None:
    attachments = []
    if not df.empty:
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        attachments.append({
            "name": "etenders_matches.xlsx",
            "contentType": XLSX_MIME,
            "contentInBase64": base64.b64encode(buf.getvalue()).decode("ascii"),
        })

    _send(subject, build_summary_html(df, missing), attachments)


def send_failure_notice(error: Exception) -> None:
    """Best-effort alert so a broken run isn't silently missed."""
    body = (
        "<p>The eTenders digest run failed before it could produce a report.</p>"
        f"<pre>{html.escape(f'{type(error).__name__}: {error}')}</pre>"
        "<p>Check the Azure Function's Application Insights logs for the full traceback.</p>"
    )
    _send("eTenders digest FAILED", body)
