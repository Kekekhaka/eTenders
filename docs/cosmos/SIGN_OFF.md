# SIGN_OFF: eTenders digest

Team Leader, 2026-10-01. Mode: **full for stages 1-6** (an audit and hardening of the existing digest), **quick for stages 7-9** (to save tokens, at the operator's request).

## What was built versus the idea

The baseline (a twice-daily fetch, filter, rank and email) now also does what the operator chose:

| Operator decision | Delivered |
|---|---|
| Mark new tenders | Memory of sent tenders (`memory.py`, a blob in Azure; a local file for `TG.py`); NEW tenders listed first |
| Report permanently missed days, no backfill | Days that left the window unfetched are named for 7 days, with a note to check the portal by hand |
| Show every tender, flag urgent ones | CLOSING SOON (within 7 days), never hidden |
| Improve both the email and the xlsx | `report.py`: banners, NEW and CLOSING SOON labels, safe links, one `Tenders` sheet |
| Keep it cheap | Design adds about R0.01/month to the measured US$0.068 (about R1.09) |
| Keep it simple | The Reviewer found the growth justified (1,750 code lines; 47 removed after review); no new dependencies |

Also fixed: untrusted API data can no longer crash a run, no formula or unsafe link reaches the xlsx, secrets cannot reach logs or the failure email, and a send counts as delivered only when Azure confirms it.

## Gate results

| Stage | Result | Evidence |
|---|---|---|
| 1 Ideologist | MET | IDEA_BRIEF, 9 success criteria |
| 2 Business Analyst | MET | REQUIREMENTS_PACK v0.4, 26 ADRs |
| 3 Architect (+ Security and DevOps design reviews) | MET | DESIGN_SPEC v0.4, verified cost budget |
| 4 Engineering | MET | contract-first; 11 integration tests |
| 5 Reviewer | **Pass** after one fix round | REVIEW_REPORT |
| 6 QA | **MET**, then 6 defects fixed | 42 of 42 ACs pass; 56 of 56 reintroduced bugs caught; no regression against the old scoring on 600 releases |
| 7 Security (quick) | **MET** | no confirmed or probable vulnerability; a fake-secret test through every sink came out clean |
| 8 DevOps (lite) | **MET** | deploy.yml hardened; operator steps written with verify commands |
| 9 Final docs | MET | README rewritten and fact-checked (I corrected 4 errors) |

Final check by the Team Leader: **457 tests pass**; every file parses under the Python 3.11 grammar; `requirements.txt` unchanged; `deploy.yml` verified complete.

## Open risks

| # | Risk | Owner |
|---|---|---|
| 1 | **Memory is not live yet.** The `digest-state` container and the managed identity do not exist (DevOps checked read-only). Until the steps in `DEPLOY_PLAN.md` §2 are done, every digest still goes out with a "memory unavailable" banner and nothing marked NEW. | Operator |
| 2 | **Silent-failure monitoring is off by default** (FR-11 and NFR-06 unmet). Options: Healthchecks.io (free; email on its free plan is **unverified**), an Azure alert (+about R8/month, needs your approval), or none. | Operator |
| 3 | The managed-identity token endpoint on this Linux Consumption app, the Blob first-write response and Healthchecks POST are **unverified**; they have only been tested against fakes. | DevOps step 4 |
| 4 | Not tested on a real Python 3.11 interpreter, in Outlook, Gmail or on phones, and the PlantUML diagrams are not rendered. | Operator |
| 5 | **Secrets sit in an NTT OneDrive folder.** `local.settings.json` holds live keys and syncs to NTT's OneDrive. Move the project to an unsynced folder and rotate the ACS and storage keys. | Operator |
| 6 | Deploy uses a long-lived publish profile with SCM basic auth on (accepted, ADR-0008). Actions are pinned by tag, not SHA. A move to OIDC is free but optional. | Operator |
| 7 | The first run after memory goes live lists every tender as NEW; the email is about 223 KiB and Gmail may clip it (accepted). | Accepted |
| 8 | SC6 (freshness) is not measured in Phase 1 (accepted). The suite takes 10 s on a quiet machine and up to 40 s on a busy one, against a 15 s target. | Accepted |
| 9 | Open question from the brief: whether your employer's policy allows bidding on government work while employed there. The commit history also carries your NTT email. | Operator |

## Not done

Nothing is committed. 25 files are changed or new; you commit them. Phase 2 (reading tender documents with an LLM) is deliberately deferred. The Azure resources have not been changed by anyone.

## Next steps, in order

1. Do the §2 steps in `DEPLOY_PLAN.md`, then watch one run's logs.
2. Choose the heartbeat option.
3. Set a personal git identity, commit and push; the workflow tests, then deploys.
4. Move the project out of OneDrive and rotate the keys.
5. Live with it for a week, then decide on Phase 2 using the success criteria.

## Cost of this run (for reference)

Agent tokens as each agent reported them: roughly 80-330k per agent for stages 1-6 (several agents were resumed for re-passes, so their reports are cumulative; I did not total them), then **37k** (Security), **20k** (DevOps) and **24k** (final docs) in quick mode. The Azure running cost is unchanged by the pipeline.
