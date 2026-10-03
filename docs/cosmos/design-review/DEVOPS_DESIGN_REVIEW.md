# DEVOPS_DESIGN_REVIEW: eTenders digest (stage 3)

- **Reviewer:** DevOps Engineer · **Date:** 2026-09-30 · **Reviews:** `docs/cosmos/DESIGN_SPEC.md` v0.1
- **Read:** DESIGN_SPEC, REQUIREMENTS_PACK, ADR-0001, 0007–0010, `host.json`, `requirements*.txt`,
  `.funcignore` (incl. the uncommitted diff), `.gitignore`, `.github/workflows/deploy.yml`,
  `function_app.py`, `pipeline.py`, `email_sender.py`.
- **Ran (read-only):** `az functionapp show`, `az resource list` (both RGs), `az storage container-rm list`,
  `az storage account blob-service-properties show`, `az storage account show`, `az lock list`,
  `az functionapp deployment slot list`, `az functionapp list-flexconsumption-locations/-runtimes`,
  `az resource show` (SCM basic-auth policy, App Insights component). No keys, settings values or
  profiles were read. **Not run:** `what-if` (no Bicep CLI here), the tests, the eTenders API.

## Live facts that affect the design (2026-09-30)

| Fact | Evidence |
|---|---|
| `digest-state` does **not** exist yet. Containers: `azure-webjobs-hosts`, `azure-webjobs-secrets`, `scm-releases` | `container-rm list` |
| Blob soft delete **on, 1 day**; versioning off; container soft delete off | `blob-service-properties show` |
| Shared-key access at default (allowed); no SAS policy or key-expiry policy on the account | `storage account show` |
| **No resource locks** on `etenders-rg` or `Tut-digital_student`; `Tut-digital_student` has **no tags** and holds only `Digital-CS1`, `Digital-EMS`, `Digital-EMS/AzureManagedDomain` | `lock list`, `group show`, `resource list` |
| No deployment slots | `slot list` (empty) |
| SCM basic auth `allow: true` | `basicPublishingCredentialsPolicies/scm` |
| App Insights: no ingestion sampling at the resource (`SamplingPercentage` null), retention 90 d | `resource show` |
| Flex Consumption **is available in `southafricanorth`**; Python 3.11 EOL **2027-10-31**, 3.12 → 2028-10-31, 3.13 → 2029-10-31, 3.14 → 2030-10-31 | `list-flexconsumption-runtimes --location southafricanorth` |
| `deploy.yml` **already has `workflow_dispatch`** (line 6), with no inputs | `deploy.yml:3-6` |

## Verdicts at a glance

| # | Topic | Verdict | Class |
|---|---|---|---|
| 1a | 404 on the blob GET is always read as `first_run` | **Must change the design before build** | blocker |
| 1b | SAS expiry is parsed from `se=`, but the design puts expiry in the stored access policy | **Must change the design before build** | blocker |
| 1c | Container, SAS and Healthchecks manual steps and their order | DEPLOY_PLAN | should-fix |
| 2 | Small Bicep for the new container and soft delete only; full estate at the Flex migration | DEPLOY_PLAN | should-fix |
| 3 | `host.json` sampling fix | Fix is correct. Prefer `isEnabled: false`; add a line-length test | should-fix (build) |
| 4 | Rollback: `ref` input and `concurrency` | DEPLOY_PLAN | should-fix |
| 5 | Linux Consumption / Python 3.11 lifecycle | New ADR now; migrate in 2027 H1 | should-fix (later) |
| 6 | CI hygiene | DEPLOY_PLAN | should-fix / nit |
| 7 | ACS in `Tut-digital_student` | Operator action before go-live, not a design change | should-fix |

---

## 1. Deployability of the new design

### 1a. BLOCKER: a missing container must not look like a first run
§5.2 and §6.2 map any `404` on `GET {MEMORY_BLOB_URL}` to `first_run`. Blob storage also returns
404 when the **container** is missing (`x-ms-error-code: ContainerNotFound`). That happens if code
ships before the container exists, the container is deleted, or the URL has a typo. Every run
would then flag every row NEW with the first-run notice, the `If-None-Match: *` PUT would fail
with 404 each time, and it would look almost healthy. If the container were later re-created,
history would reset without anyone noticing.
**Change:** `first_run` only when the status is 404 **and** `x-ms-error-code == BlobNotFound`.
Any other 404 → `unavailable`. Add one offline test. With this fix, code that ships before the
container exists degrades safely to the `unavailable` banner, so deploy order becomes a preference
rather than a hazard.

