# ADR-0009: Pinned dependencies, and offline tests gate every deploy

- **Status:** Accepted
- **Date:** 2026-09-25 (`f60e30a`); recorded 2026-09-30
- **Requirements:** NFR-10, NFR-11

## Context
Unpinned installs could change behaviour between deploys, for example with a pandas major
release. Tests that call the live API would be slow and flaky, and they would hammer a
fragile public service.

## Decision
- Pin direct dependencies exactly (`requirements.txt:1-5`; `requirements-dev.txt` adds
  `pytest==9.1.1`).
- The tests replace the network with fakes (`tests/test_pipeline.py:33-49`) and never send
  email (`tests/test_email_sender.py`). They run in about 1–2 s and gate the deploy (ADR-0008).

## Consequences
- 17 tests (10 pipeline, 7 email). They passed locally on 2026-09-30:
  `17 passed in 1.41s`.
- Transitive dependencies aren't locked (there's no lock or constraints file), so the build is only
  partly reproducible.
- Behaviour that depends on the live API (real payload shape, date semantics) isn't
  covered by any test.
