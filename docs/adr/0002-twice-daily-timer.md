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

## Update 2026-11-11: once a day, at 20:00 SAST
The operator only reads one email a day, so the schedule is now NCRONTAB `0 0 18 * * 1-5`: 18:00 UTC
(20:00 SAST), Monday to Friday. This supersedes the twice-daily part of this ADR; the filename is kept.
- **Fewer attempts per day.** A publication day is fetched on the evening it is published (partly,
  because it has not ended), then on each of the next four weekday runs, so it still gets 4 to 5
  attempts. A day that fails for good is named as missed after it leaves the 5-day window, as before.
- **Monday still reaches the weekend.** The window goes back four days, so Monday's run completes
  Friday, Saturday and Sunday. `tests/test_memory.py` simulates three weeks.
- **The AI step gets more time.** One run now judges a whole day's tenders, so `classify.BUDGET_S` went
  from 90 s to 240 s. `function_app.AI_LAST_START_S` went from 480 s to 350 s (after Cloudflare calls gained a
  retry) so the worst case still fits the 10-minute timeout (`test_the_run_fits_the_function_timeout_in_the_worst_case`).
- **Heartbeat.** If the Healthchecks.io check (ADR-0018) is ever switched on, its cron must be
  `0 18 * * 1-5`, with a grace of 60 minutes.
- **Deploys.** Avoid 17:45 to 18:15 UTC on weekdays.