### 1b. BLOCKER: where the SAS expiry lives
TB2, T-1 and ADR-0016 bind the SAS to a stored access policy "with a 2-year expiry". §8.4 logs
`sas_expires` "parsed from `se=`", and T-1 relies on it for the < 30-day notice. When the expiry is
in the policy, the token has **no `se`**, so the date can't be read. A field can't be set in both
the policy and the SAS.
**Change:** the policy (`digest-rw`) holds **permissions `rw` only**. The SAS carries `se`
(about 2 years), `spr=https` and `si=digest-rw`. You can still revoke instantly by deleting the
policy, and renewal means issuing a new SAS. The code treats a missing `se` as `"unknown"` and
must not crash. Also, because the expiry notice is the only warning before memory silently
degrades, promote it from Should to **Must**, or the DEPLOY_PLAN adds a calendar reminder.

### 1c. Manual steps, scriptability and order (DEPLOY_PLAN)

| Item | IaC? | How (the operator runs it; I don't) |
|---|---|---|
| Container `digest-state`, private | **Yes** (ARM child resource; §2) | Bicep, or `az storage container-rm create --storage-account etendersfuncstorage -g etenders-rg -n digest-state --public-access off` (management plane, no keys) |
| Stored access policy + SAS | **No** (data plane, not ARM) | `az storage container policy create … -n digest-rw --permissions rw`, then `SAS=$(az storage container generate-sas … --policy-name digest-rw --expiry <date> --https-only -o tsv)` captured in a variable, never echoed |
| `MEMORY_BLOB_URL` = `https://etendersfuncstorage.blob.core.windows.net/digest-state/memory.json?$SAS` | No (secret) | `az functionapp config appsettings set … -o none`. **`-o none` is mandatory:** this command prints *every* app setting's value by default. |
| Healthchecks.io check, `HEARTBEAT_URL` | No: sign-up, email verification and ToS are the operator's | Manual. A new check stays in "new" state and doesn't alert until its first ping, so it can be created before the code ships. Nit: a **cron** check `0 6,18 * * *` UTC with a 60-min grace alerts about 70 min after a missed run, rather than 14 h, for the same R0. |
| `.gitignore` + `.funcignore`: `etenders_memory.json`, `etenders_digest.html` (+ `infra` if §2 is adopted) | repo | Commit the pending `.funcignore` change (`.claude`, `docs`) with it |

**Order:** (1) container → (2) policy + SAS → `MEMORY_BLOB_URL` → (3) Healthchecks →
`HEARTBEAT_URL` → (4) push the code (with `host.json`). Then (5) verify: run 1 logs
`memory.read=first_run, write=ok` and the blob exists; run 2 logs `read=ok`; Healthchecks turns
green. Warn the operator that run 1 marks about 200 rows NEW (T-5).
**Timing rule:** setting app settings restarts the app, and so does a deploy. Either one kills an
in-flight run with no digest and no failure notice. Keep changes out of **05:45–06:15 and
17:45–18:15 UTC**.
**Coupling for Security:** the SAS is signed with an account key (key1 by default). Rotating that
key, or setting `allowSharedKeyAccess=false`, breaks both the SAS and, probably, `AzureWebJobsStorage`.

---

## 2. Infrastructure as code: yes, but small (DEPLOY_PLAN)

A full retrofit of the hand-made estate isn't worth it now. Declaring the function app risks
**wiping app settings**, because `siteConfig.appSettings` replaces the whole set, and putting them
in the template means secrets in parameters. The plan is also a retiring SKU, and a new Flex app
will replace it before 2027-10 (§5).

**Now:** one file, `infra/main.bicep` (about 25 lines), `targetScope` = `etenders-rg`:
- In scope: the storage account as `existing`; `blobServices/default` with
  `deleteRetentionPolicy.days = 7`, which keeps about 14 overwritten `memory.json` snapshots for
  recovery at a negligible cost; and the container `digest-state` with `publicAccess: None`.
  Optionally a `CanNotDelete` lock on `etenders-rg`.
- Out of scope: the function app, plan and App Insights (these wait for the Flex migration); all
  app settings and secrets; the stored access policy and SAS; Healthchecks; ACS (another RG, and
  only ever `existing`); the `DefaultWorkspace` in `DefaultResourceGroup-JNB`.
- **At the Flex migration:** full Bicep for the new plan, app, identity and App Insights link.
  Everything there is net-new, so what-if is clean.

**What-if:** yes. `az deployment group what-if -g etenders-rg -f infra/main.bicep` evaluates the
template against the live resources and changes nothing. It should report one Create (the
container) and one Modify (retention 1 → 7). Keep the default Incremental mode and **never pass
`--mode Complete`**, because that would delete every undeclared resource, including the app.
`blobServices/default` is a whole-object PUT, so read the what-if for any property it would reset
before the operator applies it with `az deployment group create`. Caveats I didn't verify: the
Bicep CLI isn't installed here (`az bicep install` downloads from GitHub and may be blocked by the
proxy), and what-if needs `deployments/whatIf` permission, which Reader may not include.

