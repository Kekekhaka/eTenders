# ADR-0012: Mark tenders never sent before, and list them first

- **Status:** Accepted (operator decision 2026-09-30). Designed in ADR-0016 (memory) and ADR-0020 (order and flags), DESIGN_SPEC v0.2; not built yet.
- **Requirements:** FR-12, FR-13, NFR-12; AC-04 to AC-08

## Context
Each tender appears in about 10–12 consecutive emails with nothing marking what changed
(IDEA_BRIEF gap 4; Q4). This works against the triage target (SC4).

## Decision
Keep the **full list** in every email, but flag each tender that has **never appeared in a
successfully sent digest** as *new*, and list the new ones first. This needs a
memory of sent tenders (keyed by `ocid`) that lasts between runs.

## Consequences
- The product becomes stateful for the first time. It needs a durable store, and the Architect
  has to decide where (the existing Functions storage account is the obvious candidate) within
  the ≤ R5/month ceiling.
- A tender counts as "sent" only after the send succeeds. A failed send must not use up
  the tender's "new" status.
- The first run after go-live will flag everything as new.
- If the memory can't be read, the email has to say so rather than guess (AC-08).
