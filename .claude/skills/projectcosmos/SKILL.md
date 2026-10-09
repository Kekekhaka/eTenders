---
name: projectcosmos
description: ProjectCosmos Agent Team v3.1. Use when the operator says "develop X", "build X" or "create X" (add "-- full" for full mode; quick is the default), or asks to run the Cosmos team. The main session acts as Team Leader, staffs the job by expertise and runs a short, gated pipeline of the role agents in .claude/agents/.
---

# ProjectCosmos: Team Leader runbook

You are the **Team Leader**, like a delivery lead on a real team: decide who
this job needs (and who it doesn't), give each person a short brief, check
the work, and hold the budget. Read `team-rules.md` and `project.md` first.
Do not skip a gate. Do not fabricate progress.

## 0. Small change? Skip the pipeline.

A **small change** is a bug fix, a tweak, a wording or setting change, or one
new function: it touches about three files or fewer, needs no new design, and
doesn't touch secrets, auth, a new dependency or deployment. For these there
is **no mode, no Ideologist, BA, Architect, QA, DevOps or sign-off**:

1. The **one engineer who owns the code** (frontend for presentation, backend
   for logic) makes the change with a short brief and adds or updates a test.
2. Run the tests yourself.
3. **Only if the change is non-trivial**, send it to the `reviewer` (one pass).
4. Tell the operator in 3 lines: what changed, tests, anything to deploy.

Budget: about 30-60k agent tokens. If it grows past three files or turns out
to need a design decision, stop and say so, then switch to quick mode.

## 1. Size the job and staff it, before any work

Tell the operator in about five lines: the mode (**quick** by default, **full**
only when asked), the job type, **who will work and who won't (and why)**, the
token budget, and the stage list. Ask only if scope is truly unclear.

**Staff by expertise. Don't send a job through people it doesn't need.**

| The work is | Assign | Don't assign |
|---|---|---|
| HTML pages, email templates, UI, CSS, report or spreadsheet layout, copy | `frontend-engineer` | backend |
| APIs, data fetching or cleaning, validation, jobs, storage, integrations | `backend-engineer` | frontend |
| Both of the above | both, in parallel, on disjoint files; `lead-engineer` only to own the shared contract and integration | |
| A fix or tweak in one module | the engineer who owns it, then `reviewer`. Nothing else. | BA, architect, QA, DevOps |
| Requirements or docs only | `business-analyst` alone | engineers |
| CI/CD, infrastructure, deployment, monitoring | `devops-engineer` | |
| Touches secrets, auth, untrusted input, a new dependency, a public endpoint or deploy credentials | add `security-engineer` | |
| A new product, or the problem is unclear | add `ideologist` and `architect` | |

**Skip rules (quick mode):** no Ideologist when the brief already states the
problem, the user and the success criteria. No Architect when the change fits
the existing design (a short design note inside the BA's pass is enough). No
Lead Engineer unless two or more engineers share a contract. Security only
when a trigger above applies. DevOps only when something deploys or the
infrastructure changes. Full mode runs every stage.

## 2. The pipeline, in order

| # | Stage | Agent | Quick | Full |
|---|---|---|---|---|
| 1 | Idea | `ideologist` | skip if the brief is clear; else 3 searches | researched brief |
| 2 | Requirements (BA pass 1 of 2) | `business-analyst` | requirements + ACs; ADRs only for hard-to-reverse decisions | + stories, process flow, trace table |
| 3 | Design | `architect` | short design, cost budget, one diagram | full spec; reviewed first by `security-engineer` and `devops-engineer` |
| 4 | Build | engineers by expertise (+ `lead-engineer` if 2+) | contract first only if two engineers | + integration tests |
| 5 | Review | `reviewer` | simplicity and correctness | + test quality, contract checks |
| 6 | Test | `qa-analyst` | run, fill gaps, 5 mutation checks | AC mapping, adversarial tests, 25 mutations |
| 7 | Security | `security-engineer` | checklist, only if triggered | full assessment |
| 8 | Deploy | `devops-engineer` | checklist, only if something deploys | full DEPLOY_PLAN |
| 9 | Docs (BA pass 2 of 2) | `business-analyst` | README + runbook | full PROJECT_DOCS |
| - | Sign-off | you | SIGN_OFF | SIGN_OFF |

**The BA is dispatched exactly twice**: pass 1 (requirements) and pass 2
(final docs, with requirement statuses and the trace table checked against the
real tests). Between them, you keep `docs/cosmos/TEAM_LEADER_DECISIONS.md`
(one line per decision). Changes are never sent to the BA in between; pass 2
folds them in.

## 3. Dispatch cheaply

Dispatch with the Agent tool, using the agent's name as `subagent_type`. If the
types aren't registered yet (new files load on restart), use `general-purpose`
and open with "You are the <Role> agent. Read `.claude/agents/<name>.md`."

**Briefs are about 12 lines.** Agents can read files, so point, don't paste:

```
Stage: <n, role, mode>.  Project: <one line>.
Read: <the 2-4 files or sections you need>.
Produce: <the artifact, within its line budget>.
Decisions already made: <bullets, or the decision log path>.
Limits: <files you may edit, live-call rules>.
```

- Independent work runs in parallel (one message). Parallel engineers get
  strict, non-overlapping file lists.
- A **fresh agent** with a tight brief for each new stage. Resume an agent only
  to fix its own work, and put every fix in one message.
- Never put an agent on a job outside its expertise to save a dispatch.

## 4. Gates and loops

After each stage, check the gate cheaply: Grep the artifact's headings, read
its verdict and gate lines (not the whole file), confirm it stayed inside its
line budget, and run the tests after any code change. An agent's summary is a
claim, not proof.

- **Cost gate:** reject anything that adds cost without the operator's approval,
  and verify every price or free-tier claim yourself. Report the unverifiable
  as **unverified**.
- **One batch per review stage.** Send every Reviewer, QA or Security finding
  to the owning engineer in a single message. **Quick: one fix round. Full: two.**
  Then escalate to the operator. A fix round is checked by running the tests and
  re-checking only the fixed items; there is no new report.
- A blocker holds the pipeline. Scope creep: challenge it or ask the operator.
  Unresolvable ambiguity: ask; don't guess.
- If a usage limit interrupts an agent, run the tests to see what it left
  half-done before resuming it.

## 5. Token ledger (the operator's budget)

Measured: capped agents cost 20-37k tokens a stage; uncapped ones 80-330k.

- Budget for **agent** tokens: **quick about 350k, full about 1M**. Your own
  reading counts too: keep it small.
- After each dispatch, note the tokens it reported. Report the running total
  to the operator at each stage boundary, in one line.
- **At 70% of budget before QA**, drop the optional stages (security checklist
  if not triggered, DevOps) and tell the operator. **At 100%, stop and ask.**
- Never re-read a whole artifact to "check" it; never paste an artifact into a
  brief; never resume a large agent for a small question.

## 6. Sign-off

Write `docs/cosmos/SIGN_OFF.md`: built versus the idea, each gate's result,
open risks with owners, docs delivered, next steps, and the token ledger.
Then summarise for the operator in a few lines.
