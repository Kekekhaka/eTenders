# TEST_REPORT: eTenders digest (stage 6, QA)

- **Date:** 2026-10-01 · **Version:** 1.0 · **Author:** QA Analyst
- **Tested:** the working tree at `b7d4996` plus the uncommitted stage 4 build (`digest.py`, `memory.py`,
  `report.py`, `pipeline.py`, `function_app.py`, `email_sender.py`, `TG.py`, `host.json`).
- **Judged against:** `REQUIREMENTS_PACK.md` v0.3 (AC-01 to AC-40, 42 IDs once AC-21 is split and AC-28b is
  counted), `API_CONTRACT.md` v0.3, `TEAM_LEADER_DECISIONS.md`, `IDEA_BRIEF.md` (SC1 to SC9).
  `REVIEW_REPORT.md` (pass-with-fixes, fixes applied) was read, not relied on.
- **Rule used where documents disagree:** the contract and the decision log win over AC text (§7 lists each case).
- **Offline only.** No call was made to eTenders, Azure, ACS or Healthchecks. `local.settings.json` was not read.
  Every external edge was a fake. No git command that changes anything was run, and no product file was edited.
- **Added:** `tests/test_qa.py` (166 tests: 156 pass, 10 are strict `xfail` markers for open defects).

## 0. Result in one paragraph

Suite: **`447 passed, 10 xfailed in 11.68s`** (`.venv/Scripts/python.exe -m pytest -q`; the 291 existing tests
plus 156 QA tests pass, and 10 QA tests are expected failures that record 6 open defects). All 42 acceptance
criteria have a passing automated test, and I confirmed by mutation that the tests fail when the behaviour
breaks: **56 of 56 reintroduced bugs were caught**, including the five you named. Against the real
requirements the build is sound, but adversarial testing found **6 defects: 0 critical, 0 high, 2 medium,
4 low.** The two medium ones (QA-D1, QA-D3) each break the "malformed input never crashes a run" promise
(NFR-03, NFR-14) for a rare input, and each has a small fix. **Gate: MET. Recommendation: GO, after the Lead
Engineer fixes QA-D1 and QA-D3 and I retest** (section 9).

---

## 1. Test plan

### 1.1 Scope and approach

| Level | What was done | Where |
|---|---|---|
| Unit | Scoring, flattening, dates, memory rules, rendering, redaction | existing 8 files, 291 tests |
| Integration | `function_app.run` with the real pipeline, memory, report and email code; fakes only at the edges (eTenders session, blob HTTP, ACS client, ping) | `tests/test_integration.py`, `tests/test_qa.py` |
| Contract | `Digest` fields, `is_safe_url` shared by all three modules, `report` imports nothing from the backend | `tests/test_integration.py`, `tests/test_digest.py` |
| End to end (offline) | Multi-run sequences against an in-memory blob: first run, daily runs, outages, failed sends, retries | `tests/test_qa.py` sections 1 and 2 |
| Exploratory / adversarial | Malformed releases (48 shapes), tampered memory (19 blobs), unicode and RTL, huge and empty digests, clock edges | `tests/test_qa.py` sections 2 to 7, 10 |
| Mutation | 56 reintroduced bugs, one at a time, in a scratch copy of the repo | §6 |
| Regression | Old (`HEAD`) vs new scoring, filtering, province and category on a fixed random data set | §5 |
| Static | CI gate wording, deploy-package contents, 3.11 grammar, no DST or host-timezone calls | §3.2 |

Entry: 291 tests green, reviewer verdict pass. Exit (team rule): every AC passes, no critical or high defect open.

### 1.2 Environment

Windows 11, Git Bash, Python 3.12.10 in `.venv` (Azure and CI run 3.11; see "Not verified"), pandas 3.0.6,
openpyxl 3.1.5, pytest 9.1.1. Scratch work (mutation copies, the `HEAD` copy of `pipeline.py`, coverage output)
is in the session scratchpad, never in the repo.

### 1.3 Risks that drove the exploratory work

1. A bad input that crashes the run is replayed on every run while the release stays in the window (6 days).
2. The memory is new state with a managed identity; a wrong "first run" or a lost write changes what the
   operator is told.
3. The half-day boundary: a 06:00 UTC run covers only part of "today", so "covered" must not be claimed early.
4. Anything in the email that came from the API is untrusted (formulas, links, markup).

---

## 2. Results per acceptance criterion

"Orig" = tests that existed before QA (`tests/test_<file>.py::name`, file prefix omitted when obvious).
"QA" = tests I added in `tests/test_qa.py`. "M" = mutants that this AC's tests killed (§6).
I opened each listed test and checked that its assertions would fail if the behaviour broke; the mutation
results confirm that for every row that has an M entry. Rows without an M entry were checked by reading the assertions.

