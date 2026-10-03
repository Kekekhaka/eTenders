# ADR-0003: Fetch one day at a time, newest first, under a 420 s budget, and send a partial digest

- **Status:** Accepted
- **Date:** 2026-09-24 (`18928b7`); recorded 2026-09-30
- **Requirements:** FR-02, FR-03, FR-08, NFR-02, NFR-03, NFR-04

## Context
API throughput swings between about 17 and 0.75 releases/sec, and the API returns 500s,
timeouts and HTTP 200 non-JSON pages (IDEA_BRIEF §2). At the slow end, a whole
5-day window can't be fetched inside the 10-minute ceiling (ADR-0001) at any request
timeout.

## Decision
- The window runs from `today - LOOKBACK_DAYS` (5) to today, so it covers 6 calendar days
  (`pipeline.py:16`, `276-282`).
- Split it into `CHUNK_DAYS = 1` chunks, newest first (`pipeline.py:24`, `155-165`).
- Stop fetching when `FETCH_BUDGET_SECONDS = 420` runs out (`pipeline.py:31`, `175-184`).
  The request timeout is 150 s, with `MAX_RETRIES = 2` **attempts** per page and
  a linear back-off (`pipeline.py:32-34`, `111-133`).
- A chunk that fails, or that has no time left, is recorded as `(date, reason)`, and the run
  carries on (`pipeline.py:182-190`). The email is still sent, with "(partial)" in the
  subject and a banner naming the dates (`function_app.py:21-24`,
  `email_sender.py:35-41`).
- Only API errors are absorbed. Our own bugs still raise (`pipeline.py:173-174`).

## Consequences
- Older days are dropped first, on the assumption that earlier runs already collected them.
- A day that is never fetched while it is inside the window **drops out silently**. ADR-0013
  addresses this.
- A chunk that fails part-way through pagination discards the pages it had already fetched
  for that day (the whole day is reported missing). That's simple and honest.
- Note: `MAX_RETRIES = 2` means 2 attempts in total (1 retry), not 2 retries. The
  IDEA_BRIEF's "2 retries per page" overstates it.
