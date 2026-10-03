# ADR-0016: Run memory is one JSON blob, reached over Blob REST with the Function App's managed identity, and written only after delivery

- **Status:** Accepted for build (Team Leader review decisions B1 and B2, 2026-09-30). Not built yet.
  One caveat is open: the identity token endpoint hasn't been verified on this Linux Consumption app (R-8).
- **Date:** 2026-09-30
- **Requirements:** FR-12, FR-14, FR-15, FR-22, NFR-08(e), NFR-12, NFR-14; AC-06 to AC-08, AC-12, AC-27, AC-28, AC-36
- **Source:** DESIGN_SPEC v0.2 D-1, D-2, §5.1, §5.2, §6.2, §6.3, §8.1; Security S-01, S-10; DevOps DO-1a, DO-1b (moot)

> **Amendment (TL-15, 2026-10-01; API_CONTRACT v0.3 §3.3):** the schema is simplified **before the first
> deploy**. The stored memory is `schema`, `tracking_since`, `resolved_through`, `sent` and `days`
> (`{date: "partial" | "covered"}`). The `missed` map, `updated_at`, `updated_by_run` and `days[].at` are
> dropped; missed days are derived (ADR-0013). Old keys in a blob are ignored and not written back. A
> later schema change would need a migration, which is why this was done now.
>
> **Amendment (TL-2, `docs/cosmos/TEAM_LEADER_DECISIONS.md`, 2026-09-30):** memory validation is
> **lighter**. Read at most 5 MB, parse the JSON, require `schema == 1`, convert the types and clamp the
> retention dates. *Any* error in that sequence makes memory unavailable: the run doesn't crash, marks
> nothing NEW and writes nothing. The exact-key-set checks and per-map entry caps are dropped. See
> ADR-0023.

## Context
ADR-0012 and ADR-0013 need state that lasts between runs: which `ocid`s were sent, and which
days were covered or missed. The cost ceiling is R5/month (⟨TBC-Q1⟩). The operator wants few moving
parts and no new frameworks (NFR-09). The Function App has no identity today, and the storage account
`etendersfuncstorage` already exists (DESIGN_SPEC §2.2).

## Decision
- The memory is one JSON document, `digest-state/memory.json`, in a new private container in the
  existing `etendersfuncstorage`. Its shape is in DESIGN_SPEC §5.1. The URL is a non-secret code
  constant (`memory.MEMORY_URL`), not an app setting.
- **Access uses the Function App's system-assigned managed identity, called with plain `requests`:**
  - get a token from `IDENTITY_ENDPOINT` (header `X-IDENTITY-HEADER`, resource `https://storage.azure.com/`);
  - call the Blob REST API with `Authorization: Bearer …` and `x-ms-version: 2021-08-06`;
  - grant the role **Storage Blob Data Contributor**, scoped to the `digest-state` container only.
- Read the memory before fetching. It is a **first run only** on `404` with
  `x-ms-error-code: BlobNotFound`. Every other result is **unavailable**. That includes
  `ContainerNotFound`, a missing identity, a token failure, other HTTP codes, timeouts, and invalid or
  oversized content. When the memory is unavailable, nothing is marked NEW, no missed days are named,
  a banner says so, and **nothing is written**.
- Write only after the digest is delivered, with an ETag-conditional PUT (`If-Match`, or
  `If-None-Match: *` on a first run).
- `TG.py` uses a local `FileStore` (`etenders_memory.json`). It never asks for a token, so it can't
  change the production memory (FR-22).

## Alternatives rejected
- **SAS URL (the v0.1 design).** Rejected per Security S-01. Managed identity leaves no stored
  secret (a SAS would sit in an app setting and in URLs that can leak through exception text, S-02).
  It has no expiry to manage (DO-1b), and it needs no new packages. A service SAS also stops working
  silently if the account key is rotated or shared-key access is disabled.
- **`azure-storage-blob` + `azure-identity`.** Rejected: three packages plus native `cryptography`
  (NFR-11, S-08).
- **Functions blob bindings.** Rejected: they fail the invocation before our code runs, so a digest
  can't be sent with a banner (AC-08), and `TG.py` can't use them.
- **Table storage.** Rejected: a second backend and more code for no gain.

## Consequences
- Added cost: see DESIGN_SPEC → *Cost budget* (being updated to v0.3). The earlier "about R0.01/month"
  wasn't verified at a primary source, so it has been withdrawn (ADR-0026). No new Azure resource: a container, an identity (a property of the
  existing app) and one role assignment.
- There is no secret to leak, and the token can only be minted inside the app sandbox.
- **Caveat (R-8): the managed-identity token endpoint is unverified on this Linux Consumption app.**
  If it doesn't work, the memory is permanently "unavailable": the digest still goes out, with the
  banner, but without NEW marking or missed-day tracking. DevOps verifies the endpoint at stage 8. The
  fallback, the SDK plus `azure-identity`, would need a new ADR.
- The first runs after the role is granted may show "unavailable" while the role propagates.
- A write that fails after delivery can cause a false "new" on the next run (T-2, R-9). This is accepted.
- The blob URL constant changes with one edit at the Flex migration (ADR-0022, T-10).
- Stage 8 work: create the container, enable the identity, grant the role, and verify the token endpoint.
