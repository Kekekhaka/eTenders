# WORK_BREAKDOWN: eTenders digest

- **Stage:** 4 (Lead Engineer, Plan mode), FULL mode, audit and hardening
- **Date:** 2026-10-01 · **Version:** 0.2 (applies TL-1 to TL-9 and OP-1 to OP-3 from
  `docs/cosmos/TEAM_LEADER_DECISIONS.md`)
- **Builds to:** `docs/cosmos/API_CONTRACT.md` v0.2 (written `C§n` below) and its code form `digest.py`.
  Precedence: the Team Leader decisions, then API_CONTRACT, then DESIGN_SPEC v0.2.
- **Baseline:** commit `b7d4996`, plus the operator's uncommitted `.funcignore` change.
  - Suite at the baseline: `17 passed`.
  - With the contract v0.2 (`digest.py` + `tests/test_digest.py`): **`41 passed in 1.91s`**.
  - Both counts are from my own runs of `.venv/Scripts/python.exe -m pytest -q`.
- **Cost (OP-2):** no task adds a paid resource, plan or service. The heartbeat is off by default (TL-9).

---

## 1. How the work is split

Frontend (4a) and Backend (4b) run **in parallel, as separate agents, in the same working tree**.
They meet only at `digest.py` (C§2, C§3.1) and at the **three** public functions of `report.py`
(C§3.4). Because `is_safe_url` lives in `digest.py` (TL-1), neither side waits on the other for it.
The Lead integrates at L-3.

---

## 2. File ownership (strict)

A file appears in exactly one list. **You may create or edit only the files in your own list.**
Every other file is read-only to you.

### 2.1 Frontend: may create or edit

| File | Status |
|---|---|
| `report.py` | new |
| `tests/test_report.py` | new |

### 2.2 Backend: may create or edit

| File | Status | Note |
|---|---|---|
| `pipeline.py` | edit | |
| `memory.py` | new | |
| `email_sender.py` | edit | |
| `function_app.py` | edit | |
| `TG.py` | edit | |
| `host.json` | edit | sampling off (B-9) |
| `.gitignore` | edit | append only |
| `.funcignore` | edit | **append only**: it holds the operator's uncommitted change. Re-read it right before editing. |
| `local.settings.json.example` | edit | the template only |
| `tests/test_pipeline.py` | edit | |
| `tests/test_memory.py` | new | |
| `tests/test_email_sender.py` | edit | |
| `tests/test_function_app.py` | new | |
| `tests/test_tg.py` | new, optional | |

### 2.3 Lead: owns

`digest.py`, `tests/test_digest.py`, `tests/test_integration.py` (new, at L-3), `pytest.ini`,
`requirements-dev.txt`, any `tests/conftest.py` (none is planned), and `docs/cosmos/WORK_BREAKDOWN.md`,
`API_CONTRACT.md` and `IMPLEMENTATION.md`.

### 2.4 Nobody on the engineering team edits

- `requirements.txt`: no new dependency (NFR-11).
- `.github/workflows/deploy.yml`: DevOps.
- `README.md` and the rest of `docs/**`: the BA, Architect and Team Leader.
- `etenders_matches.xlsx`: a generated file, which tests must not overwrite.
- **`local.settings.json`: never open, read, print or copy it.**

---

## 3. Rules for the parallel phase

1. **Stay in your files** (§2). Don't reformat anyone else's.
2. **Build to the contract.** Don't edit `digest.py`. If the contract is wrong or missing something,
   stop and report it to the Team Leader, citing the C§ section.
3. **Imports across the boundary.**
   - The Frontend imports only `digest`, `pandas`, `openpyxl` and the stdlib (C§3.4). It takes
     `is_safe_url` and `DISPLAY_TZ` from `digest`.
   - The Backend imports `report` only in `email_sender.py` and `TG.py`, and only its 3 public
     functions. `pipeline.py` takes `is_safe_url` from `digest`.
4. **Test in isolation.**
   - Frontend tests build `Digest` objects by hand, with a helper inside `tests/test_report.py`.
   - Backend tests replace `report`'s functions with fakes (`monkeypatch.setattr(email_sender.report, …)`).
5. **Shared tree.** A failure in a file you don't own may just mean the other agent is mid-edit.
   Re-run once. If it persists, report it; don't fix it.
6. **Before hand-off,** run the whole suite. All the tests in your files must pass. Report the exact counts.
7. **Python 3.11.** Azure runs 3.11, but the local venv is **3.12.10**. Don't use 3.12-only features:
   an f-string that reuses its own quote inside `{}`, `type X = …`, PEP 695 generics, or
   `itertools.batched`.
