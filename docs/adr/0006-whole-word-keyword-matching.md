# ADR-0006: Whole-word keyword matching

- **Status:** Accepted
- **Date:** 2026-09-21 (`4810928`); recorded 2026-09-30
- **Requirements:** FR-05

## Context
Substring matching found "ai" inside "maintenance", "api" inside "capital" and
"ssis" inside "assistance". A chairs-repair tender once scored 18.

## Decision
Compile each keyword as a whole-word, case-insensitive pattern that allows
any whitespace run inside a phrase, so line-wrapped document text still matches
(`pipeline.py:199-212`).

## Consequences
- Short keywords ("ai", "ml", "sql", "api") no longer produce false positives inside other words.
- Plurals and variants need their own entries (e.g. "dashboards" matches but "dashboard" does not).
- Ready for Phase 2 document text.
- Covered by the three `test_score_*` tests in `tests/test_pipeline.py`.
