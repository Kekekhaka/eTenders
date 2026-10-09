# eTenders digest — Azure Function

Twice a day (08:00 and 20:00 SAST) this Azure Function fetches the last five days of South African government tenders from the eTenders OCDS API. It keeps the open tenders that match the keywords or are consulting services, and asks an AI model about the rest. It then emails a ranked HTML summary plus an xlsx. The running cost is about R1 a month.

The eTenders API is unreliable: it often returns HTTP 500 for whole days. So it is called one day at a time, newest first, under a time budget. If some days cannot be fetched, the email still goes out, marked **Partial digest** and naming the missing dates. Later runs try those days again.

## How a tender gets into the digest

A tender must be **open**: it closes after the run, or has no closing date. Then it needs one of these:

1. **A keyword match.** Whole-word matches on the title and description, weighted by `KEYWORDS` in `pipeline.py`. The score also orders the list.
2. **The consulting services category** (`KEEP_ADDITIONAL_CATEGORIES`).
3. **An AI verdict.** Open tenders that 1 and 2 leave out are judged by an LLM against the data-space profile in `classify.PROMPT`: data engineering, SQL and databases, BI and reporting, plus adjacent software work. Those judged relevant are kept and labelled.

## The AI step (`classify.py`, ADR-0027)

- **Primary:** Llama 3.3 70B on Cloudflare Workers AI, **free plan**. The plan allows 10,000 neurons a day, and each tender uses about 7.
- **Fallback:** gpt-5-nano on Azure OpenAI (`etenders-ai`), reached with the Function's managed identity, so no key is stored. It is capped at **R0.20 per run**. It is used only when Cloudflare fails or the daily allowance runs out.
- **If neither answers:** the tender keeps its keyword result and is retried on the next run.
- **Verdicts are remembered** in the run memory for 14 days, so each tender is judged once.
- **Time limit:** at most 90 s per run, and no AI call starts later than 480 s into the run. The Function timeout is 10 minutes.
- **Off switch:** set `LLM_ENABLED` to anything other than `true`. No deploy is needed.

The choice of model comes from a test on 144 hand-labelled tenders, using `tools/phase2_compare.py`. Llama 70B found 4 of 4 relevant tenders, Llama 8B and nano found 3 of 4 each (nano with the most false alarms), and the keywords found 1.

## Files

| File | Purpose |
|------|---------|
| `function_app.py` | Timer trigger at 06:00 and 18:00 UTC. Runs the whole job: read memory, fetch, AI step, build, send the email, save memory, log one `RUN_SUMMARY` line. |
| `pipeline.py` | Fetches day by day, validates fields, keeps open tenders (keyword, consulting or AI-flagged), scores and orders them. |
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
- `KEEP_ADDITIONAL_CATEGORIES`: categories kept even without a keyword.
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
   - `HEARTBEAT_URL` (optional): a Healthchecks.io ping URL. Off by default.
2. **Access:** the Function's managed identity needs **Storage Blob Data Contributor** on the memory container, and **Cognitive Services OpenAI User** on `etenders-ai`. Both are already granted.
3. **Deploy:** push to `main`. GitHub Actions runs the tests, then deploys. Don't push between 05:45–06:15 or 17:45–18:15 UTC, because a deploy then would kill a run in progress.
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
- **AI-flagged (Llama)** or **AI-flagged (nano):** no keyword matched, but the model judged the tender relevant. Check nano's ones more carefully, because it was less accurate in testing.
- **xlsx:** one `Tenders` sheet with every tender and all columns, including `ai_flag`, `matched_keywords`, `score` and document links.

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

- **The eTenders API** fails for many weekdays. Those days show as partial, and then as missed if they never recover.
- **AI accuracy** comes from a small sample with only 4 relevant tenders. Watch the AI-flagged tenders for the first weeks.
- **Cloudflare's rate limit** is 300 requests a minute for text models on the free plan (Workers AI limits page, 2026-10-09). With `WORKERS = 4` that holds if a call takes 0.8 s or more; the real call time from Azure is not measured yet. If `RUN_SUMMARY` shows `cf_off: "http_429"` while the dashboard shows neurons left, lower `WORKERS` in `classify.py` to 2. To check, run this in Application Insights → Logs: `traces | where message startswith "RUN_SUMMARY" | top 1 by timestamp`.
- **First run after a memory reset:** every tender is marked NEW. Gmail may clip a large email (about 102 KB); open the xlsx.
- **Heartbeat monitoring** is off by default.

## Decisions and history

- **Architecture decisions:** `docs/adr/` (ADR-0026 is the cost guardrail, ADR-0027 the AI step).
- **Team decisions:** `docs/cosmos/TEAM_LEADER_DECISIONS.md`.
- **Sign-off:** `docs/cosmos/SIGN_OFF.md`.
- **Specification:** `docs/cosmos/REQUIREMENTS_PACK.md`, `DESIGN_SPEC.md`, `API_CONTRACT.md`.
- **Agent team:** the master copy is in `Desktop\Python\agents` (GitHub `220404773/agents`). `tools/cosmos_agents.py` is an old copy.