8. **Offline only.** No live calls, no network in tests, no secrets, no git.
9. **OneDrive:** re-read a file right before editing it.
10. **Keep it simple.** Plain functions, with no classes beyond those named in C§3.
11. **No coverage lost** when you rewrite an existing test (§7).
12. **Cost guardrail:** add nothing that costs money. The heartbeat stays off unless the operator
    configures it.

---

## 4. Order and dependencies

```
L-1 contract v0.2 (done, includes is_safe_url) ─────────────────────────────────┐
                                                                                │
  Backend:  B-1 ─┬─> B-2 ─┐                                                     │
                 └─> B-3 ─┼─> B-4 ──┐                                           │
            B-5 ──> B-6 ──┘         ├─> B-8 ──> hand-off 4b ────────────────────┤
            B-9 (any time)   F-1 ──>┴ B-7                                       │
                             F-1 ──> B-10 ──────────────────────────────────────┤
  Frontend: F-1 ─> F-2 ─> F-3 ─> F-4 ─> F-5 ─> F-6 ─> hand-off 4a ──────────────┤
                                                                                v
                                                                     L-3 integrate
```

The only cross-team dependency left is **F-1 first**: `report.py` must exist with its 3 functions
before the Backend's `import report` (B-7, B-10) works. If it isn't there yet, the Backend does other
tasks first. **Never create or stub `report.py`.**

---

## 5. Tasks

### 5.1 Lead

| ID | Task | Depends on | Satisfies | Done when |
|---|---|---|---|---|
| L-1 | `digest.py` v0.2 (C§3.1, with `is_safe_url` per TL-1 and without `covered_earlier` per TL-6), `tests/test_digest.py`, and these documents | — | ADR-0020 boundary; NFR-08(f) (the predicate); AC-21b (predicate cases) | **Done.** 41 pass. |
| L-3 | **Integrate.** Check each module against C§3 and the C§2.1 invariants. Write `tests/test_integration.py`: fake API pages → `function_app.run()` with a `FileStore(tmp_path)`, the real `report` and a fake `EmailClient`, over two runs. Run the whole suite. Write `IMPLEMENTATION.md`. | 4a and 4b | AC-01 to AC-06, AC-19 and AC-29 end to end; NFR-10 (suite < 5 s) | Every AC in §6 has a passing test, and the suite is green in < 5 s |

### 5.2 Backend

