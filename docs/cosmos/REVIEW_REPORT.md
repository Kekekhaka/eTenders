# REVIEW_REPORT: eTenders digest (stage 5, audit and hardening)

- **Date:** 2026-10-01 · **Version:** 1.0 · **Author:** Reviewer
- **Reviewed against:** `API_CONTRACT.md` v0.2 (authoritative), `TEAM_LEADER_DECISIONS.md` (TL-1 to
  TL-14, OP-1 to OP-3), `DESIGN_SPEC.md` §5, §8 and the data model, `REQUIREMENTS_PACK.md` (FR-15, AC-09
  to AC-12), and `IMPLEMENTATION.md` v1.0 (including the accepted departures in §4.2).
- **Code read in full:** `digest.py`, `pipeline.py`, `memory.py`, `report.py`, `email_sender.py`,
  `function_app.py`, `TG.py`, `host.json`, the `.funcignore`/`.gitignore`/`local.settings.json.example`
  diffs, and all 8 test files.
- **Evidence I produced myself:**
  - `.venv/Scripts/python.exe -m pytest -q` gave **`295 passed in 7.70s`**. Per file: digest 24,
    email_sender 9, function_app 44, integration 11, memory 71, pipeline 58, report 73, tg 5.
  - I ran a scratch script, with no repo file changed, that confirmed two behaviours: SF-1 (an empty
    digest's footer mentions an attachment that isn't there) and SF-3 (a crash after delivery sends
    a notice saying the digest wasn't delivered).
  - I counted lines with `ast` to split code from docstrings, comments and blank lines (§1).
- Line numbers are as of this review. The folder is OneDrive-synced, so re-read before editing.

---

## Verdict: **pass-with-fixes**

There are no blockers. There are four **should-fix** items: one wrong sentence in the email, one stale
contract section, one untested branch with a wrong notice text, and one validation step that does
more than TL-2 asks. Each is small. Everything else is a nit or an option for the Team Leader.

### The plain answer on simplicity

The code is **not bloated**. It grew from 431 lines at `b7d4996` to 1,797. Of those, **1,200 are
code**; the other 597 are docstrings, comments and blank lines, which mostly explain *why* and help
a reader. Nearly all of the growth pays for the four features the operator chose and the five
Security must-fixes. I found no dead module, no unused abstraction and no framework.

What can still go **without losing a chosen feature or a must-fix**:

| Group | Code lines saved | Needs |
|---|---|---|
| Should-fix SF-4 (stop validating two fields the code never reads) | ~7 | a one-line contract edit (§3.3, step 4) |
| Nits N-1 to N-4 (redundant re-checks, a duplicated date format, small tidy-ups) | ~6 | nothing upstream |
| **Option O-1: simplify the memory schema** (drop the `missed` map and the write-only fields) | **~35–40** | Architect (DESIGN_SPEC §5.1) and BA (AC-39 clause); **cheapest now, before the blob exists** |
| Option O-2: remove the heartbeat entirely | ~45 | Team Leader/operator; it reverses TL-9 and FR-11/FR-24/NFR-06 |
| Tests: remove duplicated tests (N-5) | ~45 test lines | nothing upstream |

So the realistic cut is **about 13 lines without upstream changes**, about **50 with O-1**, and
about **95 (5%) if the operator also drops the heartbeat**. The single biggest lever is O-1.

---

## 1. Where the lines went

| File | Total | Code | What the code pays for | Verdict |
|---|---|---|---|---|
| `pipeline.py` | 467 | 296 | ~75 lines of operator tuning (`KEYWORDS`, `CATEGORIES`, `BLOCKER_PATTERNS`, pre-existing). Per-day fetch with reasons (FR-03, FR-08). Input validation `_obj`/`_text`/`_ocid`/`_parse_time`/`_document_urls`/`flatten` (**S-04**). `build_digest`: NEW, closing soon, days left, the 5-key sort (features). | Lean. Nothing to cut except N-3. |
| `memory.py` | 461 | 319 | Token + Blob REST + FileStore (~120, ADR-0016, FR-22). TL-2 load (~95). Missed-day and sent logic (~90, features). Serialisation (~40). | Implements TL-2 (see §2). SF-4 and O-1 apply. |
| `report.py` | 423 | 306 | Subject, HTML (summary, banners, table, footer, escaping), xlsx (**S-05** formulas, safe links), design tokens with WCAG contrast (DESIGN_SPEC §8.6). | Mostly the "improve the email and xlsx" feature. N-1 and N-2 apply. |
| `function_app.py` | 197 | 129 | `redact` (**TL-4**), heartbeat (~35, TL-9), `run()` orchestration and RUN_SUMMARY. | Lean. O-2 is the only real cut. |
| `digest.py` | 105 | 61 | The contract: dataclasses, constants, `is_safe_url` (TL-1, TL-7). | Keep. |
| `email_sender.py` | 84 | 50 | Transport and the **O-2 confirmed-send check**. | Keep. |
| `TG.py` | 60 | 39 | Local runner with HTML preview (TL-5). | Keep. |

---

## 2. Does `memory.py` implement the *lighter* validation of TL-2?

**Yes, essentially.** Step by step against TL-2 and API_CONTRACT §3.3:

| TL-2 step | Code | Matches? |
|---|---|---|
| ≤ 5 MB | `memory.py:113-127` (streamed, capped at `MAX_BYTES + 1`), `_decode` `:191-198` | Yes |
| Parse JSON | `:291-294` (also catches `RecursionError`, a real crash path; keep) | Yes |
| `schema == 1` | `:295-296` | Yes |
| Convert types | `_date`/`_time`/`_dict`/`_str`/`_one_of`/`_sent`/`_days`/`_missed`, `_CONVERTERS` `:207-276`, loop `:299-304` | Yes, but it also converts two fields nothing reads: **SF-4** |
| Clamp | `_clamp` `:314-326` | Yes, plus the accepted `resolved_through` clamp (IMPLEMENTATION §4.2 #3) |
| *Dropped:* exact key sets, per-map caps, the 100-char `updated_by_run` cap, the `resolved_through` range check | Not present | Yes |

The other extras are justified. The 2 MB warning (`:38`, `:288-289`) is asked for by DESIGN_SPEC §5.1.
Rejecting a naive time (`:213-214`) was accepted as #4. Checking `state`/`kind` (`_one_of`) is in the
contract.

**Why the file is still 461 lines:** the weight is not in the validation. It is in the **model**.
Memory is held in typed form, so every stored field needs a converter on the way in (`:236-276`) and
a mirror on the way out (`_dumps`, `:429-448`). The stored schema also carries a whole map (`missed`)
that can be derived from data already kept, plus four fields that are written but never read.
Option O-1 addresses that.

---

## 3. Findings: simplification first

Tags: **[S]** marks a simplification finding, with its estimated saving.

### Should-fix

**SF-4 [S] (~7 lines): `load()` validates two fields that no code reads.** `memory.py:271-272`
convert `updated_at` and `updated_by_run`, using the `_str` helper at `:224-227`. Nothing reads them:
`after_delivery` overwrites both (`:411-413`), and `_dumps` writes the new values. A hand-edited
or odd `updated_by_run` therefore makes the whole memory "unavailable" for no reason. That is
heavier than TL-2's intent ("convert the keys the code uses"). The contract's step 4 lists them
under that same heading, so it contradicts itself.
*Fix:* delete the two `_CONVERTERS` rows and `_str`. Drop the `updated_at` row from
`tests/test_memory.py:295`, and compare without those two keys at `:562`. Amend API_CONTRACT §3.3 step 4.

**SF-1: an empty digest's footer claims an attachment.** `report.py:264-265` always appends "The
attached xlsx has every tender…". When `total == 0`, `build_xlsx` returns `None` and no attachment is
sent (AC-02). I confirmed that the text appears with `total=0` and `build_xlsx(d) is None`.
*Fix:* `if d.total: lines.append(...)`. Add `assert "attached xlsx" not in body` to
`tests/test_report.py:288` (`test_empty_digest_says_nothing_matched_and_has_no_table`).

**SF-2: API_CONTRACT is stale against TL-10 to TL-14.** The code adds `missed_named` to RUN_SUMMARY
(`function_app.py:37`, `:118`, `:151`), which TL-10 correctly requires. But API_CONTRACT §3.8 still lists
`missed_named` under "Dropped (TL-3)", and §7 O-5 is still "Open". The contract is the authoritative
interface, so it must match the code.
*Fix (Lead):* issue v0.3. Add `missed_named` (capped at 10) to §3.8, close O-5, and note TL-11 to TL-14.
Fold in the SF-4 edit to §3.3, and the N-6 wording if adopted. IMPLEMENTATION.md says it was built to
"DESIGN_SPEC v0.2", while the brief cites v0.3, so align that reference too.

**SF-3: a crash after delivery is untested, and its notice is wrong.** `function_app.py:169`
(`if summary["outcome"] is None`) keeps `digest_delivered` when, for example, `after_delivery` or
`save` raises after `send_report` returned. That was accepted as IMPLEMENTATION §4.2 #6. **No test
covers it.** A regression that drops the guard would report `failure_notice_delivered` for a
digest that was in fact delivered, and would ping `/fail`. The notice body also says "failed **before it
could deliver** a digest" (`email_sender.py:79`). I reproduced the crash, and in that case the
sentence is false.
*Fix:* (a) Add one test to `tests/test_function_app.py`: monkeypatch `memory.after_delivery` to raise,
then assert that `RuntimeError` is raised, `outcome == "digest_delivered"`, one notice is sent, and
the heartbeat (if on) hits the success URL. That is about 12 lines.
(b) Change `email_sender.py:79` to the neutral "The eTenders digest run failed." The RUN_SUMMARY
outcome already says whether the digest went out.

### Nits: simplification [S]

**N-1 [S] (~3 lines): `report.py` re-checks invariants that the contract already guarantees.**
- `report.py:200-201`: the list is filtered with `is_safe_url`, then `_link` (`:104`) checks again.
  Use `document = _link(r.document_urls[0], "View document") if r.document_urls else ...`;
  `_link` remains the single sink check.
- `report.py:347` and `:360` do the same for the xlsx, where `:413` is the sink check. Use
  `r.document_urls[0] if r.document_urls else None`.
- `report.py:182`: inside the `closing`-not-NaT branch, `days_left` is never NA (C§2.3), so this is
  just `_e(int(r.days_left))`.
- `report.py:119`, `:231`, `:232`: `.astype(bool)` on columns that C§2.3 guarantees are `bool`. Drop it.

**N-2 [S] (~3 lines): the date text is built twice.** `report.py:96` (`_when`) and `:277-278`
(`render_html` context line) both build "Mon 3 Oct". Extract `_day_text(t)` and use it in both places.

**N-3 [S] (0 lines; readability): `pipeline` and `memory` import each other.** `memory.py` imports
`pipeline` at the top, and `pipeline.build_digest` imports `memory` inside the function
(`pipeline.py:424-426`), renamed `memory_module` because the parameter `memory` shadows it. It
works and it's commented, but a reader has to work out the cycle.
*Fix (next contract change):* let `function_app`/`TG` pass `missed: list[MissedDay]` into
`build_digest`, so `pipeline` never imports `memory`. That's a C§3.2 signature change, so it isn't
worth a contract revision on its own.

**N-4 [S] (0 lines): `function_app.py:79`.** `len(url) == len(HEARTBEAT_PREFIX)` reads more
plainly as `url == HEARTBEAT_PREFIX`.

**N-5 [S] (~45 test lines): duplicated tests.** Each pair checks the same behaviour through the same code:
- `tests/test_report.py:312` (`test_tender_fields_are_html_escaped`) and `:393`
  (`test_contact_is_never_a_mailto_link`) are fully covered by `test_escaping_torture_test` (`:330`).
  Delete both.
- `tests/test_integration.py:341` (heartbeat `/fail` on non-delivery) is covered by
  `tests/test_function_app.py:301-334` (`failure_notice_delivered` case). Delete the integration one.
- `tests/test_function_app.py:263` `"send times out"` only makes the fake `Mailer` raise
  `TimeoutError`. That's the same path as `"send raises"`. The real poller timeout is tested in
  `test_email_sender.py:93` and `test_integration.py:302`. Drop the parametrize.
- `tests/test_function_app.py:432` (the `ContainerNotFound` row) duplicates `test_integration.py:272`,
  which uses the real `BlobStore`. Drop the row.
- `tests/test_report.py:260` is parametrized with a single value. Remove the decorator.

### Options for the Team Leader (not gating) [S]

**O-1 [S] (~35–40 code lines, ~25 test lines; recommended): simplify the memory schema before the
blob exists.** The `missed` map (`_missed` `:254-264`, its converter, the `report_until` clamp `:322-324`,
the `_dumps` block `:442-446`, and the `after_delivery` block `:400-408`) can be **derived** instead of
stored:
- Keep `days` entries for `MISSED_REPORT_DAYS + 1` days after they leave the window, instead of
  pruning them at `window[0]` (`:398`).
- Merge `_newly_missed` and `missed_days` into one loop, from
  `max(tracking_since, min(resolved_through + 1, window[0] − MISSED_REPORT_DAYS − 1))` to `window[0]`,
  naming every day whose state isn't `covered`. Days after `resolved_through` are named at least once
  (AC-12). The others are named while they're inside their 7-day period (OQ-BA2). `left_window_on` is
  `day + LOOKBACK_DAYS + 1`, as `MissedDay` already computes it.
- The same change can drop the write-only `days[D].at` and store `days` as `{date: "covered"|"partial"}`.

The only behaviour change: after a long gap, a day is named in the first delivered digest, rather than
in every digest for the rest of that date. FR-15 and AC-12 still hold.
The persisted model shrinks to `sent`, `days`, `tracking_since` and `resolved_through`. This is the
subtlest logic in the app, so AC-09 to AC-12 and `test_memory.py:359-459` must stay green.
**Do it now or not at all:** once the blob is deployed, a schema change needs a migration.
Needs: the Architect (DESIGN_SPEC §5.1 and the state diagram) and the BA (AC-39's `report_until` clause).

**O-2 [S] (~45 code lines, ~120 test lines): remove the heartbeat.** It is off by default and is not
one of the four features the operator chose. Removing it would delete `heartbeat_config`,
`ping_heartbeat`, the heartbeat block in `run()`, the heartbeat lines of `redact`, `Digest.heartbeat`,
`HEARTBEAT_STATES` and the footer lines. But it reverses **TL-9** and drops FR-11, FR-24 and NFR-06
(silent-failure detection). The code is small and correct, so **I do not recommend it** unless the
operator says they will never enable Healthchecks.

**Considered and kept** (each is load-bearing):
- `pipeline` validation (S-04).
- `BlobStore` over plain REST (ADR-0016, no new dependency).
- The 5 `redact` patterns (TL-4).
- `_neutralise_formulas` with `quotePrefix` and the extra leading characters (S-05; protects a cell
  when it is edited).
- The illegal-character strip in `report._text` (4 lines that stop one bad value crashing the whole
  digest).
- The design tokens and contrast test (DESIGN_SPEC §8.6).
- The NEW/earlier row groups and the xlsx fills (part of "improve the email and xlsx").
- `FileStore` (FR-22).
- The `save` retry (contract).
- The 2 MB warning (DESIGN_SPEC §5.1).

### Nits: other

**N-6: the plural ruling.** C§2.6's fixed strings produce "1 tenders" (`report.py:273`) and "1 more
tenders are…" (`:305`). **Ruling: a nit, so it doesn't gate.** If it's fixed, use the convention the
subject and footer already use, with **no new logic**: `"{total} tender(s) · …"` and
`"{n} more tender(s) in the attached xlsx only."` A pluralising helper would add branching for a
cosmetic gain. Change C§2.6 in the same v0.3 (SF-2), and update the 5 test strings at
`tests/test_report.py:176`, `:186`, `:205`, `:210` and `:293`.

**N-7: the test that pins the tuning constants.** `tests/test_pipeline.py:123-126` asserts
`LOOKBACK_DAYS == 5` etc., so a legitimate operator tuning edit fails CI (AC-24 wants one edit).
*Fix:* assert that the names exist and are positive `int`s. Many scenario tests hard-code 5-day
windows anyway, so this mainly removes a change-detector.

**N-8: a page-limit day is labelled "never fetched".** By the DESIGN_SPEC §5.1 lifecycle, a
truncated (page-limit) day stays "Absent", so it becomes `never_fetched` (`memory.py:331-342`). The
banner heading ("…before they were fetched completely") is still true. This is for the BA to note in
the docs. No code change is recommended.

**N-9: the commented-out `KEYWORDS` block.** `pipeline.py:79-89` is 11 lines of dead config. It
predates this pass, so deleting it is the operator's call.

### Praise

- **P-1:** The contract-first split works. `digest.py` is tiny, `report.py` imports nothing from the
  backend (enforced at `test_integration.py:372`), and there is one `is_safe_url` (TL-1).
- **P-2:** The O-2 confirmed-send check (`email_sender.py:48-52`) is short and correct, and its
  docstring explains the azure-core trap.
- **P-3:** Comments explain *why*, not *what*. Examples: `pipeline.py:34-43` (the per-day budget),
  `:301-304` (per-value date parsing), and `function_app.py:181` (raising outside `except`).
- **P-4:** The behaviour tests catch real regressions. `test_memory.py:500`
  (`test_a_tender_is_new_only_once`) round-trips build → save → load → build. The AC-30 leak test
  (`test_function_app.py:518`) pushes secrets through every helper. The integration tests use real
  modules, with fakes only at the edges.
- **P-5:** `run()` reads top to bottom as the contract's 7 steps (`function_app.py:103-128`).

---

## 4. Test quality

- **Would they catch a regression?** Mostly yes. The tests assert behaviour, not internals: the sort
  tie-breaks, the closing-soon boundary, the conditional writes, no write after a bad read, the confirmed
  send, the formula payloads checked in the saved XML, and the leak test.
- **Gaps:** the crash after delivery (SF-3), and the empty-digest footer (SF-1).
- **Over-tested:** the duplicates in N-5, and the constant pin in N-7. `LINK_CASES`, run at both sinks,
  repeats `test_digest`'s URL cases, but DESIGN_SPEC §8.5 asks for those cases at the sink, so they stay.
- **Re-stating the implementation:** `test_digest_has_exactly_the_contracted_fields` and
  `test_report_has_exactly_three_public_functions…` are change-detectors by design (contract guards),
  so that's acceptable. `test_report.py` `frame()` re-implements `days_left` to build fixtures; that's
  acceptable for a frontend test with no backend import.
- **Speed:** 7.70 s against the 15 s target (TL-13).

## 5. Frontend and backend against API_CONTRACT

| Contract item | Status |
|---|---|
| §2.1/§3.1 `Digest`, `PartialDay`, `MissedDay` fields and order | Match (`digest.py`, test `test_integration.py:355`) |
| §2.3 columns, types, nulls; §2.4 sort; §2.5 `is_safe_url` | Match (`pipeline.py:330-467`; invariants checked on real digests, `test_integration.py:210`) |
| §2.6 summary, banners and their order, empty text, footer, subject, escaping, SAST, xlsx | Match, except **SF-1** (an extra footer sentence on empty digests) |
| §3.4 three public functions, allowed imports | Match |
| §3.5 email timeouts, delivered = `done()` + `Succeeded` | Match |
| §3.6 `run()` sequence, redaction in exactly two places, `from None` | Match |
| §3.3/§4.2/§4.3 memory, token, Blob statuses | Match (SF-4 proposes a lighter step 4) |
| §3.8 RUN_SUMMARY | Code follows TL-10 (`missed_named`); **the contract text is stale (SF-2)** |
| §4.5 heartbeat | Match; the code is stricter than the contract (it also needs a non-empty path and a safe URL), which is fine |

## 6. Hand-offs

- **Lead Engineer:** SF-1 to SF-4 (about 30 minutes in total). Optionally N-1 to N-7 in the same pass.
- **Team Leader:** decide O-1 (the schema simplification, before deploy) and O-2 (the heartbeat).
- **Architect / BA:** only if O-1 is taken (DESIGN_SPEC §5.1, AC-39). BA: N-8 wording.
- **DevOps (stage 8):** `host.json` now disables App Insights sampling (AC-38). Confirm that ingestion
  stays inside the free allowance at 2 runs a day. I didn't verify any cost.

## 7. Not verified

- No live call to eTenders, the identity endpoint, Blob, ACS or Healthchecks (guardrail 3); all
  behaviour comes from offline fakes.
- I didn't run the code under Python 3.11 (the venv is 3.12.10). `datetime.fromisoformat` with a "Z"
  suffix needs 3.11+, which Azure provides.
- I worked out O-1's equivalence by reading the code; I haven't built or tested it.
- Cost of disabling sampling: not checked (DevOps).

---

# Re-review (after the fix log, IMPLEMENTATION §6; TL-15 to TL-17)

Scope: the fixes only. Suite: `.venv/Scripts/python.exe -m pytest -q` gave **`291 passed in 4.43s`** (I ran it).

| Item | Result | Evidence |
|---|---|---|
| SF-1 | Fixed | `report.py:268` (`if d.total:`) guards the attachment sentence. `tests/test_report.py:293` asserts it is absent on an empty digest. It would fail if the guard were removed. |
| SF-2 | Fixed | API_CONTRACT is v0.3 and lists `missed_named`. I did not re-read every section of it; I only grepped for the version and the field. |
| SF-3 | Fixed | `tests/test_function_app.py:389` patches `memory.after_delivery` to raise and checks the outcome and notice. `email_sender.py:79` now says only "The eTenders digest run failed."; `tests/test_email_sender.py:108` asserts the old wording is gone. |
| SF-4 | Fixed | `_CONVERTERS` (`memory.py:~244`) holds only `tracking_since`, `resolved_through`, `sent`, `days`. `updated_at`, `updated_by_run` and `_str` are gone. |
| O-1 / TL-15 | Correct | See below. |
| N-3 | Done | `pipeline.py` has no `memory` import. `build_digest` takes `missed` (`pipeline.py:418-420`). `function_app.py:115-116` and `TG.py:30-31` compute it. The only import is `memory` to `pipeline`, so there is no cycle. |
| N-6 | Done | `report.py:78`, `:278`, `:309` use "tender(s)". Tests at `test_report.py:205`, `:210` updated. |
| N-7 | Done | `tests/test_pipeline.py:123-132` checks shape only. No value is pinned. |
| N-1, N-2, N-4, N-5 | Done | `_day_text` exists (`report.py:93`, used at `:101` and `:282`). I did not re-check each N-1/N-4/N-5 edit line by line; the suite is green. |

**O-1 derivation, checked by reading `memory.py` `missed_days`, `_report_from` and `after_delivery`:**
- **7 days after leaving the window:** a day D leaves the window on D+6 and is named through D+13. `missed_days` starts from `window[0] - 8` (`_report_from`), which is exactly D >= run_date-13. This matches the old `report_until = left_window_on + 7`. `after_delivery` keeps `days` back to the same boundary, so the data is there to read.
- **A day no delivered digest has named is still named:** the start is `min(resolved_through + 1, _report_from)`, so any day after `resolved_through` is included however old, bounded by `MAX_GAP_DAYS` (366) and `tracking_since`. `resolved_through` advances only inside `after_delivery`, which runs only after a confirmed send. A failed send therefore names it again (AC-12). `test_a_missed_day_is_named_for_seven_days_after_it_left_the_window_then_forgotten` and the AC-12 test cover this; I read the implementation, not every test body.
- **TL-2 intact:** `load` still has the 5 MB cap, JSON parse, `schema == 1`, typed conversion with any error giving `unavailable:invalid:<key>`, and the clamps. `build_digest` still sets `is_new` all false and `missed` is empty when status is not `ok` or `first_run`. `function_app.py` still skips the write for unavailable memory.
- **Accepted behaviour change (as the Lead stated):** after a long gap, an old day is named once and then not repeated.

**New problems found:** none blocking. Two minor notes:
1. `build_digest(..., missed=())` takes a tuple default and a list annotation (`pipeline.py:420`). It is harmless.
2. `memory.py` still has `_time` and `WARN_BYTES`, both used. Nothing is dead.

I did not run the code under Python 3.11 and made no live calls.

## Final verdict: **pass**

No blocker or should-fix items remain.

**Line count:** code (`*.py` in the repo root) is **1,750**, against 1,797 before. The saving is 47 lines, which is more than the ~13 estimated for the no-upstream fixes and a little over the ~50 with O-1. Tests were not re-counted by me; the Lead reports 2,834. Totals by file, as measured with `wc -l`: `pipeline.py` 464, `memory.py` 412 (was 461), `report.py` 426, `function_app.py` 198, `digest.py` 105, `email_sender.py` 84, `TG.py` 61.