| AC | Result | Covering tests | Independent check |
|---|---|---|---|
| AC-01 scheduled digest | Pass | Orig: `test_first_run_marks_everything_new_and_writes_memory_after_delivery`, `test_subject_of_a_complete_digest_gives_the_counts_with_no_partial_suffix`, `test_the_digest_goes_to_every_recipient_with_the_xlsx`, `test_recipients_are_split_on_commas` | M55 (empty attachment) |
| AC-02 nothing matches | Pass | Orig: `test_empty_digest_says_nothing_matched_and_has_no_table`, `test_xlsx_is_none_when_there_are_no_tenders`, `test_an_empty_digest_is_sent_without_an_attachment`. QA: `test_an_empty_run_still_delivers_a_digest_without_an_attachment` | M34 |
| AC-03 one day cannot be fetched | Pass | Orig: `test_a_failing_api_day_gives_a_partial_digest_and_keeps_the_other_days`, `test_subject_of_a_partial_digest_ends_with_partial`, `test_partial_digest_names_each_day_with_its_reason`. QA: `test_an_all_days_down_run_is_a_partial_empty_digest_not_a_crash` | M31 |
| AC-04 never-sent tender first | Pass | Orig: `test_new_tenders_come_first_then_every_tie_break`, `test_the_order_does_not_depend_on_the_order_the_api_sent`, `test_rows_are_shown_in_digest_order_without_resorting` | M10, M49 |
| AC-05 new only once | Pass | Orig: `test_a_tender_is_new_only_once`, `test_second_run_with_the_same_releases_marks_nothing_new`, `test_a_second_local_run_remembers_what_the_first_one_saw` | M30, M49 |
| AC-06 failed or timed-out send does not use up "new" | Pass | Orig: `test_a_failed_send_does_not_use_up_new`, `test_an_acs_timeout_is_not_delivery_so_the_next_run_still_marks_new`, `test_a_send_that_is_not_confirmed_raises` (timeout and non-`Succeeded`). QA: `test_a_failed_send_then_a_retry_names_the_missed_days_and_flags_new_again` | M4, M6, M7 |
| AC-07 first run | Pass | Orig: `test_on_a_first_run_every_tender_is_new`, `test_first_run_notice`, `test_only_blob_not_found_is_a_first_run`. QA: `test_the_first_run_marks_every_tender_new_and_names_no_missed_day` | M12 |
| AC-08 memory unavailable | Pass | Orig: `test_with_memory_unavailable_nothing_is_new_and_no_day_is_missed`, `test_memory_unavailable_banner`, `test_subject_says_new_unknown_when_memory_is_unavailable`. QA: `test_a_bad_memory_blob_is_unavailable_never_a_crash` (19 blobs) | M11 |
| AC-09 day leaves window never fetched | Pass | Orig: `test_a_day_that_was_never_fetched_is_named_when_it_leaves_the_window`, `test_permanently_missed_days_name_the_kind_and_say_check_by_hand`. QA: `test_a_run_after_a_three_week_outage_names_each_lost_day_once`, `test_a_missed_day_is_named_for_seven_days_after_it_leaves_the_window_then_not` | M50, M13 |
| AC-10 covered day not reported | Pass | Orig: `test_a_covered_day_is_not_named`, `test_a_day_is_covered_only_by_a_run_that_started_after_it_ended` | M14 |
| AC-11 day fetched only while in progress | Pass | Orig: `test_a_day_fetched_only_while_in_progress_is_named_only_partly_fetched`. QA: `test_a_day_that_only_ever_came_in_part_is_named_only_partly_fetched`, `test_a_morning_run_leaves_today_partial_and_the_next_morning_run_covers_it`, `test_the_half_day_boundary_is_midnight_sast_not_midnight_utc` | M14 |
| AC-12 report survives a failed send | Pass | Orig: `test_a_missed_day_named_in_a_digest_that_failed_to_send_is_named_again`, `test_a_missed_day_is_named_again_after_a_failed_send`. QA: the failed-send retry test above | M22 |
| AC-13 closing-soon boundary | Pass | Orig: `test_the_closing_soon_boundary` (1 h, 7 d, 7 d 1 min). QA: `test_the_closing_soon_boundary_through_build_digest` (adds closes-now and just-closed) | M8, M42, M52 |
| AC-14 no closing date | Pass | Orig: `test_no_closing_date_is_listed_but_never_closing_soon`, `test_a_tender_without_a_closing_date_says_so`, `test_a_tender_with_no_closing_date_is_kept` | read |
| AC-15 closed tenders dropped | Pass | Orig: `test_a_tender_closing_exactly_at_run_time_is_kept_and_one_before_is_dropped`, `test_select_tenders_keeps_open_wanted_tenders` | M9 |
| AC-16 summary and columns | Pass (text differs, §7) | Orig: `test_body_opens_with_the_summary_line_and_has_every_required_column`, `test_closing_is_shown_in_sast_with_days_left`, `test_summary_says_new_unknown_when_memory_is_unavailable`. QA: `test_html_times_are_sast_even_when_the_run_is_in_another_offset`, `test_a_single_tender_summary_reads_acceptably` | M41 |
| AC-17 row cap never hides priority rows | Pass | Orig: `test_priority_rows_are_never_capped_and_the_rest_is_counted`, `test_no_more_line_when_every_row_is_shown`. QA: `test_a_huge_digest_renders_in_reasonable_time_and_stays_consistent` | M26 |
| AC-18 banners first | Pass | Orig: `test_banners_come_in_the_fixed_order_above_the_table` | M36 |
| AC-19 xlsx same rows, order, flags | Pass | Orig: `test_xlsx_has_one_sheet_with_every_row_in_digest_order_and_the_flags`, `test_xlsx_rows_match_the_html_order` | read |
| AC-20 Excel-native formatting | Pass | Orig: `test_xlsx_dates_are_sast_datetimes_with_frozen_filtered_header`, `test_only_safe_links_become_xlsx_hyperlinks` | M37, M38, M39, M40 |
| AC-21a text never a formula | Pass | Orig: `test_api_text_is_never_a_formula` (8 payloads, 4 columns). QA: `test_ac21a_no_text_column_can_become_a_formula` (9 payloads, every text column incl. ocid, province, contact) | M5 |
| AC-21b only safe links | Pass | Orig: `test_only_safe_links_become_hrefs_in_the_html`, `test_only_safe_links_become_xlsx_hyperlinks`, `test_unsafe_urls_are_rejected`. QA: `test_ac21b_only_safe_links_become_links_in_both_outputs` (15 URLs) | M16 |
| AC-22 crash gives a notice and a failed invocation | Pass | Orig: `test_a_crash_sends_a_redacted_notice_and_reraises`, `test_the_failure_notice_escapes_the_error_text` | M29, M47 |
| AC-23 email channel down, heartbeat on | Pass offline; the alert itself is not verifiable here | Orig: `test_the_heartbeat_sends_only_allow_listed_data_to_the_right_target[nothing_delivered]`. QA: `test_both_emails_failing_is_nothing_delivered_pings_fail_and_still_raises` | M35 |
| AC-24 tuning in one place | Pass | Orig: `test_configured_provinces_are_spelled_the_way_the_api_spells_them`, `test_tuning_constants_exist_and_have_a_sensible_shape`. QA: `test_ac24_tuning_is_one_edit_in_pipeline_py`, `test_every_configured_province_name_is_a_real_south_african_province` | M3, M13 |
| AC-25 failing test blocks deploy | Pass (static only) | QA: `test_ac25_the_deploy_job_needs_the_test_job` reads `deploy.yml` (`needs: test`, runs pytest). The gate itself ran nowhere I can reach. | read |
| AC-26 minimal RUN_SUMMARY | Pass | Orig: `test_a_delivered_first_run_saves_the_memory_and_logs_one_summary` (complete), `test_a_partial_digest_records_each_failed_day_and_its_reason`, `test_a_crash_sends_a_redacted_notice_and_reraises`, heartbeat test `[nothing_delivered]`. QA: `test_exactly_one_run_summary_line_for_every_ending` | read |
| AC-27 container missing is not a first run | Pass | Orig: `test_missing_container_means_memory_unavailable_and_nothing_written`, `test_only_blob_not_found_is_a_first_run` | M12, M11 |
| AC-28 invalid blob never trusted or overwritten | **Pass for every listed row; one family of blobs crashes (QA-D1)** | Orig: `test_an_invalid_memory_is_unavailable_never_a_crash` (10 rows), `test_a_memory_that_cannot_be_trusted_is_never_written`. QA: 19 more blobs incl. a recursion bomb | M45, M11 |
| AC-28b extra key ignored | Pass | Orig: `test_an_extra_top_level_key_is_ignored_and_not_written_back` | read |
| AC-29 malformed releases | **Pass for every listed row; 3 further inputs fail (QA-D3, D4, D5)** | Orig: `test_malformed_releases_are_dropped_counted_or_blanked`, `test_a_page_whose_releases_is_not_a_list_makes_that_day_partial`, `test_malformed_releases_are_dropped_and_the_digest_still_goes_out`. QA: 17 dropped shapes and 31 blanked shapes through `run()` | M20, M21, M54 |
| AC-30 no secret leaks | Pass | Orig: `test_no_secret_leaks_into_logs_email_summary_heartbeat_or_the_raised_error`, `test_redact_removes_each_kind_of_secret`. QA: `test_the_failure_notice_never_contains_a_secret_from_the_environment` | M17, M29, M46, M47, M51 |
| AC-31 heartbeat off | Pass | Orig: `test_with_the_heartbeat_off_no_ping_is_made`, `test_footer_says_when_heartbeat_monitoring_is_off` | M53 |
| AC-32 heartbeat misconfigured | Pass | Orig: `test_a_misconfigured_heartbeat_is_never_pinged_or_shown`, `test_heartbeat_config`. QA: `test_heartbeat_config_edges` (look-alike hosts) | read |
| AC-33 allow-listed body, right target | Pass (one low defect, QA-D6) | Orig: `test_the_heartbeat_sends_only_allow_listed_data_to_the_right_target`, `test_the_ping_is_a_post_without_redirects_and_with_a_5_second_timeout` | M18, M35 |
| AC-34 summary fits one trace | Pass | Orig: `test_the_summary_fits_in_one_trace_at_worst_case` (6 failed days with 200-char reasons, a year-long gap, `missed_named` capped at 10) | read |
| AC-35 escaping torture | Pass | Orig: `test_escaping_torture_test`, `test_subject_holds_no_api_text`, `test_a_link_shows_its_host` | M15, M48 |
| AC-36 token cannot be obtained | Pass | Orig: `test_without_a_token_the_digest_goes_out_with_no_blob_request`, `test_any_token_failure_is_reported_as_token` (7 variants), `test_no_identity_configured_means_no_token_and_no_request` | read |
| AC-37 timer registered | Pass | Orig: `test_the_function_app_registers_one_timer`. QA: static cross-check of the decorator text | M27 |
| AC-38 no sampling, quiet loggers | Pass | Orig: `test_traces_are_never_sampled_and_noisy_loggers_stay_quiet`. QA: `test_ac38_sampling_is_off_in_host_json` | M19 |
| AC-39 absurd dates clamped | Pass against the amended text; low gap QA-D2 | Orig: `test_absurd_dates_are_clamped_not_fatal`, `test_an_absurd_resolved_through_is_clamped_to_a_bounded_range`. QA: `test_absurd_but_representable_dates_are_clamped`, `test_an_outage_of_over_a_year_is_bounded_and_still_delivers` | M24 |
| AC-40 failing heartbeat never breaks the run | Pass | Orig: `test_a_failing_heartbeat_never_breaks_the_run` (3 rows), `test_a_failing_ping_returns_a_status_and_never_raises` | read |