| ID | Task | Depends on | Satisfies | Done when |
|---|---|---|---|---|
| B-1 | **First.** In `pipeline.py`: `API_DAY_TZ`, `CLOSING_SOON_DAYS = 7`, `MISSED_REPORT_DAYS = 7`, `window_for`, `day_end_utc`, and `from digest import is_safe_url` | — | FR-04, FR-14, FR-16, NFR-09; ADR-0017; AC-24 | Tests: `window_for` at 06:00 and 18:00 UTC; `day_end_utc` |
| B-2 | **Fetch.** `DayResult(day, ok, reason)` (C§3.2; **no** `releases`/`date_min`/`date_max`, TL-3). A non-list `releases` → `ValueError`, retried. Also: `page limit`, `http_<code>`, `out of time`, and a dedupe that passes non-dicts and bad `ocid`s through. Remove `build_matches`. | B-1 | FR-02, FR-03, NFR-03, NFR-05; AC-03 (the reason), AC-29 (last row) | Updated fetch tests, plus tests for a non-list `releases`, `page limit`, `out of time` and a non-dict release in the dedupe |
| B-3 | **Validate and select** (C§4.1): `_obj`/`_text` with illegal characters stripped; the `ocid` rule (≤ 200 chars); `document_urls` via `digest.is_safe_url`; per-value date parsing; `closing_raw`; the FR-04 filter on `run_at`; `flatten -> dict \| None`; `select_tenders -> (df, dropped)` | B-1 | FR-04, FR-05, FR-06, FR-23, NFR-03, NFR-14(a), NFR-08(f); AC-14, AC-15, AC-21b (in `flatten`), AC-29 (rows 1–7) | One test per AC-29 row; closed rows dropped; rows with no closing kept; a naive date read as SAST; unsafe URLs dropped |
| B-4 | **`build_digest`** (pure): `is_new`, `closing_soon`, `days_left`, the 5-key stable sort, the counts, `closing_soon_days`, `partial_days` (day, reason), `missed_days`, and the `memory_status` mapping. All C§2.1 invariants hold. | B-2, B-3, B-6 | FR-08, FR-12, FR-13, FR-16, NFR-04; AC-04, AC-07, AC-08, AC-13, AC-14 | AC-13 boundaries; AC-04 order with every tie-break; first_run → all NEW; unavailable → none NEW, `new_count None`, no missed days; empty frame has `TENDER_COLUMNS` |
| B-5 | **Memory I/O and the lighter validation** (C§3.3, TL-2): `MEMORY_URL`, `mi_token`, `BlobStore`, `FileStore`, and `load` (≤ 5 MB → JSON → `schema == 1` → convert types → clamp; any failure → `unavailable:<reason>`). **No** exact-key or entry-cap checks. Helpers never raise or chain. | — | FR-12, FR-22, NFR-08(e), NFR-12, NFR-14(b), NFR-15; AC-07, AC-27, AC-28 (rows 1–6), AC-36, AC-39 | Fake-HTTP tests: `BlobNotFound`; `ContainerNotFound`; AC-28 rows 1–6; every AC-36 row with **no blob request made**; AC-39 clamps; 5 MB + 1 byte; an extra top-level key is ignored |
| B-6 | **Memory logic** (pure) and `save`: `missed_days`; `after_delivery` (`sent` add, refresh and prune per OQ-BA4; day states; `resolved_through`; `missed` with `report_until`; prune); `save` with one retry | B-1, B-5 | FR-12, FR-14, FR-15, NFR-12; AC-05, AC-09, AC-10, AC-11, AC-12 | Tests for AC-05, 09, 10, 11; a gap with no runs is still classified; pruning; `conflict` on 412/409; one retry on a network error |
| B-7 | **`email_sender.py`, transport only** (C§3.5, C§4.4): remove `build_summary_html`; `send_report(d) -> {"html_bytes", "xlsx_bytes"}`; delivered only when `done()` and `Succeeded`; timeouts 90/30 s; `send_failure_notice(error_text: str)` | **F-1** | FR-07, FR-09, FR-10, NFR-02, NFR-13; AC-01, AC-02, AC-22 | Fake `EmailClient`: attachment present and absent; poller not done → raises; status `Failed` → raises; the timeouts are passed |
| B-8 | **`function_app.py`** (C§3.6): `redact`; **containment, with no `RedactingFilter`** (TL-4), and `redact()` on the notice text and the top-level log (`logging.error(redact(traceback.format_exc()))`, never `logging.exception`); `urllib3`/`azure` at WARNING; `heartbeat_config`; `ping_heartbeat` (POST); `run()`; the **minimal RUN_SUMMARY** (C§3.8, TL-3); the timer shim with `context` | B-4, B-6, B-7 | FR-01, FR-10, FR-11, FR-19, FR-24, NFR-02, NFR-06, NFR-15; AC-06, AC-12, AC-22, AC-23, AC-26 (partly, C§7 O-5), AC-27/28 (no write), AC-30, AC-31, AC-32, AC-33, AC-34, AC-37, AC-38 (loggers), AC-40 | `run()` tests for every outcome; **the AC-30 leak test**; AC-33 body, target, `allow_redirects=False`, `timeout=5`; summary < 30,000 chars; AC-37 registration |
| B-9 | **Config.** `host.json`: `samplingSettings.isEnabled: false`, with no Debug/Trace level. Append `etenders_memory.json` and `etenders_digest.html` to `.gitignore` and `.funcignore`. Add an empty `HEARTBEAT_URL` to `local.settings.json.example`. | — | FR-19, FR-22, NFR-15; AC-38 | A test reads `host.json` |
| B-10 | **`TG.py`** (C§3.7): `FileStore`; `build_xlsx` → `etenders_matches.xlsx`; `render_html` → `etenders_digest.html` (kept, TL-5); print the partial and missed days; save the local memory; no email, no token | B-4, B-6, **F-1** | FR-22; AC-21a (shared builder) | Never touches `BlobStore`/`mi_token` |

### 5.3 Frontend

