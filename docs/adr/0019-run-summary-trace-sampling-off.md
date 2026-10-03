# ADR-0019: One structured RUN_SUMMARY trace per run, with Application Insights sampling off

- **Status:** Accepted for build (Team Leader review decision B7 on sampling, 2026-09-30). **Amended by
  TL-3 and TL-4** (`docs/cosmos/TEAM_LEADER_DECISIONS.md`, 2026-09-30). Not built yet.
- **Date:** 2026-09-30
- **Requirements:** FR-19, NFR-07; AC-26, AC-34, AC-38; SC5 (SC1 partly; SC6 no longer measured from logs, see TL-3 below)
- **Source:** DESIGN_SPEC v0.2 D-5, §8.4; DevOps DO-3; baseline weakness B6; TL-3, TL-4

> **TL-3 amendment (supersedes the field list and array caps below).** RUN_SUMMARY is **minimal**.
> It holds the outcome, the counts, each day's ok/reason, the memory status, the heartbeat status and
> the email byte sizes. `new_lag_hours`, `became_covered`, `newly_missed`, `missed_named`,
> `counts.html_rows`, `fetch_s` and `date_min`/`date_max` are dropped, and so are the caps that went
> with them (`reason` stays ≤ 60 chars).
>
> **Consequence:**
> - SC5 stays measurable from logs.
> - SC1 can be measured only per run (day ok/failed), not as "covered vs permanently missed".
> - SC6 freshness (NFR-07) is no longer measurable from logs.
> - V-02 is confirmed by reasoning instead of logged date ranges: at 06:00 and 18:00 UTC, UTC and SAST
>   fall on the same date.
>
> **TL-10 amendment:** `missed_named` is kept: the ISO dates of the permanently missed days named in
> the digest, capped at 10. SC1 can therefore be measured partly from logs. API_CONTRACT §3.8 is the
> authoritative field list.
>
> **TL-4 amendment:** there is no redacting log filter. The summary contains only status strings and
> counts, so it carries no secrets by construction.

## Context
Today a run logs only the total and the missing count (`function_app.py:19`), so SC1, SC5 and SC6
can't be measured. `host.json:6-9` has adaptive sampling on (`isEnabled: true`,
`excludedTypes: "Request"`), so traces, including any summary line, could be dropped.

## Decision
- Every invocation writes exactly one line, `RUN_SUMMARY {json}`, in `finally`, through the
  redacting log filter (ADR-0024). The fields are in DESIGN_SPEC §8.4.
- Keep the line **under 30,000 characters**, because App Insights truncates a trace at 32,768 and a
  truncated line breaks `parse_json`. Cap the arrays: `new_lag_hours` at 500 values, `newly_missed`
  and `missed_named` at 60 each, and `reason` at 60 chars. A test checks a worst-case summary.
- **Application Insights sampling is off:** `host.json` →
  `logging.applicationInsights.samplingSettings.isEnabled: false`.
- The KQL for SC1, SC5 and SC6 is in DESIGN_SPEC §8.4. It hasn't been run yet, because there are no rows.

## Alternatives rejected
- **OpenTelemetry:** a new dependency for one log line.
- **Keeping sampling on with a longer `excludedTypes` list:** more configuration for no saving at two
  runs a day. It would also risk sampling out exception telemetry.

## Consequences
- SC1, SC5 and SC6 become measurable from App Insights (90-day retention).
- Exceptions and traces are never sampled out. At two runs a day, ingestion stays within the free tier.
- QA verifies the KQL after go-live (H-5).
