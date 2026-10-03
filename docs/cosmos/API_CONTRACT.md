# API_CONTRACT: eTenders digest

- **Stage:** 4 (Lead Engineer, Plan mode), FULL mode, audit and hardening
- **Date:** 2026-10-01 · **Version:** 0.4 (adds the six QA fixes; v0.3 applied TL-1 to TL-17 and OP-1 to OP-3; reconciled with the built code and the stage 5 review, SF-1 to SF-4)
- **Builds to:** `docs/cosmos/DESIGN_SPEC.md` v0.2, `docs/cosmos/REQUIREMENTS_PACK.md` v0.2,
  `docs/adr/0016`–`0025`, **`docs/cosmos/TEAM_LEADER_DECISIONS.md`**. Where they disagree, the Team
  Leader decisions win, then this contract (TL-8), then the DESIGN_SPEC. The Architect reconciles
  the DESIGN_SPEC.
- **Code form of §2:** `digest.py` (frozen dataclasses, constants and the one shared check
  `is_safe_url`, with no I/O), tested by `tests/test_digest.py`
- **Companion:** `docs/cosmos/WORK_BREAKDOWN.md` (tasks, owners, file ownership)
- **Evidence:** I read every source file, test, config file, ADR-0016 to 0025 and
  TEAM_LEADER_DECISIONS. Suite runs, all by me: 17 passed at the baseline, 22 with the v0.1 contract, and
  **41 passed in 1.91s** with v0.2. I checked three library behaviours offline in the repo venv (§2.3, §4.1,
  §4.4). v0.3: the suite is **291 passed in 3.77s**. I did not call the eTenders API, the identity endpoint, Blob storage, ACS or Healthchecks.
- **Cost (cost guardrail, OP-2):** this contract adds **no paid resource, plan or service**. The
  heartbeat is off by default (§4.5). The memory blob and its storage cost belong to the Architect's
  cost budget (DESIGN_SPEC). I didn't verify any price.

---

## 1. Change control

- `digest.py` and this document change **only through the Lead Engineer**, and always together.
- Frontend and Backend never edit `digest.py`. If you need a field or a rule changed, stop and
  report it to the Team Leader. Don't work around it.
- "Backend" means `pipeline.py`, `memory.py`, `email_sender.py`, `function_app.py` and `TG.py`.
  "Frontend" means `report.py`.

---

## 2. The `Digest` contract (Backend → Frontend)

`pipeline.build_digest(...)` returns one `digest.Digest`. `report.py` only reads it.

### 2.1 `Digest` fields

| Field | Type | Meaning | Invariants (the Backend guarantees them, the Frontend may rely on them) |
|---|---|---|---|
| `run_at` | `dt.datetime` | The run's single clock, captured once | tz-aware, UTC |
| `window` | `tuple[dt.date, dt.date]` | First and last publication day asked for, inclusive, in `pipeline.API_DAY_TZ` | `window[1] - window[0] == LOOKBACK_DAYS` days; `window[1]` is `run_at`'s date in `API_DAY_TZ` |
| `tenders` | `pd.DataFrame` | Every selected tender, flagged | Exactly `digest.TENDER_COLUMNS`, in that order, **even when empty**. Index `0..n-1`. Rows in the §2.4 order. Types per §2.3. |
| `total` | `int` | Number of rows | `== len(tenders)` |
| `new_count` | `int \| None` | Number of NEW rows | `None` **iff** `memory_status == "unavailable"`; otherwise `== tenders.is_new.sum()` |
| `closing_soon_count` | `int` | Number of closing-soon rows | `== tenders.closing_soon.sum()` |
| `closing_soon_days` | `int` | The `pipeline.CLOSING_SOON_DAYS` the flag used | For the wording only ("closing within 7 days") (TL-8, D-3) |
| `partial_days` | `tuple[PartialDay, ...]` | In-window days this run couldn't fetch completely | Newest day first. Each day is inside `window`. No duplicates. |
| `missed_days` | `tuple[MissedDay, ...]` | Permanently missed days to name in this digest | Oldest day first. Each day is **before** `window[0]`. Empty unless `memory_status == "ok"`. |
| `memory_status` | `str` | How the memory read went | One of `digest.MEMORY_STATUSES`: `"ok"`, `"first_run"`, `"unavailable"`. The detailed reason stays in RUN_SUMMARY. |
| `heartbeat` | `str` | Heartbeat configuration | One of `digest.HEARTBEAT_STATES`: `"on"`, `"off"`, `"misconfigured"` |
| `dropped_releases` | `int` | Malformed API releases left out (FR-23) | `>= 0` |

`total`, `new_count` and `closing_soon_count` are plain fields. The Backend computes them once in
`build_digest`, and RUN_SUMMARY, the heartbeat body, the subject and the HTML all use those same numbers.

`Digest` is `frozen=True, eq=False`. Frozen stops reassignment, but a DataFrame's contents can still be
changed. **The Frontend must not mutate `tenders`**: copy it first (`d.tenders.copy()`).
`eq=False` is there because pandas won't reduce `DataFrame == DataFrame` to a single bool.

### 2.2 `PartialDay` and `MissedDay`

| Class | Field | Type | Meaning / allowed values |
|---|---|---|---|
| `PartialDay` | `day` | `dt.date` | An in-window day with `DayResult.ok == False` |
| | `reason` | `str` | `"out of time"` \| `"page limit"` \| `"http_<code>"` \| an exception class name (`ReadTimeout`, `ConnectionError`, `ValueError`, …). Escape it like API text. |
| `MissedDay` | `day` | `dt.date` | A day that left the window without being covered |
| | `kind` | `str` | One of `digest.MISSED_KINDS`: `"never_fetched"`, `"only_partly_fetched"` |
| | `left_window_on` | `dt.date` | `day + LOOKBACK_DAYS + 1` |

