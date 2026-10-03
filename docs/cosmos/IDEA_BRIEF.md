# IDEA_BRIEF: eTenders digest

- **Stage:** 1 (Ideologist), FULL mode, audit of an existing product
- **Date:** 2026-09-30
- **Baseline commit:** `b7d4996` (working tree also has an uncommitted `.funcignore` change that adds `.claude` and `docs`)
- **Cost correction (2026-09-30, OP-3; edited by the BA as documentation owner):** the baseline cost is
  **US$0.068/month ≈ R1.09/month**, not "R0.07/month". The earlier figure was US dollars mislabelled as
  rand. It was corrected in §2, SC8 and A7. See `docs/cosmos/TEAM_LEADER_DECISIONS.md` and ADR-0026.
- **Sources:** the repo (`README.md`, `pipeline.py`, `function_app.py`, `email_sender.py`, `host.json`, `.github/workflows/deploy.yml`, `tests/`), facts the Team Leader supplied, and web research (section 3). The eTenders API was **not** called.

Each claim is tagged with one of four labels: **[validated]** (a validated need or a measured fact), **[probable]**, **[assumption]** (an assumption still to test) or **[speculative]** (a speculative stretch).

---

## 1. Concept in one sentence

A personal, near-free, scheduled service that reads South Africa's official
public-procurement feed (National Treasury eTenders, published as OCDS) and emails one
South African data engineering / BI consultant a short, reliable list of open government
tenders they could bid on. It aims to reach them early enough to bid, and it has to say
clearly what it could not check.

## 2. Problem, user and core value

**User.** Just one person: the operator, a South African data engineer / BI consultant
who wants to bid on government work in data engineering, BI, Power BI, SQL Server, Azure,
ETL and data migration. **[validated]** It's a single-user, personal project with no other
users or customers. Nothing here should be read as demand from anyone else.

**Problem.**
1. Relevant tenders are scattered through a national feed of about 116 releases a day, from
   every sphere of government. **[validated]** (the volume was measured)
2. The metadata is almost useless for spotting relevance. Titles are reference codes
   (e.g. "RFQ2596") and descriptions are thin. Keyword scoring on metadata found real
   data/BI matches in only **4 of 582** releases. **[validated]**
3. The real signal is in the tender documents: 87% are PDFs and about 5% are scanned images
   with no text. **[probable]**: nobody has yet measured how often the documents of a
   relevant tender actually contain the matching terms.
4. The source is unreliable. Throughput swings between about 17 and 0.75 releases/sec, and
   the API returns 500s, timeouts and HTTP 200 responses that aren't JSON. It has been down
   for days at a time. **[validated]**
5. The official portal has no category or keyword email alerts. You can bookmark individual
   tenders and get notified about changes to them, which needs a login. Commercial alert
   services cost R30 to R2,060+ a month (section 3). **[probable]** (the portal's features
   are only partly verified)

**Core value.** The operator gets a trustworthy view of every open tender in plausible
categories, delivered automatically. It never fails silently, it says which days it
couldn't cover, and it costs close to nothing (**US$0.068/month ≈ R1.09/month**, measured). Later it
should also say *which of those tenders actually match*, which needs the document
classification planned for Phase 2.

**What exists today** (the reconstructed baseline):

| Capability | Where |
|---|---|
| Timer at 06:00 and 18:00 UTC (08:00 and 20:00 SAST) | `function_app.py:12` |
| Fetches `LOOKBACK_DAYS = 5` back to today: 6 day-chunks, newest first, under a 420 s budget, with 2 retries per page | `pipeline.py:16-34`, `155-196`, `276-282` |
| Deduplicates by `ocid` (the first one seen wins, which is the newest day) | `pipeline.py:191-194` |
| Keeps tenders that are open (or have no closing date) and that fall in 13 wanted categories, nationwide | `pipeline.py:72-94`, `261-269` |
| Keyword score only *orders* the list | `pipeline.py:36-70`, `271-273` |
| "Blocker" phrase flags, checked on metadata only | `pipeline.py:97-108`, `215-218` |
| Email: top 25 rows in HTML plus the full list as xlsx, a "Partial digest" banner naming missing dates, a failure-notice email if the run crashes | `email_sender.py:12`, `33-95`; `function_app.py:26-32` |
| Offline tests (7 email, 10 pipeline), and CI that runs the tests before it deploys | `tests/`, `.github/workflows/deploy.yml` |

## 3. Prior art and standards (research summary)

No site was blocked by the NTT proxy during this research.