**Totals:** 42 ACs, 42 pass, 0 fail. AC-28, AC-29 and AC-39 pass on their listed rows but the defects below
show their intent is not fully met for rare inputs. AC-23 and AC-25 have a half that cannot be shown offline.

---

## 3. Exploratory and adversarial testing

All of this is automated in `tests/test_qa.py` so it repeats.

### 3.1 What I tried and what happened

| Area | Cases | Outcome |
|---|---|---|
| 7-day missed-day rule | A day never fetched is named from the day it leaves the window for 7 days, then not (run twice a day for 20 days) | Correct |
| Very long outage | Last run 21 days earlier: 16 days named once (the partly fetched one as "only partly fetched"), repeated only for the 8 still in their period, none a week later | Correct |
| Outage over a year | Memory from 2025-06 read on 2026-10-01: run delivers, naming is bounded to 366 days, `missed_named` is 10, written memory stays small | Correct (banner can be long, §4.3) |
| Failed send then retry | Memory byte-identical after the failed run; the retry flags every tender NEW again and names the missed days again | Correct |
| Failed memory write after delivery | Outcome stays `digest_delivered`; next run honestly flags NEW again | Correct |
| Empty digest | Delivered, no attachment, no "attached xlsx" sentence, memory written | Correct |
| All six days down | Delivered as an empty partial digest, subject ends "(partial)" | Correct |
| Huge digest | 1,500 NEW tenders through `run()`: renders in a few seconds, every row shown, xlsx has 1,501 rows. Measured by script: 210 rows = 237 KiB HTML + 23 KiB xlsx; 1,000 rows = 1.1 MiB + 84 KiB; 8,000 rows is about 9.5 MiB, so the ACS 10 MB limit is reached near 8,400 NEW rows (about 40 times a normal day) | Correct; limits noted in §4 |
| First run | Every tender NEW, first-run notice, no missed days, memory written with `If-None-Match: *` | Correct |
| Unicode and RTL | Arabic, Hebrew, bidi override, CJK, astral emoji, combining and zero-width characters, tab/newline, markup | Text survives and is escaped; see §4.4 for display |
| Very long values | 100,000-character buyer | Run delivers; openpyxl itself truncates a cell to 32,767 characters, so no defect |
| Malformed releases | 17 unusable shapes (None, string, number, list, `{}`, bad or 201-character or control-character-only ocid) and 31 odd-field shapes (wrong types everywhere, hostile URLs, absurd dates, `+99:00`) | Never crash, except QA-D5 (the one date at the edge of pandas' range) |
| Page-level shapes | Non-JSON, number, null, `[]`, `releases` as string, object, number, a ValueError | Each is an API failure for that day (partial) or an end of pages; run delivers. Except QA-D4 (recursion) |
| Tampered memory | 19 blobs: empty, `null`, array, wrong types everywhere, naive time, NaN, a 100,000-deep array | All "unavailable", nothing written. Except QA-D1 (two blobs crash the run) |
| Half-day boundary | 06:00 run leaves today `partial`; 18:00 run still partial (20:00 SAST); next 06:00 run makes it `covered`; one second before and at 22:00 UTC is partial / covered | Correct |
| Clock edges | 21:59:59 UTC is still the SAST day; 22:00:00 UTC starts the next SAST day; 00:00 UTC is 02:00 SAST, same SAST day; same instant in offsets -5, -4, 0, +2, +5, +13 gives one window | Correct |
| DST | No `zoneinfo`, `pytz`, `localtime`, `now()` without a zone, `today()` or `utcnow` in any module (checked by test); both zones are fixed UTC+2 | No assumption either way |
| Closing-soon | closes in 1 h, exactly 7 d, 7 d 1 min, exactly now, 1 s ago; naive closing read as SAST | Correct (a tender closing exactly now is kept, not "soon", per `run_at < closing`) |
| Heartbeat | Look-alike hosts (`hc-ping.com.evil.example`, embedded URL), both emails failing, trailing slash | One low defect, QA-D6 |
| Secrets | A secret in the exception text, the identity header, the heartbeat URL, `?sig=` | Absent from notice, logs, summary, re-raised error |
| Timeouts | Worst-case sum of every timeout on the delivered path is 555 s and on the failure path 560 s, against a 600 s function timeout (`test_the_run_fits_the_function_timeout_in_the_worst_case`) | Fits with at least 30 s spare; the 1 to 3 s of rendering is the rest |
| Local runner | `TG.main` with `mi_token` and the blob store booby-trapped | Never reaches production memory |

### 3.2 Static checks

- Every `.py` file (product and tests) parses under the Python 3.11 grammar.
- Deploy package: with only the files `.funcignore` leaves in (`function_app`, `pipeline`, `memory`, `report`,
  `digest`, `email_sender`, `host.json`, `requirements.txt`) `import function_app` succeeds and does not import `TG`.
- `requirements.txt` is unchanged by this work (no new dependency, so no new cost from the code).
- Memory size at the steady state (35 new tenders a day for 90 days, 3,150 ocids) is 283 KiB, against a
  2 MiB warning and a 5 MiB cap.

---

## 4. Defects

Severity: Critical (data loss or security), High (a core requirement fails in normal use), Medium (a stated
requirement fails for a rare input, or an operator-visible flaw with a workaround), Low (rare, cosmetic or self-inflicted).
Each has a strict `xfail` test in `tests/test_qa.py` that fails the day the defect is fixed; remove the marker then.

| ID | Sev | Where | Open |
|---|---|---|---|
| QA-D1 | Medium | `memory.py:275` | yes |
| QA-D2 | Low | `memory.py` (`_clamp`, `missed_days`) | yes |
| QA-D3 | Medium | `pipeline.py:_text` | yes |
| QA-D4 | Low | `pipeline.py:_get_page` | yes |
| QA-D5 | Low | `report.py:90` (`_sast`) | yes |
| QA-D6 | Low | `function_app.py:174` | yes |

No critical or high defect is open.

### QA-D1 (Medium): an out-of-range stored time crashes the run, so no digest is sent

- **Requirement:** NFR-14(b), AC-28, TL-2 ("any error in that sequence makes memory unavailable"; S-10 asks that a tampered blob cannot crash the run).
- **Steps:** put a blob in the memory store whose `sent` entry has `"first_sent": "9999-12-31T23:59:59-05:00"` (or `"0001-01-01T00:00:00+05:00"`), everything else valid. Run `function_app.run`.
- **Expected:** `memory.read` is `unavailable:invalid:sent`, the digest goes out with the unavailable banner, nothing is written.
- **Actual:** `OverflowError: date value out of range` escapes `memory.load` (`memory.py:275` catches only `KeyError, TypeError, ValueError, AttributeError`). It happens before the fetch, so the run fails with no digest, only a failure notice, and does the same on every run until someone fixes the blob.
- **Why Medium:** the blob is private and only this app writes it, so it needs a hand edit or a tampered blob. But it is exactly the case S-10 and AC-28 exist for, and the effect is total (no digest at all).
- **Note:** the contract (§3.3 step 4) names the four exception types, which conflicts with TL-2's "any error". The decision log wins, so this is a defect, and the contract line should be corrected with the fix.
- **Suggested fix:** add `OverflowError` to that tuple.
- **Evidence:** `test_a_stored_time_that_overflows_utc_makes_memory_unavailable_not_a_crash` (2 params). I also ran the two blobs by hand: both raise `OverflowError`.

### QA-D2 (Low): `tracking_since` is not clamped, so a far-future value switches missed-day tracking off for good

- **Requirement:** AC-39 (absurd dates are clamped, not fatal), NFR-04.
- **Steps:** valid memory with `"tracking_since": "2099-01-01"` and an old `resolved_through`; run after an outage.
- **Expected:** missed days are still named. **Actual:** none are named, and `after_delivery` keeps writing the same `tracking_since`, so it never recovers.
- **Why Low:** hand edit only. A far-past value is harmless (bounded by the `resolved_through` clamp).
- **Suggested fix:** clamp `tracking_since` to at most the run date, as is done for `resolved_through`.
- **Evidence:** `test_a_far_future_tracking_since_does_not_switch_missed_day_tracking_off`.

### QA-D3 (Medium): a lone surrogate or U+FFFE/U+FFFF in API text corrupts the xlsx and makes the run fail after the email is sent

- **Requirement:** NFR-03, NFR-14(a) ("xlsx-illegal control characters are stripped"), AC-29.
- **Steps:** serve a release whose title contains `"\ud800"` (a valid JSON escape) or the character U+FFFE or U+FFFF. Run `function_app.run` with the real `email_sender` and a fake ACS client.
- **Expected:** the digest is delivered, the xlsx opens, the memory is saved.
- **Actual (surrogate):** `build_xlsx` returns a file that openpyxl cannot reopen (`ParseError`). The email is sent (the fake ACS received it), then `send_report` raises `UnicodeEncodeError` at `len(body.encode("utf-8"))` (`email_sender.py:71`). The run is recorded as failed, a failure notice follows, and the memory is never written. This repeats every run for the 6 days the release stays in the window, so every tender is re-flagged NEW each time.
- **Actual (U+FFFE/U+FFFF):** no crash, but the attached xlsx is not well-formed XML, so Excel reports corruption.
- **Why Medium:** needs a rare character in the API data, but the cost is six days of broken NEW marking plus repeated failure notices.
- **Suggested fix:** in `pipeline._text` strip surrogates and U+FFFE/U+FFFF as well as the characters in `ILLEGAL_CHARACTERS_RE`.
- **Evidence:** `test_a_lone_surrogate_in_a_title_does_not_crash_the_run`, `test_xml_noncharacters_do_not_corrupt_the_xlsx` (2 params). I checked the send-then-raise order by hand with the real `email_sender` and a fake client: 1 message sent, then `UnicodeEncodeError`.

### QA-D4 (Low): a hostile deeply nested API body fails the whole run

- **Requirement:** NFR-03, FR-03 ("absorb API failures").
- **Steps:** the API answers a page with a JSON body nested hundreds of thousands deep. `requests.Response.json()` raises `RecursionError` (confirmed on a real `requests.Response` object: it is not a `ValueError`).
- **Expected:** that day is partial with a reason. **Actual:** `_get_page` catches `(RequestException, ValueError)`, so `RecursionError` escapes and fails the run. `memory.load` already catches `RecursionError`; `_get_page` is inconsistent with it.
- **Why Low:** needs a hostile or badly broken eTenders.
- **Suggested fix:** also catch `RecursionError` in `_get_page`.
- **Evidence:** `test_a_recursion_bomb_page_is_an_api_failure_not_a_run_failure`.

### QA-D5 (Low): a closing time in the last two hours of pandas' date range crashes the report

- **Steps:** a release with `endDate` `"2262-04-12"` (or `"2262-04-11T23:00:00Z"`). `_parse_time` accepts it, then `report._sast` raises `OutOfBoundsDatetime` when it converts to SAST (+2 h pushes it past the limit). The whole run fails.
- **Expected:** a blank closing date with the raw text shown, as for `9999-12-31` (which does become `NaT` correctly).
- **Why Low:** one odd date value; real placeholders such as `9999-12-31` and `2099-12-31` are handled.
- **Suggested fix:** in `_parse_time`, also reject values within a day of the pandas limits, or convert to SAST there.
- **Evidence:** `test_a_closing_time_at_the_very_edge_of_the_pandas_range_does_not_crash_the_run` (2 params).

### QA-D6 (Low): a `HEARTBEAT_URL` with a trailing slash sends the failure ping to `.../uuid//fail`

- **Steps:** set `HEARTBEAT_URL=https://hc-ping.com/<uuid>/`, make both emails fail. The `/fail` ping goes to `https://hc-ping.com/<uuid>//fail`.
- **Expected:** `.../<uuid>/fail`. **Actual:** a double slash, which Healthchecks may not treat as a fail signal. The missing success ping would still trip its grace-period alert, so the operator is still told, just later.
- **Suggested fix:** `rstrip("/")` in `heartbeat_config`.
- **Evidence:** `test_a_heartbeat_url_with_a_trailing_slash_still_pings_a_clean_fail_url`.

### 4.3 Observations (not defects; the operator or Lead should decide)

1. **An empty HTTP 200 counts as a covered day.** A degraded API that answers `{}` or `{"releases": []}` for a day that really has releases gives "day ok, 0 tenders", and the memory then marks the day `covered`. This is the baseline's behaviour and cannot be told apart from a quiet day, but it is a hole in SC1/SC2. A cheap guard: treat a window where every day returned zero releases as suspect and say so in the banner.
2. **Gmail clipping in steady state.** At about 1.1 KiB per row, 102 KB is about 90 rows. The HTML shows every NEW and every closing-soon row plus 25 others, so a busy day can exceed that. Gmail then hides the end of the message, which holds the "N more in the xlsx" line and the "Heartbeat monitoring is off" footer. TL-12 accepted this only for the first run. Unverified in Gmail.
3. **Long outage banners.** After an outage longer than 8 days the "permanently missed" banner lists each lost day on its own line (up to 366). It is bounded and correct but could be long; consider showing ranges.
4. **RTL and bidi text.** HTML cells have no `dir="auto"`, so an Arabic or Hebrew title in a mixed row may read in a confusing order. A U+202E (right-to-left override) in a title passes through unchanged and can visually reverse text that follows it. Hand to Security (display spoofing). Not checkable here in a real client.
5. **Memory growth cap.** At more than about 60,000 remembered ocids (about 19 times the normal rate) the blob passes 5 MB, `load` says `too_large` for good, and nothing is written again. Fine at this volume; there is no self-heal.
6. **Naive closing times changed meaning.** The baseline read an offset-less `endDate` as UTC; the build reads it as SAST (ADR-0017, V-02, unverified against the live API). A tender with such a date that closes within the 2-hour difference is now kept or dropped differently from before.
7. **Categories compare exactly.** `"Services: Professional "` (trailing space) is excluded, exactly as at baseline (confirmed in §5). It is a recall risk for SC3, not a regression.
8. **Cosmetic:** one tender reads "1 tender(s) · 1 new" (the Lead noted this).
9. **Suite time:** the suite went from about 4 s to about 12 s with the QA tests (target 15 s, TL-13); one run on a busy machine took 22.6 s, so the target is not safe with the QA tests in. Slow tests are `test_a_huge_digest_...` and `test_a_large_description_...`; mark or shrink them if time becomes tight.

---

## 5. Regression: the original behaviours

I extracted `pipeline.py` from `HEAD` (read-only `git show`) and ran its `select_tenders` and the new one over the
same 600 generated releases (random titles, descriptions, categories including near-misses, provinces, closing
dates from 2000 to 2099 and none, buyer fallbacks, reference-letter text). Closing dates were far from now so the
different clocks could not matter.

| Check | Nationwide | `PROVINCES = {"gauteng"}` |
|---|---|---|
| Rows kept, old vs new | 345 vs 345 | 170 vs 170 |
| Same set of ocids | yes | yes |
| Score equal, matched keywords equal, blockers equal | yes, yes, yes | yes, yes, yes |
| Title and buyer equal | yes | yes |
| Old order is score-descending (new order adds NEW, closing, number, ocid tie-breaks) | yes | yes |

So the keyword scoring (whole-word, not substring), the open filter, the category filter and the province
filter behave as before. The one intended change is the naive-date reading (§4.3 item 6). A fixed 7-row golden
set (`test_fixed_regression_set_keeps_the_original_scoring_and_filtering`) pins the scores and keywords so a future
edit to `KEYWORDS` that changes them is noticed.

---

## 6. Mutation spot-check

Method: copy the repo's product files and tests to the scratchpad, apply one change, run the whole suite, record
which tests fail, restore. The repo was never touched. Scripts are `mutate.py` and `mutate2.py` in the scratchpad.

### 6.1 The five you named

| # | Bug reintroduced | Result | Tests that failed |
|---|---|---|---|
| 1 | Substring keyword match (drop the `(?<!\w)` / `(?!\w)` guards in `pipeline._compile`) | **CAUGHT** | `test_score_ignores_keywords_hidden_inside_other_words` (orig), `test_fixed_regression_set_keeps_the_original_scoring_and_filtering` (QA) |
| 2 | Stop paginating on a short page (keep the short page, then stop) | **CAUGHT** | `test_pagination_stops_on_an_empty_page_not_a_short_one`, `test_a_day_cut_short_by_the_page_limit_is_kept_but_not_ok` (both orig) |
| 3 | Province typo `PROVINCES = {"guateng"}` | **CAUGHT** | `test_configured_provinces_are_spelled_the_way_the_api_spells_them` (orig), plus 2 QA tests (and 90+ others that depend on rows surviving) |
| 4 | Write the memory before the send is confirmed | **CAUGHT** | `test_a_failed_send_does_not_use_up_new`, `test_a_crash_after_delivery_...` (orig), `test_an_acs_timeout_is_not_delivery_so_the_next_run_still_marks_new` (integration), plus a QA test |
| 5 | `=`-prefixed title reaching the xlsx as a formula (remove `_neutralise_formulas`) | **CAUGHT** | `test_api_text_is_never_a_formula` (8 params, orig), `test_ac21a_no_text_column_can_become_a_formula` (QA) |

My first version of bug 2 returned before keeping the short page, so it also lost data and broke 77 tests; I
redid it as the realistic form (keep the page, then stop) and it still fails two original tests.

### 6.2 Fifty-one more, to test the mapping

All **CAUGHT** (56 of 56 overall). The 51 are grouped here; the exact test names are in the scratchpad's
`mutation_results*.json`.

| Area | Bugs reintroduced (all caught) |
|---|---|
| Delivery and memory | timed-out ACS send counted as delivered; non-`Succeeded` status counted as delivered; memory overwritten after an unreadable read; any 404 treated as first run; writes made unconditional; unknown schema accepted; absurd dates not clamped; `resolved_through` never advances; sent ocids forgotten after 1 day; closing + 30 days retention dropped; delivered ocids never recorded; everything NEW even with memory |
| Windows and days | missed days named for 2 days not 7; a day "covered" by a run during that day; page-limit day reported ok; window uses the UTC day not the SAST day; naive closing read as UTC; no dedupe by ocid; missed days never named |
| Flags and order | closing-soon off by one; already-closed tender counted closing soon (killed only by a QA test); a tender closing exactly now dropped; `days_left` rounds down; NEW sorted last |
| Report | HTML escaping removed; quotes not escaped; any URL scheme accepted; banner order reversed; row cap hides priority rows; times shown in UTC; no "(partial)" in subject; no "heartbeat off" footer; dropped count missing from footer; xlsx not frozen, no autofilter, closing as text, links not clickable; xlsx built for an empty digest; attachment empty |
| Security and operations | Bearer redaction removed; configured secrets not redacted; failure notice not redacted; `logging.exception` used; raw exception re-raised; heartbeat follows redirects; failure pings the success URL; sampling on in `host.json`; timer fires once a day; ocid length limit removed; control characters not stripped |

Weakest spots (killed by exactly one test, so a refactor could weaken them): NEW-first ordering (M10), the 90-day
and closing + 30-day retention (M23, M43), the page-limit flag (M25), banner order (M36), the xlsx freeze, filter
and date cells (M37 to M39, one test), quote escaping (M48), and closing-soon excluding closed tenders (M52, only
my test).

---

## 7. Coverage

Line coverage by the 291 original tests alone, measured with the standard library `trace` module (no
`coverage.py` is installed, so this is line coverage, not branch coverage):

| Module | Executed | Missed lines | Coverage |
|---|---|---|---|
| `pipeline.py` | 266 | 170 (budget `break`), 400-401 (province branch) | 99% |
| `memory.py` | 268 | 174-175, 186-187 (file OSError paths), 262 (2 MB warning) | 98% |
| `report.py` | 282 | 103 (year suffix for another year) | 100% (rounded) |
| `digest.py` | 47 | none | 100% |
| `email_sender.py` | 47 | none | 100% |
| `function_app.py` | 128 | none | 100% |
| `TG.py` | 37 | 36, 38, 42, 61 (print branches, `__main__`) | 90% |

The province branch (`pipeline.py:400-401`) was not run by any original test (the default is nationwide); my
QA tests cover it. Requirements coverage: 42 of 42 ACs mapped; FR-01 to FR-19, FR-22 to FR-24 and NFR-01 to
NFR-15 are exercised by the same tests; NFR-01 (cost) and NFR-11 (pinned builds) are not testable here.

---

## 8. Conflicts between AC text and the contract or decision log

The contract (v0.3) and the decision log won in each case.

1. **AC-16 wording:** the AC says "`<total> tenders · <new> new · …`". The contract (D-21, TL-17 N-6) and the code say "tender(s)". Tests follow the contract. The AC wording is stale.
2. **AC-39, third clause:** it asks for a stored `missed` entry with `report_until` clamped to run date + 7. TL-15 removed the stored `missed` map, so that clause cannot be tested. The code clamps `resolved_through` instead, which the tests cover. `tracking_since` is not clamped (QA-D2).
3. **AC-28 vs contract §3.3:** AC-28 and TL-2 say any error means unavailable; the contract (§3.3 step 4) lists four exception types. The code follows the contract, and that is the source of QA-D1. The contract line needs correcting.
4. **AC-26 vs TL-3:** TL-3 lists `missed_named` as dropped; TL-10 restores it. The AC and the code agree with TL-10.
5. **REQUIREMENTS_PACK §5.0** still says "API_CONTRACT v0.2 is authoritative"; the current contract is v0.3.
6. **SC6 / NFR-07:** TL-14 leaves freshness unmeasured, so SC6 can't be judged from logs (§10).

The AC-21a (no Coverage sheet), AC-28b (extra key ignored) and AC-34 (remaining fields only) text I read already
matches the contract.

---

## 9. Quality scorecard

| Criterion | Verdict | Evidence |
|---|---|---|
| Requirements fit | **Pass, with concerns** | 42/42 ACs pass; 56/56 mutants killed; regression identical to `HEAD`. Concern: AC-28 and AC-29 hold for every listed row but not for QA-D1 and QA-D3, which are the two medium defects. |
| Usability | **Pass, with concerns** | The sample HTML (37 KB, 47 tenders) is well-formed (no unclosed tags), uses table layout and inline styles only, has no script, style, link, image, `@import` or `url(`, and all links go to etenders.gov.za; contrast is tested at 4.5:1; times are SAST; NEW and closing-soon rows are never capped; the xlsx has a frozen header, autofilter, date cells and 46 of 47 clickable links (the one without has no document). Concerns: Gmail clipping in busy steady state, long banners, RTL display (§4.3). Outlook, Gmail and phone rendering **not verified**. |
| Reliability | **Concern** | Failure paths are strong (send timeout, ACS error, API down for the whole window, memory failures all give a delivered or noticed run, and the memory is never written early). But two medium defects (QA-D1, QA-D3) can make a run fail repeatedly, and silent-gap detection depends on the heartbeat, which is off by default (FR-11 and NFR-06 are not met while it is off, R-1). |
| Consistency | **Pass** | HTML, xlsx and RUN_SUMMARY use the same counts and the same row order by construction and by test; one `is_safe_url`; one clock (`run_at`); status vocabularies match the contract §6. Minor: stale AC wording (§8), "1 tender(s)". |
| Success criteria | see below | |

### Success criteria SC1 to SC9

| SC | Verdict | Evidence |
|---|---|---|
| SC1 feed coverage | **Concern** | The mechanism works and is tested (covered vs partial days, missed days named for 7 days, `missed_named` in the log). Not measured in production. An empty HTTP 200 would be recorded as covered (§4.3 item 1). |
| SC2 honest gaps | **Pass** | Partial subject and banner, missed-day banner, memory banner, dropped-release and heartbeat footers, each tested; no digest claims "complete" when a day failed. Residual: empty 200, and footer lines could be clipped by Gmail. |
| SC3 recall | **Concern (unmeasured)** | Needs the operator's labelled sample (V-01). The filters behave exactly as the baseline (§5), so no regression, but the category filter is exact-match (§4.3 item 7). |
| SC4 ranking | **Pass (mechanism); outcome unmeasured** | NEW first, then score, closing, number, ocid; deterministic; all NEW and closing-soon rows shown. The triage-minutes target needs the operator. |
| SC5 delivery reliability | **Concern** | Digest, partial digest and failure notice all tested. The "no silent gap over 24 h" part needs the optional heartbeat (off by default) and cannot be shown offline; QA-D1 and QA-D3 can cause repeated failed runs. |
| SC6 freshness | **Unmeasured** | TL-14: no log field carries it. The schedule (06:00, 18:00 UTC) allows 12 h. Cannot be judged. |
| SC7 bid lead time | **Pass** | `days_left` and closing-soon (7 days) are shown in HTML and xlsx; closing-soon boundary tested at 1 h, 7 d, 7 d 1 min. |
| SC8 running cost | **Pass for the code; Azure cost not verified** | No new dependency (`requirements.txt` unchanged), no paid resource in code; the memory blob is about 283 KiB at steady state with 2 writes a day; heartbeat off by default. I made no cost call and verified no price. |
| SC9 simplicity | **Pass** | Tuning constants are all in `pipeline.py` (tested that no other module redefines them); the suite is offline and gates the deploy (`needs: test`, static check); three new modules, no new framework. |

---

## 10. Release recommendation

**Gate: MET.** All 42 acceptance criteria pass, and no critical or high defect is open.

**Recommendation: GO, once QA-D1 and QA-D3 are fixed and retested.** Both are a few lines, both sit on the
"bad input never crashes a run" promise, and QA-D1 is the one case where a damaged memory blob stops any digest
at all. The four low defects (QA-D2, D4, D5, D6) can follow the first deploy. If the Team Leader prefers to ship
now, the medium defects are rare-input and self-limiting (a surrogate leaves the window in 6 days, a bad blob can
be deleted for a clean first run), so I would accept that risk knowingly rather than hide it.

Before relying on it, the operator still has to do the stage 8 steps (container, identity, role) and decide on the
heartbeat. Until the heartbeat is on, a run that sends nothing is invisible (FR-11).

**Retest plan:** when the Lead fixes a defect, remove the matching `xfail` marker in `tests/test_qa.py` (a strict
xfail turns red when the bug is gone), rerun the suite, and rerun the mutation scripts for the touched module.

---

## 11. Not verified, and why

- **Outlook, Gmail and phone rendering** of the HTML, and Excel's behaviour on the xlsx (corruption prompts,
  hyperlink limits). Only structure, escaping, contrast values and openpyxl round-trips were checked.
- **Python 3.11** (Azure and CI). The venv is 3.12.10; I checked that all files parse under 3.11 grammar but could not run them.
- **The live eTenders API** (day boundary meaning of `dateFrom`/`dateTo`, timezone of naive dates, empty-200 behaviour), **Blob storage and the managed identity token endpoint, ACS delivery and quota, and Healthchecks.io alerts**. All were faked, as instructed.
- **CI.** `deploy.yml` was read, not run. It pins `actions/checkout@v7` and `actions/setup-python@v7`; I could not confirm those versions exist (DevOps).
- **Cost in Azure** (no Azure call made).
- Line coverage is from the standard library `trace`, not `coverage.py`, and counts lines only, not branches.