`PartialDay.covered_earlier` was **removed** (TL-6).

### 2.3 `tenders` columns

Nulls: in pandas 3.0.6 (the pinned version), a text column is inferred as `StringDtype` and a missing
value reads back as **`NaN`**, not `None` (checked offline in the repo venv). **Always test for a null
with `pd.isna(x)`.** The Backend never stores the text `"None"`, `"nan"` or `"NaT"`.

| Column | Type | Null? | Rule |
|---|---|---|---|
| `ocid` | str | never | Non-empty, ≤ 200 chars, unique in the frame |
| `tender_number`, `title` (≤ 200 chars), `buyer`, `province`, `category`, `method`, `contact`, `matched_keywords`, `blockers` | str | yes | **Untrusted API text**: only `str` inputs are kept, with xlsx-illegal control characters stripped. Escape it in HTML; never let it become a formula. `""` and null render the same (blank). `contact` is plain text, never `mailto:`. |
| `closing`, `published` | `datetime64[ns, UTC]` | `NaT` | Parsed from ISO 8601. A value with no offset is read in `API_DAY_TZ`. Unparseable → `NaT`. |
| `closing_raw` | str | yes | The original closing text. Show it when `closing` is `NaT`. |
| `days_left` | `Int64` (nullable) | `pd.NA` when `closing` is `NaT` | `ceil((closing − run_at) / 1 day)`, ≥ 0 |
| `closing_soon` | bool | never | `run_at < closing ≤ run_at + closing_soon_days`; `False` when `closing` is `NaT` |
| `is_new` | bool | never | `ocid` not in `memory.sent`. All `True` on `first_run`, all `False` on `unavailable`. |
| `score` | int | never | Keyword score (ADR-0005, 0006) |
| `briefing_compulsory` | object: `True` / `False` / `None` | yes | Only a real JSON boolean is kept |
| `document_urls` | object: `list[str]` | never (may be `[]`) | At most 3, each passing `digest.is_safe_url`, in API order |

### 2.4 Ordering (FR-13, AC-04)

The Backend sorts once, with a **stable** sort, on these keys:

1. `is_new` descending (NEW first)
2. `score` descending
3. `closing` ascending, `NaT` last
4. `tender_number` ascending, null last
5. `ocid` ascending

Then it resets the index. **The Frontend never re-sorts.** The HTML and xlsx show rows in `tenders`
order, so they agree by construction (AC-19).

The row filter runs before the sort (FR-04, AC-15): keep a row when `closing >= run_at` or `closing` is `NaT`.

### 2.5 `digest.is_safe_url(u) -> bool` (TL-1) and `digest.strip_unwritable(text) -> str`

This is the **one** definition. `pipeline.flatten` uses it, and so does `report.py` at the sink.
Both import it from `digest`. `pipeline.py` may re-export it with `from digest import is_safe_url`.

It returns `True` only when all of these hold:
- `u` is a `str` of at most `digest.MAX_URL_CHARS` (2,000) characters;
- it has no whitespace and no control characters (below `\x20`, and `\x7f`);
- `urlsplit(u).scheme` is `http` or `https` (case-insensitive);
- `urlsplit(u).hostname` is non-empty.

It never raises: a `ValueError` from `urlsplit` gives `False`. Tested in `tests/test_digest.py`, which
covers 4 safe cases and 15 rejected ones (`javascript:`, `file:`, UNC, `ftp:`, an empty host,
whitespace, control characters, DEL, an over-long URL, a malformed host, and non-strings).

`digest.strip_unwritable(text)` (QA-D3) removes the characters openpyxl treats as illegal **plus lone
surrogates (U+D800 to U+DFFF) and U+FFFE/U+FFFF**, which would corrupt the xlsx or make the HTML fail to
encode. `pipeline._text` uses it on every API string, and `report` uses it again at the xlsx sink.

### 2.6 What the Frontend does with a `Digest`

The Frontend owns the layout. These are the **fixed** parts that other tests and ACs depend on.

**Rows in the HTML (OQ-BA1, AC-17).** Show every row with `is_new or closing_soon`, plus the first
`OTHER_ROWS_CAP = 25` other rows, all in `tenders` order. If any rows are left out, show
`"{n} more tender(s) in the attached xlsx only."`

**Order of the body (FR-17, AC-18):**
1. The summary line (AC-16): `"{total} tender(s) · {new_count} new · {closing_soon_count} closing within {closing_soon_days} days"`.
   When `new_count` is `None`, the middle part reads `"new unknown"`.
2. Banners, in this order, each only when it applies:
   1. **Permanently missed** (`missed_days` non-empty): each day with its kind as text, `"never fetched"`
      or `"only partly fetched"`, and an instruction to check **etenders.gov.za** by hand for those
      dates (AC-09, AC-11).
   2. **Partial** (`partial_days` non-empty): starts with `"Partial digest."` and names each day as
      `"{day} ({reason})"`, e.g. `2026-09-23 (ReadTimeout)` (AC-03).
   3. **Memory unavailable** (`memory_status == "unavailable"`):
      `"New-tender marking and missed-day tracking were unavailable for this run."` (AC-08, AC-27,
      AC-28); **or** the **first-run notice** (`memory_status == "first_run"`), containing
      `"first run with new-tender marking"` (AC-07).
