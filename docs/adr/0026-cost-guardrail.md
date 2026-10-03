# ADR-0026: Cost guardrail: keep the running cost at today's level

- **Status:** Accepted (operator instruction OP-2, 2026-09-30)
- **Date:** 2026-09-30
- **Requirements:** NFR-01 (SC8); applies to every decision that touches cost (notably ADR-0016, ADR-0018, ADR-0019)
- **Source:** `docs/cosmos/TEAM_LEADER_DECISIONS.md` OP-2 and OP-3; `.claude/skills/projectcosmos/team-rules.md` → *Cost guardrail*

## Context
The operator runs the product on a personal budget. The baseline cost had been misreported as
"R0.07/month", a US-dollar figure labelled as rand (OP-3). The measured figure is **US$0.068/month ≈
R1.09/month** (Cost Management, 17–30 Sep 2026, US$0.00228 a day on average, at Microsoft's R16.00 per
US$). The subscription bills in US dollars. Several design figures had been estimated rather than
checked at source.

## Decision
- **Keep the running cost at today's level.** The Phase 1 ceiling (SC8, NFR-01) stays at ≤ R5/month,
  but the working target is today's ≈ R1.09/month.
- **Nothing that adds cost goes in without the operator's explicit approval.** A paid resource,
  plan, SKU, service or subscription can be offered as an option with its cost, never as the default.
- **Prefer what is already free:** built-in platform features, existing resources, and free tiers that
  the actual usage fits inside with room to spare.
- **Verify every cost and free-tier claim at the primary source:** the vendor's pricing page, the
  Azure Retail Prices API (`currencyCode=ZAR`) or `az` output.
  - Cite the source and the date.
  - State the free-tier limits, and what happens when they're exceeded.
  - Label anything you can't confirm as **unverified**.
- **Currency:** always say which currency a figure is in. Quote rand per month, converted at Microsoft's
  own rate, and name the assumption that drives the figure (volume, ingestion size).
- **Ownership:** the Architect owns the cost budget (DESIGN_SPEC → *Cost budget*). DevOps and Security
  check each recommendation against it and flag anything that would raise the monthly cost.

## Consequences
- Heartbeat option B (an Azure log-search alert) can't be the default. It needs a verified price and the
  operator's approval (TL-9). Option A (Healthchecks.io free plan) or C (none) keep the cost unchanged.
- Earlier cost figures were corrected in IDEA_BRIEF (§2, SC8, A7), REQUIREMENTS_PACK (NFR-01),
  ADR-0001 and ADR-0010. A7's "below R1/month" was restated, because it is false at ≈ R1.09.
- Design-cost figures are owned by DESIGN_SPEC → *Cost budget* (being updated to v0.3). Other
  documents point to it instead of copying figures.
- V-05 (re-measure the cost after go-live) becomes the check that this guardrail held.
