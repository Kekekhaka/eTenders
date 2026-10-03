# ADR-0007: Email through Azure Communication Services from an Azure-managed domain, as an HTML summary plus an xlsx attachment

- **Status:** Accepted
- **Date:** 2026-09-17; recorded 2026-09-30
- **Requirements:** FR-07, FR-08, FR-09, FR-11, NFR-06

## Context
The digest has to reach the operator's inbox with no mail server to run and at close
to zero cost. The operator reads both the HTML table and the Excel file (operator
decision, 2026-09-30).

## Decision
Send through ACS Email (`azure-communication-email`) using a connection string and
sender address from app settings, to a comma-separated `RECIPIENT_EMAIL` list
(`email_sender.py:15-24`, `62-70`). The sender is an Azure-managed `*.azurecomm.net`
domain (README.md:59-67). The body is an HTML summary of the top `TABLE_ROWS = 25`
rows, and the full list is attached as `etenders_matches.xlsx` (`email_sender.py:12`, `33-85`).
If a run crashes, a failure notice goes out over the same channel (`email_sender.py:88-95`,
`function_app.py:26-32`).

## Consequences
- No server to run, and the cost is negligible.
- **The failure notice shares the channel it's reporting on.** If ACS or its settings fail,
  both emails fail and the only record is in App Insights (SC5 gap; FR-11).
- Deliverability from a managed domain is unverified (A8), and managed domains have low
  sending limits. That's fine for one recipient.
- Tender fields are HTML-escaped (`email_sender.py:27-30`). The xlsx path is **not**
  sanitised: a text field starting with `=` is stored as a formula (verified offline on
  2026-09-30 with pandas + openpyxl). See NFR-08 and the hand-off to Security.
