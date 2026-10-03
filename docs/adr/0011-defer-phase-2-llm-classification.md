# ADR-0011: Defer Phase 2 (LLM document classification); if built, use Azure-hosted models

- **Status:** Accepted (deferral). The trigger for starting Phase 2 is TBC (Q9).
- **Date:** recorded 2026-09-30
- **Requirements:** FR-20 (deferred)

## Context
The signal is in the tender documents, not the metadata (IDEA_BRIEF §2). An LLM
classifier is estimated at R6–22/month. NVIDIA's free API tier was considered, but its
terms forbid production use.

## Decision
Don't build Phase 2 now. If it is built, use Azure-hosted models (the IDEA_BRIEF lists
gpt-5-nano, DeepSeek-V4-Flash, gpt-oss-120b and gpt-4.1-mini as candidates), **not** the
NVIDIA free tier.

## Consequences
- Phase 1 stays simple and near-free, and its recall is bounded by the category filter (ADR-0005).
- Starting Phase 2 needs evidence: the operator's labelled sample (Q2) and the trigger (Q9).
- Scanned PDFs (about 5%) and the ≤ R25/month Phase 2 ceiling are left for that phase.
