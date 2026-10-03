# ADR-0023: Validate untrusted input at both inbound boundaries; malformed data degrades a digest and never fails it

- **Status:** Accepted for build (Team Leader review decisions B4 and B5, 2026-09-30). Not built yet.
- **Date:** 2026-09-30
- **Requirements:** NFR-03, NFR-05, NFR-12, NFR-14, FR-23; AC-27 to AC-29, AC-36, AC-39
- **Source:** DESIGN_SPEC v0.2 D-8, §5.2, §7 TB1 and TB2; Security S-04, S-10; baseline weakness B7

> **Amendments (`docs/cosmos/TEAM_LEADER_DECISIONS.md`, 2026-09-30):**
> - **TL-2, memory:** the checks are a 5 MB read cap, JSON parse, `schema == 1`, type conversion
>   and date clamps. *Any* error in that sequence means **unavailable**: no crash, nothing NEW, no
>   write. The exact-key-set checks and per-map entry caps (DESIGN_SPEC §5.2) are dropped. The memory
>   validation below should be read with this in mind.
> - **TL-8 / D-8, API:** `flatten` also drops (and counts) any release whose `ocid` is longer than
>   200 characters.

## Context
Only the top-level API response is type-checked (`pipeline.py:123-124`). Security showed offline
that five malformed inputs crash the run: `{"tender": "x"}`, a non-dict release, `title` as an
object, `documents: ["u"]`, and a title containing `\x0b` (in `to_excel`). Each crash turns the digest
into a failure notice. It repeats on every run while the bad release stays in the 6-day window, so
NFR-03 is only partly met. The new memory blob is a second inbound input. A tampered or hand-edited
blob with the wrong types would crash the pure functions in the same way (S-10).

## Decision
- **API boundary (TB1):**
  - `releases` must be a list. Otherwise the page is treated as an API failure and retried, like non-JSON.
  - Each release must be a dict. Nested objects are read only through `_obj()` (a dict, or else
    `{}`), and text only through `_text()` (a `str`, or else null). xlsx-illegal control characters
    are stripped.
  - A release whose `ocid` is not a non-empty `str` is dropped and **counted**. The count appears in
    RUN_SUMMARY and in the digest footer (FR-23).
  - URLs must pass `is_safe_url` (ADR-0021).
- **Memory boundary (TB2):** `load()` checks the full shape and types (DESIGN_SPEC §5.2) and caps the
  read at 5 MB. It converts values to typed dates and datetimes, and it clamps retention dates:
  `first_sent ≤ run_at`, `closing ≤ run_at + 365 d`, `report_until ≤ run_date + 7 d`. An invalid blob
  is `unavailable`. It is never overwritten, and it never makes everything NEW.
- **No blanket `except`** (NFR-05). Typed accessors that can't raise on bad types are used instead of catching errors.
- A reset runbook covers a persistently invalid blob (DESIGN_SPEC §5.2; it goes into the DEPLOY_PLAN).

## Consequences
- One bad record can't block delivery. A dropped release is visible only as a count (T-9).
- The validator must change in step with the memory schema. Any change to the schema bumps `schema`
  (T-8). An over-strict validator shows as "memory unavailable", which is visible, not silent.
- Build note (BA, from the code): today `fetch_releases` calls `rel.get("ocid")` while deduplicating
  (`pipeline.py:191-194`), before `flatten` runs. A non-dict release crashes there first. The validation
  therefore has to cover the dedupe step as well as `flatten`.
