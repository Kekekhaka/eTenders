# ADR-0017: One run clock; a day is covered only when a delivered run fetched it after the day ended; the API day timezone is one constant

- **Status:** Accepted for build (DESIGN_SPEC v0.2, 2026-09-30). Not built yet. The timezone is an assumption until V-02 confirms it.
- **Date:** 2026-09-30
- **Requirements:** FR-04, FR-14, FR-16; AC-10, AC-11, AC-13 to AC-15; V-02
- **Source:** DESIGN_SPEC v0.2 D-4, §8.3; baseline weaknesses B1 and B2

## Context
Today "today" is the host's local date (`pipeline.py:279`): UTC on Azure and SAST on the laptop.
"Now" is read a second time inside the filter (`pipeline.py:262`). The coverage rule (FR-14), the
closing-soon flag (FR-16) and `days_left` all need a single clock. The timezone the API uses for
`dateFrom`/`dateTo` is unconfirmed (V-02).

## Decision
- Capture `run_at = datetime.now(UTC)` once per run. Every date calculation uses it.
- The window is `today − LOOKBACK_DAYS … today`, where `today` is `run_at` in `API_DAY_TZ`.
- `API_DAY_TZ` (UTC+2, SAST, assumed) is the **only** place the API's day timezone is assumed.
  South Africa has no daylight saving, so a fixed offset is enough (no `tzdata`).
- Day D is **covered** only when three things hold: a run with `run_at ≥ end of D` (00:00 on D+1
  in `API_DAY_TZ`) fetched D successfully, the fetch wasn't truncated by `MAX_PAGES`, and that run's
  digest was delivered. A day fetched only while it was still in progress is `partial`.

## Consequences
- Scheduled runs (06:00 and 18:00 UTC) give the same result whether the API uses UTC or SAST
  (DESIGN_SPEC §8.3), so V-02 can't break them.
- If V-02 shows the API uses another timezone, the fix is a one-line change to `API_DAY_TZ`. QA
  confirms it from the `date_min`/`date_max` values logged in RUN_SUMMARY (H-5).
- An off-schedule run between 22:00 and 24:00 UTC could pick the wrong window if the assumption is
  wrong (T-6). This is accepted.