3. The table (AC-16). Columns include: the flags as text (`NEW`, `CLOSING SOON`), closing in SAST,
   days left, tender #, buyer, title, category, and a link to the first document. With no `closing`,
   show `closing_raw` if there is one, or else `"no closing date"` (AC-14). When `total == 0`, show
   `"No matching tenders were found in this run."` instead of the table (AC-02).
4. The footer:
   - `"Heartbeat monitoring is off."` when `heartbeat != "on"` (AC-31, AC-32);
   - `"{n} malformed release(s) from the eTenders API were left out."` when `dropped_releases > 0` (AC-29);
   - a sentence that the attached xlsx has every tender with all columns, **only when `total > 0`**
     (there is no attachment on an empty digest; SF-1).

**Subject (FR-07, FR-08, FR-17, AC-35).** It is built from counts and constants only, never from API
text or a `reason`: `"eTenders digest - {total} tender(s), {new_count} new"`, or `"…, new unknown"`
when `new_count` is `None`. Append `" (partial)"` when `partial_days` is non-empty.

**Escaping (S-06, AC-35):**
- One helper, `_e(x) = html.escape("" if pd.isna(x) else str(x), quote=True)`, at **every**
  interpolation: cells, banners, `reason`, dates, counts and notes. Handle `document_urls` one URL at a time
  (don't call `pd.isna` on a list).
- Double-quote every attribute. An `href` must pass `is_safe_url` and is then escaped.
- No API value goes into `style`, `class` or any tag or attribute name. Colours are constants chosen by
  the backend booleans.
- Show a link's host as, or next to, its text.
- Inline styles only. No scripts, no remote images (NFR-13).

**Time.** Show `closing` and `published` in `digest.DISPLAY_TZ` (SAST). In the xlsx they are **naive**
SAST datetimes, because openpyxl can't store tz-aware values.

**xlsx (FR-18, ADR-0021, TL-5): one sheet, `Tenders`.**
- It holds every row of `tenders`, in order. The flag columns are headed exactly `new`,
  `closing_soon` and `days_left` (AC-19). `closing` and `published` are real date/time cells (AC-20).
  The header row is frozen and has an autofilter, and the column widths are readable. The first safe
  document URL is a clickable hyperlink (AC-20, AC-21b).
- **No `Coverage` sheet** (TL-5). Coverage is shown in the email banners.
- **No formulas (AC-21a):** before saving, set `data_type = "s"` on every cell that openpyxl marked
  `"f"`, walking the whole workbook. Don't prefix values with `'` or a space. `quotePrefix` is optional.
- Hyperlinks are set only for URLs that pass `is_safe_url` at the sink.
- `build_xlsx` returns `None` **iff** `total == 0`. The email then has no attachment (FR-09, AC-02).

---

## 3. Module interfaces

Public names only. Anything else is private (`_name`) to its owner.

### 3.1 `digest.py` (Lead)

```python
DISPLAY_TZ: dt.timezone                 # UTC+2, "SAST"
MEMORY_STATUSES = ("ok", "first_run", "unavailable")
HEARTBEAT_STATES = ("on", "off", "misconfigured")
MISSED_KINDS = ("never_fetched", "only_partly_fetched")
TENDER_COLUMNS: tuple[str, ...]         # §2.3, in order
MAX_URL_CHARS = 2000
class PartialDay(day, reason)                           # frozen
class MissedDay(day, kind, left_window_on)              # frozen
class Digest(run_at, window, tenders, total, new_count, closing_soon_count,
             closing_soon_days, partial_days, missed_days, memory_status,
             heartbeat, dropped_releases)               # frozen, eq=False
def is_safe_url(u: object) -> bool                      # §2.5
```

### 3.2 `pipeline.py` (Backend)

```python
# Tuning: stays one edit in this file (NFR-09, AC-24)
LOOKBACK_DAYS = 5; CLOSING_SOON_DAYS = 7; MISSED_REPORT_DAYS = 7
KEYWORDS, PROVINCES, CATEGORIES, BLOCKER_PATTERNS          # unchanged
BASE, PAGE_SIZE = 2000, MAX_PAGES = 50, FETCH_BUDGET_SECONDS = 420,
REQUEST_TIMEOUT = 150, MAX_RETRIES = 2, RETRY_BACKOFF_SECONDS = 10   # unchanged
API_DAY_TZ: dt.timezone      # UTC+2. The ONLY place the API's day timezone is assumed (V-02).

@dataclass(frozen=True)
class DayResult:             # Backend-internal (pipeline → memory, function_app). Not in digest.py.
    day: dt.date
    ok: bool                 # every page fetched without error and without hitting MAX_PAGES
    reason: str | None       # None when ok; else the PartialDay.reason vocabulary

def window_for(run_at: dt.datetime) -> tuple[dt.date, dt.date]
def day_end_utc(day: dt.date) -> dt.datetime            # 00:00 on day+1 in API_DAY_TZ, as UTC
def score(text: str) -> tuple[int, str]                  # unchanged
def blockers(text: str) -> str                           # unchanged
def fetch_releases(date_from: dt.date, date_to: dt.date) -> tuple[list, list[DayResult]]
    # Newest day first; one DayResult per day in the window, in that order.
    # Dedupes by ocid only among dicts with a valid ocid (§4.1). Every other item passes
    # through untouched, so flatten rejects it and select_tenders counts it.
    # Only API failures are absorbed (NFR-05).
def flatten(rel: object) -> dict | None                  # None = rejected (§4.1). Never raises on bad types.
def select_tenders(releases: list, run_at: dt.datetime) -> tuple[pd.DataFrame, int]
    # (open, wanted rows with the §2.3 text/date/url columns, dropped_count). Not yet flagged or sorted.
def build_digest(releases: list, day_results: list[DayResult], memory: dict | None,
                 memory_status: str, heartbeat: str, run_at: dt.datetime,
                 missed: list[MissedDay] = ()) -> Digest   # pure
    # memory_status is the full load() status; "unavailable:*" becomes "unavailable" in the Digest.
    # `missed` is memory.missed_days(memory, window), computed by the caller, so that pipeline.py
    # never imports memory.py (review N-3).
```

`DayResult` no longer has `releases`, `date_min` or `date_max` (TL-3). `build_matches()` is removed.

### 3.3 `memory.py` (Backend, new)

```python
MEMORY_URL = "https://etendersfuncstorage.blob.core.windows.net/digest-state/memory.json"
MAX_BYTES = 5 * 1024 * 1024
BLOB_API_VERSION = "2021-08-06"

def mi_token() -> tuple[str | None, str]
    # (token, "ok") | (None, "no_identity") if IDENTITY_ENDPOINT or IDENTITY_HEADER is unset
    # | (None, "token") on any failure. Never raises; never logs the token, header or response body.
class BlobStore:
    def __init__(self, url: str, get_token=mi_token, http=requests): ...
    def get_text(self) -> tuple[str, str | None, str | None]   # (status, text, etag), §4.3
    def put_text(self, text: str, etag: str | None) -> str     # "ok" | "conflict" | "failed:<reason>"
    # The token is fetched at most once per instance (one instance per run). If it fails, no blob request is made.
class FileStore:                       # TG.py and tests
    def __init__(self, path: str): ...
    def get_text(self) -> tuple[str, str | None, str | None]   # absent file → ("first_run", None, None)
    def put_text(self, text: str, etag: str | None) -> str     # etag ignored; write a tmp file, then os.replace

def load(store, run_at: dt.datetime) -> tuple[dict | None, str, str | None]   # never raises; see below
def missed_days(memory: dict | None, window: tuple[dt.date, dt.date]) -> list[MissedDay]   # pure; None -> []
def after_delivery(memory: dict | None, digest: Digest, day_results: list[DayResult],
                   run_at: dt.datetime) -> dict                    # pure; memory None = first run
def save(store, memory: dict, etag: str | None) -> str
    # "ok" | "conflict" | "failed:<reason>". Never raises. One retry after 5 s on failed:timeout /
    # failed:network only. etag None → If-None-Match: *.
```

**`load()`, lighter validation (TL-2).** It runs these steps in order, and the first failure stops it
with `(None, "unavailable:<reason>", None)`:
1. `store.get_text()`. Any status other than `ok` is returned as it is (`first_run` or `unavailable:*`). The body is capped at 5 MB, which gives `too_large`.
2. Parse the JSON → `bad_json`.
3. Check `schema == 1` → `schema`.
4. **Convert types.** Read each of the four keys the code uses into typed values, in this order:
   `tracking_since` and `resolved_through` (ISO dates → `dt.date`), `sent` (each entry has `first_sent`
   and `closing`, ISO datetimes **with a UTC offset** → UTC `dt.datetime`; `closing` may be `null`) and
   `days` (a map of ISO date → `"partial"` or `"covered"`). A failure gives `invalid:<key>`, naming
   the top-level key being converted. Catch exactly `(KeyError, TypeError, ValueError,
   AttributeError, OverflowError)` (`OverflowError`: a time that overflows when converted to UTC, QA-D1), **only around this conversion step**. This is not a blanket `except` (NFR-05).
5. **Clamp** (applied, not errors; AC-39): `first_sent ≤ run_at`; `closing ≤ run_at + 365 d`;
   `resolved_through` held between the run date − 366 days and the run date; `tracking_since` held at or
   before `resolved_through + 1 day` (QA-D2, so a far-future value can't switch missed-day tracking off).

**Dropped (TL-2, TL-15):** the exact-key-set checks, the per-map entry caps, and any validation of
fields the code never reads. Unknown extra keys, including the old `updated_at`, `updated_by_run` and
`missed`, are **ignored** and are not written back.

**The stored memory (schema 1, simplified by TL-15 before the first deploy):**

```json
{"schema":1,"tracking_since":"2026-09-25","resolved_through":"2026-09-24",
 "sent":{"ocds-9t57fa-123456":{"first_sent":"2026-09-30T06:07:41Z","closing":"2026-10-15T09:00:00Z"}},
 "days":{"2026-09-29":"covered","2026-09-30":"partial"}}
```

There is **no stored `missed` map**: it is derived by `missed_days(memory, window)`.
- A day is named when it is before `window[0]`, not `covered`, not before `tracking_since`, and **either**
  inside its reporting period (it left the window at most `MISSED_REPORT_DAYS` = 7 days ago, so
  `day ≥ window[0] − 8`, OQ-BA2) **or** after `resolved_through` (no delivered digest has named it yet;
  AC-12). The kind is `only_partly_fetched` for a `partial` day, otherwise `never_fetched`.
- `after_delivery` keeps each `days` entry until `window[0] − 8`, so the reporting period can be
  derived, and moves `resolved_through` up to `window[0] − 1`: that digest named every earlier gap.
- Behaviour change from v0.2: a day older than its reporting period, named once after a long gap, is not
  repeated for the rest of that date. FR-15 and AC-09 to AC-12 still hold.

**In the process** the values are typed: dates are `dt.date` (also the keys of `days`) and `first_sent` /
`closing` are UTC `dt.datetime`. `save` writes dates as `YYYY-MM-DD` and datetimes as
`YYYY-MM-DDTHH:MM:SSZ`.

### 3.4 `report.py` (Frontend, new): pure, no I/O, no Azure imports. **Three public functions** (TL-3, TL-5)

```python
OTHER_ROWS_CAP = 25
def render_subject(d: Digest) -> str
def render_html(d: Digest) -> str
def build_xlsx(d: Digest) -> bytes | None        # None iff d.total == 0; one sheet, "Tenders"
```

Allowed imports: the stdlib, `pandas`, `openpyxl` and `digest`. **Nothing** from `pipeline`, `memory`,
`email_sender` or `function_app`.

### 3.5 `email_sender.py` (Backend): transport only

```python
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DIGEST_TIMEOUT_S = 90; NOTICE_TIMEOUT_S = 30
def send_report(d: Digest) -> dict
    # report.render_subject / render_html / build_xlsx, then one email.
    # Returns {"html_bytes": int, "xlsx_bytes": int} (xlsx_bytes is 0 without an attachment).
    # Raises when the send isn't confirmed (§4.4): the caller treats that as "not delivered".
def send_failure_notice(error_text: str) -> None
    # error_text arrives ALREADY REDACTED. Subject "eTenders digest FAILED"; html.escape'd in <pre>.
    # Raises when the send isn't confirmed.
```

`build_summary_html` is removed (its job moves to `report.render_html`).

### 3.6 `function_app.py` (Backend)

```python
app: func.FunctionApp
def redact(text: str) -> str                       # DESIGN_SPEC §7 TB3 patterns → "REDACTED"
def heartbeat_config() -> tuple[str, str | None]   # ("on", url) | ("off", None) | ("misconfigured", None)
def ping_heartbeat(url: str, body: dict) -> str    # "ok" | "failed:<Class>" | "http:<code>"; never raises (§4.5)
def run(run_at: dt.datetime, store, send_report, send_failure_notice, ping,
        *, run_id: str = "local", past_due: bool = False) -> dict      # returns the summary dict

@app.timer_trigger(schedule="0 0 6,18 * * *", arg_name="timer", run_on_startup=False)
def etenders_digest(timer: func.TimerRequest, context: func.Context) -> None
    # run(now UTC, BlobStore(MEMORY_URL), email_sender.send_report, email_sender.send_failure_notice,
    #     ping_heartbeat, run_id=context.invocation_id, past_due=timer.past_due)
```

**Redaction by containment (TL-4).** There is **no `RedactingFilter`** on the root logger or its handlers.
- The blob, token and heartbeat helpers catch `requests.RequestException` inside themselves and return a
  status. They never raise, re-raise or chain anything that carries a URL or header.
- `redact()` is applied in exactly two places: the **failure-notice text** and the **top-level exception
  log**. The top-level handler logs `redact(traceback.format_exc())` with `logging.error`. It must **not**
  use `logging.exception(...)` or `exc_info=True`, because those would log the raw text.
- Re-raise with `raise RuntimeError(text) from None`, where `text` is already redacted. AC-30 requires
  the re-raised error to hold no secret and have no chained cause.
- At import, set the `urllib3` and `azure` loggers to `WARNING` (AC-38).
- The AC-30 leak test stays. Security re-checks this design at stage 7.

**`run()` sequence:**
1. `hb_state, hb_url = heartbeat_config()`
2. `memory, status, etag = memory.load(store, run_at)`
3. `releases, days = pipeline.fetch_releases(*pipeline.window_for(run_at))`
4. `d = pipeline.build_digest(releases, days, memory, status, hb_state, run_at)`
5. `email = send_report(d)`. Once this returns, the digest is **delivered**.
6. Only if `status` is `ok` or `first_run`: `save(store, after_delivery(...), etag)`. Otherwise
   `memory.write = "skipped"`, and **no write request is made** (AC-27, AC-28).
7. If `hb_state == "on"`: `ping(hb_url, body)`.

On an exception in steps 2–6:
1. `text = redact(f"{type(exc).__name__}: {exc}")`.
2. Log the redacted traceback.
3. Try `send_failure_notice(text)`.
4. If the heartbeat is on, `ping(hb_url + "/fail", body)`.
5. `raise RuntimeError(text) from None`.

A failed memory write after delivery is **not** an exception: it goes into `memory.write`, and the
outcome stays `digest_delivered`.

**A crash after delivery** (for example in `after_delivery`) still sends the notice and re-raises, but the
outcome stays `digest_delivered` and the heartbeat pings success, because the digest did go out. So the
notice wording is neutral ("The eTenders digest run failed."); it never says the digest wasn't delivered.

### 3.7 `TG.py` (Backend): local runner

`main()` uses `memory.FileStore("etenders_memory.json")`, never `BlobStore` or `mi_token` (FR-22). The
heartbeat is `"off"`. It writes `report.build_xlsx(d)` to `etenders_matches.xlsx` (when not `None`)
and `report.render_html(d)` to `etenders_digest.html` (**the preview is kept**, TL-5), prints each
partial and missed day, and then saves the local memory (D-7). It sends no email.

### 3.8 RUN_SUMMARY, minimal (TL-3)

One line per invocation, in `finally`:
`logging.info("RUN_SUMMARY %s", json.dumps(summary, separators=(",", ":")))`.

```json
{"v":1,"run_id":"…","started_at":"2026-10-01T06:00:01Z","duration_s":318.2,"past_due":false,
 "window":["2026-09-26","2026-10-01"],
 "days":[{"day":"2026-10-01","ok":true},{"day":"2026-09-27","ok":false,"reason":"ReadTimeout"}],
 "dropped_releases":0,"missed_named":["2026-09-18"],
 "counts":{"total":182,"new":31,"closing_soon":12},
 "memory":{"read":"ok","write":"ok"},
 "email":{"html_bytes":58211,"xlsx_bytes":40210},
 "outcome":"digest_delivered","error":null,"heartbeat":"off"}
```

- Fields the run didn't reach are `null`. `error` holds the exception **class name only**.
- `reason` is capped at 60 characters, and the line must stay under 30,000 characters (AC-34).
- `dropped_releases` stays, because FR-23 and AC-29 require it.
- `missed_named` (TL-10): ISO dates of the permanently missed days named in this digest, oldest first,
  **capped at 10**.
- **Dropped (TL-3):** `new_lag_hours`, `became_covered`, `newly_missed`, `counts.html_rows`, `fetch_s`,
  `date_min`/`date_max`, `days[].releases` and `memory.bytes`.

---

## 4. External contracts

### 4.1 eTenders OCDS API (untrusted, TB1)

**Request.** `GET https://ocds-api.etenders.gov.za/api/OCDSReleases`, `Accept: application/json`,
`PageNumber=<n>&PageSize=2000&dateFrom=<YYYY-MM-DD>&dateTo=<YYYY-MM-DD>` with `dateFrom == dateTo`.
Unchanged (ADR-0003, 0004).

**Timeouts.** Each attempt uses `timeout=min(150, remaining budget)`. There are 2 attempts per page, with
a 10 s × attempt backoff, never past the 420 s budget. Pages are read until an **empty** page, for at
most 50 pages.

| Response | Treated as | Day result |
|---|---|---|
| HTTP error | API failure, retried | `ok=False`, `reason="http_<code>"` |
| Timeout / connection error | API failure, retried | `reason` = the exception class |
| 200 non-JSON, or JSON that isn't an object | API failure, retried (`ValueError`) | `reason="ValueError"` |
| `releases` present but **not a list** | API failure, retried (`ValueError`) (FR-03) | `reason="ValueError"` (AC-29) |
| `releases` missing, `null` or `[]` | End of pages | — |
| 50 non-empty pages | Truncated (B5) | `ok=False`, `reason="page limit"`; the releases received are **kept** |
| Budget spent before the day starts | Not fetched | `ok=False`, `reason="out of time"` |

Releases from a day that failed part-way are discarded, as today.

**Fields consumed and their validation.**
- `_obj(x)`: `x` if it is a `dict`, else `{}`.
- `_text(x)`: `x` with `openpyxl`'s `ILLEGAL_CHARACTERS_RE` characters removed, if it is a `str`;
  otherwise `None`. A string that is `""` after stripping becomes `None`.
- There is no blanket `except` (NFR-05).

| JSON path (in a release) | Used for | Validation → on a bad type |
|---|---|---|
| (the release itself) | — | Must be a `dict`; otherwise **dropped and counted** |
| `ocid` | `ocid`, the dedupe key, the memory key | `_text`, then non-empty and ≤ 200 chars; otherwise **dropped and counted** (TL-8, D-8) |
| `description` | score text | `_text` |
| `buyer.name` | `buyer` (fallback) | `_obj` → `_text` |
| `tender` | everything below | `_obj` (`"tender": "x"` → blank fields; the release is kept, AC-29) |
| `tender.id` | `tender_number` | `_text` |
| `tender.title` | `title`, score text | `_text`, first 200 chars |
| `tender.description`, `.eligibilityCriteria`, `.specialConditions` | score and blocker text | `_text` |
| `tender.category` / `tender.province` | the column and its filter | `_text` |
| `tender.procurementMethodDetails` | `method` | `_text` |
| `tender.procuringEntity.name` | `buyer` (first choice) | `_obj` → `_text` |
| `tender.contactPerson.email` | `contact` | `_obj` → `_text` |
| `tender.tenderPeriod.endDate` / `.startDate` | `closing` + `closing_raw` / `published` | `_obj` → `_text` → parsed; unparseable → `NaT` |
| `tender.briefingSession.compulsory` | `briefing_compulsory` | `_obj`; kept only if `isinstance(v, bool)` |
| `tender.documents[].url` | `document_urls` | `documents` must be a `list` (else `[]`). Each item → `_obj`, `url` → `_text` → `digest.is_safe_url`. Keep the first 3 safe URLs. |

`release.date` is **no longer read** (TL-3 dropped `date_min`/`date_max`).

**Date parsing (verified offline).** In pandas 3.0.6, running `pd.to_datetime(series, utc=True)` over a
column that mixes naive and offset values turned the offset value into `NaT`. It also reads naive values
as UTC. So parse **each value on its own**, attach `API_DAY_TZ` when the value is naive, and convert to UTC.

### 4.2 Managed-identity token endpoint

| Item | Contract |
|---|---|
| Request | `GET {IDENTITY_ENDPOINT}?resource=https://storage.azure.com/&api-version=2019-08-01`, header `X-IDENTITY-HEADER: {IDENTITY_HEADER}`; 5 s timeout, no retry |
| Success | HTTP 200 with JSON containing a non-empty string `access_token` |
| Failure | Endpoint or header unset → `no_identity`. Anything else → `token`. |
| Secrecy | The token stays in a local variable or the per-run `BlobStore`. The token, the header value and the response body are never logged, put in a status, or raised. |
| Verification | **Not verified on this Linux Consumption app** (R-8, V-06; DevOps at stage 8) |

### 4.3 Blob REST (`MEMORY_URL`)

Common headers: `Authorization: Bearer <token>` and `x-ms-version: 2021-08-06`. Never `verify=False`.

**GET** (`timeout=10`, 1 attempt, `stream=True`, read at most `MAX_BYTES + 1` bytes):

| Result | `get_text` status |
|---|---|
| Token not available | `unavailable:no_identity` / `unavailable:token`, and **no GET is sent** (AC-36) |
| 200 | `ok`, plus the UTF-8 `text` and `etag` (the `ETag` header, verbatim) |
| Body over 5 MB / not UTF-8 | `unavailable:too_large` / `unavailable:bad_json` |
| 404 with `x-ms-error-code: BlobNotFound` | `first_run` (**the only first-run case**) |
| 404 with `x-ms-error-code: ContainerNotFound` | `unavailable:ContainerNotFound` (AC-27) |
| Any other status | `unavailable:http_<code>` |
| `requests.Timeout` / other `RequestException` | `unavailable:timeout` / `unavailable:network` |

**PUT** (`timeout=10`): headers `x-ms-blob-type: BlockBlob`, `Content-Type: application/json`, and either
`If-Match: <etag>` or, on a first run, `If-None-Match: *`. The body is compact UTF-8 JSON.

| Result | `put_text` status |
|---|---|
| 201 | `ok` |
| 412 / 409 `BlobAlreadyExists` | `conflict` (409 comes from the Azure docs; **not verified live**) |
| Any other status | `failed:http_<code>` |
| Timeout / network / no token | `failed:timeout` / `failed:network` / `failed:no_identity` or `failed:token` |

These helpers catch `requests.RequestException` inside themselves and never raise, wrap or chain it (TL-4).

### 4.4 Azure Communication Services Email

| Item | Contract |
|---|---|
| Client | `EmailClient.from_connection_string(ACS_CONNECTION_STRING)` (`azure-communication-email==1.1.0`) |
| Message | `{"senderAddress", "recipients": {"to": [{"address": a}, …]}, "content": {"subject", "html"}, "attachments": […]}`. `RECIPIENT_EMAIL` is split on commas. |
| Attachment | Only when `build_xlsx` returns bytes: `{"name": "etenders_matches.xlsx", "contentType": XLSX_MIME, "contentInBase64": …}` |
| Send | `poller = client.begin_send(message)`, then `result = poller.result(timeout=90)` (digest) or `30` (notice) |
| **Delivered** (TL-8, O-2) | **Only when `poller.done()` and `result["status"] == "Succeeded"`.** Otherwise raise; the caller takes the failure path. |
| Limits | 10 MB per request including base64. The Azure-managed domain allows 5 emails/min and 10/hour. |

Why: in `azure-core`, `LROPoller.result(timeout)` joins the polling thread for `timeout` seconds and then
returns whatever it has. It **doesn't raise when time runs out**
(`.venv/Lib/site-packages/azure/core/polling/_poller.py:314-338`). A timed-out send is not delivered.

### 4.5 Heartbeat: optional, off by default, no cost by default (TL-9)

| Item | Contract |
|---|---|
| Config | `HEARTBEAT_URL` unset or blank → `"off"`. Trailing `/` characters are removed first, so `/fail` is appended cleanly (QA-D6) (**the default**, and nothing is called). A value not starting with `https://hc-ping.com/` → `"misconfigured"`. Otherwise `"on"`. |
| Target | `HEARTBEAT_URL` after a delivered digest. `HEARTBEAT_URL + "/fail"` for `failure_notice_delivered` and `nothing_delivered` (AC-33). |
| Request | `requests.post(target, json=body, timeout=5, allow_redirects=False)`, POST (TL-8, O-3) |
| Body | Exactly `{"run_id": str, "outcome": str, "counts": {"total", "new", "closing_soon"} \| null}`. No exception text, URL or tender text. |
| Response | Status only: 2xx → `ok`, else `http:<code>`. `RequestException` → `failed:<Class>`. Never raised (AC-40). |
| Cost | The code adds no cost. The options are the operator's choice at deploy time (TL-9). **A:** the Healthchecks.io Hobbyist plan is free for 20 jobs and we'd use 1 (the Team Leader verified this at healthchecks.io/pricing on 2026-09-30); email alerts on the free plan are **unverified**. **B:** an Azure log alert adds cost and needs a verified price plus operator approval. **C:** none. |

---

## 5. Configuration

| Setting | Where | Secret? | Required? | Missing or wrong → |
|---|---|---|---|---|
| `ACS_CONNECTION_STRING` | app setting | **yes** | yes | crash → failure path (the notice fails too → `nothing_delivered`) |
| `SENDER_EMAIL`, `RECIPIENT_EMAIL` (comma-separated) | app settings | no | yes | same |
| `HEARTBEAT_URL` | app setting | low sensitivity (S-03); still never logged | **no; unset by default** | `off` / `misconfigured` + footer (FR-24) |
| `IDENTITY_ENDPOINT`, `IDENTITY_HEADER` | injected by the platform once the system-assigned identity is on | header **yes** | for memory | `unavailable:no_identity` + banner |
| `AzureWebJobsStorage`, `FUNCTIONS_WORKER_RUNTIME` | app settings | first **yes** | host only | not read by our code |
| `REQUESTS_CA_BUNDLE` | local only | no | behind the corporate proxy | never fix TLS with `verify=False` |
| `MEMORY_URL`, tuning constants | code (`memory.py`, `pipeline.py`) | no | — | one-line edits (T-10, AC-24) |

Stage 8 prerequisites for memory (not done here): a private `digest-state` container, the
system-assigned identity, and Storage Blob Data Contributor scoped to that container.
Nobody reads `local.settings.json`. `local.settings.json.example` may gain an empty `HEARTBEAT_URL`.

---

## 6. Status vocabularies

| Where | Values |
|---|---|
| `memory.load` / RUN_SUMMARY `memory.read` | `ok` · `first_run` · `unavailable:<reason>`, where `<reason>` ∈ `no_identity`, `token`, `ContainerNotFound`, `http_<code>`, `timeout`, `network`, `too_large`, `bad_json`, `schema`, `invalid:<key>`, `file` (FileStore) |
| `memory.save` / `memory.write` | `ok` · `conflict` · `failed:<reason>` (`timeout`, `network`, `http_<code>`, `no_identity`, `token`, `file`) · `skipped` |
| `Digest.memory_status` | `ok` · `first_run` · `unavailable` |
| `Digest.heartbeat` | `on` · `off` · `misconfigured` |
| RUN_SUMMARY `heartbeat` | `ok` · `failed:<Class>` · `http:<code>` (with a colon, as in AC-40) · `off` · `misconfigured` |
| RUN_SUMMARY `outcome` | `digest_delivered` · `failure_notice_delivered` · `nothing_delivered` |
| `PartialDay.reason` / `DayResult.reason` | `out of time` · `page limit` · `http_<code>` · an exception class name |
| `MissedDay.kind` | `never_fetched` · `only_partly_fetched` |

---

## 7. Open points

| # | Point | Status / interim |
|---|---|---|
| O-1 | Home of `is_safe_url` | **Closed (TL-1):** `digest.py`, done and tested |
| O-2 | ACS timeout not raising | **Closed (TL-8):** §4.4 is authoritative; the Architect reconciles the DESIGN_SPEC |
| O-3 | Heartbeat GET with a body | **Closed (TL-8):** POST |
| O-4 | The Healthchecks POST, the Blob 409 and the MI token endpoint come from the docs | Open: DevOps, stage 8 (V-06) |
| O-5 | AC-26 / FR-19 and permanently missed days | **Closed (TL-10):** `missed_named`, capped at 10 |
| O-6 | AC-28 "extra top-level key" row | **Closed (TL-11):** the BA amends it; an extra key is ignored and the read is `ok` |
| O-7 | AC-21a Coverage-sheet clause | **Closed (TL-11):** the BA drops it |
| O-8 | AC-34 worst-case setup | **Closed (TL-11):** the BA restates it with the remaining fields |
| O-9 | Memory schema | **Closed (TL-15):** simplified (§3.3) before the first deploy; DESIGN_SPEC §5.1 and AC-39's `report_until` clause need the Architect and the BA |
| O-10 | NFR-10 time target; SC6 | **Closed (TL-13, TL-14):** 15 s; SC6 stays unmeasured in Phase 1 (BA records both) |

---

## 8. Deviations from DESIGN_SPEC v0.2 (D-18 to D-21 are new in v0.3)

All are confirmed by TL-1 to TL-8, except where marked otherwise.

| # | Design says | This contract says | Authority |
|---|---|---|---|
| D-1 | `Digest` types live in `pipeline.py` | `digest.py` | Brief, TL-7 |
| D-2 | `DISPLAY_TZ` in `pipeline.py` | `digest.DISPLAY_TZ` | Lead (the Frontend needs no `pipeline` import) |
| D-3 | No way to say "7 days" | `Digest.closing_soon_days` | TL-8 |
| D-4 | Derived counts; lists | Plain fields; tuples | Lead |
| D-5 | `mi_token() -> str \| None` | `-> tuple[str \| None, str]` | TL-8 |
| D-6 | No `run_id` / `past_due` | Keyword-only on `run`; `run_id` on `after_delivery` | TL-8 |
| D-7 | When `TG.py` saves is unstated | After its files are written | Lead |
| D-8 | `ocid` "non-empty" | Also ≤ 200 chars, or dropped | TL-8 |
| D-9 | Dropped count from fetch (diagram) or select (§6.1) | `select_tenders` | Lead (§6.1) |
| D-11 | API HTTP errors give `HTTPError` | `http_<code>` | Lead |
| D-12 | `is_safe_url` in `pipeline.py` | `digest.is_safe_url` | TL-1 |
| D-13 | Strict memory validation (§5.2) | Lighter validation (§3.3) | TL-2 |
| D-14 | Full RUN_SUMMARY (§8.4) | Minimal (§3.8) | TL-3 |
| D-15 | `RedactingFilter` on the root logger and its handlers | Containment, plus `redact()` on the notice and the top-level log | TL-4 |
| D-16 | `Coverage` sheet; `PartialDay.covered_earlier` | Both removed | TL-5, TL-6 |
| D-17 | Heartbeat via GET | POST; off by default | TL-8, TL-9 |

| D-18 | Stored `missed` map, `updated_at`, `updated_by_run`, `days[].at` (§5.1) | Removed; `missed` is derived (§3.3) | TL-15 |
| D-19 | `build_digest` calls `memory.missed_days` | The caller passes `missed` in | Review N-3 |
| D-20 | `missed_days(memory, window, run_at)`; `after_delivery(..., run_id)` | `run_at` and `run_id` dropped | TL-15 |
| D-21 | Summary `"{total} tenders"` and `"{n} more tenders are in…"` | `tender(s)` | TL-17 (N-6) |

(D-10, `html_rows`, is withdrawn by TL-3.)
