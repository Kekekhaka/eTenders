# Team Leader decisions

Binding on every agent. The BA records the lasting ones as ADRs.

## Operator instructions (2026-09-30)

- **OP-1: Diagrams in PlantUML, not Mermaid** (ADR-0025).
- **OP-2: Cost guardrail.** Keep the running cost where it is now. Nothing
  that adds cost goes in without the operator's approval, and every
  recommendation is verified at the primary source, including free-tier limits.
  See `.claude/skills/projectcosmos/team-rules.md` → *Cost guardrail*.
- **OP-3: Baseline cost corrected.** Earlier documents say "R0.07/month"
  (the Team Leader's error: that was US dollars). The measured figure is
  **US$0.068/month ≈ R1.09/month** (Cost Management, 17–30 Sep 2026, US$0.00228
  a day on average, at Microsoft's R16.00 per US$). The subscription bills in
  USD. Every document must use the corrected figure.

## Decisions on the Lead Engineer's plan (WORK_BREAKDOWN §8)

| ID | Decision | Why |
|---|---|---|
| TL-1 | `is_safe_url` goes in `digest.py` (Lead-owned): one pure, ~10-line helper shared by Frontend and Backend. It's the only logic in the contract module. | The two engineers then share nothing but the contract, so the parallel build has no ordering dependency. |
| TL-2 | **Lighter memory validation.** Read at most 5 MB, parse the JSON, require `schema == 1`, convert types and clamp the retention dates. *Any* error in that sequence makes memory **unavailable**: the run doesn't crash, marks nothing NEW and writes nothing. Drop the exact-key-set checks and the per-map entry caps. | Meets the intent of Security S-10 (a tampered blob must not crash the run or fake NEW marks) with far less code. The blob is private and only this app writes it. |
| TL-3 | **Minimal RUN_SUMMARY:** outcome, counts, per-day ok/reason, memory status, heartbeat status, email bytes. Drop `new_lag_hours`, `became_covered`, `newly_missed`, `missed_named`, `counts.html_rows`, `fetch_s`, `date_min`/`date_max`. `report.py` keeps three public functions (no `html_rows`). | These fields serve KQL that hasn't been run. At the scheduled 06:00/18:00 UTC runs, UTC and SAST fall on the same date, so the V-02 date range isn't needed. |
| TL-4 | **Redaction by containment.** Helpers return a status and never re-raise or chain exceptions that carry URLs or headers. Apply one `redact()` to the failure-notice text and the top-level exception log. Keep `urllib3` and `azure` loggers at WARNING, and keep the AC-30 leak test. **Drop** the `RedactingFilter` on the root logger and every handler. | With the managed identity (ADR-0016), no secret travels in a URL: tokens are in headers, the blob URL isn't secret, and the heartbeat UUID is low-sensitivity (S-03). Security re-checks this at stage 7. |
| TL-5 | **Drop the xlsx `Coverage` sheet.** **Keep** the `TG.py` HTML preview. | Coverage already appears in the email banner. The preview is small, lets the operator see the email without sending it, and serves as the Frontend's rendered sample. |
| TL-6 | Drop `PartialDay.covered_earlier`. | Nice to have; it threads memory into the partial banner for little benefit. |
| TL-7 | Keep `digest.py` as the contract module (a third new module, over NFR-09's 2). | It earns its place as the contract-first interface. |
| TL-8 | **API_CONTRACT is authoritative** for O-2 (an ACS send counts as delivered only if the poller is `done()` and the status is `Succeeded`; a timed-out send is *not* delivered), O-3 (heartbeat by POST), D-3 (`closing_soon_days`), D-5/D-6 (the signature additions) and D-8 (`flatten` drops any `ocid` longer than 200 characters). The Architect reconciles DESIGN_SPEC to match. | O-2 is a real defect, verified in the installed library. The others are small, unambiguous corrections. |
| TL-10 | **Restore `missed_named`** in RUN_SUMMARY: a list of ISO dates for the permanently missed days named in this digest, **capped at 10 entries**. This resolves O-5: AC-26 and FR-19 stand as written for this field. | It's the only log-side evidence of lost days (SC1), and it costs one small field. The rest of TL-3 stands. |
| TL-11 | **Amend the ACs that the cuts made stale** (BA): AC-28, where an extra top-level key is now *ignored* and the read succeeds (TL-2, O-6); AC-21a, where the Coverage-sheet clause is removed (TL-5, O-7); and AC-34, where the worst-case setup uses only the fields that remain (TL-3/TL-10, O-8). | Keeps the requirements consistent with the contract the engineers build to. |
| TL-12 | **Accept Gmail clipping on the first run only.** On the first run every tender is NEW, so the email is about 223 KiB, and Gmail clips anything over about 102 KB (unverified). No contract change. | It happens once after deployment. Normal runs measure 67–76 KiB. The xlsx always has the full list, and Gmail's "view entire message" link still shows the rest. |
| TL-13 | **NFR-10's test-suite time target goes from 5 s to 15 s** (BA to amend). | The suite takes 6.6 s, almost all of it importing libraries (about 3.5 s) and collecting tests. Speeding that up gains nothing for a check that runs on every push. |
| TL-14 | **SC6 freshness (NFR-07) stays unmeasured in Phase 1** (BA to record). | After TL-3 no log field carries it. Adding one isn't worth the code for one user; revisit in Phase 2. |
| TL-15 | **Accept review option O-1:** simplify the memory schema before the first deploy. Drop the stored `missed` map (derive it instead) and the fields only ever written, never read. Also fix SF-4 (remove `updated_at` / `updated_by_run` validation). | Saves about 40 code and 25 test lines with no feature loss. After the blob is deployed, a schema change would need a migration, so it's now or never. |
| TL-16 | **Reject review option O-2** (removing the heartbeat). | Reverses TL-9 and FR-11. It is free and off by default, and it's the operator's only way to learn about silent failures. |
| TL-17 | Review nits: **fix** N-1 to N-5 and N-7. The tuning-constant test (N-7) must not fail when the operator legitimately edits `CATEGORIES` or `KEYWORDS`. **N-6:** use "tender(s)". **N-8:** the BA rewords it. **Keep N-9:** the commented-out `KEYWORDS` block is the operator's own on/off toggles, not dead code. | Cheap simplifications. N-7 protects the operator's tuning workflow. N-9 respects an operator edit. |
| TL-9 | **The heartbeat stays optional by configuration, with no cost by default.** Option A (Healthchecks.io): free Hobbyist plan, 20 jobs, we use 1 (verified at healthchecks.io/pricing, 2026-09-30). Email alerts on the free plan are **unverified**: the docs don't state it, so the operator confirms at sign-up. Option B (Azure log-search alert) adds cost and needs a verified price plus operator approval. Option C: none. | OP-2. |