---

## 3. Observability: `host.json` sampling (should-fix, ships with the build)

The proposed `"excludedTypes": "Request;Trace"` is valid, and it does stop Python worker traces
(the `RUN_SUMMARY` line included) from being sampled. The fix is **correct**. Two refinements:
- **Prefer `"isEnabled": false`.** At about 2 runs a day, adaptive sampling saves nothing, and
  turning it off also keeps **Exception** telemetry, which holds the crash traceback, unsampled.
  Nothing samples at ingestion (`SamplingPercentage` null). If the Architect keeps sampling on,
  use `"Request;Trace;Exception"`.
- **Line length.** An App Insights trace message is truncated at 32,768 chars. A truncated JSON
  line makes `parse_json` fail, and the KQL then silently drops that run from SC1, SC5 and SC6.
  Add a test asserting `len(line) < 30000` on a worst-case summary (500 lags, 6 days, many missed
  days).
- The Smart Detection action group has 0 receivers and there are no alert rules. That is
  acceptable, since the heartbeat is the alert (ADR-0018). The §8.4 KQL gets verified after go-live
  (H-6).

---

## 4. Deploy and rollback (DEPLOY_PLAN)

**Today:** every push to `main` runs the tests and then deploys (`deploy.yml:3-47`). There are no
slots, so there's no swap rollback. The options are:
1. **`git revert` + push**: the gated path with an audit trail, about 3–5 min. This is the primary
   rollback, and it works now.
2. **Run workflow** on a branch or tag at the good commit. `workflow_dispatch` accepts a
   branch or tag, **not a bare SHA**, and it runs *that* ref's workflow file. The repo has no tags.
3. Re-deploying an older package from `scm-releases`. Whether old packages are retained isn't
   verified, so don't rely on it.

**Recommended (stage 8):**
- `workflow_dispatch.inputs.ref` (string, optional). Both jobs use
  `actions/checkout` with `ref: ${{ inputs.ref || github.sha }}`, so a chosen SHA is **tested and
  deployed with the current workflow**. Pass the input only through `with:`, never interpolated into
  `run:`, to avoid script injection.
- `concurrency: { group: deploy-prod, cancel-in-progress: false }` so a rollback and a push can't
  interleave.

**What a rollback doesn't undo:** app settings and `memory.json`. The design is rollback-safe:
pre-memory code ignores the blob, and an unknown schema → `unavailable` and is never overwritten
(§5.1). Rolling back for a few days and then forward reports those days as missed (T-4) and
re-flags tenders as NEW. That's acceptable, but document it. If a bad build writes valid but wrong
memory, restore an earlier soft-deleted snapshot (this needs the 7-day retention from §2).

---

## 5. Lifecycle risks, H-4 (should-fix: ADR now, work in 2027 H1)