**Official channel.** eTenders (etenders.gov.za) is National Treasury's statutory portal.
Treasury Instruction No. 1 of 2015/16 made it compulsory for departments,
constitutional institutions and public entities to advertise bids and publish awards
there, and municipalities followed from July 2015. The portal's own page advertises
"Subscribe to notifications" and saved tenders once you log in. Third-party guides
(protenders.co.za) say it has **no category/keyword email alerts**, so users check it by
hand every day. My reading, which I haven't verified by logging in, is that the
notifications cover changes to tenders you have bookmarked, not discovery of new ones.
Press coverage suggests compliance with the "publish everything" rule is uneven (Daily
Maverick, 2021, "What's gone wrong with government's tender website?"; I read only the
headline).

**Commercial alert services** (prices in rand per month):

| Service | Price | Checked how |
|---|---|---|
| OnlineTenders, SA | Basic R850 (10 keywords, email alerts); Premium R1,020; Corporate R2,060 | pricing page fetched |
| BidHits | from R710 | search snippet only |
| Tender Services | R550 | search snippet only |
| TenderAlerts | R199–249 | search snippet only |
| TenderSearch | R199 (starter) | search snippet only |
| TenderBulletins | R120–150 | search snippet only |
| TenderFinder | R79 | search snippet only |
| Only Tenders (app) | R30 | search snippet only |
| etenderportal.co.za, protenders.co.za | free listings / alerts | article pages |

Several commercial tools, Apify scrapers and GitHub projects consume the same eTenders
OCDS API. As far as I can tell from their marketing, the commercial services match on
**location, category and keywords**, which is the same thin metadata that fails here. I
found no evidence that any of them match on the text inside the documents.
**[speculative]**: not verified. It matters, because document matching is the one thing
that would make this product clearly better than a R79 to R850 subscription and not just
cheaper.

**OCDS and the eTenders publication.**
- OCDS publishes immutable **releases** (each one tagged `planning`, `tender`,
  `tenderUpdate`, `award`, `contract` and so on) and **records** that merge them into a
  `compiled` snapshot for each `ocid`. The API used here is the *releases* endpoint,
  filtered by release date (`dateFrom`/`dateTo`), so one tender can show up as several
  releases over time.
- The National Treasury publication (OCP Data Registry, publication 143; OCID prefix
  `ocds-9t57fa`) covers February 2017 to September 2026. It has about 35.3k tenders,
  9.6k awards, 1.7k contracts and 37.7k documents. Its known quality issues are
  **duplicate release IDs**, empty fields, placeholder values and a mostly missing `parties`
  array. There is no API key, and the licence is PDDL (open).
- The Transparency Portal (data.etenders.gov.za) says the service is in **public beta**. It
  offers **monthly bulk files** (JSON/CSV/XLSX), and it states that the data "is not
  guaranteed to be accurate … must not be used for any critical decision making". The
  contact is cpo@treasury.gov.za.

Sources: <https://www.etenders.gov.za/Home/opportunities?id=1>,
<https://www.protenders.co.za/etenders-south-africa>,
<https://etenderportal.co.za/articles/how-to-find-tenders-online-free-sources-alerts-shortcuts/>,
<https://www.onlinetenders.co.za/pricing-and-plans.aspx>, <https://www.bidhits.co.za/home.php>,
<https://tenderfinder.co.za/>, <https://tenderalerts.co.za/>, <https://www.onlytenders.co.za/>,
<https://standard.open-contracting.org/latest/en/getting_started/releases_and_records/>,
<https://data.open-contracting.org/en/publication/143>, <https://data.etenders.gov.za/Home/LearnMore>,
<https://www.gov.za/news/media-statements/treasury-launches-central-supplier-database-and-e-tender-publication-portal>,
<https://www.dailymaverick.co.za/article/2021-06-11-whats-gone-wrong-with-governments-tender-website/>.

## 4. Measurable success criteria

These are the baseline that later stages judge against. "Baseline" is what is known
today, and "Unmeasured" means the product doesn't yet log enough to tell. The targets are
my proposals, and the operator should confirm them (section 7).

