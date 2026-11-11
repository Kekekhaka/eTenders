# eTenders digest — Azure Function

Once a day, Monday to Friday at 20:00 SAST (no run on weekends), this Azure Function fetches the last five days of South African government tenders from the eTenders OCDS API. An AI model decides which open tenders fit a data-engineering and BI profile, and keywords are only the fallback. It then emails a ranked HTML summary plus an xlsx. The running cost is about R1 a month.

The eTenders API is unreliable. It is called one day at a time, newest first, under a time budget. It answers HTTP 500 when a page contains a record it cannot serve, so a failing page is split in halves until the bad record is alone and only that record is skipped (ADR-0029). The email footer says how many records were skipped. If a whole day cannot be fetched (the server is down, or a timeout), the email still goes out, marked **Partial digest** and naming the missing dates, and later runs try that day again.

## How a tender gets into the digest

A tender must be **open**: it closes after the run, or has no closing date. Then the AI decides (ADR-0028). Every open tender is judged against the profile in `classify.PROMPT`: data engineering, SQL and databases, BI and reporting, plus adjacent software work.

| Situation | Result | Email label |
|---|---|---|
| AI says YES (Llama) | in the digest | none |
| AI says YES (nano fallback) | in the digest | **AI: nano** |
| AI says NO | out. A keyword match it rejected goes on the xlsx sheet *Keyword match, AI said no* | |
| No AI verdict this run | in the digest only if a keyword matches | **NOT AI-CHECKED** |
| `LLM_ENABLED` off | the old rule: keyword match or consulting category | none |

Keyword score (`KEYWORDS` in `pipeline.py`) still orders the list. The consulting category is sent to the AI as context but no longer keeps a tender on its own: on 9 October it let in 38 tenders, almost all unrelated.

## The AI step (`classify.py`, ADR-0027 and ADR-0028)

- **Primary:** Llama 3.3 70B on Cloudflare Workers AI, **free plan**. The plan allows 10,000 neurons a day, and each tender uses about 7.
- **Fallback:** gpt-5-nano on Azure OpenAI (`etenders-ai`), reached with the Function's managed identity, so no key is stored. It is capped at **R0.20 per run**. It is used only when Cloudflare fails or the daily allowance runs out.
- **Cloudflare retries:** a timeout, connection error or HTTP 5xx is tried once more. Cloudflare is switched off for the rest of the run after 3 failed tenders **in a row**, or at once on a 401, 403 or 429.
- **If neither answers:** the tender falls back to its keyword result and is retried on the next run.
- **Verdicts are remembered** in the run memory for 14 days, so each tender is judged once.
- **Time limit:** at most 240 s per run, and no AI call starts later than 350 s into the run, so the email and the memory write still fit in the Function's 10-minute timeout. Tenders not judged in time are judged on the next run.
- **Off switch:** set `LLM_ENABLED` to anything other than `true`. No deploy is needed.

The choice of model comes from a test on 144 hand-labelled tenders, using `tools/phase2_compare.py`. Llama 70B found 4 of 4 relevant tenders, Llama 8B and nano found 3 of 4 each (nano with the most false alarms), and the keywords found 1.

## Briefs from the tender documents (`documents.py`, ADR-0030)

Off until `BRIEFS_ENABLED=true`. For each **new** tender the AI approved (at most 6 a run), the run
reads all its documents from the eTenders portal (up to 8 files) and writes a brief in three parts,
under the title in the email and in the xlsx `brief` column:

1. **Summary** (one AI call on the cover page and the scope of work): Scope, Deliverables, Duration,
   Briefing.
2. **Must submit and Evaluation** (a second AI call on every page about requirements and
   evaluation, from all the files): every mandatory item and returnable, one per line, numbers
   copied exactly; then the evaluation stage by stage.
3. **Found in the documents**: key items found by plain pattern matching, not by the AI (reference
   letters and how many, compulsory briefing, functionality threshold, 80/20 or 90/10, CSD, tax,
   B-BBEE, SBD forms and more), so an item the AI leaves out still shows.

- **What it reads:** PDF and Word files. Scanned PDFs and `.zip` folders are not read (about 4 in 19
  tenders in the November 2026 survey); those tenders get no brief.