- Deadlines: Python 3.11 on Functions ends **2027-10-31** (confirmed via `az`). Linux Consumption
  retires **2028-09-30** (DESIGN_SPEC §13; I didn't re-check it). The 3.11 date is the binding one.
- **Plan: one move, not two.** A new **Flex Consumption** app in `southafricanorth` (available) on
  **Python 3.13** (EOL 2029-10), or 3.12 if a pinned wheel lags. There's no in-place SKU change, so
  it's a new app plus a timer cut-over, which is where the full Bicep (§2) earns its keep.
- Bundle into the same move: **OIDC deploy** (`azure/login`) with SCM basic auth turned off again
  (ADR-0008 revisit), and optionally a **managed identity** to retire the SAS (T-1). Keep the
  420 s budget at cut-over, even though Flex allows longer timeouts. Re-measure cost (V-05).
- **When:** the BA records **ADR-0022** now. Start by **2027-03-01**, finish by **2027-06-30**, with
  a hard stop of 2027-10-31. Put calendar reminders in the DEPLOY_PLAN runbook. Reissue the SAS at
  cut-over.
- The design has nothing specific to Linux Consumption, so **no change is needed now**.

---

## 6. CI hygiene (DEPLOY_PLAN)

| Item | Recommendation | Class |
|---|---|---|
| Actions on tags (`deploy.yml:16,18,32,34,42`) | Pin full SHAs with a `# vX.Y.Z` comment. `Azure/functions-action` is the priority, since it receives the publish profile. Add `.github/dependabot.yml` (`github-actions`, monthly) to keep the SHAs current. | should-fix |
| Token scope | Add `permissions: contents: read` at the top of the workflow | should-fix |
| Pull requests | Add `pull_request: branches: [main]`, and guard `deploy` with `if: github.event_name != 'pull_request'`. The test job needs no secrets, so fork PRs are fine. | should-fix |
| Tested ≠ deployed (NFR-11) | A committed `constraints.txt` generated on **Linux / Python 3.11**, not Windows, and used with `-c` in both jobs | should-fix |
| Caching | `setup-python` with `cache: pip`, `cache-dependency-path: requirements*.txt` | nit |
| Indexing failures | An import error in production means "0 functions" with no alert until the heartbeat. QA should confirm that a test imports `function_app` and sees the timer registered (the `run()` tests probably cover this). | nit |

---

## 7. ACS in `Tut-digital_student` (should-fix, before go-live)

**Risk:** high impact, moderate likelihood. The RG looks like a disposable tutorial, has no tags
and no lock, and holds the **only** channel for both the digest and the failure notice. The
heartbeat detects a deletion within 14 h but can't restore it. Re-creating it gives a new
connection string and a **new managed-domain sender address**, so `SENDER_EMAIL` changes and inbox
trust (V-04) resets. The 10 emails/hour quota is shared across the subscription.

**Recommendation (operator, minutes, R0):**
1. `az lock create --name keep-etenders-email --lock-type CanNotDelete -g Tut-digital_student --notes "Email for etenders-digest-func"`.
   This blocks deletes only; sending and config changes still work. Do the same on `etenders-rg`.
2. Tag the RG `usedBy=etenders-digest`, and record the ownership in the DEPLOY_PLAN (plus an ADR-0007 note).
3. **Don't move or re-create ACS now.** An `az resource move` is possible in principle, but it's
   unverified for Email Services and domains and adds risk for little gain. Revisit at the Flex
   migration.
4. Operator question: does anything else in the subscription send mail? If so, the quota is at risk.

---

## Fold-in list for the Architect

1. §5.2 / §6.2: `first_run` only on 404 + `BlobNotFound`; any other 404 → `unavailable` (+ test). **Blocker.**
2. TB2 / T-1 / ADR-0016 / §8.4: the policy holds `rw` only; the SAS carries `se`, `spr=https` and `si`; a missing `se` → `"unknown"`; the expiry notice becomes Must. **Blocker.**
3. §8.4 / ADR-0019: `samplingSettings.isEnabled: false` (or add `Exception`); a test that the summary line is < 30,000 chars.
4. H-3: note that the container, the 7-day soft delete (and the optional lock) live in `infra/main.bicep`; the SAS and settings are steps in the DEPLOY_PLAN runbook.
5. H-4 → propose ADR-0022 (Flex + Python 3.13, 2027 H1), with the dates from §5.
