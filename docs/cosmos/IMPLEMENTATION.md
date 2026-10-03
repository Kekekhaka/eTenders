# IMPLEMENTATION: eTenders digest (stage 4, integrated build)

> v1.1: after the stage 5 review the suite stands at **291 passed in 3.77s**. See section 6 (fix log). The
> memory schema in section 1 is the simplified one (TL-15): `sent`, `days`, `tracking_since`, `resolved_through`.

- **Date:** 2026-10-01 · **Version:** 1.0 · **Author:** Lead Engineer (Integrate mode)
- **Built to:** `docs/cosmos/API_CONTRACT.md` v0.3, `docs/cosmos/WORK_BREAKDOWN.md` v0.2,
  `docs/cosmos/TEAM_LEADER_DECISIONS.md` (TL-1 to TL-17, OP-1 to OP-3), DESIGN_SPEC v0.2 (the Architect reconciles it).
  Precedence: the TL decisions, then API_CONTRACT, then DESIGN_SPEC.
- **Baseline:** commit `b7d4996` (17 tests).
- **Result:** I ran `.venv/Scripts/python.exe -m pytest -q` and got **`295 passed in 7.13s`**.
  That is the Team Leader's verified 284, plus my 11 integration tests. The NFR-10 target is now 15 s (TL-13).
- **Other checks I ran:**
  - All 15 `.py` files parse under the Python 3.11 grammar (`ast.parse(..., feature_version=(3, 11))`).
  - `requirements.txt` is unchanged (`git status`), so there are no new dependencies.
- **Cost (OP-2):** the code adds no paid resource. The heartbeat is off by default. The memory blob sits
  in the existing storage account, and its cost is in the Architect's budget. I verified no prices.

---

## 1. What changed, file by file

| File | Owner | Change |
|---|---|---|
| `digest.py` (new) | Lead | The contract: the `Digest`, `PartialDay` and `MissedDay` frozen dataclasses, `TENDER_COLUMNS`, the status vocabularies, `DISPLAY_TZ`, and the one shared check `is_safe_url` (TL-1). |
| `pipeline.py` | Backend | The run's single clock (`window_for`, `day_end_utc`, `API_DAY_TZ`). A per-day fetch with `DayResult` reporting `page limit`, `http_<code>` and `out of time`; a `releases` value that isn't a list is retried. Input validation in `flatten` (`_obj`/`_text`, illegal characters stripped, the `ocid` rule, safe URLs, per-value date parsing). `select_tenders(releases, run_at) -> (df, dropped)`, and the pure `build_digest` (NEW, closing soon, days left, the 5-key stable sort). `build_matches` is removed. Tuning constants are still all here (AC-24). |
| `memory.py` (new) | Backend | `mi_token`, `BlobStore` (Blob REST with a managed-identity bearer token and conditional PUT) and `FileStore`. `load` uses the lighter validation (TL-2: ≤ 5 MB, JSON, `schema == 1`, typed conversion, clamps; any failure → unavailable). The pure `missed_days` and `after_delivery`, and `save` with one retry. |
| `report.py` (new) | Frontend | Pure rendering with three public functions (TL-3): `render_subject`, `render_html` (summary line, banners, row rule, footer, escaping, SAST times, safe links) and `build_xlsx` (one `Tenders` sheet, no formulas, safe hyperlinks, frozen header, autofilter). |
| `email_sender.py` | Backend | Transport only. A send counts as delivered only when the poller is `done()` and the status is `Succeeded` (TL-8, O-2). Timeouts are 90 s for the digest and 30 s for the notice. `send_failure_notice` takes text that is already redacted. |
| `function_app.py` | Backend | An injectable `run()` orchestrator. `redact()` is applied to the notice and to the top-level log, with secrets kept inside the helpers (TL-4). The `urllib3`/`azure` loggers are at WARNING. The optional heartbeat is a POST. One minimal `RUN_SUMMARY` line (TL-3, TL-10). The timer shim passes `invocation_id` and `past_due`. |
| `TG.py` | Backend | The local runner: a local `FileStore`, the shared xlsx builder, an `etenders_digest.html` preview, no email, no token (FR-22). |
| `host.json` | Backend | `samplingSettings.isEnabled: false` (AC-38). |
| `.gitignore`, `.funcignore` | Backend | Appended `etenders_memory.json`, `etenders_memory.json.tmp` and `etenders_digest.html`. The operator's earlier uncommitted `.funcignore` lines are kept. |
| `local.settings.json.example` | Backend | Added `"HEARTBEAT_URL": ""`. |
| `tests/test_digest.py` (new) | Lead | Contract tests: frozen objects, columns, timezone, 19 `is_safe_url` cases |
| `tests/test_integration.py` (new) | Lead | 11 end-to-end and contract tests (§3) |
| `tests/test_pipeline.py`, `tests/test_email_sender.py` | Backend | Rewritten to the new interfaces; the 4 HTML tests moved to `test_report.py` |
| `tests/test_memory.py`, `tests/test_function_app.py`, `tests/test_tg.py` (new) | Backend | Memory, orchestration (incl. the AC-30 leak test and AC-37 registration), local runner |
| `tests/test_report.py` (new) | Frontend | 73 rendering tests (incl. AC-21a/b, AC-35) |
| `docs/cosmos/samples/email_sample.html`, `.xlsx` (new) | Frontend | Rendered samples of the email and attachment |