- **Cost:** up to about 870 neurons a brief on the free Cloudflare plan; nano fallback about R0.04 a
  brief, capped at R0.10 a run.
- **Safety:** only https links on `www.etenders.gov.za`, size-capped, never saved to disk; the
  document text is passed to the model as untrusted data, and the brief is escaped in the email.
- **Check it:** `python tools/brief_try.py <tender number> [days back]` makes a brief for one tender
  from your laptop (needs `CF_ACCOUNT_ID` and `CF_API_TOKEN`). The brief can still miss or misread
  details: read the documents before you bid.

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Timer trigger at 18:00 UTC (20:00 SAST), Monday to Friday. Runs the whole job: read memory, fetch, AI step, build, send the email, save memory, log one `RUN_SUMMARY` line. |
| `pipeline.py` | Fetches day by day, validates fields, keeps the open tenders the AI approves (keywords as fallback), scores and orders them. |
| `documents.py` | Downloads a tender's documents and returns the text from its scope of work, for the brief. |
| `classify.py` | The AI step: Cloudflare Llama first, Azure nano as fallback, with the cost cap and time limit. |
| `memory.py` | State between runs: tenders already sent, dates covered, AI verdicts. In Azure it's a blob reached with the managed identity; locally it's a JSON file. |
| `report.py` | Renders the HTML email, the subject line and the xlsx. Does no I/O. |
| `email_sender.py` | Sends the email through Azure Communication Services (ACS). |
| `digest.py` | The data contract shared by `pipeline.py` and `report.py`. |
| `TG.py` | Local runner: writes the xlsx and an HTML preview without sending anything. Uses local file memory. |
| `tests/` | Offline tests (496). CI runs them before every deploy. |
| `tools/phase2_compare.py` | Model comparison tool (not deployed). See its docstring. |

## Tuning: edit `pipeline.py`

- `LOOKBACK_DAYS` (4): the window is today minus 4 days to today. A day that fails to fetch is retried while it's in the window.
- `CLOSING_SOON_DAYS` (5): tenders closing within this many days get the **CLOSING SOON** label.
- `MISSED_REPORT_DAYS` (5): how long a day that was never fetched stays named in the email.
- `KEYWORDS`: keyword to weight. A match keeps the tender; the weight ranks it.
- `KEEP_ADDITIONAL_CATEGORIES`: categories kept even without a keyword, used only when the AI step is off.
- `PROVINCES`: empty means nationwide. The API no longer sends a province, so leave it empty.

