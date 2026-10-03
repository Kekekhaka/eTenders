# ADR-0013: Report permanently missed days, without backfilling

- **Status:** Accepted (operator decision 2026-09-30; this answers Q8). Designed in ADR-0016 (memory) and ADR-0017 (covered-day rule), DESIGN_SPEC v0.2; not built yet.
- **Requirements:** FR-14, FR-15, NFR-12; AC-09 to AC-12

> **Amendment (TL-15, `docs/cosmos/TEAM_LEADER_DECISIONS.md`, 2026-10-01; API_CONTRACT v0.3 §3.3):**
> the memory no longer stores a `missed` map. Permanently missed days are **derived** at run time by
> `missed_days(memory, window)` from `days`, `tracking_since` and `resolved_through`.
> - The decision above is unchanged: no backfill; name each missed day; the 7-day reporting period (OQ-BA2).
> - A day is named while it is within 7 days of leaving the window, or if it is after `resolved_through`
>   (no delivered digest has named it yet, AC-12).
> - Behaviour change: a day older than its period, named once after a long gap, isn't repeated afterwards.
> - A day that hit the page limit is stored as `never_fetched`. The banner says "before they were fetched
>   completely", which is true (review N-8).

## Context
A publication day that is never fetched while it's inside the 6-day window
drops out without notice (IDEA_BRIEF gap 2). API outages lasting days make this realistic.
Monthly bulk files could be used to backfill, but that adds a second data path.

## Decision
Don't backfill. When a publication day leaves the lookback window without ever having been
fetched completely, name it plainly in the email so the operator can check
etenders.gov.za by hand. This needs a memory, between runs, of which days have been fetched.

## Consequences
- Every day is accounted for: it's either covered or named (SC1: "100% of days that are never fetched get named").
- The same store as ADR-0012 holds this, so there's one new dependency, not two.
- A day counts as covered only if a run fetched it after the day had ended. Otherwise
  a 06:00 fetch of "today" would hide the tenders published later that day (FR-14).
- There is no history before go-live, so days before it are never reported.
- The operator does the manual check. The product doesn't recover the tenders.