| # | Criterion | Measure | Baseline | Target |
|---|---|---|---|---|
| SC1 | **Feed coverage.** Every publication day gets fetched while it is still inside the lookback window. | % of calendar days in the last 30 that at least one run fetched successfully before the day left the window. | Unmeasured. Missing days are named in the email but not stored, and a day that leaves the window unfetched disappears without notice. | ≥ 95% of days, excluding days the API itself served nothing all window. 100% of days that are never fetched get named somewhere. |
| SC2 | **Honest gaps.** A digest never claims to be complete when it isn't. | % of emails with a missing chunk that carry the "Partial" subject and banner naming the dates. | Covered by a test (`test_partial_digest_names_the_missing_dates`); not checked in production | 100% |
| SC3 | **Recall of relevant tenders.** No relevant tender is filtered out. | Of the tenders the operator judges relevant in a labelled sample (≥ 50), or a proxy set found by searching document text, the % that appear in some digest. | Unmeasured. The category filter (`pipeline.py:268-269`) is the only gate, and its recall is unknown. | ≥ 90% (Phase 1); ≥ 95% (Phase 2) |
| SC4 | **Ranking usefulness (triage burden).** Relevant tenders sit near the top. | Share of the operator's relevant tenders that appear in the email's top 25 rows; operator-reported triage minutes per day. | Weak. With about 25–37 rows/day over a 6-day window, one email lists about 150–200 open tenders, most scoring 0 and in arbitrary order among ties. | Phase 1: ≤ 15 min/day triage (operator to confirm). Phase 2: ≥ 80% of relevant tenders in the top 10. |
| SC5 | **Delivery reliability.** Every scheduled run ends in an email the operator receives. | % of scheduled runs (2/day) that produce a digest, a partial digest or a failure notice, by mailbox vs. App Insights invocations over 30 days. | Unmeasured. The failure notice goes through the same ACS channel (`email_sender.py:88-95`), so an ACS or config failure is silent. | ≥ 95% of runs; no silent gap longer than 24 h |
| SC6 | **Freshness.** A new tender reaches the operator soon after publication. | Median and 90th-percentile hours from `published` (tenderPeriod.startDate) to the first email that contains it, while the API is healthy. | Unmeasured. The structural best case is about 12 h (the schedule interval) plus the publisher's lag. | Median ≤ 12 h, P90 ≤ 24 h on healthy days |
| SC7 | **Bid lead time.** The tender arrives with enough time left to bid. | % of listed tenders with ≥ N days to closing when first emailed (N set by the operator). | Unmeasured | Report it. The target depends on the operator's N (open question) |
| SC8 | **Running cost.** It stays cheap. | Azure cost per month in rand (Cost Management), for Functions, storage, ACS email and later the LLM. | **US$0.068/month ≈ R1.09/month** (measured in Cost Management, 17–30 Sep 2026; the subscription bills in USD; converted at Microsoft's R16.00 per US$) | Phase 1 ≤ R5/month. Phase 2 ≤ R25/month (inside the R6–22 estimate). Both stay below the cheapest keyword alert service (R30–79) |
| SC9 | **Simplicity / changeability.** It stays easy to read and to tune. | The tests pass in CI before every deploy. Tuning categories, keywords or provinces takes one edit in `pipeline.py`. No new framework or layer. | Met: 17 offline tests, CI gate, tuning constants at `pipeline.py:16-94` | Keep it met |

## 5. Scope

**Must-have now** (the Phase 1 baseline, which is what exists):
- A scheduled twice-daily fetch that survives slow and failing API responses: one day at a
  time, newest first, under a wall-clock budget, with the missing days reported.
- Open-tender, category and province filters that can be tuned in one place, plus a keyword
  score used for ordering.
- An email digest (HTML summary plus the full xlsx), a partial-digest banner and a
  failure notice.
- Offline tests that gate the CI deploy, and no secrets in the repo.

**Gaps against these criteria** (candidates for the BA to prioritise; I'm not prescribing
solutions):
1. SC1, SC5 and SC6 can't be measured today, because nothing records per-run outcomes
   (which dates were fetched or missing, row counts, whether the send succeeded).
2. A day that is still unfetched when it leaves the 5-day window drops out silently. The
   API's multi-day outages make this realistic. Monthly bulk files exist as a possible
   backfill source.
3. Silent non-delivery: if ACS or its settings fail, the failure notice fails too.
4. There's no memory between runs. Each tender reappears in roughly 10 consecutive emails
   with nothing marking it as new, which works against SC4.
5. The HTML top 25 is ordered by a score that is 0 for nearly every row, so it isn't a
   meaningful "top".
6. There's no filter on release `tag`, and rows without a closing date are kept
   (`pipeline.py:262`). Whether award or contract releases get into the digest this way is
   unverified.

**Later (Phase 2, deliberately deferred):**
- Download the tender documents and classify their relevance with a low-cost LLM on
  Azure (gpt-5-nano, DeepSeek-V4-Flash, gpt-oss-120b or gpt-4.1-mini; about
  R6–22/month). Include the blocker checks on the document text.
- Decide how to handle scanned PDFs (about 5%): OCR, or list them as "unreadable, check
  manually".
- A feedback loop where the operator marks tenders relevant or not, to tune the
  categories, keywords and prompt and to measure SC3/SC4.

**Out of scope** (unless the operator says otherwise):
- More than one user, a SaaS/commercial offering, or a web UI or dashboard.
- Preparing or submitting bids, CSD/B-BBEE compliance and document packs.
- Analysing awards and competitors.
- Sources other than eTenders (SITA, SOE and municipal portals, private-sector tenders) and
  other countries. See open question Q7.

## 6. Assumptions and confidence

| # | Assumption | Class | Confidence |
|---|---|---|---|
| A1 | eTenders carries essentially all public tenders the operator could bid on (it has been mandatory since 2015). | assumption | Medium. Compliance is reportedly uneven. |
| A2 | The 13 wanted categories contain nearly all relevant tenders. | assumption | **Low.** Never measured, and it is the only filter (drives SC3). |
| A3 | A new tender shows up as a release dated close to its publication, so a release-date window catches it. | probable | Medium |
| A4 | A 5-day lookback with two runs a day recovers from typical API failures. | assumption | Low–medium. Outages have lasted "days". |
| A5 | Twice-daily delivery is fresh enough; tenders stay open long enough to bid. | probable | Medium. RFQs can have short windows. |
| A6 | The API stays free, keyless and at the same endpoint and shape. | assumption | Medium. It is labelled "public beta". |
| A7 | Phase 1 running cost stays at about today's level, US$0.068/month ≈ R1.09/month, well inside the R5 ceiling. *(Corrected 2026-09-30, OP-3: the earlier "below R1/month" rested on a US-dollar figure mislabelled as rand, and it is false at ≈ R1.09.)* | validated | High (US$0.068/month measured in Cost Management, 17–30 Sep 2026) |
| A8 | ACS email from an Azure-managed domain reaches the inbox, not spam. | assumption | Medium. Not checked. |
| A9 | Tender documents hold enough text to judge relevance. | probable | Medium (87% PDF, about 5% scanned) |
| A10 | An LLM classifier on the documents will lift precision and recall enough to justify R6–22/month. | assumption | Low–medium. Untested. |
| A11 | Commercial alert services match only on metadata, so they share this product's weakness. | speculative | Low. Not verified. |
| A12 | Closing dates and categories in the feed are accurate enough to filter on. | assumption | Medium. Treasury disclaims accuracy, and the registry notes empty and placeholder fields. |
| A13 | There is one user and a handful of recipients; no scale is needed. | validated | High |
| A14 | The operator values readable code over cleverness. | validated | High (stated) |

## 7. Open questions for the operator

1. **Cost ceiling:** what monthly amount in rand is the hard limit, for Phase 1 and for
   Phase 2?
2. **Relevance ground truth:** will you label a sample of about 50–100 tenders from past
   digests as relevant or not? Without it, SC3 and SC4 can't be measured and Phase 2 can't
   be judged.
3. **Triage budget:** how many minutes a day are you willing to spend on the digest, and
   do you read the HTML table or only the xlsx?
4. **Repeats:** is it acceptable for a tender to appear in about 10 emails, or do you want
   new tenders marked (or only new ones sent)?
5. **Lead time:** what is the minimum number of days before closing that still makes a
   tender worth seeing (N in SC7)?
6. **Eligibility limits:** are you CSD-registered? What contract size can you deliver
   alone? Should certain buyers, values or blocker types (e.g. OEM accreditation, CIDB)
   exclude a tender outright?
7. **Other sources:** are SITA, SOE portals, municipal sites or private-sector tenders in
   scope, or is eTenders enough?
8. **Long outages:** when a day can't be fetched within 5 days, should it be backfilled
   (e.g. from the monthly bulk files) or only reported?
9. **Phase 2 trigger:** what evidence would make you start Phase 2 (for example, "Phase 1
   recall is below 90%" or "triage takes more than 15 min/day")?
10. **Outside-work policy:** bidding on government work as an individual while employed
    elsewhere may fall under an employer's outside-business or conflict-of-interest policy.
    Have you confirmed this is fine, and that the Azure subscription and recipient
    addresses are personal, not your employer's?