To change what the AI looks for, edit `PROMPT` in `classify.py`. Then re-run the comparison tool before relying on the result.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q          # offline tests
python TG.py                 # builds etenders_matches.xlsx and etenders_digest.html
```

`TG.py` does not call the AI. Azure runs Python 3.11, so use 3.11 syntax only.

## Deploy to Azure

1. **Settings** on the Function App. Set them with `az functionapp config appsettings set ... -o none` and never commit them.
   - `ACS_CONNECTION_STRING`, `SENDER_EMAIL` (`DoNotReply@...azurecomm.net`), `RECIPIENT_EMAIL` (comma-separated).
   - `LLM_ENABLED=true`, `CF_ACCOUNT_ID`, `CF_API_TOKEN` (needs Workers AI permission), `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_DEPLOYMENT=gpt-5-nano`.
   - `BRIEFS_ENABLED=true` (optional): briefs from the tender documents.
   - `HEARTBEAT_URL` (optional): a Healthchecks.io ping URL. Off by default.
2. **Access:** the Function's managed identity needs **Storage Blob Data Contributor** on the memory container, and **Cognitive Services OpenAI User** on `etenders-ai`. Both are already granted.
3. **Deploy:** push to `main`. GitHub Actions runs the tests, then deploys. Don't push between 17:45 and 18:15 UTC on a weekday, because a deploy then would kill a run in progress.
4. **After every deploy,** check the timer is registered:
   `az functionapp function list -g etenders-rg -n etenders-digest-func --query "[].name" -o tsv`
   It must list `etenders_digest`. If it's empty, re-sync the triggers, or scheduled runs never fire:
   ```powershell
   $sub = az account show --query id -o tsv
   az rest --method post --url "https://management.azure.com/subscriptions/$sub/resourceGroups/etenders-rg/providers/Microsoft.Web/sites/etenders-digest-func/syncfunctiontriggers?api-version=2022-03-01"
   ```

Rollback steps are in `docs/cosmos/DEPLOY_PLAN.md`.

## How to read the email

- **Banners** at the top:
  - **Permanently missed days:** check these dates on etenders.gov.za by hand.
  - **Partial digest:** some days couldn't be fetched yet.
  - **Memory unavailable:** state couldn't be read, so nothing is marked NEW.
- **NEW:** you haven't had this tender in an earlier digest.
- **CLOSING SOON:** closes within 5 days.
- **AI: nano:** judged by the fallback model, which was less accurate in testing. Check it more carefully.
- **NOT AI-CHECKED:** the AI was unavailable for this tender, so it's in on a keyword match only.
- **xlsx:** the `Tenders` sheet has every tender and all columns. `ai_flag` says who decided: `Llama`, `nano` or `not AI-checked`. A second sheet, *Keyword match, AI said no*, lists keyword matches the AI rejected. Skim it for the first weeks to catch AI mistakes.

## Checking a run

Each run logs one `RUN_SUMMARY` JSON line to Application Insights. It includes days fetched, counts, memory status, email result and an `ai` section:
- remembered and candidate counts;
- calls answered by `llama` and by `nano`;
- `cf_off` and `nano_off` reasons;
- `nano_zar` spent.

Check the Cloudflare dashboard (Workers AI) for neuron use.

## Cost

| Item | Monthly |
|---|---|
| Function, storage, ACS email, Application Insights | about US$0.068 (about R1.09), measured 2026-09-30 |
| Cloudflare Workers AI (free plan) | R0. Calls fail rather than bill when the allowance runs out. |
| Azure gpt-5-nano fallback | normally about R0. Worst case R12 (R0.20 × 2 runs × 30 days), if Cloudflare were down all month. |
| Managed identity and role assignments | R0 |

Prices are from the Azure Retail Prices API (ZAR, South Africa North) and Cloudflare's pricing page, October 2026. Microsoft's rate is R16.00 per US$.

## Known limits

- **The eTenders API** cannot serve some records (1 to 7 a day in October 2026, usually among the first of the day). They are skipped and counted in the email footer, so a relevant tender could be missing: search etenders.gov.za by hand if you suspect a gap. Whole days still fail when the server is down or times out.
- **AI accuracy** comes from a small sample with only 4 relevant tenders. For the first weeks, watch the *Keyword match, AI said no* sheet for tenders the AI wrongly rejected.
- **Cloudflare's rate limit** is 300 requests a minute for text models on the free plan (Workers AI limits page, 2026-10-09). The AI step runs 8 calls at once (`WORKERS` in `classify.py`) but starts them at least 0.25 s apart (`CF_MIN_INTERVAL_S`), so it never asks for more than 240 a minute. On 11 November 2026 a call took about 6 s, and 4 workers judged only about 150 of 218 new tenders in the time allowed; 8 should clear a day. If `RUN_SUMMARY` shows a large `unclassified` count, raise `WORKERS`; if it shows `cf_off: "http_429"` while the dashboard shows neurons left, raise `CF_MIN_INTERVAL_S`. To check, run this in Application Insights > Logs: `traces | where message startswith "RUN_SUMMARY" | top 1 by timestamp`.
- **First run after a memory reset:** every tender is marked NEW. Gmail may clip a large email (about 102 KB); open the xlsx.
- **Heartbeat monitoring** is off by default.

## Decisions and history

- **Architecture decisions:** `docs/adr/` (ADR-0026 is the cost guardrail, ADR-0027 the AI step, ADR-0028 the AI as decider, ADR-0029 skipping bad API records, ADR-0030 briefs from tender documents).
- **Team decisions:** `docs/cosmos/TEAM_LEADER_DECISIONS.md`.
- **Sign-off:** `docs/cosmos/SIGN_OFF.md`.
- **Specification:** `docs/cosmos/REQUIREMENTS_PACK.md`, `DESIGN_SPEC.md`, `API_CONTRACT.md`.
- **Agent team:** the master copy is in `Desktop\Python\agents` (GitHub `220404773/agents`). `tools/cosmos_agents.py` is an old copy.
