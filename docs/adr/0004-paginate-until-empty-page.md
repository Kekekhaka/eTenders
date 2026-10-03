# ADR-0004: Stop paginating on an empty page, never on a short page; PageSize 2000

- **Status:** Accepted
- **Date:** 2026-09-20 (`8ee04bc`); recorded 2026-09-30
- **Requirements:** FR-02

## Context
The API returns an arbitrary number of releases per page, below `PageSize`, even
on pages that aren't the last. Stopping at the first short page once silently
dropped about 85% of releases.

## Decision
Keep requesting pages until a page comes back with **no releases**
(`pipeline.py:147-149`). Use `PAGE_SIZE = 2000` to make fewer, larger requests, with a
`MAX_PAGES = 50` safety stop (`pipeline.py:25-28`).

## Consequences
- Every day costs at least one extra request (the empty page).
- If `MAX_PAGES` were ever reached, the loop would end without reporting
  truncation (`pipeline.py:140`). At 2000 × 50 = 100k releases/day against about 116 real
  releases/day, that's theoretical.
- Covered by `test_pagination_stops_on_an_empty_page_not_a_short_one`.