The deploy package now also contains `digest.py`, `memory.py` and `report.py`. None of them is in
`.funcignore` (checked). `TG.py`, `tests/` and `docs/` stay excluded.

---

## 2. How to build, test and run

**Set up (once):** `python -m venv .venv`, then `.venv/Scripts/python.exe -m pip install -r requirements-dev.txt`.
The local venv is 3.12.10, while Azure runs 3.11, so code must keep to the 3.11 grammar.

**Test:** `.venv/Scripts/python.exe -m pytest -q`. The tests are fully offline. Fakes stand in for the
eTenders API, Blob storage, ACS and Healthchecks.

**Run locally (no email, no Azure):** `.venv/Scripts/python.exe TG.py`. This:
- calls the live eTenders API. Behind the corporate proxy, set `REQUESTS_CA_BUNDLE`; never `verify=False`.
- writes `etenders_matches.xlsx` and **`etenders_digest.html`**, the HTML preview. Open it in a browser
  to see exactly what the email body would be.
- prints the subject line, partial days and missed days;
- keeps its own memory in `etenders_memory.json`. Delete that file to see a first run again.

It never touches the production blob or asks for a token.

**In Azure:** the timer runs `etenders_digest` at 06:00 and 18:00 UTC. To drive it in a test, call
`function_app.run(...)` or `function_app.etenders_digest(timer, context)` directly. Backend note:
`app.get_functions()` raises if it's called twice in one process.

---

## 3. Integration checks (`tests/test_integration.py`)

These run end to end through `function_app.run` with the real `pipeline`, `memory`, `report` and
`email_sender`. The fakes are only at the edges: a fake `requests.Session` for eTenders, a fake HTTP
object behind the real `BlobStore`, a fake `EmailClient`, and a fake ping.

| Test | Covers |
|---|---|
| First run: everything NEW; memory written after delivery with `If-None-Match: *`; HTML and xlsx in Digest order | AC-01, AC-07, AC-19, FR-12 |
| Second run, same releases: nothing NEW; `If-Match` write | AC-05 |
| `ContainerNotFound`: `new unknown`, unavailable banner, **no PUT** | AC-08, AC-27 |
| One API day times out: `(partial)` subject, `2026-09-30 (ReadTimeout)` banner, other days kept | AC-03, FR-08 |
| ACS times out: failure notice sent, **no memory write**, and the next run is still a first run with all NEW | AC-06, AC-22, TL-8/O-2 |
| Malformed releases: 2 dropped and counted, footer shown, `\x0b` stripped from HTML and xlsx, digest delivered | AC-29, FR-23 |
| Heartbeat on, digest not delivered: one `/fail` POST with only the allow-listed keys | AC-33 |
| Contract: `Digest`/`PartialDay`/`MissedDay` fields exactly as C§3.1; `report` has exactly 3 public functions, each taking `d`; `report` imports nothing from the Backend (checked on the AST); `pipeline`, `report` and `digest` share one `is_safe_url` | C§2, C§3.4, TL-1, TL-3 |

