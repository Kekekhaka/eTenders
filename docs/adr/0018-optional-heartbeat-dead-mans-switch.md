# ADR-0018: Silent-failure detection is an optional dead-man's switch, switched on by configuration

- **Status:** Accepted for build (Team Leader review decision B8, 2026-09-30). Not built yet.
  **The operator picks the option at deploy time** (H-4). Until the operator picks A or B, FR-11 and NFR-06 are not met.
- **Date:** 2026-09-30
- **Requirements:** FR-11, FR-24, NFR-04, NFR-06, NFR-15; AC-23, AC-31 to AC-33
- **Source:** DESIGN_SPEC v0.2 D-3, §6.2, §7 TB5, §8.2; Security S-03; DevOps DO-1c; residual risks R-1 to R-3;
  **amended by TL-8 (O-3) and TL-9** (`docs/cosmos/TEAM_LEADER_DECISIONS.md`) and ADR-0026

> **Amendments (2026-09-30):**
> - **TL-8 / O-3:** the ping is sent by **POST** (API_CONTRACT is authoritative), not GET.
> - **TL-9 / ADR-0026:** heartbeat monitoring stays optional, with **no cost by default**.
>   - Option A uses the Healthchecks.io **free Hobbyist plan**: 20 jobs, of which we use 1 (verified
>     by the Team Leader at healthchecks.io/pricing, 2026-09-30). Whether the free plan sends **email
>     alerts is unverified**, so the operator confirms it at sign-up.
>   - Option B adds cost, so it needs a **verified price and the operator's approval**. The "about R9"
>     figure below was unverified and converted at a guessed rate, so it is withdrawn. See DESIGN_SPEC →
>     *Cost budget* (being updated to v0.3).
>   - Option C: none.

## Context
The digest and the failure notice both go through ACS (`email_sender.py:62-70`). If ACS or the
app itself fails, the operator hears nothing (FR-11, NFR-06). ADR-0010 ruled out a Service Health
alert. The cost ceiling is R5/month (⟨TBC-Q1⟩), and the cost guardrail (ADR-0026) keeps the running cost at today's level. The operator wants to control both the cost and
whether a third party is involved.

## Decision
- Heartbeat monitoring is **optional, switched on by configuration**, via the app setting `HEARTBEAT_URL`.
- **When it's set** (it must start with `https://hc-ping.com/`):
  - after a delivered digest, ping `HEARTBEAT_URL`. Otherwise ping `HEARTBEAT_URL/fail`.
  - Use `allow_redirects=False` and a 5 s timeout, with no retry. The body is a fixed allow-list
    (`run_id`, `outcome`, `counts`) with no exception text, URLs or tender text.
  - Ignore the response apart from its status. A ping failure is recorded in RUN_SUMMARY and never raised.
  - Treat the URL as a secret and redact it (ADR-0024).
- **When it's unset**, skip the pings. Both RUN_SUMMARY and the digest footer say heartbeat
  monitoring is off. A value with the wrong prefix counts as `misconfigured`: no ping is sent, and the
  footer says monitoring is off.
- **The operator chooses one option at deploy time:**

  | Option | Cost/month | FR-11 / NFR-06 |
  |---|---|---|
  | A. Healthchecks.io heartbeat (free Hobbyist plan, 20 jobs, we use 1; email alerts on the free plan unverified; recommended as a cron check `0 6,18 * * *` UTC with a 60-minute grace; 2FA on; alerts to a mailbox that doesn't depend on ACS) | R0 | Met once deployed |
  | B. Azure log-search alert: no `RUN_SUMMARY` with `outcome = digest_delivered` in 13 h, sent to an action-group email | Adds cost: **unverified**; needs a verified price and operator approval (TL-9, ADR-0026) | Met once deployed |
  | C. None | R0 | **Not met** (R-1) |

## Consequences
- **While heartbeat monitoring is off (option C, or before A or B is set up), FR-11 and NFR-06 are
  not met.** A total ACS or app failure is then silent. Every digest footer says so (FR-24), so the gap is visible.
- With option B, the digest footer also says "Heartbeat monitoring is off". That is accurate,
  because the alert lives outside the app.
- Option A: anyone who has the URL can suppress or forge alerts, but it exposes no data. If
  Healthchecks.io goes away, detection is lost silently (R-2). This is mitigated by the `heartbeat`
  field in RUN_SUMMARY and a monthly look at the check (runbook).
- A Healthchecks check stays silent until its first ping, so it can be created before the code ships.
