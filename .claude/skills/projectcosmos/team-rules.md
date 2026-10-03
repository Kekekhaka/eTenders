# ProjectCosmos: rules every agent follows

Read this file and `project.md` (next to it) before anything else.
`project.md` holds this project's test command, cost ceiling and no-go rules.

## Team and artifacts

Agents cannot dispatch agents. The Team Leader (the main session) dispatches
everyone and routes each artifact to the next stage.

| Artifact | Path | Owner | Budget |
|---|---|---|---|
| IDEA_BRIEF | `docs/cosmos/IDEA_BRIEF.md` | Ideologist | 150 lines |
| REQUIREMENTS_PACK | `docs/cosmos/REQUIREMENTS_PACK.md` | Business Analyst | 350 lines |
| ADRs | `docs/adr/NNNN-title.md` | Business Analyst | 30 lines each |
| DESIGN_SPEC | `docs/cosmos/DESIGN_SPEC.md` | Architect | 350 lines |
| WORK_BREAKDOWN, API_CONTRACT | `docs/cosmos/` | Lead Engineer | 150 lines each |
| IMPLEMENTATION | `docs/cosmos/IMPLEMENTATION.md` | Lead Engineer | 120 lines |
| REVIEW_REPORT, TEST_REPORT, SECURITY_REPORT | `docs/cosmos/` | Reviewer, QA, Security | 150 lines each |
| DEPLOY_PLAN | `docs/cosmos/DEPLOY_PLAN.md` | DevOps | 150 lines |
| PROJECT_DOCS | `README.md`, `docs/` | Business Analyst | as needed |
| SIGN_OFF | `docs/cosmos/SIGN_OFF.md` | Team Leader | 80 lines |

## Spend tokens carefully (the operator pays for every one)

- **Read only what you need.** Your brief names the files. For any document
  over 300 lines, find the section with Grep, then read just that range. Never
  read a whole artifact "for context", and don't re-read what you just wrote.
- **Cite, don't copy.** Refer to upstream requirements by ID (FR-03, AC-12).
  Don't restate them. Tables and bullets beat prose.
- **Stay inside the artifact's line budget** (above). If the work needs more,
  say so in your reply instead of padding the file.
- **Quick mode has hard caps:** at most 25 tool calls, read only the files
  your brief names, an artifact of at most 100 lines, and no second pass.
  (In this team's own measurements, capped agents used 20-37k tokens; uncapped
  ones used 80-330k for the same kind of stage.)
- **Test cheaply.** Run the tests that cover what you changed while you work;
  run the full suite once at the end.
- **One pass.** Batch your questions into your reply. Don't make the work
  depend on a round-trip you could avoid by choosing and stating an assumption.

## How to work

- **Evidence over assertion.** Cite `file:line`. Say what you did not run.
  Never report a check as passing unless you ran it and saw it pass.
- **Keep it simple.** The operator values code a human can read at a glance.
  No new framework, layer or abstraction unless a requirement needs it.
- **Stay in your role and your files.** Raise scope drift to the Team Leader.
- **Classify findings** in your role's vocabulary and give each a concrete fix.

## Hard guardrails (override any brief)

1. **Secrets:** never read, print or copy secret files, keys, connection
   strings or publish profiles; never run a command that prints them.
2. **Cloud:** read-only (`show`, `list`, `what-if`). Never create, change or
   delete a resource.
3. **External services:** no live calls unless your brief explicitly allows
   it. Use tests, fakes and cached sample data.
4. **Git:** never commit, push, reset or change config. The operator commits.
5. **Files:** the folder may be cloud-synced and rewritten mid-session.
   Re-read a file right before you edit it.
6. **Cost:** add nothing that costs money without the operator's explicit
   approval. Verify every price and free-tier limit at its primary source and
   state the date. Label anything you could not verify **unverified**. Quote
   monthly costs in the operator's currency and say which assumption drives
   them. The Architect owns the cost budget; everyone else works inside it.

## Reply format (at most 200 words)

1. **Artifact(s):** paths written or changed.
2. **Gate:** MET or NOT MET, and why.
3. **Findings / hand-offs:** most important first.
4. **Not verified:** what you could not check.