Every scenario also checks the Digest the real pipeline built against the C§2.1–2.4 invariants (columns,
index, counts, `new_count` vs `memory_status`, partial-day order, safe URLs, the NEW/score order).

**Integration defects found:** none. All 11 tests passed on their first run against the delivered
code, and I changed no Frontend or Backend file.

---

## 4. Deviations from the design and the contract

### 4.1 Contract-level deviations (all accepted by the Team Leader)

API_CONTRACT §8 lists D-1 to D-17, accepted through TL-1 to TL-8. In short:
- the contract lives in `digest.py`, and `is_safe_url` is there too;
- `closing_soon_days` was added to the `Digest`;
- `mi_token` returns a reason;
- `run_id` and `past_due` were added;
- `flatten` drops any `ocid` over 200 characters;
- memory validation is lighter (TL-2);
- RUN_SUMMARY is minimal, plus `missed_named` capped at 10 (TL-3, TL-10);
- secrets are handled by containment, with no log filter (TL-4);
- there is no Coverage sheet, and `covered_earlier` is gone (TL-5, TL-6);
- the heartbeat is a POST and is off by default (TL-8, TL-9).

### 4.2 Backend's reported departures: my verdicts

| # | Departure | Verdict | Reason |
|---|---|---|---|
| 1 | `missed_named` in RUN_SUMMARY | **Accept** | It's TL-10 (capped at 10; `function_app.py:37`, `:118`). It closes O-5, so AC-26 is now fully covered. |
| 2 | `window` kept in RUN_SUMMARY | **Accept** | It's already in the API_CONTRACT §3.8 v0.2 field list, so it's not a departure. It costs two dates. |
| 3 | `resolved_through` clamped to [run date − 366 d, run date] | **Accept** | It's a clamp, which TL-2 allows, not a validation rule. A future date would silently stop missed days ever being reported. A very old one would make one run classify years of days. Both are now bounded, and nothing is rejected. |
| 4 | A stored time without a UTC offset → memory unavailable | **Accept** | `save` always writes `…Z`, so a naive time can only come from a hand edit. TL-2 says any conversion error → unavailable, and the banner makes it visible. |
| 5 | `redact()` skips configured values shorter than 8 characters | **Accept** | Replacing a 1–7 character value everywhere would garble the notice and logs. The real secrets are long: a Healthchecks ping UUID is 36 characters, and the identity header is a long platform value. The fixed patterns (Bearer, JWT, `accesskey=`, SAS) still apply. The AC-30 leak test passes. |
| 6 | A crash **after** delivery: failure notice sent and re-raised, but outcome stays `digest_delivered` and the heartbeat pings success | **Accept** | The operator did get the digest, so `digest_delivered` and the success ping are true. The notice and the re-raise still make the defect loud (NFR-05). As with T-2, if the crash came before `save`, the next run may re-flag those tenders NEW. This is accepted (R-9). |
| 7 | Category text still counts toward the keyword score | **Accept** | That is the baseline behaviour (ADR-0005, 0006), and the existing score tests rely on it. Changing scoring is out of scope for this hardening pass. |
| 8 | `HEARTBEAT_URL` in `local.settings.json.example` with no comment | **Accept** | JSON has no comments. The meaning is documented in API_CONTRACT §5 and here: leave it empty for no heartbeat (the default, no cost). |
| 9 | `etenders_memory.json.tmp` ignored in git and the deploy | **Accept** | `FileStore` writes the temporary file before `os.replace`. A crash mid-write could leave it behind, and it must never be committed or deployed. |

### 4.3 Other notes (for the Reviewer)

- **Nit, from my contract wording:** the summary line in C§2.6 is `"{total} tenders …"`, so one tender
  reads "1 tenders". The subject already uses "tender(s)". It's cosmetic; amending C§2.6 is a one-word change.
