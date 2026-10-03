# ADR-0015: Improve both the HTML email table and the Excel attachment

- **Status:** Accepted (operator decision 2026-09-30; this answers the second half of Q3). Designed in ADR-0020 (`Digest` / `report.py`) and ADR-0021 (xlsx safety), DESIGN_SPEC v0.2; the visual layout is Frontend's; not built yet.
- **Requirements:** FR-17, FR-18, NFR-08, NFR-13; AC-16 to AC-21

## Context
The operator reads both. Today the HTML shows the top 25 rows by a score that is 0 for
almost every row, in 5 columns, with no flag, category or link. The xlsx is a raw
`DataFrame.to_excel` dump: dates are stored as text, there are no filters or frozen header, and
fields from the API can turn into formulas (`email_sender.py:33-85`).

## Decision
Improve both outputs so they carry the same flags (new, closing soon), the same
order and readable SAST dates. The HTML must never hide a new or closing-soon
tender behind the row cap. The xlsx must be sortable and filterable, and safe to open.

## Consequences
- The digest's presentation now needs a specification. Its column set is proposed in
  the REQUIREMENTS_PACK for the operator to confirm.
- Email size grows. It must stay inside the ACS message limits (the Architect checks the current limit).
- It fixes the formula-injection weakness as a side effect (NFR-08).
