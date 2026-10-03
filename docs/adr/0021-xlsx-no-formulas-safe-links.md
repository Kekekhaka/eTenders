# ADR-0021: The xlsx never contains a formula; links are http(s) with a host; one builder serves the Function and `TG.py`

- **Status:** Accepted for build (DESIGN_SPEC v0.2; Team Leader review decision B6, the extended AC-21; 2026-09-30). Not built yet.
- **Date:** 2026-09-30
- **Requirements:** NFR-08(c), NFR-08(f), FR-18, FR-22; AC-20, AC-21a, AC-21b
- **Source:** DESIGN_SPEC v0.2 D-6, §5.3 (`is_safe_url`), §8.5; Security S-05

> **Amendments (`docs/cosmos/TEAM_LEADER_DECISIONS.md`, 2026-09-30):**
> - **TL-5:** the xlsx **Coverage sheet is dropped**. Coverage appears in the email banners. The type
>   fix still applies to every sheet the workbook has, which is now just the tenders sheet. AC-21a no
>   longer mentions a Coverage sheet. The `TG.py` HTML preview is kept.
> - **TL-1:** `is_safe_url` lives in `digest.py`, not `pipeline.py`.
>
> Mentions of the Coverage sheet and `pipeline.is_safe_url` below are superseded.

## Context
`email_sender.py:78` and `TG.py:16` write the xlsx with raw `df.to_excel`. On 2026-09-30 an offline
check showed that a title such as `=HYPERLINK(...)` is stored as a formula (NFR-08c, not met).
Security confirmed that openpyxl stores as a formula only strings that start with `=`, and that
includes DDE payloads. Document links will be added (FR-18). Unchecked links could carry
`javascript:`, `file://` or UNC targets (an NTLM hash leak).

## Decision
- `report.build_xlsx` is the **only** xlsx writer. The Function and `TG.py` both use it.
- It writes with `pd.ExcelWriter(engine="openpyxl")`. Before closing, it walks every cell of
  **every sheet**, the Coverage sheet included, and sets `data_type = "s"` wherever openpyxl set
  `"f"`. The product never writes a formula of its own.
- Values are **not** prefixed with `'` or a space. `cell.quotePrefix = True` on text that starts
  with `= + - @ \t \r` is optional defence in depth (Frontend's call).
- Hyperlinks are set explicitly, and only for URLs that pass `pipeline.is_safe_url`. The URL must
  be a `str` of at most 2,000 characters, with no whitespace or control characters, an `http` or
  `https` scheme and a non-empty host. The check runs in `flatten` and again at the sink.
- No CSV export of this data may be added unless the CSV prefix rule is added with it.

## Consequences
- This closes the formula, DDE and NTLM-link paths in both runtimes (Azure and `TG.py`).
- Extended AC-21 (AC-21a, AC-21b) tests every prefix, the DDE payload and the Coverage sheet, plus
  the unsafe-link cases.
- The data isn't altered: what the API sent is what the operator sees.
