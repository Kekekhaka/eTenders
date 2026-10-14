# ADR-0029: Split a failing page to skip only the bad records

- **Status:** Accepted (operator approval, 2026-10-14)
- **Date:** 2026-10-14
- **Code:** `pipeline._fetch_day`, `pipeline._fetch_block`, `pipeline.DayResult.skipped`; `digest.Digest.skipped_releases`

## Context
From 5 October most weekdays came back HTTP 500, and the digest marked them partial or missed. Probing
the API on 14 October showed:
- The 500 has an empty body. It is not a format change.
- It happens whenever a page holds certain records. The failing request fails at every page size
  (5, 10, 50, 2000), while neighbouring pages load.
- Fetching one record at a time showed 1 to 7 unservable records per day, usually among the first ten
  of the day. Every other record on those days (100+) loaded fine.

The old code asked for the whole day in one page, so a single bad record lost the day.

## Decision
- `PAGE_SIZE` is 1024, a power of two.
- If a page keeps failing with a server error (HTTP 5xx), split it into its two halves (pages `2p-1` and
  `2p` of half the size) and fetch each. Repeat until the failing record is alone, then skip it.
- A record is skipped only after two attempts at size 1. The day stays **ok**, and the number skipped is
  recorded in `DayResult.skipped`, `RUN_SUMMARY` (per day) and the email footer.
- Limits stop a server that is simply down from being hammered: 120 split requests and 20 skipped
  records per day. Past either, or if the server answered no request at all, the day fails as it did
  before.
- Timeouts and 4xx errors are not split.

## Consequences
- Days that were lost now load. Tested on 5 to 14 October: 1,059 records fetched and 18 skipped, where
  7 of the 10 days had failed completely.
- About 20 extra requests per bad record (a few seconds). No cost.
- **A skipped record may be a relevant tender.** The email says how many were skipped so the operator
  can search etenders.gov.za by hand.
- The same bad record is skipped again on every run while it stays in the window, so the footer
  count is "currently unreadable", not "new".
- The API's pagination must stay offset-based. If it changed, the halves would not line up; the
  tests assume the current behaviour.
