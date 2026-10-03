# ADR-0002: Twice-daily timer at 06:00 and 18:00 UTC

- **Status:** Accepted
- **Date:** 2026-09-17; recorded 2026-09-30
- **Requirements:** FR-01, NFR-07

## Context
The operator wants a morning and an evening look at new tenders. The API is
unreliable, so more than one attempt per day improves coverage. South Africa has
no daylight saving time.

## Decision
NCRONTAB `0 0 6,18 * * *` (UTC), which is 08:00 and 20:00 SAST all year
(`function_app.py:11-12`), with `run_on_startup=False`. A past-due run is logged as a
warning (`function_app.py:14-15`).

## Consequences
- The structural best-case freshness is about 12 h plus the publisher's lag (SC6).
- With a 6-day window (ADR-0003), each publication day gets about 12 attempts, and each
  tender shows up in about 10–12 consecutive emails. That repetition motivates ADR-0012.
- The run count stays tiny, so cost stays near zero (ADR-0001).
