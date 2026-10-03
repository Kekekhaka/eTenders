# ADR-0005: The category filter is the volume control; the search is nationwide; the keyword score orders the list and never gates it

- **Status:** Accepted
- **Date:** 2026-09-21 (`c0c40bf`); recorded 2026-09-30
- **Requirements:** FR-04, FR-05, FR-06, NFR-09

## Context
Titles are opaque reference codes and descriptions are thin. Keyword scoring on
metadata found real matches in only 4 of 582 releases (IDEA_BRIEF §2). A province typo
("guateng") once discarded every Gauteng tender.

## Decision
- Keep open tenders (or ones with no closing date) whose category is in the 13
  `CATEGORIES` (`pipeline.py:78-94`, `261-269`).
- `PROVINCES = set()` means nationwide (`pipeline.py:72-73`). A test checks the
  spelling of any province added.
- The keyword score only sorts the list (`pipeline.py:271-273`). A zero-score tender
  in a wanted category is kept.

## Consequences
- Recall depends on assumption A2 (the 13 categories hold nearly all relevant tenders),
  which has **low confidence and has never been measured** (SC3; the operator owns the
  labelled-sample validation, Q2).
- Most rows score 0, so the "top" of the list is mostly arbitrary among ties
  (`sort_values` is not stable by default). FR-13 adds a deterministic tie-break.
- Tuning stays a one-line edit in `pipeline.py` (SC9).