| ID | Task | Depends on | Satisfies | Done when |
|---|---|---|---|---|
| F-1 | **First.** `report.py` with its **three** public functions (`render_subject`, `render_html`, `build_xlsx`), `_e` and `OTHER_ROWS_CAP = 25`. Rough bodies are fine. | `digest.py` (done) | unblocks B-7 and B-10 | `import report` works |
| F-2 | `render_subject` (C§2.6) | F-1 | FR-07, FR-08, FR-17; AC-01, AC-03, AC-35 (subject) | Complete, partial, "new unknown", torture input |
| F-3 | `render_html` (C§2.6): the summary line, banner order, table, row rule, "N more", empty digest, footer, SAST, escaping, links via `digest.is_safe_url`, `contact` as plain text | F-1 | FR-08, FR-09, FR-15, FR-17, FR-23/FR-24 footer, NFR-04, NFR-08(b), NFR-08(f), NFR-13; AC-02, AC-03, AC-07, AC-08, AC-09, AC-11, AC-14, AC-16, AC-17, AC-18, AC-21b (HTML), AC-27/28 (banner), AC-29 (footer), AC-31/32 (footer), AC-35 | AC-16, AC-17 (40 new, cap 25), AC-18, the AC-35 torture test, AC-21b (HTML), each fixed string in C§2.6 |
| F-4 | `build_xlsx` (C§2.6, **one sheet `Tenders`, no Coverage sheet**, TL-5): naive SAST datetimes, frozen header, autofilter, widths, hyperlinks only through `is_safe_url`, every `"f"` cell → `"s"`, `None` when `total == 0` | F-1 | FR-18, NFR-08(c), NFR-08(f); AC-02, AC-19, AC-20, AC-21a, AC-21b (xlsx) | AC-21a: every payload in a title → `data_type "s"` after `load_workbook`; no `<f>` in any sheet XML; nothing prefixed. AC-21b (xlsx). AC-20 checks. |
| F-5 | Size check | F-3 | NFR-13 | A 60-row digest with 200-char titles renders ≤ 100 KB |
| F-6 | Re-create the four HTML tests leaving `tests/test_email_sender.py` (§7) in `tests/test_report.py` | F-3 | FR-08, FR-17, NFR-08(b) | The four behaviours are tested |

---

## 6. Acceptance-criteria coverage

"Int" = `tests/test_integration.py` (L-3). The rows marked † changed in v0.2.

| AC | Task(s) | Test file(s) |
|---|---|---|
| AC-01 | B-7, B-8, F-2, L-3 | test_email_sender, test_function_app, test_report, Int |
| AC-02 | F-3, F-4, B-7, L-3 | test_report, test_email_sender, Int |
| AC-03 | B-2, B-4, F-2, F-3, L-3 | test_pipeline, test_report, Int |
| AC-04 | B-4, L-3 | test_pipeline, Int |
| AC-05 | B-6, L-3 | test_memory, Int |
| AC-06 | B-8, L-3 | test_function_app, Int |
| AC-07 | B-5, B-4, F-3 | test_memory, test_pipeline, test_report |
| AC-08 | B-4, F-3 | test_pipeline, test_report |
| AC-09 | B-6, F-3 | test_memory, test_report |
| AC-10 | B-6 | test_memory |
| AC-11 | B-6, F-3 | test_memory, test_report |
| AC-12 | B-6, B-8 | test_memory, test_function_app |
| AC-13 | B-4 | test_pipeline |
| AC-14 | B-3, B-4, F-3 | test_pipeline, test_report |
| AC-15 | B-3 | test_pipeline |
| AC-16 | F-3 | test_report |
| AC-17 | F-3 | test_report |
| AC-18 | F-3 | test_report |
| AC-19 | F-4, L-3 | test_report, Int |
| AC-20 | F-4 | test_report |
| AC-21a † | F-4 (the `Tenders` sheet; the Coverage-sheet clause is moot under TL-5, C§7 O-7); B-10 uses the same builder | test_report |
| AC-21b † | L-1 (predicate), B-3, F-3, F-4 | test_digest, test_pipeline, test_report |
| AC-22 | B-7, B-8 | test_email_sender, test_function_app |
| AC-23 | B-8 (the alert itself: stage 8) | test_function_app |
| AC-24 | B-1; the existing province test | test_pipeline |
| AC-25 | No build work (`deploy.yml` already has `needs: test`) | — |
| AC-26 † | B-8: **partly**. The minimal summary records requested and missing days, but not permanently missed days (C§7 O-5, awaiting the TL/BA). | test_function_app |
| AC-27 | B-5, B-8, F-3 | test_memory, test_function_app, test_report |
| AC-28 † | B-5 (rows 1–6), B-8, F-3. Row 7, "extra top-level key", no longer holds under TL-2 (C§7 O-6). | test_memory, test_function_app, test_report |
| AC-29 | B-2, B-3, B-4, F-3, L-3 | test_pipeline, test_report, Int |
| AC-30 † | B-8: containment plus `redact()` on the notice and the top-level log (TL-4); B-5 helpers | test_function_app |
| AC-31 | B-8, F-3 | test_function_app, test_report |
| AC-32 | B-8, F-3 | test_function_app, test_report |
| AC-33 | B-8 | test_function_app |
| AC-34 † | B-8, against a worst case of 6 failed days with 200-char reasons (its *Given* refers to dropped fields; C§7 O-8) | test_function_app |
| AC-35 | F-2, F-3 | test_report |
| AC-36 | B-5, B-8 | test_memory, test_function_app |
| AC-37 | B-8 | test_function_app |
| AC-38 | B-8 (loggers), B-9 (host.json) | test_function_app |
| AC-39 | B-5 | test_memory |
| AC-40 | B-8 | test_function_app |