- **Accepted:** a first-run email is about 223 KiB and Gmail will clip it once (TL-12). Normal runs are
  67–76 KiB (Frontend's measurement).
- **TL-13:** the suite takes 7.13 s against the new 15 s target.
- **TL-14:** SC6 freshness stays unmeasured in Phase 1.
- **For the BA (TL-11):** amend AC-28 (an extra key is ignored), AC-21a (no Coverage clause), AC-34 (a
  worst case using only the remaining fields), and NFR-10's time target (TL-13).

---

## 5. Operator steps before this runs in Azure

DevOps details these at stage 8. Until they're done, the digest still goes out every run with the
banner "New-tender marking and missed-day tracking were unavailable", which is safe but has no memory.

1. **Create the blob container** `digest-state` (private) in the existing storage account
   `etendersfuncstorage`. Its URL is the code constant `memory.MEMORY_URL`.
2. **Turn on the system-assigned managed identity** of the Function App `etenders-digest-func`.
3. **Grant the role** *Storage Blob Data Contributor* to that identity, **scoped to the `digest-state`
   container only**. Allow a few minutes for it to propagate; the first runs may show "unavailable".
4. **Verify the identity token endpoint** on this Linux Consumption app (R-8, V-06). The first
   RUN_SUMMARY after deploy should show `memory.read` as `first_run`, and later runs `ok`.
5. **Heartbeat (optional; off and free by default, TL-9):** to use Healthchecks.io, create one cron check
   (`0 6,18 * * *` UTC, 60-minute grace) and set the app setting `HEARTBEAT_URL`. Confirm at sign-up that the
   free plan sends email alerts, because that's unverified. An Azure log alert adds cost and needs a verified
   price plus approval.
6. **Deploy** the code. No new app settings are required, and the dependencies are unchanged.
7. **Expect the first run** to mark every tender NEW and show the first-run notice. Gmail may clip that
   one email (TL-12).

If the memory blob ever becomes unreadable, follow the reset runbook in DESIGN_SPEC §5.2: restore it, or
delete `memory.json` for a clean first run. Never delete it to "fix" a permissions or container problem.

---

## 6. Fix log (stage 5 review: pass-with-fixes; TL-15 to TL-17)

Result: **`291 passed in 3.77s`** (was 295). Line counts before and after (`wc -l`):
code (`*.py` in the repo root) **1,797 → 1,750 (net −47)**; tests (`tests/*.py`) **2,860 → 2,834 (net −26)**,
after adding the new SF-1, SF-3 and missed-day tests. All files parse under the 3.11 grammar; `requirements.txt`
is unchanged.

| Item | What I changed | Files |
|---|---|---|
| **TL-15 / SF-4 / O-1** | Simplified the memory schema before the first deploy. The stored memory is now `schema`, `tracking_since`, `resolved_through`, `sent`, and `days` as `{date: "partial" or "covered"}`. Dropped: the stored `missed` map (and its converter, the `report_until` clamp and serialisation), `updated_at`, `updated_by_run`, `days[].at`, and `_str`/`_one_of`/`_missed`. `missed_days(memory, window)` now **derives** the missed days: a day is named while it is within 7 days of leaving the window, and also if it is after `resolved_through`. `after_delivery` keeps `days` entries for 8 days past the window for this. **TL-2 is unchanged**: 5 MB cap, `schema == 1`, type conversion, clamps, any error means unavailable with no NEW marks and no write. **The 7-day behaviour the operator chose is kept** and proved by `test_a_missed_day_is_named_for_seven_days_after_it_left_the_window_then_forgotten` and the AC-12 test. One effect: a day older than its period, named once after a long gap, is not repeated afterwards. | `memory.py`, `function_app.py`, `TG.py`, `tests/test_memory.py`, `tests/test_pipeline.py`, `tests/test_function_app.py` |
| **N-3** | `pipeline.py` no longer imports `memory`. `build_digest` takes an optional `missed` list, which the callers compute with `memory.missed_days`. | `pipeline.py`, `function_app.py`, `TG.py` |
| **SF-1** | The footer's "attached xlsx" sentence appears only when `total > 0`. Assertion added. | `report.py`, `tests/test_report.py` |
| **SF-3** | Test for a crash after delivery: `RuntimeError`, one notice, heartbeat pings success, outcome stays `digest_delivered`. The failure notice now says only "The eTenders digest run failed." (test asserts the old wording is gone). | `email_sender.py`, `tests/test_function_app.py`, `tests/test_email_sender.py` |
| **SF-2** | API_CONTRACT **v0.3**: `missed_named`, the simplified schema, the new signatures, O-5 to O-10 closed, D-18 to D-21. | `docs/cosmos/API_CONTRACT.md` |
| **N-1, N-2** | `report.py` no longer re-filters URLs before `_link` or the xlsx sink check; dropped `.astype(bool)` on boolean columns and the `days_left` NA check; one `_day_text` helper. | `report.py` |
| **N-4** | `url == HEARTBEAT_PREFIX`. | `function_app.py` |
| **N-5** | Removed the duplicated tests: two in `test_report.py`, the integration heartbeat test, the "send times out" and `ContainerNotFound` rows in `test_function_app.py`, and a one-value parametrize. | tests |
| **N-6** | "tender(s)" in the summary line and the "more tender(s)" line (contract and tests updated). | `report.py`, tests |
| **N-7** | The tuning-constants test checks shape only (positive ints, lowercase and non-empty sets, unique keywords), so an operator edit to `CATEGORIES` or `KEYWORDS` can't break CI. | `tests/test_pipeline.py` |
| N-8, N-9 | Not touched (BA wording; the commented `KEYWORDS` block is the operator's toggles). | — |

I edited Frontend and Backend files for these fixes, as the Team Leader permitted (neither was running).
Still for others: the BA amends AC-28, AC-21a, AC-34, AC-39 (the `report_until` clause), NFR-10 and SC6; the
Architect reconciles DESIGN_SPEC §5.1 (the schema and day-state diagram) with API_CONTRACT v0.3.

### 6.1 QA fix log (TEST_REPORT defects QA-D1 to QA-D6)

The 10 strict `xfail` tests in `tests/test_qa.py` now pass without their markers (I removed the markers, so
each is a normal test). Each asserts the delivered outcome, so it fails if the fix is removed. The full suite
gives **457 passed**: 10.61 s on the last run, and 22 s on two earlier runs while the machine was busy. NFR-10's
target is 15 s, so it passes when the machine is quiet (QA measured 11.7 s) and can miss when it is loaded; flagged for the
Team Leader. QA's tests are most of the growth (slowest call 3.3 s); the rest is collection and imports.

| ID | Fix | File |
|---|---|---|
| QA-D1 | `load()` also catches `OverflowError` in the conversion step. An out-of-range stored time now makes memory unavailable, and the digest is still sent (TL-2). | `memory.py` |
| QA-D3 | New `digest.strip_unwritable` removes lone surrogates and U+FFFE/U+FFFF as well as openpyxl's illegal characters. `pipeline._text` and `report._text` use it, replacing their direct use of `ILLEGAL_CHARACTERS_RE`. | `digest.py`, `pipeline.py`, `report.py` |
| QA-D2 | `tracking_since` is clamped to at most `resolved_through + 1 day`. | `memory.py` |
| QA-D4 | `RecursionError` is treated as an API failure for that day, in `_get_page` and in `fetch_releases`. | `pipeline.py` |
| QA-D5 | `_parse_time` returns NaT for a time later than the end of pandas' range minus one day, so the SAST conversion cannot overflow. | `pipeline.py` |
| QA-D6 | `heartbeat_config` removes trailing `/` characters from `HEARTBEAT_URL`. | `function_app.py` |

Net lines: code **+17** (1,750 → 1,767); `tests/test_qa.py` **−22** (the 10 `xfail` markers and their reason text).
API_CONTRACT is now v0.4 (§2.5, §3.3, §4.5). I accidentally emptied `digest.py` while applying these (a failed
write), and rebuilt it from the version in this session; its tests pass.
