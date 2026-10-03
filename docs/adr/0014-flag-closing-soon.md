# ADR-0014: Show every open tender, and flag the ones closing within 7 days

- **Status:** Accepted (operator decision 2026-09-30). Designed in ADR-0017 (run clock) and ADR-0020 (flags), DESIGN_SPEC v0.2; not built yet.
- **Requirements:** FR-04, FR-16; AC-13 to AC-15

## Context
Some tenders (RFQs especially) have short windows (A5). The operator hasn't
set a minimum lead time (Q5), so nothing should be hidden for being close to its deadline.

## Decision
Keep every open tender. Mark as **closing soon** any tender whose closing date is
after the run time and no more than **7 days** after it. Make the 7 a single tunable
constant in `pipeline.py`.

## Consequences
- No tender is dropped for being urgent, so recall isn't affected.
- Timezones must be handled correctly (closing dates carry offsets; show them in SAST).
- A tender with no closing date is never marked closing soon. It's shown as "no closing date".
- This is separate from SC7's lead-time N, which is still open (Q5).
