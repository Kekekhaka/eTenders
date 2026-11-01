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

## Update 2026-11-01: Monday to Friday only
The operator found that the eTenders portal publishes little or nothing at weekends, so the schedule is
now NCRONTAB `0 0 6,18 * * 1-5` (Monday to Friday, same times). 06:00 and 18:00 UTC fall on the same
calendar day in SAST, so the weekday is the same in both zones.
- **Nothing is lost.** A window reaches `LOOKBACK_DAYS` (4) days back, so Monday's runs cover Friday
  (fetched complete after it ended), Saturday and Sunday. `tests/test_memory.py` runs three weeks of
  weekday runs and checks that no weekend day is named as missed.
- **No email arrives on Saturday or Sunday.** Anything published at the weekend appears on Monday.
- Public holidays are not special-cased: the function still runs, and finds little.
- If the Healthchecks.io heartbeat (ADR-0018) is ever switched on, its cron check must change to
  `0 6,18 * * 1-5` too, or it will report a missed run every weekend.
