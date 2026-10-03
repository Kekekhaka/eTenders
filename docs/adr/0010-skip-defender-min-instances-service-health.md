# ADR-0010: Skip Microsoft Defender plans, minimum instances and a Service Health alert

- **Status:** Accepted
- **Date:** recorded 2026-09-30
- **Requirements:** NFR-01, NFR-06

## Context
Azure recommendations and defaults suggest Defender plans, always-ready (minimum)
instances and Service Health alerts. The product costs **US$0.068/month ≈ R1.09/month** (SC8; Cost
Management, 17–30 Sep 2026, at Microsoft's R16.00 per US$). *(Corrected 2026-09-30, OP-3: this used to
say "about R0.07/month", which was US dollars mislabelled as rand.)*

## Decision
Don't enable them. Defender plans and minimum instances would each add a new recurring cost, and its
size is **unverified**: no price has been checked at a primary source. A Service Health alert doesn't
match the product's failure modes. The real failure modes are the eTenders API being down, which Azure
Service Health doesn't report, and email non-delivery. Under ADR-0026, anything that adds cost would
also need the operator's approval.

*(Corrected 2026-09-30, OP-3: an earlier version said each would cost "30–400x the product". That ratio
was computed against the mislabelled R0.07 base, so it has been withdrawn, not recomputed.)*

## Consequences
- Cost stays inside the Phase 1 ceiling (≤ R5/month, Q1 TBC).
- There is no platform-level threat detection. The attack surface is small: timer trigger only, no
  HTTP trigger, and no inbound data other than the API responses.
- Silent non-delivery (SC5) still has to be solved some other cheap way (FR-11). *Update
  2026-09-30:* ADR-0018 designs this as an optional heartbeat, and the operator chooses the option at deploy time.