NFRs without an AC:
- NFR-02: the timeouts in B-5, B-7 and B-8.
- NFR-09: no new dependency.
- NFR-10: L-3.
- NFR-11: `requirements.txt` is unchanged.
- NFR-13: F-5.

---

## 7. The existing 17 tests

| Test (today) | File owner | Fate |
|---|---|---|
| 3 × `test_score_*` | Backend | unchanged |
| `test_configured_provinces_are_spelled_the_way_the_api_spells_them` | Backend | unchanged (AC-24) |
| `test_day_chunks_run_newest_first_and_cover_each_day_once` | Backend | Keep, or replace with a newest-first, each-day-once test if `_day_chunks` is removed. `CHUNK_DAYS` must be 1. |
| `test_pagination_stops_on_an_empty_page_not_a_short_one` | Backend | keep; add `page limit` |
| `test_a_non_json_error_page_is_retried` | Backend | keep; add a non-list `releases` |
| `test_a_failing_day_is_reported_without_losing_the_other_days` | Backend | update to `DayResult` |
| `test_a_bug_in_our_code_is_not_disguised_as_an_api_outage` | Backend | unchanged |
| `test_select_tenders_keeps_open_wanted_tenders_best_first` | Backend | update to the new signature; ordering moves to B-4 |
| `test_tender_fields_are_html_escaped`, `test_missing_values_render_as_blank_cells`, `test_partial_digest_names_the_missing_dates`, `test_complete_digest_has_no_partial_warning` | Backend file → **moves** | The Frontend re-creates them in `test_report.py` (F-6). The Backend deletes the originals with `build_summary_html`. |
| `test_failure_notice_escapes_the_error_text` | Backend | update: takes a `str` |
| `test_missing_setting_fails_with_a_clear_message`, `test_recipients_are_split_on_commas` | Backend | unchanged |

`tests/test_digest.py` (Lead) adds 24 tests: the 5 contract tests and 19 `is_safe_url` cases.

---

## 8. Team Leader decisions applied, and what's still open

| TL | Applied in |
|---|---|
| TL-1 `is_safe_url` in `digest.py` | `digest.py`, `tests/test_digest.py`, C§2.5, B-1, B-3, F-3, F-4 |
| TL-2 lighter memory validation | C§3.3, B-5 |
| TL-3 minimal RUN_SUMMARY; 3 report functions | C§3.2 (`DayResult`), C§3.4, C§3.5, C§3.8, B-2, B-7, B-8 |
| TL-4 containment, no root/handler filter | C§3.6, B-8 |
| TL-5 no Coverage sheet; HTML preview kept | C§2.6, C§3.7, F-4, B-10 |
| TL-6 no `covered_earlier` | `digest.py`, C§2.2 |
| TL-7 keep `digest.py` | — |
| TL-8 contract authoritative (O-2, O-3, D-3, D-5, D-6, D-8) | C header, C§8 |
| TL-9 heartbeat optional, no cost by default | C§4.5, C§5 |
| OP-1 to OP-3 | No diagrams or cost figures in these documents. The cost line is in the header. |

**Still open: requirement text that the TL decisions contradict** (C§7, for the Team Leader and the BA):
- **O-5 (needs a decision):** AC-26 and FR-19 still require "permanently missed" days in RUN_SUMMARY.
  Either amend them, or reinstate one capped `missed_named` list.
- **O-6:** delete AC-28's "extra top-level key" row.
- **O-7:** drop AC-21a's Coverage-sheet clause.
- **O-8:** restate AC-34's *Given*.
