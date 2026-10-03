# ADR-0020: A frozen `Digest` dataclass is the contract between the pipeline and a pure `report.py`

- **Status:** Accepted for build (DESIGN_SPEC v0.2, 2026-09-30). Not built yet.
- **Date:** 2026-09-30
- **Requirements:** FR-08, FR-13, FR-16, FR-17, FR-18, FR-23, FR-24, NFR-04, NFR-08(b), NFR-13; AC-04, AC-13 to AC-20, AC-35
- **Source:** DESIGN_SPEC v0.2 D-7, §5.3, §8.6; Security S-06

> **Amendments (`docs/cosmos/TEAM_LEADER_DECISIONS.md`, 2026-09-30):**
> - **TL-1:** `is_safe_url` lives in `digest.py`, the Lead-owned contract module, not in `pipeline.py`.
> - **TL-7:** `digest.py` is kept as the contract module. That makes 3 new modules, over NFR-09's 2,
>   and the Team Leader accepted this.
> - **TL-6:** `PartialDay.covered_earlier` is dropped.
> - **TL-3:** `report.py` keeps three public functions, with no `html_rows`.
> - **TL-8:** the API_CONTRACT is authoritative.
>   - O-2: a send is *delivered* only if the ACS poller is `done()` and its status is `Succeeded`. A
>     timed-out send is not delivered.
>   - D-3: `closing_soon_days`.
>   - D-5/D-6: the signature additions.

## Context
Today `email_sender.py` both decides what goes into the email and renders it, and it sorts by score
only, with no tie-break (`pipeline.py:273`). The new flags, banners and the OQ-BA1 row rule need
one clear boundary, so that Frontend (4a) and Backend (4b) can work in parallel.

## Decision
- The backend (`pipeline.py`) returns a frozen `Digest` (DESIGN_SPEC §5.3), which holds:
  - validated, flagged rows in UTC;
  - one stable order: `is_new` desc → `score` desc → `closing` asc (no date last) →
    `tender_number` asc → `ocid` asc;
  - the partial days, missed days, memory status, heartbeat status and dropped-release count.
- A new **pure** module, `report.py` (owned by Frontend, with no I/O and no Azure imports), renders the
  subject, the HTML and the xlsx. It owns:
  - the SAST display;
  - the row rule: every new and closing-soon row, plus up to 25 others (OQ-BA1);
  - the banner order (permanently missed → partial → memory unavailable, or the first-run notice);
  - the footer (heartbeat off, dropped releases);
  - the S-06 escaping rules. One helper, `_e()`, is applied at every interpolation. No API value
    goes into `style`, `class` or tag or attribute names. `href` values pass `is_safe_url` and
    are escaped. `contact` is plain text.
- The subject is built from counts and constants only.
- `email_sender.py` is transport only, with bounded ACS timeouts (90 s for the digest, 30 s for the notice).

## Consequences
- Frontend and Backend can build in parallel against one contract, and both sides are tested
  offline with plain data.
- The HTML and xlsx use the same order and flags by construction (AC-19).
- There is one new module, and it earns its place as the contract boundary (NFR-09).
